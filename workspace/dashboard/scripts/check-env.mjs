// 시험장 PC 환경 점검. `node scripts/check-env.mjs` — 대시보드에 필요한 것과 hindsight 상태를 확인하고 고칠 방법을 알려 준다.
//   HINDSIGHT_URL(기본 http://localhost:8888), AIDIC_DASHBOARD_PORT(기본 8787), AIDIC_PHONE_DIR 를 따른다.
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DASH = path.resolve(HERE, '..');
const WS = path.resolve(DASH, '..');
const PHONE = path.resolve(WS, process.env.AIDIC_PHONE_DIR || '../Mobile Storage');
const PORT = Number(process.env.AIDIC_DASHBOARD_PORT || 8787);
const HS = (process.env.HINDSIGHT_URL || 'http://localhost:8888').replace(/\/$/, '');
const BANK = 'aidic-scenario';

let bad = 0;
const ok = (msg) => console.log(`  ✓ ${msg}`);
const fail = (msg, fix) => { bad++; console.log(`  ✗ ${msg}${fix ? `\n      → ${fix}` : ''}`); };
const note = (msg) => console.log(`  · ${msg}`);

async function getJson(url, ms = 5000) {
  const res = await fetch(url, { signal: AbortSignal.timeout(ms) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

console.log('\n[대시보드]');
const major = Number(process.versions.node.split('.')[0]);
major >= 20 ? ok(`Node ${process.versions.node}`) : fail(`Node ${process.versions.node} (20 이상 필요)`, 'Node 20 이상을 설치한다');

const SETS = ['variants', 'judge_scores', 'recall_metrics', 'recall_queries', 'photos', 'findings', 'adjudications', 'samples'];
const missing = SETS.filter((n) => !existsSync(path.join(DASH, 'data', `${n}.json`)));
missing.length ? fail(`결과 파일 없음: ${missing.join(', ')}`, 'git pull 로 workspace/dashboard/data 를 받는다') : ok(`결과 스냅숏 ${SETS.length}개`);

if (!existsSync(PHONE)) {
  fail(`폰 폴더 없음: ${PHONE}`, '저장소의 Mobile Storage 폴더가 있는지, 아니면 AIDIC_PHONE_DIR 로 경로를 준다');
} else {
  let samples = [];
  try { samples = JSON.parse(await readFile(path.join(DASH, 'data', 'samples.json'), 'utf8')).rows ?? []; } catch { /* 위에서 이미 알림 */ }
  const lost = samples.filter((s) => !existsSync(path.join(PHONE, s.id)));
  lost.length ? fail(`사진 ${lost.length}장 없음 (예: ${lost[0].id})`, 'Mobile Storage 를 다시 받는다') : ok(`폰 폴더 사진 ${samples.length}장`);
}

const free = await new Promise((resolve) => {
  const srv = net.createServer().once('error', () => resolve(false)).once('listening', () => srv.close(() => resolve(true)));
  srv.listen(PORT, '0.0.0.0');
});
free ? ok(`포트 ${PORT} 비어 있음`) : note(`포트 ${PORT}을(를) 이미 쓰고 있다(대시보드가 이미 떠 있으면 정상). 아니면 AIDIC_DASHBOARD_PORT 로 바꾼다`);

const ips = Object.entries(os.networkInterfaces()).flatMap(([n, a]) => (a ?? []).filter((x) => x.family === 'IPv4' && !x.internal).map((x) => `${x.address} (${n})`));
ips.length ? ok(`다른 PC가 접속할 주소: ${ips.map((ip) => `http://${ip.split(' ')[0]}:${PORT}`).join(', ')}`) : fail('사내망 IPv4 주소가 없다', '네트워크 연결을 확인한다');
note('다른 PC에서 안 열리면 Windows 방화벽에서 node.exe 를 개인 네트워크에 허용한다');

console.log(`\n[hindsight] ${HS}`);
const zip = path.join(DASH, 'hindsight', `${BANK}.zip`);
existsSync(zip) ? ok(`은행 파일 ${path.relative(path.resolve(WS, '..'), zip)}`) : fail('은행 파일(aidic-scenario.zip)이 없다', 'git pull 로 workspace/dashboard/hindsight 를 받는다');
try {
  const v = await getJson(`${HS}/version`);
  const [ma, mi] = String(v.api_version ?? '0.0').split('.').map(Number);
  (ma > 1 || (ma === 1 && mi >= 2)) ? ok(`hindsight ${v.api_version}`) : fail(`hindsight ${v.api_version} (1.2 이상 권장)`, '은행 옮기기(transfer/import)가 없는 판일 수 있다. hindsight 를 올린다');
  try {
    const spec = await getJson(`${HS}/openapi.json`, 10000);
    spec.paths?.['/v1/default/banks/{bank_id}/transfer/import'] ? ok('은행 가져오기(transfer/import) 지원') : fail('은행 가져오기(transfer/import) 없음', 'hindsight 를 1.2 이상으로 올린다');
  } catch { note('openapi.json 을 읽지 못해 transfer/import 지원 여부는 확인하지 못했다'); }
  try {
    const docs = await getJson(`${HS}/v1/default/banks/${BANK}/documents?limit=1`);
    note(`은행 ${BANK} 이(가) 이미 있다 (문서 ${docs.total ?? '?'}개). 다시 가져오려면 target_bank_id 를 다른 이름으로 한다`);
  } catch { ok(`은행 ${BANK} 아직 없음(가져오기 가능)`); }
} catch (e) {
  fail(`hindsight 에 접속하지 못했다 (${e.message})`, 'hindsight 를 띄우거나 HINDSIGHT_URL 로 주소를 준다. 대시보드만 쓸 거면 무시해도 된다');
}
note('임베딩 모델은 HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-m3 (다국어) 인지 hindsight 설정에서 직접 확인한다');

console.log(bad ? `\n고칠 것 ${bad}개` : '\n모두 준비됐다');
process.exitCode = bad ? 1 : 0;
