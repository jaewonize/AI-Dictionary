// 사진 색인 품질 대시보드 서버. Node 내장 모듈만 쓰므로 npm install 없이 `node server.mjs`로 뜬다.
// 결과는 커밋된 스냅숏(dashboard/data/*.json)에서, 사진은 저장소의 폰 폴더(Mobile Storage/) 원본에서 읽는다.
// 그래서 저장소를 받은 다른 PC에서도 같은 명령으로 같은 화면이 뜬다. 스냅숏 갱신은 scripts/export-data.mjs.
//   환경 변수: AIDIC_DASHBOARD_HOST(기본 0.0.0.0 — 같은 망의 다른 PC에서 접속), AIDIC_DASHBOARD_PORT(기본 8787),
//             AIDIC_PHONE_DIR(폰 폴더, workspace/ 기준 상대 경로 가능. 기본 ../Mobile Storage)
import http from 'node:http';
import { createReadStream } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WS = path.resolve(HERE, '..');
const PUBLIC = path.join(HERE, 'public');
const DATA = path.join(HERE, 'data');
const PHONE = path.resolve(WS, process.env.AIDIC_PHONE_DIR || '../Mobile Storage');
const HOST = process.env.AIDIC_DASHBOARD_HOST || '0.0.0.0';
const PORT = Number(process.env.AIDIC_DASHBOARD_PORT || 8787);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
};
const STATIC = new Set(['index.html', 'app.js', 'app.css', 'favicon.svg']);
const SETS = ['variants', 'judge_scores', 'recall_metrics', 'recall_queries', 'photos', 'findings', 'adjudications',
  'samples', 'scenario_runs', 'scenario_returns', 'scenario_photos'];

async function readRows(name) {
  try {
    const j = JSON.parse(await readFile(path.join(DATA, `${name}.json`), 'utf8'));
    return { rows: Array.isArray(j) ? j : (j.rows ?? []), exportedAt: j.exported_at ?? null };
  } catch {
    return null;
  }
}

const imgUrl = (id) => `/img/${id.split('/').map(encodeURIComponent).join('/')}`;

// 데이터에 나온 사진 id만 내준다. 폰 폴더의 다른 파일이나 폴더 밖 경로는 열지 않는다.
let allowed = new Set();

async function buildData() {
  const out = { missing: [], exported_at: null };
  for (const name of SETS) {
    const r = await readRows(name);
    if (!r) {
      if (!name.startsWith('scenario_')) out.missing.push(`dashboard/data/${name}.json`);
      out[name] = null;
      continue;
    }
    out[name] = r.rows;
    if (r.exportedAt && (!out.exported_at || r.exportedAt > out.exported_at)) out.exported_at = r.exportedAt;
  }
  const ids = new Set();
  for (const s of out.samples ?? []) {
    ids.add(s.id);
    s.img_url = imgUrl(s.id);
  }
  for (const p of out.scenario_photos ?? []) {
    ids.add(p.photo_id);
    p.img_url = imgUrl(p.photo_id);
  }
  allowed = ids;
  return out;
}

function send(res, status, body, type = 'text/plain; charset=utf-8', extra = {}) {
  res.writeHead(status, { 'Content-Type': type, 'X-Content-Type-Options': 'nosniff', ...extra });
  res.end(body);
}

async function sendFile(req, res, file, cache) {
  let st;
  try {
    st = await stat(file);
    if (!st.isFile()) throw new Error('not a file');
  } catch {
    return send(res, 404, '없는 파일입니다.');
  }
  res.writeHead(200, {
    'Content-Type': TYPES[path.extname(file).toLowerCase()] ?? 'application/octet-stream',
    'Content-Length': st.size,
    'Cache-Control': cache,
    'X-Content-Type-Options': 'nosniff',
  });
  if (req.method === 'HEAD') return res.end();
  createReadStream(file).on('error', () => res.destroy()).pipe(res);
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method !== 'GET' && req.method !== 'HEAD') return send(res, 405, '읽기만 됩니다.', undefined, { Allow: 'GET, HEAD' });
    const url = new URL(req.url, 'http://localhost');
    let p;
    try {
      p = decodeURIComponent(url.pathname);
    } catch {
      return send(res, 400, '잘못된 주소입니다.');
    }

    if (p === '/api/data') {
      const data = await buildData();
      return send(res, 200, req.method === 'HEAD' ? '' : JSON.stringify(data), TYPES['.json'], { 'Cache-Control': 'no-store' });
    }
    if (p === '/healthz') return send(res, 200, 'ok');

    if (p.startsWith('/img/')) {
      const id = p.slice(5);
      if (!allowed.size) await buildData();
      if (!allowed.has(id)) return send(res, 404, '대시보드에 없는 사진입니다.');
      const file = path.resolve(PHONE, id);
      if (!file.startsWith(PHONE + path.sep)) return send(res, 400, '잘못된 경로입니다.');
      return sendFile(req, res, file, 'public, max-age=3600');
    }

    const name = p === '/' ? 'index.html' : p.slice(1);
    if (STATIC.has(name)) return sendFile(req, res, path.join(PUBLIC, name), 'no-cache');
    return send(res, 404, '없는 주소입니다.');
  } catch (err) {
    console.error(err);
    return send(res, 500, '서버 오류가 났습니다.');
  }
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') console.error(`포트 ${PORT}을(를) 이미 쓰고 있습니다. AIDIC_DASHBOARD_PORT 로 다른 포트를 정하세요.`);
  else console.error(err);
  process.exit(1);
});

server.listen(PORT, HOST, async () => {
  const data = await buildData();
  console.log('사진 색인 품질 대시보드');
  console.log(`  이 PC:    http://localhost:${PORT}`);
  if (HOST === '0.0.0.0' || HOST === '::') {
    for (const [ifname, addrs] of Object.entries(os.networkInterfaces())) {
      for (const a of addrs ?? []) {
        if (a.family === 'IPv4' && !a.internal) console.log(`  다른 PC:  http://${a.address}:${PORT}   (${ifname})`);
      }
    }
  }
  console.log(`  결과:     ${DATA}${data.exported_at ? ` (내보낸 시각 ${data.exported_at})` : ''}`);
  console.log(`  사진:     ${PHONE}`);
  if (data.missing.length) console.log(`  없는 결과 파일: ${data.missing.join(', ')}`);
  console.log('  끄려면 Ctrl+C');
});
