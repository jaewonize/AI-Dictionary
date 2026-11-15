'use strict';
// 사진 색인 품질 대시보드 화면. /api/data 의 결과로 숫자·차트·표를 그린다(외부 라이브러리 없음).

const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = 'http://www.w3.org/2000/svg';

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c !== null && c !== undefined) e.append(c);
  return e;
}

function sv(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(+v) ? null : +v);
const F = {
  score: (v) => (num(v) === null ? '—' : num(v).toFixed(2)),
  pct: (v) => (num(v) === null ? '—' : `${Math.round(num(v) * 100)}%`),
  usd: (v) => (num(v) === null ? '—' : `$${num(v).toFixed(4)}`),
  sec: (v) => (num(v) === null ? '—' : `${num(v).toFixed(1)}초`),
  int: (v) => (num(v) === null ? '—' : Math.round(num(v)).toLocaleString('ko-KR')),
  two: (v) => (num(v) === null ? '—' : num(v).toFixed(2)),
};

const ORDER = ['claude_medium', 'claude_low', 'argon_ultra', 'argon_high', 'flash38_high'];
const BANKS = [...ORDER, 'mixA_argon_ultra', 'mixB_argon_ultra'];
const KIND_KO = { scene: '장소·장면', food_object: '음식·사물', text: '사진 속 글자', people: '사람·활동', document: '문서·캡처', discriminate: '비슷한 사진 가려내기' };
const LAB_KO = { answer: '정답', trap: '함정', exhibition: '전시장', other: '그 밖' };
const CARDS = [
  ['claude_medium', 'Claude Opus 5.5 · effort medium', 'Anthropic · claude-opus-5-5'],
  ['argon_ultra', 'Gemini 4 Argon · 해상도 ULTRA_HIGH', 'Google · gemini-4-argon · thinking MEDIUM'],
  ['argon_high', 'Gemini 4 Argon · 해상도 HIGH(기본과 같음)', 'Google · gemini-4-argon · thinking MEDIUM'],
];
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const state = { data: null, judge: 'avg', arm: 'fused', photo: null };

// ---------------------------------------------------------------- 공통
const tip = {
  el: null,
  show(ev, lines) {
    this.el ??= $('#tip');
    this.el.replaceChildren(...lines.filter(Boolean).map((t) => el('div', { text: t })));
    this.el.hidden = false;
    const pad = 14;
    const w = this.el.offsetWidth;
    const h = this.el.offsetHeight;
    const x = Math.min(ev.clientX + pad, window.innerWidth - w - 8);
    const y = ev.clientY - h - pad < 8 ? ev.clientY + pad : ev.clientY - h - pad;
    this.el.style.left = `${Math.max(8, x)}px`;
    this.el.style.top = `${y}px`;
  },
  hide() {
    if (this.el) this.el.hidden = true;
  },
};

const rows = (name) => state.data?.[name] ?? [];
const find = (name, pred) => rows(name).find(pred);
const sampleByKey = () => new Map(rows('samples').map((s) => [s.key, s]));

function chartFrame(box, height, label) {
  box.replaceChildren();
  const W = Math.max(box.clientWidth, 300);
  const svg = sv('svg', { width: '100%', height, viewBox: `0 0 ${W} ${height}`, role: 'img', 'aria-label': label });
  box.appendChild(svg);
  return { svg, W };
}

function textWidth(svg, text, cls = 'lbl') {
  const t = sv('text', { x: -9999, y: -9999, class: cls }, svg);
  t.textContent = text;
  const w = t.getComputedTextLength();
  t.remove();
  return w;
}

