import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { blockReason, splitCommands, type Git } from "./guard-git.ts";

type FakeGit = Git & { calls: { dir: string; all: boolean }[] };
const fakeGit = (branch: string | undefined, names: string[] = [], diff = "", config: Record<string, string[]> = {}): FakeGit => {
  const calls: { dir: string; all: boolean }[] = [];
  return {
    calls,
    branch: () => branch,
    staged: (dir, all) => (calls.push({ dir, all }), { names, diff }),
    config: (_dir, key) => config[key] ?? [],
  };
};

test("명령을 따옴표·백슬래시를 풀어 조각별 단어로 나누고 heredoc 본문은 지운다", () => {
  assert.deepEqual(splitCommands(`git -C "with space" commit -m 'a; b' && echo x\\ y`), [["git", "-C", "with space", "commit", "-m", "a; b"], ["echo", "x y"]]);
  assert.deepEqual(splitCommands("cat <<'EOF'\ngit push origin main\nEOF\ngit status"), [["cat"], ["git", "status"]]);
  assert.deepEqual(splitCommands("cat <<EOF\nEOF\ngit push"), [["cat"], ["git", "push"]]);
  assert.deepEqual(splitCommands('git commit -m "<b>" 2>&1 > log.txt <in'), [["git", "commit", "-m", "<b>"]]);
  assert.deepEqual(splitCommands('git -C . \\\ncommit -m "a\\\nb"'), [["git", "-C", ".", "commit", "-m", "ab"]]);
});

test("줄 연속·합친 셸 옵션·값을 따로 받는 전역 옵션으로 쓴 commit도 검사에 닿는다", () => {
  assert.match(blockReason("git -C . \\\ncommit -m x", "/repo", fakeGit("main")) ?? "", /main에는 직접 커밋/);
  for (const cmd of ["bash -lc 'git commit -m x'", "bash --noprofile --norc -lc 'git commit -m x'", "sh -ec 'git push origin main'"]) {
    assert.match(blockReason(cmd, "/repo", fakeGit("feat/a")) ?? "", /감싼/, cmd);
  }
  for (const cmd of ["git --git-dir .git commit -m x", "git --work-tree . --git-dir .git push", "git -c a=b --namespace n commit"]) {
    assert.match(blockReason(cmd, "/repo", fakeGit("main")) ?? "", /전역 옵션/, cmd);
  }
});

test("main에서는 커밋을 막고 다른 브랜치에서는 통과시킨다", () => {
  assert.match(blockReason('git commit -m "x"', "/repo", fakeGit("main")) ?? "", /main에는 직접 커밋/);
  assert.match(blockReason('git -C "with space" commit -m x', "/repo", fakeGit("main")) ?? "", /main에는 직접 커밋/);
  assert.equal(blockReason('git commit -m "x"', "/repo", fakeGit("feat/a")), undefined);
  assert.equal(blockReason("git status && git log --oneline", "/repo", fakeGit("main")), undefined);
});

test("commit·push는 단독 호출만 검사하고 다른 명령과 이으면 막는다", () => {
  for (const cmd of [
    "git add -A && git commit -m x",
    "git switch main; git commit -m x",
    "git checkout main && git push",
    "git commit -m x && git commit --amend -m y",
    "git status && git diff --cached && git commit -m x",
    'cd "my repo" && git commit -m x',
    "(cd ../other && git status --short); git commit -m x",
    "cd ../other | git commit -m x",
    "git commit -m x; git push",
    "GIT_INDEX_FILE=.git/alt-index; git commit -m x",
  ]) {
    const git = fakeGit("feat/a");
    assert.match(blockReason(cmd, "/repo", git) ?? "", /단독 Bash 호출/, cmd);
    assert.deepEqual(git.calls, [], cmd);
  }
  assert.equal(blockReason("git status && git log --oneline", "/repo", fakeGit("main")), undefined);
  assert.equal(blockReason("cd sub && git status", "/repo", fakeGit("main")), undefined);
});

