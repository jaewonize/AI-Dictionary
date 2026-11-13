// Claude Code PreToolUse(Bash) 훅: 토큰·API 키가 있는 .env·.env.*를 Bash로 읽으려 하면 막는다. settings.json의 Read(./.env) deny 규칙은 Read 도구에만 걸려 head·grep 같은 Bash 읽기는 못 막는다.
// 입력은 stdin의 훅 JSON(tool_input.command, cwd)이고, 막을 때는 이유를 stderr에 쓰고 exit 2로 끝낸다(Claude가 이유를 받는다).
// 명령 단어(붙여 쓴 것까지 리다이렉션 대상, sh -c·eval·node -e·$( ) 같은 다시 나눠 실행되는 문자열, 인터프리터로 들어가는 heredoc 본문, 인용하지 않은 heredoc의 명령 치환 포함)에
// .env 경로나 그에 맞을 수 있는 glob이 있거나, .env가 있는 디렉터리(cd·pushd로 바뀐 디렉터리 포함)를 grep -r로 훑으면 막는다. 변수·인코딩으로 만든 경로, 파일에 쓴 뒤 실행하는 스크립트, .env를 스스로 읽는 도구(docker compose 등)는 잡지 못한다.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { HEREDOC, binName, splitCommands, stripHeredocs } from "./guard-git.ts";