function steps(lo, hi, step) {
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

function gridX(svg, x, ticks, top, bottom, fmt) {
  for (const t of ticks) {
    sv('line', { x1: x(t), x2: x(t), y1: top, y2: bottom, class: 'grid' }, svg);
    const tx = sv('text', { x: x(t), y: bottom + 16, 'text-anchor': 'middle' }, svg);
    tx.textContent = fmt(t);
  }
}

function hover(node, lines) {
  node.addEventListener('pointermove', (ev) => tip.show(ev, lines));
  node.addEventListener('pointerleave', () => tip.hide());
}

function emptyChart(box, msg = '데이터 없음') {
  box.replaceChildren(el('p', { class: 'muted', text: msg }));
}

// ---------------------------------------------------------------- 머리말·판정·카드
function drawTop() {
  const d = state.data;
  $('#stamp').textContent = d.exported_at ? `결과 내보낸 시각 ${new Date(d.exported_at).toLocaleString('ko-KR')}` : '결과 시각 모름';
  const miss = $('#missing');
  miss.hidden = !d.missing?.length;
  if (d.missing?.length) miss.textContent = `없는 결과 파일: ${d.missing.join(', ')} — workspace/dashboard 에서 node scripts/export-data.mjs 를 돌린다.`;

  const avg = (v) => find('judge_scores', (r) => r.variant === v && r.judge === 'avg')?.overall;
  const hit3 = (v) => find('recall_metrics', (r) => r.bank === v && r.arm === 'fused' && r.kind === 'all');
  const q = hit3('claude_medium')?.queries;
  const take = $('#takeaway');
  take.replaceChildren(
    '두 심사 평균 종합 점수(5점 만점)는 Claude Opus 5.5 medium ', el('b', { text: F.score(avg('claude_medium')) }),
    ', Gemini 4 Argon ULTRA_HIGH ', el('b', { text: F.score(avg('argon_ultra')) }),
    ', Argon 기본 해상도 ', el('b', { text: F.score(avg('argon_high')) }),
    `다. 한국어 질의 `, el('b', { text: F.int(q) }), `개로 검색했을 때 정답 사진이 3위 안에 든 비율은 같은 순서로 `,
    el('b', { text: F.pct(hit3('claude_medium')?.hit3) }), ', ', el('b', { text: F.pct(hit3('argon_ultra')?.hit3) }), ', ',
    el('b', { text: F.pct(hit3('argon_high')?.hit3) }), '다.',
  );

  $('#findings-list').replaceChildren(...rows('findings').map((r) => el('li', {}, el('span', { class: 'kind', text: r.kind }), el('span', { text: r.text }))));

  $('#kpi-cards').replaceChildren(...CARDS.map(([v, title, maker]) => {
    const j = find('judge_scores', (r) => r.variant === v && r.judge === 'avg') ?? {};
    const h = hit3(v) ?? {};
    const c = find('variants', (r) => r.variant === v) ?? {};
    const stat = (label, value) => el('div', { class: 'stat' }, el('span', { text: label }), el('b', { text: value }));
    return el('article', { class: 'card' },
      el('h3', { text: title }), el('div', { class: 'maker', text: maker }),
      stat('심사 종합', F.score(j.overall)), stat('정확성', F.score(j.accuracy)), stat('사진 속 글자', F.score(j.text)),
      stat('검색 3위 안 적중', F.pct(h.hit3)), stat('사진당 비용(정가)', F.usd(c.cost_per_photo_list_usd)),
      stat('응답 시간 중앙값', F.sec(c.latency_median_s)));
  }));
}

// ---------------------------------------------------------------- 시나리오 A(hindsight)
function drawScenario() {
  const runs = rows('scenario_runs');
  const has = runs.length > 0;
  $('#scenario-empty').hidden = has;
  $('#scenario-body').hidden = !has;
  if (!has) return;
  const photos = rows('scenario_photos');
  const pmap = new Map(photos.map((p) => [p.photo_id, p]));
  const day = photos.filter((p) => p.label !== 'other').sort((a, b) => `${a.date} ${a.time}`.localeCompare(`${b.date} ${b.time}`));
  $('#scenario-day').replaceChildren(...day.map((p) => el('div', { class: 'dayitem' },
    el('img', { src: p.img_url, alt: `${p.time} ${p.place}`, loading: 'lazy', class: `lab-${p.label}`, title: `${p.time} · ${p.place} · ${p.badge} · ${p.scene}` }),
    el('span', { text: `${p.time} · ${LAB_KO[p.label]}` }),
    el('span', { text: `${p.place} · ${p.badge}` }))));

  const rets = rows('scenario_returns');
  $('#scenario-table tbody').replaceChildren(...runs.map((r) => {
    const mine = rets.filter((x) => x.phase === r.phase && x.method === r.method).sort((a, b) => a.rank - b.rank);
    const thumbs = el('div', { class: 'rets' }, ...mine.map((x) => {
      const p = pmap.get(x.photo_id);
      if (!p) return null;
      return el('img', { class: `ret lab-${x.label}`, src: p.img_url, loading: 'lazy', alt: `${x.rank}. ${p.date} ${p.time} ${p.place}`,
        title: `${x.rank}번째 · ${p.date} ${p.time} · ${p.place} · ${p.badge} · ${LAB_KO[x.label]}` });
    }));
    return el('tr', {},
      el('td', { text: r.phase_label }), el('td', { text: r.method_label }),
      el('td', { class: 'n', text: F.int(r.correct) }), el('td', { class: 'n', text: F.int(r.traps) }), el('td', { class: 'n', text: F.int(r.other) }),
      el('td', { class: 'n', text: r.tool_calls === null || r.tool_calls === undefined ? '—' : F.int(r.tool_calls) }),
      el('td', { class: 'n', text: r.seconds === null || r.seconds === undefined ? '—' : Math.round(r.seconds).toString() }),
      el('td', {}, thumbs));
  }));
  $('#scenario-reasons').replaceChildren(...runs.filter((r) => r.reasoning).map((r) => el('p', {},
    el('b', { text: `${r.phase_label} · ${r.method_label}: ` }), el('span', { text: r.reasoning }))));
}

// ---------------------------------------------------------------- 블라인드 심사
function drawJudgeChart() {
  const box = $('#judge-chart');
  const items = ORDER.map((v) => {
    const c = find('judge_scores', (r) => r.variant === v && r.judge === 'claude');
    const g = find('judge_scores', (r) => r.variant === v && r.judge === 'gemini');
    return { label: (c ?? g)?.label, c: num(c?.overall), g: num(g?.overall) };
  }).filter((r) => r.label && (r.c !== null || r.g !== null));
  if (!items.length) return emptyChart(box);
  const rowH = 38;
  const mt = 6;
  const mb = 30;
  const H = mt + mb + rowH * items.length;
  const { svg, W } = chartFrame(box, H, '변형마다 Claude 심사와 Gemini 심사의 종합 점수');
  const ml = Math.min(Math.max(...items.map((r) => textWidth(svg, r.label))) + 14, W * 0.48);
  const vals = items.flatMap((r) => [r.c, r.g]).filter((v) => v !== null);
  const lo = Math.max(1, Math.floor((Math.min(...vals) - 0.25) * 4) / 4);
  const right = W - 100;
  const x = (v) => ml + ((v - lo) / (5 - lo)) * (right - ml);
  const step = 5 - lo > 2 ? 0.5 : 0.25;
  gridX(svg, x, steps(lo, 5, step), mt, H - mb, (v) => v.toFixed(step < 0.5 ? 2 : 1));
  items.forEach((r, i) => {
    const y = mt + rowH * i + rowH / 2;
    sv('text', { x: ml - 10, y, dy: '0.35em', 'text-anchor': 'end', class: 'lbl' }, svg).textContent = r.label;
    if (r.c !== null && r.g !== null) sv('line', { x1: x(r.c), x2: x(r.g), y1: y, y2: y, class: 'link' }, svg);
    const diff = r.c !== null && r.g !== null ? `두 심사 차이 ${r.c - r.g >= 0 ? '+' : ''}${(r.c - r.g).toFixed(2)}` : '';
    [['c', 's1', 'Claude 심사'], ['g', 's2', 'Gemini 4 Argon 심사']].forEach(([k, cls, name]) => {
      if (r[k] === null) return;
      hover(sv('circle', { cx: x(r[k]), cy: y, r: 7, class: `dot ${cls}` }, svg), [r.label, `${name} ${r[k].toFixed(2)}`, diff]);
    });
    const hi = Math.max(...[r.c, r.g].filter((v) => v !== null));
    sv('text', { x: x(hi) + 12, y, dy: '0.35em', class: 'val' }, svg).textContent = [r.c, r.g].map((v) => (v === null ? '—' : v.toFixed(2))).join(' · ');
  });
}

function drawJudgeTable() {
  const crit = ['accuracy', 'text', 'coverage', 'distinctiveness', 'retrievability', 'overall'];
  $('#judge-table tbody').replaceChildren(...ORDER.map((v) => find('judge_scores', (r) => r.variant === v && r.judge === state.judge)).filter(Boolean).map((r) =>
    el('tr', {}, el('td', { text: r.label }), ...crit.map((c) => el('td', { class: 'n', text: F.score(r[c]) })),
      el('td', { class: 'n', text: F.int(r.errors) }), el('td', { class: 'n', text: F.int(r.best) }))));
}

function drawAdjudications() {
  const sm = sampleByKey();
  $('#adj-table tbody').replaceChildren(...rows('adjudications').map((r) => {
    const s = sm.get(r.photo);
    const cell = el('div', { class: 'qthumbs' },
      s ? el('button', { type: 'button', 'aria-label': `${r.photo} 사진을 아래에서 열기`, onclick: () => pick(r.photo, true) }, el('img', { src: s.img_url, alt: r.photo, loading: 'lazy' })) : null,
      el('span', { text: r.photo_label || r.photo }));
    return el('tr', {}, el('td', {}, cell), el('td', { class: 'wide', text: r.issue }), el('td', { class: 'wide', text: r.verdict }));
  }));
}

// ---------------------------------------------------------------- 검색 실험
function barChart(box, items, series, label, fmt, domainMax = 1) {
  if (!items.length) return emptyChart(box);
  const barH = 9;
  const gap = 3;
  const groupH = series.length * (barH + gap) + 14;
  const mt = 4;
  const mb = 28;
  const H = mt + mb + groupH * items.length;
  const { svg, W } = chartFrame(box, H, label);
  const ml = Math.min(Math.max(...items.map((r) => textWidth(svg, r.label))) + 14, W * 0.5);
  const right = W - 52;
  const x = (v) => ml + (v / domainMax) * (right - ml);
  gridX(svg, x, steps(0, domainMax, domainMax / 5), mt, H - mb, fmt);
  items.forEach((r, i) => {
    const top = mt + groupH * i + 6;
    sv('text', { x: ml - 10, y: top + (series.length * (barH + gap)) / 2, dy: '0.35em', 'text-anchor': 'end', class: 'lbl' }, svg).textContent = r.label;
    series.forEach(([key, name, cls], k) => {
      const v = num(r[key]) ?? 0;
      const yy = top + k * (barH + gap);
      hover(sv('rect', { x: x(0), y: yy, height: barH, width: Math.max(0, x(v) - x(0)), rx: 2, class: cls }, svg), [r.label, `${name}: ${fmt(v)}`, ...(r.extra ? [r.extra] : [])]);
      sv('text', { x: x(v) + 4, y: yy + barH / 2, dy: '0.35em', class: 'val' }, svg).textContent = fmt(v);
    });
  });
}

function drawRecall() {
  const items = BANKS.map((b) => find('recall_metrics', (r) => r.bank === b && r.arm === state.arm && r.kind === 'all')).filter(Boolean)
    .map((r) => ({ ...r, label: r.bank_label, extra: `MRR ${F.two(r.mrr)} · nDCG@5 ${F.two(r.ndcg5)} · 질의 ${r.queries}개` }));
  barChart($('#recall-chart'), items, [['hit1', '1위', 's1'], ['hit3', '3위 안', 's2'], ['hit5', '5위 안', 's3']],
    '은행마다 정답 사진이 1위, 3위 안, 5위 안에 든 비율', F.pct);
}

function drawTokenizer() {
  const items = ORDER.map((b) => {
    const nat = find('recall_metrics', (r) => r.bank === b && r.arm === 'bm25_native' && r.kind === 'all');
    const big = find('recall_metrics', (r) => r.bank === b && r.arm === 'bm25_bigram' && r.kind === 'all');
    return nat && big ? { label: big.bank_label, nat: nat.mrr, big: big.mrr, extra: `1위 적중: 띄어쓰기 ${F.pct(nat.hit1)} · 2글자 ${F.pct(big.hit1)}` } : null;
  }).filter(Boolean);
  barChart($('#token-chart'), items, [['nat', '띄어쓰기 단위(native) MRR', 's4'], ['big', '2글자 단위(pgroonga) MRR', 's1']],
    '변형마다 띄어쓰기 단위와 2글자 단위 BM25의 MRR', F.two);
}

function drawKinds() {
  const kinds = [...new Set(rows('recall_metrics').filter((r) => r.arm === 'fused' && r.kind !== 'all').map((r) => r.kind))];
  $('#kind-table tbody').replaceChildren(...kinds.map((k) => {
    const any = find('recall_metrics', (r) => r.bank === 'claude_medium' && r.arm === 'fused' && r.kind === k);
    return el('tr', {}, el('td', { text: KIND_KO[k] ?? k }), el('td', { class: 'n', text: F.int(any?.queries) }),
      ...BANKS.map((b) => el('td', { class: 'n', text: F.pct(find('recall_metrics', (r) => r.bank === b && r.arm === 'fused' && r.kind === k)?.hit3) })));
  }));
}

function drawQueries() {
  const sm = sampleByKey();
  $('#query-table tbody').replaceChildren(...rows('recall_queries').map((q) => {
    const prim = el('div', { class: 'qthumbs' });
    for (const k of String(q.primary || '').split(' ').filter(Boolean)) {
      const s = sm.get(k);
      if (s) prim.append(el('button', { type: 'button', 'aria-label': `${k} 사진을 아래에서 열기`, title: `${k} 열기`, onclick: () => pick(k, true) }, el('img', { src: s.img_url, alt: k, loading: 'lazy' })));
      prim.append(el('span', { text: k }));
    }
    const ranks = ORDER.map((v) => {
      const rank = q[`rank_${v}`];
      const cls = rank === null || rank === undefined ? 'bad' : rank <= 1 ? 'good' : rank <= 3 ? 'mid' : 'bad';
      return el('td', { class: 'n' }, el('span', { class: `rank ${cls}`, text: rank === null || rank === undefined ? '없음' : String(rank) }));
    });
    return el('tr', {}, el('td', { text: q.qid }), el('td', { class: 'wide', text: q.query, title: q.why || '' }), el('td', { text: KIND_KO[q.kind] ?? q.kind }), el('td', {}, prim), ...ranks);
  }));
}

// ---------------------------------------------------------------- 사진별 비교
function pick(key, scroll) {
  state.photo = key;
  drawPhotos();
  if (scroll) $('#photos').scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
}

function drawPhotos() {
  const samples = rows('samples');
  if (!state.photo && samples.length) state.photo = samples[0].key;
  $('#photo-thumbs').replaceChildren(...samples.map((s) => el('button', {
    type: 'button', class: 'thumb', 'aria-pressed': String(s.key === state.photo), 'aria-label': `${s.key} ${s.id}`, onclick: () => pick(s.key, false),
  }, el('img', { src: s.img_url, alt: '', loading: 'lazy' }), el('span', { text: s.key }))));
  const sel = $('#photo-sel');
  sel.replaceChildren(...samples.map((s) => new Option(`${s.key} · ${s.id}`, s.key)));
  sel.value = state.photo ?? '';
  const s = samples.find((x) => x.key === state.photo);
  $('#photo-view').replaceChildren(...(s ? [
    el('a', { href: s.img_url, target: '_blank', rel: 'noopener', title: '원본 크기로 열기' }, el('img', { src: s.img_url, alt: `${s.key} ${s.id}` })),
    el('figcaption', { text: `${s.key} · ${s.id} · ${s.width}×${s.height}${s.frames > 1 ? ' · MPO 첫 장' : ''}` }),
  ] : []));
  $('#photo-table tbody').replaceChildren(...ORDER.map((v) => find('photos', (r) => r.photo === state.photo && r.variant === v)).filter(Boolean).map((r) =>
    el('tr', {},
      el('td', { text: r.label }), el('td', { text: r.photo_type }), el('td', { class: 'wide', text: r.scene }),
      el('td', { class: 'wide' }, el('pre', { class: 'pre', text: r.text_in_photo || '(없음)' })),
      el('td', { class: 'n', text: F.score(r.claude_overall) }), el('td', { class: 'n', text: F.score(r.gemini_overall) }),
      el('td', { class: r.claude_errors ? 'err' : 'muted', text: r.claude_errors || '없음' }),
      el('td', { class: r.gemini_errors ? 'err' : 'muted', text: r.gemini_errors || '없음' }))));
}

function drawCost() {
  $('#cost-table tbody').replaceChildren(...ORDER.map((v) => find('variants', (r) => r.variant === v)).filter(Boolean).map((r) =>
    el('tr', {}, el('td', { text: r.label }), el('td', { class: 'n', text: F.int(r.in_tok) }), el('td', { class: 'n', text: F.int(r.out_tok) }),
      el('td', { class: 'n', text: F.usd(r.cost_per_photo_list_usd) }), el('td', { class: 'n', text: F.usd(r.cost_per_photo_usd) }),
      el('td', { class: 'n', text: F.sec(r.latency_median_s) }), el('td', { class: 'n', text: F.sec(r.latency_mean_s) }),
      el('td', { class: 'n', text: F.int(r.scene_chars) }), el('td', { class: 'n', text: F.int(r.text_chars) }))));
}

// ---------------------------------------------------------------- 시작
function drawCharts() {
  drawJudgeChart();
  drawRecall();
  drawTokenizer();
}

function drawAll() {
  drawTop();
  drawScenario();
  drawCharts();
  drawJudgeTable();
  drawAdjudications();
  drawKinds();
  drawQueries();
  drawPhotos();
  drawCost();
}

async function load() {
  const btn = $('#reload');
  btn.disabled = true;
  try {
    const res = await fetch('/api/data', { cache: 'no-store' });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    state.data = await res.json();
    drawAll();
  } catch (err) {
    const miss = $('#missing');
    miss.hidden = false;
    miss.textContent = `결과를 불러오지 못했다(${err.message}). 대시보드 서버가 켜져 있는지 확인한다.`;
    $('#stamp').textContent = '불러오지 못함';
  } finally {
    btn.disabled = false;
  }
}

$('#reload').addEventListener('click', load);
$('#judge-sel').addEventListener('change', (e) => { state.judge = e.target.value; drawJudgeTable(); });
$('#arm-sel').addEventListener('change', (e) => { state.arm = e.target.value; drawRecall(); });
$('#photo-sel').addEventListener('change', (e) => pick(e.target.value, false));
let resizeTimer = 0;
let lastWidth = window.innerWidth;
window.addEventListener('resize', () => {
  if (window.innerWidth === lastWidth || !state.data) return;
  lastWidth = window.innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(drawCharts, 150);
});
load();