test("환경변수 대입·env를 붙인 commit·push는 막는다", () => {
  for (const cmd of ["GIT_INDEX_FILE=.git/alt-index git commit -m x", "env GIT_INDEX_FILE=.git/alt-index git commit -m x", "GIT_TRACE=0 git commit -m x", "env git push", "HOME=/tmp/h git push origin"]) {
    assert.match(blockReason(cmd, "/repo", fakeGit("feat/a")) ?? "", /환경변수 대입/, cmd);
  }
  assert.equal(blockReason("GIT_PAGER=cat git log", "/repo", fakeGit("main")), undefined);
  for (const cmd of [
    "env bash -c 'git commit -m x'",
    "GIT_TRACE=0 bash -c 'git commit -m x'",
    "env -S 'git commit -m x'",
    "env -iS'git commit -m x'",
    "env --split-string='git push origin main'",
    "env -i /bin/sh -c 'git push origin main'",
    "env bash -c 'git commit -m x' _ -S",
    "env -u HOME -C . bash -c 'git commit -m x'",
    `env -u eval "GIT_AUTHOR_NAME=O'Reilly" git commit -m x`,
  ]) {
    assert.match(blockReason(cmd, "/repo", fakeGit("main")) ?? "", /환경변수 대입/, cmd);
  }
});

test("다른 명령 뒤에 감싼 셸·중첩된 감싸기의 commit·push도 막는다", () => {
  for (const cmd of [
    "timeout 5 sh -c 'git commit -m x'",
    "xargs bash -c 'git push origin main'",
    "/bin/bash -c 'git commit -m x'",
    `bash -c "eval 'git commit -m x'"`,
    "nice env bash -c 'git commit -m x'",
    `timeout 5 bash -c 'exec "$@"' _ git commit -m x`,
    `sh -c '"$@"' sh git push origin main`,
    `xargs echo eval "'" git commit -m x`,
  ]) {
    assert.match(blockReason(cmd, "/repo", fakeGit("main")) ?? "", /감싼/, cmd);
  }
  assert.equal(blockReason("timeout 5 sh -c 'npm test'", "/repo", fakeGit("main")), undefined);
  assert.equal(blockReason("env FOO=1 npm test", "/repo", fakeGit("main")), undefined);
  // 감싸기가 겹겹이 이어져도 빨리 끝난다
  const started = Date.now();
  assert.equal(blockReason(Array(200).fill("env eval").join(" ") + " npm test", "/repo", fakeGit("main")), undefined);
  assert.equal(blockReason(Array(200).fill("sh").join(" ") + ` -c '${Array(50).fill("sh sh -c").join(" ")} npm test'`, "/repo", fakeGit("main")), undefined);
  assert.ok(Date.now() - started < 1000);
});

test("감싼 실행과 모르는 전역 옵션의 commit·push는 막는다", () => {
  assert.match(blockReason('sh -c "git push origin main"', "/repo", fakeGit("feat/a")) ?? "", /감싼/);
  assert.match(blockReason("eval git commit -m x", "/repo", fakeGit("feat/a")) ?? "", /감싼/);
  assert.match(blockReason("echo a | xargs git -C x push", "/repo", fakeGit("feat/a")) ?? "", /감싼/);
  assert.match(blockReason("git -c remote.origin.push=HEAD:main push origin", "/repo", fakeGit("feat/a")) ?? "", /전역 옵션/);
  assert.match(blockReason("git --git-dir=x commit -m y", "/repo", fakeGit("feat/a")) ?? "", /전역 옵션/);
  for (const cmd of ["git -C ~/repo push", 'git -C "$D" commit -m x', 'git -C "`pwd`" commit -m x']) {
    assert.match(blockReason(cmd, "/repo", fakeGit("feat/a")) ?? "", /전역 옵션/, cmd);
  }
  assert.equal(blockReason("git --no-pager commit -m x", "/repo", fakeGit("feat/a")), undefined);
  assert.equal(blockReason('bash -c "npm test"', "/repo", fakeGit("feat/a")), undefined);
});