// .env·.env.local·.envrc 등(.gitignore·guard-git.ts와 같은 범위). 앞은 경로 구분자(/·경로 뒤 \)·옵션 값(--env-file=)·git rev(HEAD:). 정규식 이스케이프(\.env)는 제외한다.
// 접미는 공백·따옴표·백틱에서 끊어 메시지 글(`.env.local`·`echo x`)을 경로로 보지 않는다
const ENV_PATH = /(?:^|[/=:]|[^\s'"`=]\\)\.env(?:rc|\.[^/\\\s'"`]*)?$/i;
const ENV_ENTRY = /^\.env(?:rc|\.|$)/i;
// 단어 안 어디든 붙은 리다이렉션 연산자(split의 구분자로 남김)
const REDIRECTS = /(\d*(?:&>>?|>>?&?|<<<|<<?&?|>\|))/;
// 인용하지 않은 heredoc 본문에서 셸이 실행하는 명령 치환(괄호 두 겹까지)
const SUBSTITUTION = /\$\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)|`[^`]*`/g;
const SHELL = /^(?:ba|z|da|k)?sh$/;
// 셸 밖에서 코드 문자열을 받는 명령과 그 옵션. 값은 옵션에 붙여 쓰거나(캡처: -S'cat x'·--eval=…) 다음 단어다. 다른 명령의 -e(rg -e 패턴 등)는 코드로 보지 않는다
const CODE_OPTION: [RegExp, RegExp][] = [
  [/^(?:node|nodejs|bun)$/, /^(?:-[A-Za-z]*[ep]|--(?:eval|print))(?:=([\s\S]*))?$/],
  [/^python[\d.]*$/, /^-[A-Za-z]*c([\s\S]*)$/],
  [/^(?:perl|ruby)$/, /^-[A-Za-z]*[eE]([\s\S]*)$/],
  [/^php$/, /^-r([\s\S]*)$/],
  [/^env$/, /^(?:-[A-Za-z]*S|--split-string=?)([\s\S]*)$/],
];
// heredoc·here-string 본문을 코드로 읽는 명령
const INTERPRETER = /^(?:(?:ba|z|da|k)?sh|node|nodejs|deno|bun|python[\d.]*|perl|ruby|php)$/;
// 값을 다음 인자로 받는 grep 긴 옵션
const GREP_VALUE = new Set(["--regexp", "--file", "--max-count", "--after-context", "--before-context", "--context", "--include", "--exclude", "--exclude-from", "--exclude-dir", "--label", "--binary-files", "--devices", "--directories"]);

const BLOCK = {
  read: ".env·.env.*에는 토큰·API 키가 있어 Bash에서 그 경로를 쓰는 명령(읽기·존재 확인·복사 포함)은 막는다. 꼭 필요하면 사용자에게 직접 실행해 달라고 한다.",
  grep: ".env·.env.*가 있는 디렉터리를 grep -r로 훑으면 토큰·API 키까지 읽으므로 막는다. Grep 도구나 rg(.gitignore·숨김 파일을 기본으로 건너뜀)를 쓰거나 .env가 없는 하위 디렉터리를 지정한다.",
};

// 명령이 돌 수 있는 디렉터리들. undefined는 셸이 펼쳐 알 수 없는 디렉터리(cd "$D"·cd -)
type Dirs = (string | undefined)[];

// 막아야 하면 이유를, 아니면 undefined를 돌려준다
export function blockReason(command: string, cwd: string): string | undefined {
  // 같은 문자열·디렉터리는 한 번만 나눠 보므로(seen) 감싸기가 겹쳐도 빨리 끝난다
  const seen = new Set<string>();
  const visit = (script: string, start: Dirs): string | undefined => {
    const key = `${start.map(d => d ?? "?").join("\0")}\n${script}`;
    if (seen.has(key)) return undefined;
    seen.add(key);
    let dirs = start;
    for (const words of splitCommands(script, true)) {
      if (grepsEnvDir(words, dirs)) return BLOCK.grep;
      const inner = codeStrings(words);
      const interpreted = words.some(w => INTERPRETER.test(binName(w.split(REDIRECTS)[0])));
      for (let i = 0; i < words.length; i++) {
        // 따옴표 밖 리다이렉션은 명령·인자에 붙어 있어도(cat<.env, x>.env) 대상을 따로 연다
        const segments = words[i].split(REDIRECTS);
        for (let k = 0; k < segments.length; k += 2) {
          if (mentionsEnv(segments[k])) return BLOCK.read;
          // here-string은 인터프리터가 받을 때만 코드다
          if (interpreted && segments[k - 1]?.includes("<<<")) inner.push(segments[k] || (words[i + 1] ?? ""));
        }
      }
      for (const s of inner) {
        const reason = visit(s, dirs);
        if (reason) return reason;
      }
      dirs = changedDirs(words, dirs);
    }
    // 큰따옴표 안 명령 치환과 heredoc은 cd를 모두 반영한 디렉터리로 본다(앞 디렉터리도 남아 있어 넓게 본다)
    for (const s of quotedSubstitutions(script)) {
      const reason = visit(s, dirs);
      if (reason) return reason;
    }
    for (const m of script.matchAll(HEREDOC)) {
      if (!m[0].includes("\n")) continue;
      const body = m[0].slice(m[0].indexOf("\n") + 1, m[0].lastIndexOf("\n")).replace(/\r$/, "");
      // <<EOF 앞 같은 줄의 마지막 명령이나 표시 뒤 같은 줄의 파이프(cat <<'EOF' | node)에 인터프리터가 있으면 본문 전체가 코드로 실행된다.
      // cat·git commit -F - 등의 본문(메시지)은 구분자를 인용하지 않았을 때(<<EOF) 셸이 실행하는 명령 치환만 본다
      const before = splitCommands(script.slice(script.lastIndexOf("\n", m.index - 1) + 1, m.index)).at(-1) ?? [];
      const after = splitCommands(m[0].slice(0, m[0].indexOf("\n")).replace(/\r$/, "").replace(/^<<-?[ \t]*(['"]?)\w+\1/, "").split(/[;&]/)[0]).flat();
      const owner = [...before, ...after];
      const code = owner.some(w => INTERPRETER.test(binName(w))) ? [body] : m[1] ? [] : [...body.matchAll(SUBSTITUTION)].map(s => s[0]);
      for (const s of code) {
        const reason = visit(s, dirs);
        if (reason) return reason;
      }
    }
    return undefined;
  };
  return visit(command, [cwd]);
}

// 조각에서 다시 나눠 실행되는 문자열: eval 뒤 단어 전체, 셸 -c(-lc·-cu처럼 어디에 합쳐도)의 스크립트(그 뒤 첫 옵션 아닌 인자), CODE_OPTION의 값
function codeStrings(words: string[]): string[] {
  const out: string[] = [];
  words.forEach((w, i) => {
    const name = binName(w);
    const rest = words.slice(i + 1);
    if (name === "eval") out.push(rest.join(" "));
    if (SHELL.test(name)) {
      const c = rest.findIndex(a => /^-[A-Za-z]*c[A-Za-z]*$/.test(a));
      if (c >= 0) out.push(rest.slice(c + 1).find(a => !a.startsWith("-")) ?? "");
    }
    const option = CODE_OPTION.find(([cmd]) => cmd.test(name))?.[1];
    if (option) {
      rest.forEach((a, j) => {
        const m = option.exec(a);
        if (m) out.push(m[1] || (rest[j + 1] ?? ""));
      });
    }
  });
  return out;
}

// 큰따옴표 안의 명령 치환($( )·`…`)을 꺼낸다. 작은따옴표 안은 글자 그대로라 실행되지 않고, 따옴표 밖 치환은 splitCommands가 ( ) `로 나눈다
function quotedSubstitutions(script: string): string[] {
  const text = stripHeredocs(script);
  const out: string[] = [];
  let dq = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") {
      i++;
    } else if (c === "'" && !dq) {
      const end = text.indexOf("'", i + 1);
      i = end < 0 ? text.length : end;
    } else if (c === '"') {
      dq = !dq;
    } else if (dq && c === "`") {
      const end = text.indexOf("`", i + 1);
      const stop = end < 0 ? text.length : end;
      out.push(text.slice(i + 1, stop));
      i = stop;
    } else if (dq && c === "$" && text[i + 1] === "(") {
      let j = i + 1;
      for (let depth = 0; j < text.length; j++) {
        if (text[j] === "(") depth++;
        else if (text[j] === ")" && --depth === 0) break;
      }
      out.push(text.slice(i + 2, j));
      i = j;
    }
  }
  return out;
}

