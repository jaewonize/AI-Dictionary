// 사진 색인 품질 대시보드 서버. Node 내장 모듈만 쓰므로 npm install 없이 `node server.mjs`로 뜬다.
// 결과는 커밋된 스냅숏(dashboard/data/*.json)에서, 사진은 저장소의 폰 폴더(Mobile Storage/) 원본에서 읽는다.
// 그래서 저장소를 받은 다른 PC에서도 같은 명령으로 같은 화면이 뜬다. 스냅숏 갱신은 scripts/export-data.mjs.
// 맨 아래 '내 실제 데이터' 절(/api/bali, /bali-img/)은 개인 사진이라 저장소에 넣지 않고 var/bali/ 결과를 그 자리에서 읽으며,
// 이 PC(localhost)에서 온 요청에만 답한다. 좌표는 내보내지 않는다.
//   환경 변수: AIDIC_DASHBOARD_HOST(기본 0.0.0.0 — 같은 망의 다른 PC에서 접속), AIDIC_DASHBOARD_PORT(기본 8787),
//             AIDIC_PHONE_DIR(폰 폴더, workspace/ 기준 상대 경로 가능. 기본 ../Mobile Storage),
//             AIDIC_BALI_DIR(실데이터 결과 폴더, workspace/ 기준 상대 경로 가능. 기본 var/bali)
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
const BALI = path.resolve(WS, process.env.AIDIC_BALI_DIR || 'var/bali');
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
const STATIC = new Set(['index.html', 'app.js', 'app.css', 'bali.js', 'favicon.svg']);
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

// ---- 내 실제 데이터(var/bali/) ----
const LOOPBACK = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);
const isLocal = (req) => LOOPBACK.has(req.socket.remoteAddress);