test("heredoc 본문과 따옴표 안의 git 명령 글자는 명령으로 보지 않는다", () => {
  assert.equal(blockReason("cat > a.ts <<'EOF'\nrun(\"git add -A && git commit -m x\");\ngit push origin main\nEOF", "/repo", fakeGit("feat/a")), undefined);
  assert.equal(blockReason('echo "git add . && git commit" > note.txt', "/repo", fakeGit("feat/a")), undefined);
  const git = fakeGit("feat/a");
  assert.equal(blockReason("git commit -F - <<'EOF'\nfix: a && b; c\n\ngit push origin main\nEOF", "/repo", git), undefined);
  assert.equal(git.calls.length, 1);
  assert.equal(blockReason('git commit -m "$(cat <<\'EOF\'\nmsg; git push origin main\nEOF\n)"', "/repo", fakeGit("feat/a")), undefined);
  assert.ok(blockReason("cat <<EOF\nx\nEOF\ngit push origin main", "/repo", fakeGit("feat/a")));
});

test("큰따옴표·인용하지 않은 heredoc의 명령 치환과 셸 표준입력으로 감싼 commit·push를 막는다", () => {
  for (const cmd of [
    'git commit -m "$(git add credentials.txt; printf cleanup)"',
    'git commit -m "$(git add credentials.txt)"',
    'git commit -m "`git add credentials.txt`"',
    'git commit -m "`git add \\"credentials.txt\\"`"',
    'git commit -m "$(printf \')\'; git add credentials.txt)"',
    'git commit -m "$(echo ")" && git add credentials.txt)"',
    'git commit -m $\'a\\\'\' -m "$(git add credentials.txt)"',
    'git commit -m "${MSG:-$(git add credentials.txt)}"',
    'git commit -m "$(>credentials.txt)"',
    "git commit -F - <<EOF\ncleanup $(git add credentials.txt)\nEOF",
    'git commit -m "$(cat <<EOF\ncleanup $(git add credentials.txt)\nEOF\n)"',
    'git commit -m "$(cat <<\'EOF\'\nmsg\nEOF\ngit add credentials.txt)"',
    'echo "$(git commit -m x)"',
    'echo "`echo \\`git commit -m x\\``"',
  ]) {
    const git = fakeGit("feat/a");
    assert.match(blockReason(cmd, "/repo", git) ?? "", /단독 Bash 호출/, cmd);
    assert.deepEqual(git.calls, [], cmd);
  }
  for (const cmd of [
    `bash -c 'echo "$(git commit -m x)"'`,
    "bash <<'EOF'\ngit commit -m x\nEOF",
    "cat <<'EOF' | sh\ngit push origin main\nEOF",
    "bash <<< 'git push origin main'",
  ]) {
    assert.match(blockReason(cmd, "/repo", fakeGit("feat/a")) ?? "", /감싼/, cmd);
  }
  assert.ok(blockReason("# don't push\ngit push origin main", "/repo", fakeGit("feat/a")));
  for (const cmd of [
    "git commit -m 'literal $(git add credentials.txt) and `sk-`'",
    'git commit -m "literal \\$(git add credentials.txt) and \\`sk-\\`"',
    'git commit -m "$(cat <<\'EOF\'\nmsg; $(git add credentials.txt)\nEOF\n)"',
    "git commit -F - <<EOF\nplain message without substitution\nEOF",
    'echo "$(date)"',
  ]) {
    assert.equal(blockReason(cmd, "/repo", fakeGit("feat/a")), undefined, cmd);
  }
});

test("경로 지정·작업 트리에서 고르는 커밋은 막고 메시지 옵션 값은 경로로 보지 않는다", () => {
  for (const cmd of ["git commit --only -m x source.ts", "git commit -o -m x a.ts", "git commit -m x a.ts", "git commit -i -m x", "git commit -m x -- a.ts", "git commit -p", "git commit --pathspec-from-file=f"]) {
    assert.match(blockReason(cmd, "/repo", fakeGit("feat/a")) ?? "", /경로를 지정/, cmd);
  }
  assert.match(blockReason('git commit -m "<b>" a.ts', "/repo", fakeGit("feat/a")) ?? "", /경로를 지정/);
  for (const cmd of ["git commit -m x 2>&1", "git commit -qm x", "git commit -F msg.txt", "git commit --author 'A <a@b>' -m x", "git commit --amend --no-edit", "git commit -S -m x"]) {
    assert.equal(blockReason(cmd, "/repo", fakeGit("feat/a")), undefined, cmd);
  }
});

