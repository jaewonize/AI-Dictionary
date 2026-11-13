import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { blockReason } from "./guard-env.ts";

const tempDir = (t: test.TestContext, files: string[] = []) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-guard-env-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  for (const f of files) {
    fs.mkdirSync(path.dirname(path.join(dir, f)), { recursive: true });
    fs.writeFileSync(path.join(dir, f), "A=1\n");
  }
  return dir;
};

test("Bash 인자·리다이렉션·따옴표를 푼 단어로 쓴 .env 경로를 막는다", () => {
  for (const cmd of [
    "head -c 0 .env",
    "grep -cE '^(OPENAI_API_KEY|ANTHROPIC_API_KEY|DATABASE_URL)=' .env",
    "cat ./.env.local",
    "cat server/.env.production",
    'cat "$HOME/repo/.env"',
    "cat < .env",
    "head <.env",
    'cat .e""nv',
    "cat .e\\nv",
    "source .env",
    ". ./.env",
    "node --env-file=.env x.js",
    "f=.env; cat $f",
    "git show HEAD:.env",
    "cat .ENV",
    "echo x > .env",
    "cat {.env,x}",
    "cat x{,.env.local}",
    "cat .{env,env.staging}",
    "cat sub/.en{v,x}",
    "cat .e{m..o}v",
    "cat .env{,.{local,test}}",
    "cat<.env",
    "head -n1<./.env.local",
    "echo x>.env",
    "bash<<<'cat .env'",
    'echo "$(<.env)"',
    'cat "sub\\.env"',
    "cat 'sub\\.env'",
    "cat 'C:\\repo\\.env.local'",
    "cat .envrc",
    "cat sub/.envrc",
  ]) {
    assert.match(blockReason(cmd, "/repo") ?? "", /Bash에서 그 경로/, cmd);
  }
});

test(".env에 펼쳐질 수 있는 glob만 막는다", () => {
  for (const cmd of ["cat .e*", "ls .*", "cat .en?", "cat .env*", "cat .envrc*", "cat .env.p*", "cat .env.pr?d", "cat .[e]nv", "cat .[!x]nv", "cat .??v", "cat .e*l", "cat sub/.e*", "cat 'sub\\.e*'", "cat .en?.staging", "cat .*.staging", "cat .e?v.x[0-9]", "cat .[z-ae]nv"]) {
    assert.match(blockReason(cmd, "/repo") ?? "", /Bash에서 그 경로/, cmd);
  }
  for (const cmd of [
    "ls *.ts",
    "cat *",
    "ls .git*",
    "cat .eslintrc*",
    "cat .[x]nv",
    "cat .{git,eslint}rc",
    "echo {1..3}",
    "cat a{b,c}.ts",
    "gh api x --jq '.[] | {user: .user.login, state, submitted_at}'",
    "jq '.[0].name' a.json",
    "jq -r '.[]' a.json",
    "echo '[z-a]'",
    "grep '[9-0]' x.txt",
    `node -e 'x.filter(t=>["E-1","T-2"].includes(t))'`,
  ]) {
    assert.equal(blockReason(cmd, "/repo"), undefined, cmd);
  }
});

test("셸·eval·인터프리터 코드·명령 치환·here-string으로 다시 실행되는 문자열 안의 .env도 막는다", () => {
  for (const cmd of [
    "bash -c 'cat .env'",
    `sh -lc "grep X .env"`,
    "bash -cu 'cat .env'",
    "bash -cx 'cat .env'",
    "bash -x -c 'cat .env'",
    "perl -ne 'print' -e 'open(F, \".env\")'",
    "eval cat .env",
    "env -S 'cat .env'",
    "env -S'cat .env'",
    `node -e "console.log(require('fs').readFileSync('.env', 'utf8'))"`,
    `node --eval="require('fs').readFileSync('.env')"`,
    `python3 -c "print(open('.env').read())"`,
    `echo "$(cat .env)"`,
    "echo `cat .env`",
    "bash <<< 'cat .env'",
    "timeout 5 bash -c \"eval 'cat .env'\"",
  ]) {
    assert.match(blockReason(cmd, "/repo") ?? "", /Bash에서 그 경로/, cmd);
  }
});

test("인터프리터로 들어가는 heredoc 본문만 검사하고 메시지·파일 내용은 통과시킨다", () => {
  assert.ok(blockReason("python3 - <<'EOF'\nprint(open('.env').read())\nEOF", "/repo"));
  assert.ok(blockReason("bash <<EOF\ncat .env\nEOF", "/repo"));
  assert.ok(blockReason("echo ok && node <<'EOF'\nrequire('fs').readFileSync('.env')\nEOF", "/repo"));
  // 표시 뒤 파이프로 인터프리터가 받는 본문도 코드다
  assert.ok(blockReason("cat <<'EOF' | node\nconsole.log(require('fs').readFileSync('.env', 'utf8'))\nEOF", "/repo"));
  assert.ok(blockReason("cat <<'EOF' | tee /dev/null | bash\ncat .env\nEOF", "/repo"));
  assert.equal(blockReason("cat <<'EOF' | tee notes.md\n.env 는 커밋하지 않는다\nEOF", "/repo"), undefined);
  assert.equal(blockReason("cat <<'EOF' > notes.md && node x.js\n.env 는 커밋하지 않는다\nEOF", "/repo"), undefined);
  // 구분자를 인용하지 않은 heredoc은 셸이 본문의 명령 치환을 실행한다
  assert.ok(blockReason("cat <<EOF\n$(cat .env)\nEOF", "/repo"));
  assert.ok(blockReason("cat <<EOF > a.txt\nx `head .env` y\nEOF", "/repo"));
  assert.ok(blockReason('git commit -F - <<EOF\nmsg $(echo "$(cat .env)")\nEOF', "/repo"));
  assert.equal(blockReason("cat <<'EOF'\n$(cat .env)\nEOF", "/repo"), undefined);
  assert.equal(blockReason("cat <<EOF\n.env 는 커밋하지 않는다 $(date)\nEOF", "/repo"), undefined);
  assert.equal(blockReason("cat <<'EOF' > a.md\n.env 는 커밋하지 않는다\nEOF", "/repo"), undefined);
  assert.equal(blockReason("git commit -F - <<'EOF'\nfix: .env 읽기를 막음\nEOF", "/repo"), undefined);
  assert.equal(blockReason(`git commit -m "$(cat <<'EOF'\nfix: .env 읽기를 막음\n\n- .env\nEOF\n)"`, "/repo"), undefined);
});