// cd·pushd 뒤 명령은 바뀐 디렉터리에서 돈다. 서브셸((cd x); …)일 수도 있으므로 앞 디렉터리도 남긴다
function changedDirs(words: string[], dirs: Dirs): Dirs {
  const i = words.findIndex(w => w !== "builtin" && w !== "command");
  if (!/^(?:cd|pushd)$/.test(words[i] ?? "")) return dirs;
  const target = words.slice(i + 1).find(a => !/^-[LPe@]+$/.test(a));
  const home = (target ?? "~").replace(/^~(?=[/\\]|$)/, os.homedir());
  const next = target === "-" || /[$`*?[]/.test(home) ? [undefined] : dirs.map(d => (path.isAbsolute(home) ? home : d && path.resolve(d, home)));
  return [...new Set([...dirs, ...next])];
}

// 단어가 .env 경로이거나 .env에 펼쳐질 수 있는 glob인지. 중괄호 확장(.{env,x})은 펼친 결과마다, 코드 인자('.env', 'utf8')는 , { }로 나눈 조각마다 본다
function mentionsEnv(word: string): boolean {
  return [...expandBraces(word), ...word.split(/[,{}]/)].some(mentionsEnvPiece);
}

// 셸 중괄호 확장을 앞뒤 글자를 붙여 펼친다(a{b,c}d → abd·acd, .e{m..o}v → .emv·.env·.eov). 결과가 많으면 앞 64개만 본다
function expandBraces(word: string, limit = 64): string[] {
  for (let i = 0; i < word.length; i++) {
    if (word[i] !== "{") continue;
    const commas: number[] = [];
    let j = i;
    for (let depth = 0; j < word.length; j++) {
      if (word[j] === "{") depth++;
      else if (word[j] === "}" && --depth === 0) break;
      else if (word[j] === "," && depth === 1) commas.push(j);
    }
    if (j >= word.length) break;
    const body = word.slice(i + 1, j);
    const range = /^(-?\d+|[A-Za-z])\.\.(-?\d+|[A-Za-z])$/.exec(body);
    const alts = commas.length
      ? [i, ...commas].map((c, k) => word.slice(c + 1, commas[k] ?? j))
      : range
        ? sequence(range[1], range[2], limit)
        : undefined;
    // {x}처럼 펼치지 않는 중괄호는 글자로 두고 다음 {를 찾는다
    if (!alts) continue;
    const out: string[] = [];
    for (const alt of alts) {
      for (const s of expandBraces(word.slice(0, i) + alt + word.slice(j + 1), limit)) {
        out.push(s);
        if (out.length >= limit) return out;
      }
    }
    return out;
  }
  return [word];
}

// {1..3}·{a..c} 시퀀스. 숫자끼리나 글자끼리만 펼친다
function sequence(from: string, to: string, limit: number): string[] | undefined {
  const numeric = /\d/.test(from);
  if (numeric !== /\d/.test(to)) return undefined;
  const [a, b] = numeric ? [Number(from), Number(to)] : [from.charCodeAt(0), to.charCodeAt(0)];
  const step = a <= b ? 1 : -1;
  const out: string[] = [];
  for (let n = a; out.length < limit; n += step) {
    out.push(numeric ? String(n) : String.fromCharCode(n));
    if (n === b) break;
  }
  return out;
}

function mentionsEnvPiece(word: string): boolean {
  if (ENV_PATH.test(word)) return true;
  // 셸 glob은 점으로 시작하는 이름에만 점 파일을 펼친다. .env·.env.*·.envrc에 맞을 수 있으면(.*·.e*·.[e]nv·.en?.staging) 막고,
  // jq 필터(.[]·.[0].name)나 정규식(\.e*)처럼 맞을 수 없는 것은 통과시킨다
  const base = word.replace(/^(?:.*[/=:]|.*[^\s'"`=]\\)/, "");
  if (!base.startsWith(".")) return false;
  const tokens = globTokens(base);
  return tokens !== undefined && globMatchesEnv(tokens);
}