test("main으로 갈 수 있는 푸시만 막는다", () => {
  assert.ok(blockReason("git push", "/repo", fakeGit("main")));
  assert.ok(blockReason("git push origin main", "/repo", fakeGit("feat/a")));
  assert.ok(blockReason("git push -f upstream HEAD:refs/heads/main", "/repo", fakeGit("feat/a")));
  assert.ok(blockReason("git push origin HEAD", "/repo", fakeGit("main")));
  for (const all of ["--all", "--branches", "--mirror", "--repo=x"]) assert.ok(blockReason(`git push ${all} origin`, "/repo", fakeGit("feat/a")), all);
  assert.ok(blockReason("git push origin :", "/repo", fakeGit("feat/a")));
  assert.ok(blockReason("git push origin 'refs/heads/*:refs/heads/*'", "/repo", fakeGit("feat/a")));
  assert.equal(blockReason("git push -u origin feat/a", "/repo", fakeGit("feat/a")), undefined);
  assert.equal(blockReason("git push -o ci.skip origin feat/a", "/repo", fakeGit("feat/a")), undefined);
  assert.equal(blockReason("git push origin feat/a", "/repo", fakeGit("main")), undefined);
  assert.equal(blockReason("git push origin main-docs", "/repo", fakeGit("feat/a")), undefined);
});

test("refspec을 생략한 푸시는 설정된 원격·목적지를 git과 같은 순서로 본다", () => {
  assert.ok(blockReason("git push origin", "/repo", fakeGit("feat/a", [], "", { "remote.origin.push": ["HEAD:refs/heads/main"] })));
  assert.ok(blockReason("git push", "/repo", fakeGit("feat/a", [], "", { "branch.feat/a.pushRemote": ["up"], "remote.up.push": ["feat/a:main"] })));
  for (const mirror of ["true", "1", "yes", "On", ""]) assert.ok(blockReason("git push", "/repo", fakeGit("feat/a", [], "", { "remote.origin.mirror": [mirror] })), mirror);
  assert.equal(blockReason("git push", "/repo", fakeGit("feat/a", [], "", { "remote.origin.mirror": ["0"] })), undefined);
  assert.ok(blockReason("git push", "/repo", fakeGit("feat/a", [], "", { "push.default": ["matching"] })));
  assert.ok(blockReason("git push", "/repo", fakeGit("feat/a", [], "", { "push.default": ["upstream"], "branch.feat/a.merge": ["refs/heads/main"] })));
  assert.equal(blockReason("git push", "/repo", fakeGit("feat/a", [], "", { "push.default": ["upstream"], "branch.feat/a.merge": ["refs/heads/feat/a"] })), undefined);
  assert.equal(blockReason("git push origin", "/repo", fakeGit("feat/a", [], "", { "remote.origin.push": ["HEAD"] })), undefined);
});

test("스테이징한 .env와 토큰 값을 막고 문서 속 접두만 있는 줄은 통과시킨다", () => {
  assert.match(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["server/.env.local"])) ?? "", /\.env/);
  assert.match(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["server\\.ENV"])) ?? "", /\.ENV/);
  assert.match(blockReason('"C:\\Program Files\\Git\\cmd\\git.exe" commit -m x', "/repo", fakeGit("main")) ?? "", /main에는 직접 커밋/);
  assert.match(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["설정/.env"])) ?? "", /\.env/);
  assert.match(blockReason("git commit -m x", "/repo", fakeGit("feat/a", [".envrc"])) ?? "", /\.envrc/);
  assert.equal(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["docs/.env.md/a.ts", "a.env"])), undefined);
  assert.equal(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["env.example"])), undefined);
  // 실제 키처럼 보이는 문자열이 파일에 그대로 남지 않게 조각을 이어 만든다
  const tokens = [
    ["sk", "ant", "abcdefghij1234567890"].join("-"),
    ["sk", "proj", "abcdefghij1234567890"].join("-"),
    "ghp" + "_" + "a".repeat(36),
    "github" + "_pat_" + "A1".repeat(11),
    "AKIA" + "ABCDEFGHIJ123456",
    "AIza" + "B".repeat(35),
  ];
  for (const token of tokens) {
    assert.match(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["a.ts"], `+const t = "${token}";`)) ?? "", /토큰/, token);
    assert.equal(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["a.ts"], `-const t = "${token}";`)), undefined, token);
  }
  assert.equal(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["AGENTS.md"], "+토큰·API 키(`sk-`·`ghp_`·`AKIA` 등)")), undefined);
  assert.equal(blockReason("git commit -m x", "/repo", fakeGit("feat/a", ["a.ts"], "+const desk = \"task-list-item-with-a-long-name\";")), undefined);
});