test("env.example·메시지 속 .env 글자는 통과시킨다", () => {
  for (const cmd of [
    "cat env.example",
    "cp env.example /tmp/x",
    'git commit -m "fix: .env 읽기 막음"',
    'gh pr create --title "fix: Bash의 .env 읽기를 훅으로 막음" --body-file /tmp/b.md',
    "echo $process.env",
    `node -e "console.log(process.env.PORT)"`,
    "cat .gitignore",
    `grep -n "\\.env" docs/USAGE.md`,
    "gh api x -f body='`head -n1<./.env.local`·`echo x>.env` 테스트를 넣었습니다.'",
    // 코드를 실행하지 않는 명령의 -e·here-string 값은 다시 나누지 않는다
    "rg -e '\\.env' notes.txt",
    "grep -e '\\.env' -e 'cat .env' notes.txt",
    "sed -e 's/\\.env/x/' notes.txt",
    `grep x <<< "cat .env"`,
    // 작은따옴표 안의 $( )·`…`는 실행되지 않는다
    "echo '$(cat .env)'",
    "echo '`cat .env`'",
    "npm test",
  ]) {
    assert.equal(blockReason(cmd, "/repo"), undefined, cmd);
  }
});

test(".env가 있는 디렉터리를 재귀 grep하면 막는다", t => {
  const dir = tempDir(t, [".env", "sub/a.ts", "nested/.env.local"]);
  const posixDir = dir.replaceAll("\\", "/");
  for (const cmd of ["grep -rn X", "grep -R X .", `grep -rin X '${dir}'`, `grep -rin X ${posixDir}`, "grep -r -e X", "grep --recursive X .", "grep -d recurse X .", "grep --directories=recurse X", "grep -nr --include '*.ts' X .", "grep -r X nested", "grep -rn '' 2>/dev/null", "grep -rn X 2> /dev/null", "grep -rn X >out.txt 2>&1", "grep -r X *", 'grep -r X "$D"', "xargs grep -r X"]) {
    assert.match(blockReason(cmd, dir) ?? "", /grep -r/, cmd);
  }
  for (const cmd of ["grep -rn X sub", "grep -n X a.ts", "grep -e -r X .", "grep -A 3 X sub/a.ts", "rg -n X", `grep -r X '${path.join(dir, "sub")}'`, `grep -r X ${path.posix.join(posixDir, "sub")}`]) {
    assert.equal(blockReason(cmd, dir), undefined, cmd);
  }
  const clean = tempDir(t, ["a.ts"]);
  assert.equal(blockReason("grep -rn X .", clean), undefined);
});

test("cd·pushd로 바뀐 디렉터리의 재귀 grep도 .env가 있으면 막는다", t => {
  const dir = tempDir(t, ["a.ts", "nested/.env", "clean/a.ts"]);
  const posixNested = path.posix.join(dir.replaceAll("\\", "/"), "nested");
  for (const cmd of [
    "cd nested && grep --recursive X .",
    "cd nested; grep -rn X",
    `cd '${path.join(dir, "nested")}' && grep -r X .`,
    `cd ${posixNested} && grep -r X .`,
    "pushd nested >/dev/null && grep -r X .",
    "(cd nested && grep -r X .)",
    "cd clean && cd ../nested && grep -r X .",
    "bash -c 'cd nested && grep -r X .'",
    "cd nested && bash <<'EOF'\ngrep -r X .\nEOF",
    'cd "$D" && grep -r X .',
    "cd - && grep -r X .",
  ]) {
    assert.match(blockReason(cmd, dir) ?? "", /grep -r/, cmd);
  }
  for (const cmd of ["grep --recursive X .", "cd clean && grep -r X .", "cd nested && grep -n X a.ts", 'cd "$D" && grep -r X /nonexistent']) {
    assert.equal(blockReason(cmd, dir), undefined, cmd);
  }
});

test("겹친 감싸기도 빨리 끝난다", () => {
  const started = Date.now();
  assert.equal(blockReason(Array(200).fill("eval").join(" ") + " npm test", "/repo"), undefined);
  assert.equal(blockReason(Array(200).fill("sh").join(" ") + ` -c '${Array(50).fill("sh sh -c").join(" ")} npm test'`, "/repo"), undefined);
  assert.ok(Date.now() - started < 1000);
});

test("훅으로 실행하면 막을 때 exit 2와 이유를, 아니면 exit 0을 낸다", t => {
  const dir = tempDir(t, [".env"]);
  const hook = path.join(import.meta.dirname, "guard-env.ts");
  const run = (command: string) => spawnSync(process.execPath, [hook], { input: JSON.stringify({ tool_input: { command }, cwd: dir }), encoding: "utf8", timeout: 10000 });
  const blocked = run("head -c 0 .env");
  assert.equal(blocked.status, 2);
  assert.match(blocked.stderr, /사용자에게 직접 실행/);
  assert.equal(run("grep -rn X").status, 2);
  assert.equal(run("git status").status, 0);
});
