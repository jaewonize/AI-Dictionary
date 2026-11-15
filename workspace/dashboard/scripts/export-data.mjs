// 실험 결과(workspace/var/, git에 안 올라감)를 대시보드 스냅숏(dashboard/data/, git에 올라감)으로 내보낸다.
// 비교·시험 스크립트를 다시 돌린 뒤 `node scripts/export-data.mjs` 를 실행하고 data/ 를 커밋하면,
// 저장소를 받은 다른 PC에서도 같은 결과가 보인다. 코드 본문·data URL·로컬 경로는 옮기지 않는다.
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DASH = path.resolve(HERE, '..');
const WS = path.resolve(DASH, '..');
const OUT = path.join(DASH, 'data');
const COMPARE = path.join(WS, 'var', 'compare');
const SCENARIO = path.join(WS, 'var', 'scenario');

async function readJson(file) {
  try {
    return JSON.parse(await readFile(file, 'utf8'));
  } catch {
    return null;
  }
}

const exportedAt = new Date().toISOString();
await mkdir(OUT, { recursive: true });

async function write(name, rows, madeBy) {
  const body = { exported_at: exportedAt, made_by: madeBy, rows };
  await writeFile(path.join(OUT, `${name}.json`), JSON.stringify(body, null, 1) + '\n', 'utf8');
  console.log(`  ${name}.json  ${rows.length}행`);
}

let done = 0;
const COMPARE_SETS = ['variants', 'judge_scores', 'recall_metrics', 'recall_queries', 'photos', 'findings', 'adjudications'];
for (const name of COMPARE_SETS) {
  const j = await readJson(path.join(COMPARE, 'dash', `${name}.json`));
  if (!j) {
    console.log(`  건너뜀: var/compare/dash/${name}.json 이 없다 (compare_describe.py dashdata 를 먼저 돌린다)`);
    continue;
  }
  await write(name, j.rows ?? j, 'workspace/scripts/compare_describe.py dashdata');
  done++;
}

const samples = await readJson(path.join(COMPARE, 'samples.json'));
if (samples) {
  await write('samples', samples.map((s) => ({ key: s.key, id: s.id, width: s.width, height: s.height, frames: s.frames, orientation: s.orientation })),
    'workspace/scripts/compare_describe.py prepare');
  done++;
}

for (const name of ['scenario_runs', 'scenario_returns', 'scenario_photos']) {
  const j = await readJson(path.join(SCENARIO, 'dash', `${name}.json`));
  if (!j) {
    console.log(`  건너뜀: var/scenario/dash/${name}.json 이 없다 (scenario_hindsight.py dash 를 먼저 돌린다)`);
    continue;
  }
  const rows = (j.rows ?? j).map(({ thumb, ...r }) => r); // 썸네일 data URL 은 빼고 폰 폴더 원본을 쓴다
  await write(name, rows, 'workspace/scripts/scenario_hindsight.py dash');
  done++;
}
console.log(`${done}개 파일을 ${OUT} 에 내보냈다 (${exportedAt})`);