test("commit -a는 추적 중인 수정까지, -C는 그 디렉터리를 검사한다", () => {
  const git = fakeGit("feat/a");
  blockReason("git commit -am x", "/repo", git);
  blockReason("git commit --amend --no-edit", "/repo", git);
  blockReason("git -C sub commit -m x", "/repo", git);
  blockReason("git -C sub -C ../other commit -m x", "/repo", git);
  assert.deepEqual(git.calls, [
    { dir: "/repo", all: true },
    { dir: "/repo", all: false },
    { dir: path.resolve("/repo", "sub"), all: false },
    { dir: path.resolve("/repo", "other"), all: false },
  ]);
});

test("실제 저장소에서 인용될 경로의 .env와 설정된 push 목적지를 막는다", t => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "agent-guard-"));
  t.after(() => fs.rmSync(repo, { recursive: true, force: true }));
  const hook = path.join(import.meta.dirname, "guard-git.ts");
  const git = (...args: string[]) => execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", timeout: 5000 });
  const run = (command: string) => spawnSync(process.execPath, [hook], { input: JSON.stringify({ tool_input: { command }, cwd: repo }), encoding: "utf8", timeout: 10000 });
  git("init", "-q", "-b", "main");
  fs.writeFileSync(path.join(repo, "a.txt"), "a\n");
  git("add", "a.txt");
  assert.match(run("git commit -m initial").stderr, /main에는 직접 커밋/);
  git("switch", "-q", "-c", "feat/a");
  fs.mkdirSync(path.join(repo, "설정"));
  fs.writeFileSync(path.join(repo, "설정", ".env"), "A=1\n");
  git("add", "-f", "설정/.env");
  const commit = run("git commit -m x");
  assert.equal(commit.status, 2);
  assert.match(commit.stderr, /설정\/\.env/);
  git("config", "remote.origin.push", "HEAD:refs/heads/main");
  assert.equal(run("git push origin").status, 2);
  assert.equal(run("git status").status, 0);
});