// 셸 glob을 글자 판정 목록으로 나눈다(*는 null). *·?·닫힌 [ ]가 없으면 셸이 펼치지 않으므로 undefined
function globTokens(glob: string): (((c: string) => boolean) | null)[] | undefined {
  const tokens: (((c: string) => boolean) | null)[] = [];
  let wild = false;
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    // [ 뒤 첫 글자(!·^ 다음)의 ]는 닫는 괄호가 아니라 글자다
    const neg = c === "[" && (glob[i + 1] === "!" || glob[i + 1] === "^");
    const close = c === "[" ? glob.indexOf("]", i + (neg ? 3 : 2)) : -1;
    if (c === "*" || c === "?" || close > 0) wild = true;
    if (c === "*") {
      tokens.push(null);
    } else if (c === "?") {
      tokens.push(() => true);
    } else if (close > 0) {
      // 거꾸로 된 범위([z-a]) 등으로 정규식을 만들 수 없으면 아무 글자에나 맞는다고 본다. 셸은 그 범위만 버리고 나머지 글자([z-ae]의 e)는 살리므로 막는 쪽으로 넓게 본다
      let set: RegExp | undefined;
      try {
        set = new RegExp(`[${neg ? "^" : ""}${glob.slice(i + (neg ? 2 : 1), close).replace(/[\\\]^[]/g, "\\$&")}]`, "i");
      } catch {}
      tokens.push(x => set?.test(x) ?? true);
      i = close;
    } else {
      tokens.push(x => x.toLowerCase() === c.toLowerCase());
    }
  }
  return wild ? tokens : undefined;
}

// glob이 .env 자체나 .env.로 시작하는 어떤 이름에 맞을 수 있는지. ".env"를 읽고 패턴이 끝나거나, ".env."를 읽은 뒤 남은 상태가 있으면
// (남은 글자·?·[ ]·*는 늘 맞는 접미를 만들 수 있다) 맞는다
function globMatchesEnv(tokens: (((c: string) => boolean) | null)[]): boolean {
  const closure = (states: Set<number>) => {
    for (const s of states) if (tokens[s] === null) states.add(s + 1);
    return states;
  };
  const run = (text: string) => {
    let states = closure(new Set([0]));
    for (const ch of text) {
      const next = new Set<number>();
      for (const s of states) {
        const t = tokens[s];
        if (t === null) next.add(s);
        else if (t?.(ch)) next.add(s + 1);
      }
      states = closure(next);
    }
    return states;
  };
  return run(".env").has(tokens.length) || run(".env.").size > 0 || run(".envrc").has(tokens.length);
}

