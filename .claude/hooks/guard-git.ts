// Claude Code PreToolUse(Bash) 훅: main에 직접 커밋·푸시하거나 토큰·.env를 커밋하려 하면 막는다. AGENTS.md "브랜치와 흐름"·"커밋" 규칙을 하네스가 강제한다.
// 입력은 stdin의 훅 JSON(tool_input.command, cwd)이고, 막을 때는 이유를 stderr에 쓰고 exit 2로 끝낸다(Claude가 이유를 받는다).
// 훅은 명령 실행 전 상태만 보므로 허용 목록으로 판정한다. commit·push는 단독 호출이고 전역 옵션이 -C <그대로 쓴 경로>·--no-pager뿐일 때만 검사해 통과시키고,
// 다른 명령(cd·git add·괄호 등)·환경변수 대입·env·감싼 실행과 섞이거나 경로 지정 커밋이면 결과를 확정할 수 없어 막는다.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 흔한 토큰·API 키 형식: GitHub(ghp_·github_pat_), OpenAI·Anthropic(sk-…), AWS 액세스 키(AKIA…), Google API 키(AIza…)
const SECRET = /\b(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{22,}|sk-[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35})/;
// .gitignore의 .env*와 같은 범위(.env·.env.local·.envrc 등). Windows 역슬래시 경로와 대소문자 무시 파일 시스템도 함께 본다
const ENV_FILE = /(?:^|[/\\])\.env[^/\\]*$/i;
export const binName = (p: string) => p.split(/[/\\]/).pop()?.replace(/\.exe$/i, "") ?? "";
export const HEREDOC = /<<-?[ \t]*(['"]?)(\w+)\1[^\r\n]*(?:\r?\n[\s\S]*?)?\r?\n[ \t]*\2[ \t]*(?=\r?\n|$)/g;

export type Git = {
  // git -C <dir>의 현재 브랜치, 저장소가 아니면 undefined
  branch: (dir: string) => string | undefined;
  // 커밋에 들어갈 파일 이름과 diff. all이면 commit -a처럼 추적 중인 수정도 포함한다
  staged: (dir: string, all: boolean) => { names: string[]; diff: string };
  // git config --get-all 값, 없으면 빈 배열
  config: (dir: string, key: string) => string[];
};

const BLOCK = {
  alone: "git commit·push는 다른 명령(cd·git add·괄호 등)과 잇지 않고 단독 Bash 호출로 실행한다. 다른 디렉터리는 git -C <경로>로 지정한다.",
  wrapped: "sh -c·eval·xargs 등으로 감싼 git commit·push는 훅이 검사할 수 없어 막는다. git을 직접 실행한다.",
  env: "환경변수 대입(GIT_INDEX_FILE= 등)이나 env를 붙인 git commit·push는 훅이 검사할 대상을 확정할 수 없어 막는다. 대입 없이 실행한다.",
  globals: "그대로 쓴 경로의 -C와 --no-pager 밖의 git 전역 옵션(-c·--git-dir, ~·$를 쓴 -C 등)을 붙인 commit·push는 훅이 결과를 확정할 수 없어 막는다. 옵션 없이 실행한다.",
  pathspec: "경로를 지정하거나 작업 트리에서 고르는 커밋(git commit <경로>·--only·--include·--patch·--interactive)은 막는다. git add로 스테이징한 뒤 경로 없이 git commit을 실행한다.",
  commitMain: "main에는 직접 커밋하지 않는다(AGENTS.md \"브랜치와 흐름\"). main이 아닌 작업 브랜치(예: feat/*, hackathon)에서 커밋한다.",
  pushMain: "main으로 갈 수 있는 푸시는 막는다(AGENTS.md \"브랜치와 흐름\"). git push <원격> <브랜치>처럼 main이 아닌 브랜치를 지정해 푸시한다.",
  secret: "커밋할 변경에 토큰·API 키(ghp_·sk-·AKIA 등)로 보이는 값이 있다(AGENTS.md \"커밋\"). 값을 지우고 다시 스테이징한다.",
};

// 값을 다음 인자로 받는 commit·push 옵션
const COMMIT_VALUE = new Set(["--message", "--file", "--reuse-message", "--reedit-message", "--author", "--date", "--fixup", "--squash", "--trailer", "--cleanup", "--template"]);
const COMMIT_SHORT_VALUE = "mFCct";
const PUSH_VALUE = new Set(["-o", "--push-option", "--receive-pack", "--exec"]);
// 값을 다음 인자로 받을 수 있는 git 전역 옵션
const GLOBAL_VALUE = new Set(["-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"]);

// 막아야 하면 이유를, 아니면 undefined를 돌려준다
export function blockReason(command: string, cwd: string, git: Git): string | undefined {
  const parts = executedParts(command);
  const kinds = parts.map(callKind);
  if (kinds.includes("wrapped")) return BLOCK.wrapped;
  if (kinds.includes("env")) return BLOCK.env;
  if (commandSubstitutions(command).shellBodies.some(b => scriptCallsCommitOrPush(b))) {
    const first = dropPrefixes(parts[0] ?? [])[0] ?? "";
    return /^\w+=/.test(first) || binName(first) === "env" ? BLOCK.env : BLOCK.wrapped;
  }
  if (!kinds.includes("direct")) return undefined;
  // 앞뒤 명령(cd·괄호 안의 cd·git add·대입·명령 치환 등)이 실행 디렉터리와 상태를 바꿀 수 있으므로 단독 호출만 검사한다
  if (kinds[0] !== "direct" || splitCommands(command).length > 1) return BLOCK.alone;
  const parsed = parseGit(dropPrefixes(parts[0]).slice(1));
  if (parsed.unknownGlobal) return BLOCK.globals;
  if (parts.length > 1) return BLOCK.alone;
  const target = parsed.dir === undefined ? cwd : path.resolve(cwd, parsed.dir);
  const branch = git.branch(target);
  if (parsed.sub === "push") return pushMayReachMain(parsed.rest, branch, target, git) ? BLOCK.pushMain : undefined;
  const commit = parseCommit(parsed.rest);
  if (commit.pathspec) return BLOCK.pathspec;
  if (branch === "main") return BLOCK.commitMain;
  let staged: { names: string[]; diff: string };
  try {
    staged = git.staged(target, commit.all);
  } catch {
    return "커밋할 변경을 읽지 못해 막는다. git status로 저장소 상태를 확인한다.";
  }
  const { names, diff } = staged;
  const envFiles = names.filter(n => ENV_FILE.test(n));
  if (envFiles.length) return `.env 파일은 커밋하지 않는다(AGENTS.md "커밋"): ${envFiles.join(", ")}. git restore --staged로 뺀다.`;
  if (diff.split(/\r?\n/).some(l => l.startsWith("+") && !l.startsWith("+++") && SECRET.test(l))) return BLOCK.secret;
  return undefined;
}

// 조각이 commit·push를 부르는 방식: 직접(git … commit), 대입·env를 붙임, 다른 명령으로 감쌈, 부르지 않음(undefined)
function callKind(raw: string[]): "direct" | "env" | "wrapped" | undefined {
  const words = dropPrefixes(raw);
  if (!words.length) return undefined;
  const [cmd, ...args] = words;
  if (binName(cmd) === "git" && /^(?:commit|push)$/.test(parseGit(args).sub ?? "")) return "direct";
  if (!callsCommitOrPush(words)) return undefined;
  return /^\w+=/.test(cmd) || binName(cmd) === "env" ? "env" : "wrapped";
}

// 명령을 ; & | 줄바꿈 괄호로 나눈 조각마다, 따옴표·백슬래시를 풀어 단어 목록으로 만든다. heredoc 본문은 지운다.
// keepRedirects면 리다이렉션(< .env 등)을 떼지 않고 단어로 남긴다
export function splitCommands(command: string, keepRedirects = false): string[][] {
  const text = stripHeredocs(command);
  const parts: string[][] = [];
  // plain: 단어가 따옴표 밖 글자로 시작했는지(따옴표 안의 <·>는 리다이렉션이 아니다)
  let words: { text: string; plain: boolean }[] = [];
  let word: { text: string; plain: boolean } | undefined;
  const add = (s: string, plain: boolean) => {
    word = word ?? { text: "", plain };
    word.text += s;
  };
  const endWord = () => {
    if (word) words.push(word);
    word = undefined;
  };
  const endPart = () => {
    endWord();
    const kept = keepRedirects ? words.map(w => w.text) : stripRedirects(words);
    if (kept.length) parts.push(kept);
    words = [];
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "#" && !word) {
      const nl = text.indexOf("\n", i + 1);
      i = nl < 0 ? text.length : nl - 1;
    } else if (c === "$" && text[i + 1] === "'") {
      let j = i + 2;
      add("", false);
      for (; j < text.length && text[j] !== "'"; j++) {
        if (text[j] === "\\" && j + 1 < text.length) j++;
        add(text[j], false);
      }
      i = j;
    } else if (c === "'") {
      const end = text.indexOf("'", i + 1);
      const stop = end < 0 ? text.length : end;
      add(text.slice(i + 1, stop), false);
      i = stop;
    } else if (c === '"') {
      let j = i + 1;
      add("", false);
      for (; j < text.length && text[j] !== '"'; j++) {
        if (text[j] === "\\" && (text[j + 1] === "\n" || (text[j + 1] === "\r" && text[j + 2] === "\n"))) {
          j += text[j + 1] === "\r" ? 2 : 1;
          continue;
        }
        if (text[j] === "\\" && j + 1 < text.length && '"\\$`'.includes(text[j + 1])) {
          add(text[++j], false);
        } else if (text[j] === "$" && text[j + 1] === "(") {
          const end = findParenEnd(text, j + 1);
          add(text.slice(j, end + Math.min(1, text.length - end)), false);
          j = end;
        } else if (text[j] === "`") {
          let k = j + 1;
          for (; k < text.length && text[k] !== "`"; k++) if (text[k] === "\\") k++;
          add(text.slice(j, k + Math.min(1, text.length - k)), false);
          j = k;
        } else {
          add(text[j], false);
        }
      }
      i = j;
    } else if (c === "\\" && (text[i + 1] === "\n" || (text[i + 1] === "\r" && text[i + 2] === "\n"))) {
      i += text[i + 1] === "\r" ? 2 : 1; // 줄 연속: 백슬래시와 줄바꿈(LF·CRLF)을 함께 지운다
    } else if (c === "\\" && i + 1 < text.length) {
      add(text[++i], false);
    } else if (c === "&" && (text[i - 1] === ">" || text[i - 1] === "<" || text[i + 1] === ">")) {
      add(c, true);
    } else if (/[;&|\r\n()`]/.test(c)) {
      endPart();
    } else if (/\s/.test(c)) {
      endWord();
    } else {
      add(c, true);
    }
  }
  endPart();
  return parts;
}

// $( 뒤의 '(' 위치(start)에서 시작해 따옴표·백슬래시·주석을 건너뛰고 짝이 맞는 ')' 위치를 찾는다
function findParenEnd(text: string, start: number): number {
  let depth = 0;
  let wordStart = true;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") {
      i += text[i + 1] === "\r" && text[i + 2] === "\n" ? 2 : 1;
      wordStart = false;
    } else if (c === "#" && wordStart && i > start) {
      const nl = text.indexOf("\n", i + 1);
      i = nl < 0 ? text.length : nl - 1;
    } else if (c === "$" && text[i + 1] === "'") {
      for (i += 2; i < text.length && text[i] !== "'"; i++) if (text[i] === "\\") i++;
      wordStart = false;
    } else if (c === "'") {
      const end = text.indexOf("'", i + 1);
      i = end < 0 ? text.length : end;
      wordStart = false;
    } else if (c === '"') {
      for (i++; i < text.length && text[i] !== '"'; i++) {
        if (text[i] === "\\") i += text[i + 1] === "\r" && text[i + 2] === "\n" ? 2 : 1;
        else if (text[i] === "$" && text[i + 1] === "(") i = findParenEnd(text, i + 1);
        else if (text[i] === "`") {
          for (i++; i < text.length && text[i] !== "`"; i++) if (text[i] === "\\") i++;
        }
      }
      wordStart = false;
    } else if (c === "`") {
      for (i++; i < text.length && text[i] !== "`"; i++) if (text[i] === "\\") i++;
      wordStart = false;
    } else if (c === "(") {
      depth++;
      wordStart = true;
    } else if (c === ")") {
      if (--depth === 0) return i;
      wordStart = true;
    } else {
      wordStart = /[\s;&|]/.test(c);
    }
  }
  return text.length;
}