async function readJsonl(file) {
  const text = await readFile(file, 'utf8');
  return text.split(/\r?\n/).filter((l) => l.trim()).map((l) => JSON.parse(l));
}
const readText = (file) => readFile(file, 'utf8').catch(() => null);
const sum = (xs) => xs.reduce((a, b) => a + (Number(b) || 0), 0);
function median(xs) {
  const s = xs.map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  if (!s.length) return null;
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
function mostCommon(xs) {
  const m = new Map();
  for (const x of xs) if (x !== null && x !== undefined) m.set(x, (m.get(x) || 0) + 1);
  let best = null;
  let n = 0;
  for (const [k, v] of m) if (v > n) [best, n] = [k, v];
  return best;
}
const safeName = (v) => (typeof v === 'string' && /^[\w.-]+$/.test(v) ? v : null);
const promptText = (v) => (safeName(v) ? readText(path.join(WS, 'app', 'prompts', `${v}.md`)) : null);
const fmtNear = (xs) => (xs ?? []).map((x) => `${x.name}(${x.kind}, ${x.m}m)`).join(', ') || '없음';

// id → 화면용 사본(var/bali 기준 상대 경로). /bali-img/ 는 여기 있는 id만 내준다.
let baliPhotos = new Map();

async function buildBali() {
  const catalog = await readJsonl(path.join(BALI, 'catalog.jsonl'));
  const descs = new Map();
  for (const d of await readJsonl(path.join(BALI, 'data', 'descriptions.jsonl')).catch(() => [])) descs.set(d.id, d);
  let places = null;
  try {
    places = JSON.parse(await readFile(path.join(BALI, 'data', 'places.json'), 'utf8'));
  } catch {
    places = null;
  }
  const calls = await readJsonl(path.join(BALI, 'claude_calls.jsonl')).catch(() => []);

  const map = new Map();
  const photos = [];
  for (const r of catalog) {
    if (r.kind === 'video') continue;
    if (r.screen_copy) map.set(r.id, r.screen_copy);
    const d = descs.get(r.id);
    const p = places?.photos?.[r.id] ?? {};
    photos.push({
      id: r.id, source: r.source, badge: r.badge, time: r.time ?? null, time_basis: r.time_basis ?? null,
      received_at: r.received_at ?? null, width: r.width, height: r.height,
      model_width: r.model_width, model_height: r.model_height,
      place_group: p.place_group ?? null, visit: p.visit ?? null,
      area_ko: p.area_ko ?? '', area_en: p.area_en ?? '', near_ko: p.near_ko ?? '', near_en: p.near_en ?? '',
      desc: d ? {
        place: d.place ?? '', scene: d.scene ?? '', text_in_photo: d.text_in_photo ?? '', photo_type: d.photo_type ?? '',
        model: d.model, effort: d.effort, prompt_version: d.prompt_version, input_tokens: d.input_tokens,
        output_tokens: d.output_tokens, cost_usd: d.cost_usd, ms: d.ms, error: d.error ?? null,
      } : null,
      img: `/bali-img/${encodeURIComponent(r.id)}`,
    });
  }
  baliPhotos = map;

  const groups = (places?.groups ?? []).map((g) => ({
    group: g.group, photos: g.photos, days: g.days ?? [], spread_m: g.spread_m,
    area_ko: g.area_ko ?? '', area_en: g.area_en ?? '', near_ko: g.near_ko ?? '', near_en: g.near_en ?? '', note: g.note ?? '',
    nearby: { towns: fmtNear(g.nearby?.towns), districts: fmtNear(g.nearby?.districts), spots: fmtNear(g.nearby?.spots) },
  }));
  // places.py 가 Claude에 보낸 글을 places.json 의 같은 값으로 다시 만든다(좌표는 처음부터 들어 있지 않다).
  const placesRequest = groups.length
    ? '아래 묶음마다 지명을 붙여 주세요.\n\n' + groups.map((g) => `[${g.group}] 사진 ${g.photos}장, 묶음 반경 약 ${g.spread_m}m\n`
      + `  마을·동네: ${g.nearby.towns}\n  행정 구역: ${g.nearby.districts}\n  명소: ${g.nearby.spots}`).join('\n\n')
    : null;

  const byPurpose = new Map();
  for (const c of calls) {
    const k = `${c.purpose}\u0000${c.model}\u0000${c.effort}`;
    if (!byPurpose.has(k)) byPurpose.set(k, []);
    byPurpose.get(k).push(c);
  }
  const purposes = [...byPurpose.values()].map((cs) => ({
    purpose: cs[0].purpose, model: cs[0].model, effort: cs[0].effort, calls: cs.length,
    input_tokens: sum(cs.map((c) => c.input_tokens)), output_tokens: sum(cs.map((c) => c.output_tokens)),
    cache_read_input_tokens: sum(cs.map((c) => c.cache_read_input_tokens)), cost_usd: sum(cs.map((c) => c.cost_usd)),
    ms_median: median(cs.map((c) => c.ms)), first_at: cs[0].at ?? null, last_at: cs[cs.length - 1].at ?? null,
    fallback: cs.filter((c) => c.fallback).length,
  }));

  const ds = [...descs.values()];
  const dVersion = mostCommon(ds.map((d) => d.prompt_version));
  const describePy = await readText(path.join(WS, 'pipeline', 'describe.py'));
  const exists = async (rel) => stat(path.join(BALI, rel)).then((s) => s.isFile(), () => false);

  let index = null;
  if (await exists('index.jsonl')) {
    const idxRows = await readJsonl(path.join(BALI, 'index.jsonl')).catch(() => []);
    const pRows = idxRows.filter((r) => r.kind === 'photo');
    const dRows = idxRows.filter((r) => r.kind === 'document');
    const byType = {};
    const bySource = {};
    for (const r of pRows) {
      const pt = r.photo_type || '미분류';
      byType[pt] = (byType[pt] || 0) + 1;
      const sc = r.badge || r.source || '출처 없음';
      bySource[sc] = (bySource[sc] || 0) + 1;
    }
    index = {
      rows: idxRows.length,
      photos: pRows.length,
      documents: dRows.length,
      missing_time: pRows.filter((r) => !r.time).length,
      missing_place: pRows.filter((r) => !r.area_ko && !r.place_group).length,
      with_near: pRows.filter((r) => Boolean(r.near_ko)).length,
      missing_scene: pRows.filter((r) => !r.scene).length,
      with_text: pRows.filter((r) => Boolean((r.text_in_photo || '').trim())).length,
      linked: pRows.filter((r) => Boolean(r.linked_to)).length,
      photo_types: byType,
      sources: bySource,
      log: await readText(path.join(BALI, 'logs', 'build_index.log')),
    };
  }

  let answerKey = null;
  try {
    const rawAk = JSON.parse(await readFile(path.join(BALI, 'answer_key.json'), 'utf8'));
    const tests = Array.isArray(rawAk) ? rawAk : (Array.isArray(rawAk?.tests) ? rawAk.tests : []);
    answerKey = tests.map((t) => ({
      id: t.id,
      kind: t.kind ?? '',
      query: t.query ?? '',
      answer_ids: Array.isArray(t.answer_ids) ? t.answer_ids : [],
      optional_ids: Array.isArray(t.optional_ids) ? t.optional_ids : [],
      trap_ids: Array.isArray(t.trap_ids) ? t.trap_ids : [],
      note: t.note ?? '',
    }));
  } catch {
    answerKey = null;
  }

  let evalReport = null;
  try {
    const rawEv = JSON.parse(await readFile(path.join(BALI, 'eval_results.json'), 'utf8'));
    const rawResults = Array.isArray(rawEv.results) ? rawEv.results : [];
    const totalN = Number(rawEv.total ?? rawResults.length) || 0;
    const passedN = Number(rawEv.passed) || 0;
    evalReport = {
      at: rawEv.evaluated_at ?? rawEv.at ?? null,
      model: rawEv.model ?? null,
      total: totalN,
      passed: passedN,
      failed: Number(rawEv.failed ?? (totalN - passedN)) || 0,
      exact_match: Number(rawEv.exact ?? rawEv.exact_match) || 0,
      calls: Number(rawEv.total_calls ?? rawEv.calls) || 0,
      cost_usd: Number(rawEv.total_cost_usd ?? rawEv.cost_usd) || 0,
      elapsed_s: Number(rawEv.elapsed_s ?? (sum(rawResults.map((r) => r.elapsed_ms)) / 1000)) || 0,
      results: rawResults.map((r) => ({
        id: r.id,
        kind: r.kind ?? '',
        query: r.query ?? '',
        note: r.note ?? '',
        passed: Boolean(r.passed),
        exact_match: Boolean(r.exact_match),
        status: r.status ?? '',
        error: r.error ?? null,
        answer_ids: Array.isArray(r.answer_ids) ? r.answer_ids : [],
        optional_ids: Array.isArray(r.optional_ids) ? r.optional_ids : [],
        trap_ids: Array.isArray(r.trap_ids) ? r.trap_ids : [],
        got_ids: Array.isArray(r.got_ids) ? r.got_ids : [],
        matched_must: Array.isArray(r.matched_must) ? r.matched_must : [],
        matched_optional: Array.isArray(r.matched_optional) ? r.matched_optional : [],
        missing_ids: Array.isArray(r.missing_ids) ? r.missing_ids : [],
        extra_ids: Array.isArray(r.extra_ids) ? r.extra_ids : [],
        trap_hits: Array.isArray(r.trap_hits) ? r.trap_hits : [],
        recall: r.recall ?? null,
        precision: r.precision ?? null,
        result_sentence: r.result_sentence ?? '',
        cutoffs: Array.isArray(r.cutoffs) ? r.cutoffs : [],
        calls: Number(r.calls) || 0,
        cost_usd: Number(r.cost_usd) || 0,
        elapsed_s: Number(r.elapsed_s ?? ((Number(r.elapsed_ms) || 0) / 1000)) || 0,
      })),
    };
  } catch {
    evalReport = null;
  }

  let hindsight = null;
  const hsRetainRaw = await readText(path.join(BALI, 'hindsight_retain.json'));
  const hsPilotRaw = await readText(path.join(BALI, 'hindsight_pilot.json'));
  const hsEvalRaw = await readText(path.join(BALI, 'hindsight_eval.json'));
  if (hsRetainRaw || hsPilotRaw || hsEvalRaw) {
    let hsRetain = null;
    let hsPilot = null;
    let hsEval = null;
    try { if (hsRetainRaw) hsRetain = JSON.parse(hsRetainRaw); } catch { hsRetain = null; }
    try { if (hsPilotRaw) hsPilot = JSON.parse(hsPilotRaw); } catch { hsPilot = null; }
    try { if (hsEvalRaw) hsEval = JSON.parse(hsEvalRaw); } catch { hsEval = null; }
    hindsight = {
      retain: hsRetain,
      pilot: hsPilot,
      eval: hsEval,
      logs: {
        pilot: await readText(path.join(BALI, 'logs', 'hs_pilot.log')),
        retain: await readText(path.join(BALI, 'logs', 'hs_retain.log')),
        eval: await readText(path.join(BALI, 'logs', 'hs_eval.log')),
      },
    };
  }

  return {
    dir: path.relative(WS, BALI).split(path.sep).join('/'),
    catalog: { photos: photos.length, videos: catalog.filter((r) => r.kind === 'video').length },
    photos,
    groups,
    describe: {
      model: mostCommon(ds.map((d) => d.model)), effort: mostCommon(ds.map((d) => d.effort)), prompt_version: dVersion,
      system: await promptText(dVersion),
      user_prompt: describePy?.match(/USER_PROMPT\s*=\s*"([^"]*)"/)?.[1] ?? null,
      log: await readText(path.join(BALI, 'describe.log')),
    },
    places: places ? {
      model: places.model, prompt_version: places.prompt_version, system: await promptText(places.prompt_version),
      request: placesRequest,
    } : null,
    index,
    answer_key: answerKey,
    eval: evalReport,
    hindsight,
    calls: { count: calls.length, total_usd: sum(calls.map((c) => c.cost_usd)), purposes },
    next: [
      { step: 'link', file: 'data/links.jsonl', done: await exists('data/links.jsonl') },
      { step: 'build_index', file: 'index.jsonl', done: await exists('index.jsonl') },
      { step: 'eval', file: 'eval_results.json', done: await exists('eval_results.json') },
      { step: 'hindsight_retain', file: 'hindsight_retain.json', done: await exists('hindsight_retain.json') },
      { step: 'hindsight_eval', file: 'hindsight_eval.json', done: await exists('hindsight_eval.json') },
    ],
  };
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

    if (p === '/api/bali') {
      const json = (status, body) => send(res, status, req.method === 'HEAD' ? '' : JSON.stringify(body), TYPES['.json'], { 'Cache-Control': 'no-store' });
      if (!isLocal(req)) return json(403, { error: '개인 사진이라 이 PC(localhost)에서만 볼 수 있습니다.' });
      let data;
      try {
        data = await buildBali();
      } catch (err) {
        return json(404, { error: `실데이터 결과(${path.relative(WS, BALI)})를 읽지 못했습니다: ${err.code || err.message}` });
      }
      return json(200, data);
    }

    if (p.startsWith('/bali-img/')) {
      if (!isLocal(req)) return send(res, 403, '개인 사진이라 이 PC(localhost)에서만 볼 수 있습니다.');
      const id = p.slice('/bali-img/'.length);
      if (!baliPhotos.size) await buildBali().catch(() => null);
      const rel = baliPhotos.get(id);
      if (!rel) return send(res, 404, '실데이터 목록에 없는 사진입니다.');
      const file = path.resolve(BALI, rel);
      if (!file.startsWith(BALI + path.sep)) return send(res, 400, '잘못된 경로입니다.');
      return sendFile(req, res, file, 'private, max-age=3600');
    }

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
  console.log(`  실데이터: ${BALI} (맨 아래 절, 이 PC에서만)`);
  if (data.missing.length) console.log(`  없는 결과 파일: ${data.missing.join(', ')}`);
  console.log('  끄려면 Ctrl+C');
});