// grep -r·-R·-d recurse로 .env·.env.*를 바로 담은 디렉터리를 훑는지. 경로가 없으면 현재 디렉터리를 훑는다
function grepsEnvDir(words: string[], dirs: Dirs): boolean {
  const start = words.findIndex(w => /^[ef]?grep$/.test(binName(w)));
  if (start < 0) return false;
  // 리다이렉션(2>/dev/null, > out)은 검색 경로가 아니므로 연산자와 대상을 뗀다
  const args: string[] = [];
  for (let i = start + 1; i < words.length; i++) {
    const segments = words[i].split(REDIRECTS);
    if (segments.length === 1 || segments[0] !== "") args.push(segments[0]);
    if (segments.length > 1 && segments.at(-1) === "") i++;
  }
  let recursive = false;
  let patternGiven = false;
  const operands: string[] = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--") {
      operands.push(...args.slice(i + 1));
      break;
    }
    if (a.startsWith("--")) {
      const [name, value] = a.includes("=") ? [a.slice(0, a.indexOf("=")), a.slice(a.indexOf("=") + 1)] : [a, GREP_VALUE.has(a) ? args[++i] : undefined];
      if (name === "--recursive" || name === "--dereference-recursive" || (name === "--directories" && value === "recurse")) recursive = true;
      if (name === "--regexp" || name === "--file") patternGiven = true;
    } else if (a.startsWith("-") && a.length > 1) {
      for (let j = 1; j < a.length; j++) {
        if (a[j] === "r" || a[j] === "R") recursive = true;
        if ("efmABCdD".includes(a[j])) {
          const value = j < a.length - 1 ? a.slice(j + 1) : args[++i];
          if (a[j] === "e" || a[j] === "f") patternGiven = true;
          if (a[j] === "d" && value === "recurse") recursive = true;
          break;
        }
      }
    } else {
      operands.push(a);
    }
  }
  if (!recursive) return false;
  const paths = patternGiven ? operands : operands.slice(1);
  return (paths.length ? paths : ["."]).some(p => dirs.some(d => hasEnvEntry(p, d)));
}

function hasEnvEntry(p: string, cwd: string | undefined): boolean {
  // 셸이 펼치는 $·`는 실제 경로를 알 수 없으므로 막는 쪽으로 본다
  if (/[$`]/.test(p)) return true;
  // glob(*·src/*·src\*)은 펼쳐질 디렉터리를 본다
  const g = p.search(/[*?[]/);
  const sep = g < 0 ? -1 : Math.max(p.lastIndexOf("/", g), p.lastIndexOf("\\", g));
  const plain = (g < 0 ? p : sep < 0 ? "." : p.slice(0, sep + 1)).replace(/^~(?=[/\\]|$)/, os.homedir());
  // cd "$D" 뒤처럼 현재 디렉터리를 알 수 없으면 상대 경로는 막는 쪽으로 본다
  if (cwd === undefined && !path.isAbsolute(plain)) return true;
  const dir = path.resolve(cwd ?? "/", plain);
  try {
    return fs.statSync(dir).isDirectory() && fs.readdirSync(dir).some(n => ENV_ENTRY.test(n));
  } catch {
    return false;
  }
}

export function runHook(): void {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const command = input?.tool_input?.command;
  if (typeof command === "string") {
    const reason = blockReason(command, input.cwd ?? process.cwd());
    if (reason) {
      process.stderr.write(reason + "\n");
      process.exit(2);
    }
  }
}

if (process.argv[1] && fs.existsSync(process.argv[1]) && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runHook();
}