test("실제 저장소에서 diff.relative·별도 인덱스·서브셸 cd로도 .env 커밋과 main 커밋을 막는다", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-guard-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const hook = path.join(import.meta.dirname, "guard-git.ts");
  const run = (command: string, cwd: string) => spawnSync(process.execPath, [hook], { input: JSON.stringify({ tool_input: { command }, cwd }), encoding: "utf8", timeout: 10000 });
  const init = (name: string, branch: string) => {
    const repo = path.join(root, name);
    fs.mkdirSync(repo);
    const git = (...args: string[]) => execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", timeout: 5000 });
    git("init", "-q", "-b", branch);
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init");
    return { repo, git };
  };

  const rel = init("rel", "feat/a");
  fs.mkdirSync(path.join(rel.repo, "sub"));
  fs.writeFileSync(path.join(rel.repo, "sub", "a.txt"), "a\n");
  fs.writeFileSync(path.join(rel.repo, ".env"), "A=1\n");
  rel.git("add", "-f", ".env", "sub/a.txt");
  rel.git("config", "diff.relative", "true");
  assert.equal(run("git commit -m x", path.join(rel.repo, "sub")).status, 2);
  assert.equal(run("git -C sub commit -m x", rel.repo).status, 2);

  const alt = init("alt", "feat/a");
  fs.writeFileSync(path.join(alt.repo, ".envrc"), "A=1\n");
  execFileSync("git", ["-C", alt.repo, "add", "-f", ".envrc"], { env: { ...process.env, GIT_INDEX_FILE: path.join(alt.repo, ".git", "alt-index") }, timeout: 5000 });
  assert.equal(run("git commit -m x", alt.repo).status, 0);
  assert.equal(run("env GIT_INDEX_FILE=.git/alt-index git commit -m x", alt.repo).status, 2);
  assert.equal(run("GIT_INDEX_FILE=.git/alt-index git commit -m x", alt.repo).status, 2);

  const bin = init("bin", "feat/a");
  const token = ["sk", "ant", "abcdefghij1234567890"].join("-");
  fs.writeFileSync(path.join(bin.repo, ".gitattributes"), "data.bin diff=visible\n");
  fs.writeFileSync(path.join(bin.repo, "data.bin"), `\0${token}\n`);
  bin.git("config", "diff.visible.textconv", "cat");
  bin.git("add", ".gitattributes", "data.bin");
  assert.equal(run("git commit -m x", bin.repo).status, 2);

  const main = init("main", "main");
  init("other", "feat/a");
  assert.equal(run("git commit -m x", main.repo).status, 2);
  assert.equal(run("(cd ../other && git status --short); git commit -m x", main.repo).status, 2);
  assert.equal(run("git commit -m x", path.join(root, "other")).status, 0);
});