const unescapeBacktick = (s: string, dq: boolean) =>
  s.replace(/\\(\r?\n|.)/g, (m, ch) => (ch === "\n" || ch === "\r\n" ? "" : (dq ? '"\\$`' : "\\$`").includes(ch) ? ch : m));

function isBareCat(script: string): boolean {
  const { subs, shellBodies } = commandSubstitutions(script);
  if (subs.length || shellBodies.length) return false;
  const parts = splitCommands(script, true);
  return parts.length === 1 && parts[0].length === 1 && parts[0][0] === "cat";
}

// 큰따옴표·인용하지 않은 heredoc 안의 명령 치환($( )·`…`)과 셸 인터프리터가 읽는 heredoc·here-string 본문을 꺼낸다.
// 메시지 전달용 "$(cat <<'EOF'…)"처럼 안전한 heredoc을 인자·리다이렉션 없는 cat으로 읽기만 하는 치환은 상태 변경이 없으므로 건너뛴다
function commandSubstitutions(command: string): { subs: string[]; shellBodies: string[] } {
  const subs: string[] = [];
  const shellBodies: string[] = [];
  let hadSafeHeredoc = false;
  for (const m of command.matchAll(HEREDOC)) {
    if (!m[0].includes("\n")) continue;
    const body = m[0].slice(m[0].indexOf("\n") + 1, m[0].lastIndexOf("\n")).replace(/\r$/, "");
    const before = splitCommands(command.slice(command.lastIndexOf("\n", m.index - 1) + 1, m.index)).at(-1) ?? [];
    const after = splitCommands(m[0].slice(0, m[0].indexOf("\n")).replace(/\r$/, "").replace(/^<<-?[ \t]*(['"]?)\w+\1/, "").split(/[;&]/)[0]).flat();
    if ([...before, ...after].some(w => /^(?:ba|z|da|k)?sh$/.test(binName(w)))) {
      shellBodies.push(body);
      continue;
    }
    const hSubs: string[] = [];
    if (!m[1]) {
      for (let i = 0; i < body.length; i++) {
        if (body[i] === "\\") {
          i += body[i + 1] === "\r" && body[i + 2] === "\n" ? 2 : 1;
        } else if (body[i] === "$" && body[i + 1] === "(") {
          const end = findParenEnd(body, i + 1);
          hSubs.push(body.slice(i + 2, end));
          i = end;
        } else if (body[i] === "`") {
          let j = i + 1;
          for (; j < body.length && body[j] !== "`"; j++) if (body[j] === "\\") j++;
          hSubs.push(unescapeBacktick(body.slice(i + 1, j), false));
          i = j;
        }
      }
    }
    if (hSubs.length) subs.push(...hSubs);
    else hadSafeHeredoc = true;
  }
  for (const words of splitCommands(command, true)) {
    if (!words.some(w => /^(?:ba|z|da|k)?sh$/.test(binName(w.split("<<<")[0])))) continue;
    for (let i = 0; i < words.length; i++) {
      const idx = words[i].indexOf("<<<");
      if (idx >= 0) shellBodies.push(words[i].slice(idx + 3) || (words[++i] ?? ""));
    }
  }
  const text = stripHeredocs(command);
  let wordStart = true;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") {
      i += text[i + 1] === "\r" && text[i + 2] === "\n" ? 2 : 1;
      wordStart = false;
    } else if (c === "#" && wordStart) {
      const nl = text.indexOf("\n", i + 1);
      i = nl < 0 ? text.length : nl - 1;
    } else if (c === "$" && text[i + 1] === "'") {
      for (i += 2; i < text.length && text[i] !== "'"; i++) if (text[i] === "\\") i++;
      wordStart = false;
    } else if (c === "'") {
      const end = text.indexOf("'", i + 1);
      i = end < 0 ? text.length : end;
      wordStart = false;
    } else if (c === '"') {
      for (i++; i < text.length && text[i] !== '"'; i++) {
        if (text[i] === "\\") {
          i += text[i + 1] === "\r" && text[i + 2] === "\n" ? 2 : 1;
        } else if (text[i] === "$" && text[i + 1] === "(") {
          const end = findParenEnd(text, i + 1);
          const raw = text.slice(i + 2, end);
          if (!(hadSafeHeredoc && isBareCat(raw))) subs.push(raw);
          i = end;
        } else if (text[i] === "`") {
          let j = i + 1;
          for (; j < text.length && text[j] !== "`"; j++) if (text[j] === "\\") j++;
          const raw = unescapeBacktick(text.slice(i + 1, j), true);
          if (!(hadSafeHeredoc && isBareCat(raw))) subs.push(raw);
          i = j;
        }
      }
      wordStart = false;
    } else if (c === "$" && text[i + 1] === "(") {
      const end = findParenEnd(text, i + 1);
      subs.push(text.slice(i + 2, end));
      i = end;
      wordStart = false;
    } else if (c === "`") {
      let j = i + 1;
      for (; j < text.length && text[j] !== "`"; j++) if (text[j] === "\\") j++;
      subs.push(unescapeBacktick(text.slice(i + 1, j), false));
      i = j;
      wordStart = false;
    } else {
      wordStart = /[\s;&|()]/.test(c);
    }
  }
  return { subs, shellBodies };
}

function executedParts(command: string, seen = new Set<string>()): string[][] {
  if (seen.has(command)) return [];
  seen.add(command);
  const out = splitCommands(command);
  for (const sub of commandSubstitutions(command).subs) {
    const inner = executedParts(sub, seen);
    out.push(...(inner.length ? inner : [[""]]));
  }
  return out;
}

// heredoc은 표시(<<'EOF')와 본문을 지우고 표시 뒤의 같은 줄(> 파일, )" 등)만 남긴다
export function stripHeredocs(command: string): string {
  return command.replace(HEREDOC, m => (m.includes("\n") ? m.slice(0, m.indexOf("\n")).replace(/\r$/, "") : m).replace(/^<<-?[ \t]*(['"]?)\w+\1/, ""));
}

// 리다이렉션(2>&1, > 파일, <파일)은 명령 인자가 아니므로 뗀다. 연산자만 있는 단어면 다음 단어(대상)도 뗀다
function stripRedirects(words: { text: string; plain: boolean }[]): string[] {
  const out: string[] = [];
  for (let i = 0; i < words.length; i++) {
    const m = words[i].plain ? /^\d*(?:&>>?|>>?&?|<<?<?&?|>\|)(.*)$/.exec(words[i].text) : null;
    if (!m) out.push(words[i].text);
    else if (m[1] === "") i++;
  }
  return out;
}

function scriptCallsCommitOrPush(script: string, seen = new Map<string, boolean>()): boolean {
  if (!seen.has(script)) {
    seen.set(script, false);
    const { shellBodies } = commandSubstitutions(script);
    seen.set(script, shellBodies.some(b => scriptCallsCommitOrPush(b, seen)) || executedParts(script).some(w => callsCommitOrPush(w, seen)));
  }
  return seen.get(script)!;
}

// 단어 어디서든 commit·push를 부르는지: git 뒤 하위 명령(xargs git commit, bash -c 'exec "$@"' _ git commit 등)과,
// 다시 나눠 실행되는 문자열(셸 -c 스크립트, env -S 값, eval 인자) 안. 스크립트를 본 뒤에도 뒤 단어(스크립트에 넘기는 실행 인자)를 계속 훑는다.
// 같은 스크립트는 한 번만 나눠 보므로(seen) 감싸기가 겹쳐도 빨리 끝난다
function callsCommitOrPush(words: string[], seen = new Map<string, boolean>()): boolean {
  const inScript = (script: string) => scriptCallsCommitOrPush(script, seen);
  for (let i = 0; i < words.length; i++) {
    const name = binName(words[i]);
    const rest = words.slice(i + 1);
    if (name === "git" && /^(?:commit|push)$/.test(parseGit(rest).sub ?? "")) return true;
    // eval은 뒤 단어를 이어 붙여 다시 나눠 실행한다. env -u eval처럼 인자일 수도 있으므로 원래 단어도 계속 훑는다
    if (name === "eval") {
      if (inScript(rest.join(" "))) return true;
    } else if (name === "env") {
      // env 옵션은 COMMAND 앞까지만 읽는다. -S 값만 다시 나눠 실행된다
      for (let j = 0; j < rest.length && (rest[j].startsWith("-") || /^\w+=/.test(rest[j])); j++) {
        const s = /^(?:-[A-Za-z0-9]*S|--split-string(?:=|$))(.*)$/.exec(rest[j]);
        if (s && inScript(s[1] || (rest[++j] ?? ""))) return true;
        if (!s && /^(?:-[A-Za-z0-9]*[uC]|--unset|--chdir)$/.test(rest[j])) j++;
      }
    } else if (/^(?:ba|z|da|k)?sh$/.test(name)) {
      // -c는 -lc·-ec처럼 다른 짧은 옵션과 합쳐 쓸 수 있고, 스크립트는 그 뒤 첫 옵션 아닌 인자다
      const c = rest.findIndex(a => /^-[A-Za-z]*c[A-Za-z]*$/.test(a));
      if (c >= 0 && inScript(rest.slice(c + 1).find(a => !a.startsWith("-")) ?? "")) return true;
    }
  }
  return false;
}

// 실행 환경을 바꾸지 않는 command·exec·time·nohup 앞 단어를 뗀다. VAR=값·env는 git이 볼 대상을 바꿀 수 있어 남긴다
function dropPrefixes(words: string[]): string[] {
  let i = 0;
  while (i < words.length && /^(?:command|exec|time|nohup)$/.test(words[i])) i++;
  return words.slice(i);
}

function parseGit(args: string[]): { dir?: string; sub?: string; rest: string[]; unknownGlobal: boolean } {
  let dir: string | undefined;
  let unknownGlobal = false;
  let found: { sub?: string; rest: string[] } = { rest: [] };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "-C") {
      const next = args[++i] ?? "";
      // 셸이 펼치는 ~·$·`는 훅이 실제 경로를 알 수 없다
      if (/^~|[$`]/.test(next)) unknownGlobal = true;
      dir = dir === undefined || path.isAbsolute(next) ? next : path.join(dir, next);
    } else if (a === "--no-pager") {
      continue;
    } else if (a.startsWith("-")) {
      unknownGlobal = true;
      if (GLOBAL_VALUE.has(a)) i++;
    } else {
      found = { sub: a, rest: args.slice(i + 1) };
      break;
    }
  }
  // 모르는 전역 옵션이 값을 어떻게 받았는지 확정할 수 없으므로, 인자 어디든 commit·push가 있으면 그것을 하위 명령으로 본다
  const guarded = args.findIndex(x => x === "commit" || x === "push");
  if (unknownGlobal && guarded >= 0 && found.sub !== "commit" && found.sub !== "push") found = { sub: args[guarded], rest: args.slice(guarded + 1) };
  return { dir, ...found, unknownGlobal };
}

// commit -a 여부와, 경로·--only·--include처럼 인덱스 밖의 내용을 담을 수 있는지
function parseCommit(rest: string[]): { all: boolean; pathspec: boolean } {
  let all = false;
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === "--" || /^--(?:only|include|pathspec-from-file|patch|interactive)(?:=|$)/.test(a)) return { all, pathspec: true };
    if (a === "--all") all = true;
    else if (a.startsWith("--")) {
      if (COMMIT_VALUE.has(a)) i++;
    } else if (a.startsWith("-") && a.length > 1) {
      for (let j = 1; j < a.length; j++) {
        if (a[j] === "a") all = true;
        if ("oip".includes(a[j])) return { all, pathspec: true };
        if (COMMIT_SHORT_VALUE.includes(a[j])) {
          if (j === a.length - 1) i++;
          break;
        }
      }
    } else {
      return { all, pathspec: true };
    }
  }
  return { all, pathspec: false };
}

// git push [옵션] [원격 [refspec...]]의 목적지에 main이 들어갈 수 있는지.
// refspec이 없으면 git과 같은 순서로 원격(branch.<b>.pushRemote → remote.pushDefault → branch.<b>.remote → origin)과
// remote.<원격>.push, 없으면 push.default(matching·upstream·simple·current)로 목적지를 정한다
function pushMayReachMain(rest: string[], branch: string | undefined, dir: string, git: Git): boolean {
  const positional: string[] = [];
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (/^--(?:all|branches|mirror|repo)(?:=|$)/.test(a)) return true;
    if (PUSH_VALUE.has(a)) i++;
    else if (!a.startsWith("-")) positional.push(a);
  }
  const last = (key: string) => git.config(dir, key).at(-1);
  let refspecs = positional.slice(1);
  if (!refspecs.length) {
    const remote = positional[0] ?? (branch && (last(`branch.${branch}.pushRemote`) ?? last("remote.pushDefault") ?? last(`branch.${branch}.remote`))) ?? "origin";
    if (gitBool(last(`remote.${remote}.mirror`))) return true;
    refspecs = git.config(dir, `remote.${remote}.push`);
    if (!refspecs.length) {
      const mode = last("push.default") ?? "simple";
      if (mode === "nothing") return false;
      if (mode === "matching") return true;
      if (mode === "upstream" || mode === "tracking") return branch === "main" || headName(last(`branch.${branch}.merge`)) === "main";
      return branch === "main";
    }
  }
  return refspecs.some(spec => {
    if (spec.includes("*") || /^\+?:$/.test(spec)) return true;
    const dst = spec.replace(/^\+/, "").split(":").pop() ?? "";
    return (dst === "HEAD" || dst === "@" ? branch : headName(dst)) === "main";
  });
}

// git 불리언 규칙: true·yes·on·0이 아닌 정수, 값 없이 키만 쓴 경우(빈 문자열)가 참
function gitBool(value: string | undefined): boolean {
  if (value === undefined) return false;
  return value === "" || /^(?:true|yes|on)$/i.test(value) || (/^-?\d+$/.test(value) && Number(value) !== 0);
}

function headName(ref: string | undefined): string | undefined {
  return ref?.replace(/^refs\/heads\//, "");
}

const run = (dir: string, args: string[]) =>
  execFileSync("git", ["-C", dir, ...args], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "ignore"] });

const realGit: Git = {
  branch(dir) {
    try {
      // symbolic-ref는 첫 커밋 전(git init -b main)에도 브랜치를 준다. 분리된 HEAD면 실패해 undefined
      return run(dir, ["symbolic-ref", "--short", "-q", "HEAD"]).trim();
    } catch {
      return undefined;
    }
  },
  staged(dir, all) {
    const base = all ? "HEAD" : "--cached";
    // -z: core.quotePath로 인용된 이름이 아니라 원래 경로를 받는다. --no-relative: diff.relative여도 하위 디렉터리 밖 변경까지 본다.
    // --diff-filter=d: 이미 추적 중인 .env를 git rm으로 지우는 정리 커밋은 허용한다.
    // --no-textconv·--text: textconv로 바꾼 내용이 아니라 커밋될 내용을, 바이너리 파일도 원문 그대로 본다
    const opts = ["--no-relative", "--no-textconv"];
    return {
      names: run(dir, ["diff", base, ...opts, "--diff-filter=d", "--name-only", "-z"]).split("\0").filter(Boolean),
      diff: run(dir, ["diff", base, ...opts, "--text", "--no-color", "--no-ext-diff", "-U0"]),
    };
  },
  config(dir, key) {
    try {
      // 값 없이 키만 쓴 설정은 빈 줄로 나오므로 끝의 줄바꿈(LF·CRLF)만 뗀다
      return run(dir, ["config", "--get-all", key]).replace(/\r?\n$/, "").split(/\r?\n/);
    } catch {
      return [];
    }
  },
};

export function runHook(): void {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const command = input?.tool_input?.command;
  if (typeof command === "string") {
    const reason = blockReason(command, input.cwd ?? process.cwd(), realGit);
    if (reason) {
      process.stderr.write(reason + "\n");
      process.exit(2);
    }
  }
}

if (process.argv[1] && fs.existsSync(process.argv[1]) && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runHook();
}