test("settings.json 훅 명령과 .githooks(pre-commit·pre-push)가 Windows·Linux 셸에서 모두 동작한다", t => {
  const projectRoot = path.resolve(import.meta.dirname, "..", "..");
  const settings = JSON.parse(fs.readFileSync(path.join(projectRoot, ".claude", "settings.json"), "utf8"));
  const hookCmds: string[] = settings.hooks.PreToolUse[0].hooks.map((h: { command: string }) => h.command);

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-githooks-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const remote = path.join(root, "remote.git");
  const repo = path.join(root, "work with space");
  execFileSync("git", ["init", "-q", "--bare", remote], { timeout: 5000 });
  execFileSync("git", ["init", "-q", "-b", "main", repo], { timeout: 5000 });
  const gitSpawn = (...args: string[]) => spawnSync("git", ["-C", repo, ...args], { encoding: "utf8", timeout: 5000 });
  gitSpawn("config", "user.name", "t");
  gitSpawn("config", "user.email", "t@t");
  gitSpawn("config", "core.hooksPath", path.join(projectRoot, ".githooks"));
  gitSpawn("remote", "add", "origin", remote);

  // 1) settings.json의 셸 명령이 기본 셸(Windows cmd / Linux sh)·bash와 공백 경로에서도 정상 차단·통과한다
  for (const cmd of hookCmds) {
    const res = spawnSync(cmd, { shell: true, env: { ...process.env, CLAUDE_PROJECT_DIR: projectRoot }, input: JSON.stringify({ tool_input: { command: "git status" }, cwd: repo }), encoding: "utf8", timeout: 10000 });
    assert.equal(res.status, 0, res.stderr);
  }
  const gitBash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  if (fs.existsSync(gitBash) || process.platform !== "win32") {
    for (const cmd of hookCmds) {
      const res = spawnSync(gitBash, ["-c", cmd], { env: { ...process.env, CLAUDE_PROJECT_DIR: projectRoot }, input: JSON.stringify({ tool_input: { command: "git status" }, cwd: repo }), encoding: "utf8", timeout: 10000 });
      assert.equal(res.status, 0, res.stderr);
    }
  }
  const blockedGit = spawnSync(hookCmds[0], { shell: true, env: { ...process.env, CLAUDE_PROJECT_DIR: projectRoot }, input: JSON.stringify({ tool_input: { command: "git commit -m x" }, cwd: repo }), encoding: "utf8", timeout: 10000 });
  assert.equal(blockedGit.status, 2, blockedGit.stderr);
  const blockedEnv = spawnSync(hookCmds[1], { shell: true, env: { ...process.env, CLAUDE_PROJECT_DIR: projectRoot }, input: JSON.stringify({ tool_input: { command: "cat .env" }, cwd: repo }), encoding: "utf8", timeout: 10000 });
  assert.equal(blockedEnv.status, 2, blockedEnv.stderr);

  // 2) .githooks/pre-commit: main 직접 커밋 차단 → 작업 브랜치 허용 → .env 차단 → 토큰 차단
  fs.writeFileSync(path.join(repo, "ok.txt"), "hello\n");
  gitSpawn("add", "ok.txt");
  assert.notEqual(gitSpawn("commit", "-m", "on-main").status, 0);

  gitSpawn("switch", "-q", "-c", "feat/demo");
  assert.equal(gitSpawn("commit", "-m", "feat: ok").status, 0);

  fs.writeFileSync(path.join(repo, ".env.local"), "A=1\n");
  gitSpawn("add", "-f", ".env.local");
  assert.notEqual(gitSpawn("commit", "-m", "leak-env").status, 0);
  // 이미 잘못 추적된 .env 파일을 제거하는 정리 커밋은 두 훅 모두 통과시킨다
  assert.equal(gitSpawn("commit", "--no-verify", "-m", "seed-tracked-env").status, 0);
  fs.rmSync(path.join(repo, ".env.local"));
  const allowCommitAllDelete = spawnSync(hookCmds[0], { shell: true, env: { ...process.env, CLAUDE_PROJECT_DIR: projectRoot }, input: JSON.stringify({ tool_input: { command: "git commit -am remove-env" }, cwd: repo }), encoding: "utf8", timeout: 10000 });
  assert.equal(allowCommitAllDelete.status, 0, allowCommitAllDelete.stderr);
  gitSpawn("add", "-u", ".env.local");
  const allowStagedDelete = spawnSync(hookCmds[0], { shell: true, env: { ...process.env, CLAUDE_PROJECT_DIR: projectRoot }, input: JSON.stringify({ tool_input: { command: "git commit -m remove-env" }, cwd: repo }), encoding: "utf8", timeout: 10000 });
  assert.equal(allowStagedDelete.status, 0, allowStagedDelete.stderr);
  assert.equal(gitSpawn("commit", "-m", "chore: remove tracked env").status, 0);

  const secret = ["sk", "proj", "abcdefghij1234567890"].join("-");
  fs.writeFileSync(path.join(repo, "ok.txt"), `key=${secret}\n`);
  gitSpawn("add", "ok.txt");
  assert.notEqual(gitSpawn("commit", "-m", "leak-token").status, 0);
  gitSpawn("checkout", "--", "ok.txt");

  // NUL이 섞인 바이너리 파일의 토큰도 pre-commit이 텍스트로 검사해 차단한다
  fs.writeFileSync(path.join(repo, "data.bin"), Buffer.from(`\0key=${secret}\n`));
  gitSpawn("add", "data.bin");
  assert.notEqual(gitSpawn("commit", "-m", "leak-binary-token").status, 0);
  gitSpawn("restore", "--staged", "data.bin");
  fs.rmSync(path.join(repo, "data.bin"));

  // 커밋 인자의 명령 치환으로 시크릿 파일을 몰래 스테이징하려 해도 guard-git이 차단한다
  fs.writeFileSync(path.join(repo, "credentials.txt"), `token=${secret}\n`);
  const blockedSub = spawnSync(hookCmds[0], { shell: true, env: { ...process.env, CLAUDE_PROJECT_DIR: projectRoot }, input: JSON.stringify({ tool_input: { command: 'git commit -m "$(git add credentials.txt; printf cleanup)"' }, cwd: repo }), encoding: "utf8", timeout: 10000 });
  assert.equal(blockedSub.status, 2, blockedSub.stderr);
  fs.rmSync(path.join(repo, "credentials.txt"));

  // 3) .githooks/pre-push: main으로 푸시 차단, 작업 브랜치 푸시 허용
  assert.notEqual(gitSpawn("push", "origin", "HEAD:refs/heads/main").status, 0);
  assert.equal(gitSpawn("push", "origin", "feat/demo").status, 0);
});
