/* 갤러리에서 직접 찾기 — 10초짜리 장면을 시간 t 하나로 그린다: render(t).
   주소에 ?gpt=3.2 를 붙이면 그 시점에서 멈춘다 (확인 · 프레임 단위 녹화용). */
(() => {
  const GP = /*GP_MANIFEST*/null;
  const root = document.querySelector('[data-gp]');
  if (!root || !GP) return;
  const $ = s => root.querySelector(s);
  const slide = root.closest('.slide');

  const T = 10;                                  // 한 바퀴(초)
  const CELL = 88.5, PITCH = 90.5, HEAD = 44, GAP = 6;
  const LIST_TOP = 84, VH = 780 - 84 - 58;       // 사진 목록이 보이는 영역
  const KB_H = 306;

  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const prog = (t, a, b) => clamp((t - a) / (b - a));
  const lerp = (a, b, p) => a + (b - a) * p;
  const smooth = p => p * p * (3 - 2 * p);
  const outCubic = p => 1 - Math.pow(1 - p, 3);
  const accel = p => Math.pow(p, 1.6);

  /* ── 사진첩 만들기: 2026.11.12부터 과거로, 날짜별 묶음 ── */
  let seed = 20261112;
  const rnd = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296;
  const idx = Object.fromEntries(GP.names.map((n, i) => [n, i]));
  const CATS = [['food', 26], ['cafe', 12], ['city', 12], ['flower', 8], ['pet', 8], ['park', 7], ['night', 7], ['sea', 6], ['mountain', 6], ['sky', 5], ['museum', 3]]
    .filter(([c]) => GP.cats[c]);
  const catTotal = CATS.reduce((s, [, w]) => s + w, 0);
  const pickCat = () => { let r = rnd() * catTotal; for (const [c, w] of CATS) { if ((r -= w) < 0) return c; } return CATS[0][0]; };
  const LOCS = ['서울 서초구', '서울 성동구', '서울 마포구', '서울 종로구', '경기 수원시', '강원 강릉시', '부산 해운대구', '제주 서귀포시'];
  const DAY = 864e5, TODAY = Date.UTC(2026, 10, 12);
  const dkey = ms => new Date(ms).toISOString().slice(0, 10);
  // 같은 날 사진은 같은 분류에서 이어서 뽑는다 — 하루치가 한 자리에서 찍은 것처럼 보이게
  const fromCat = (cat, n) => {
    const [s, c] = GP.cats[cat], o = Math.floor(rnd() * c);
    return Array.from({ length: n }, (_, k) => rnd() < .15 ? Math.floor(rnd() * GP.names.length) : s + (o + k) % c);
  };

  // 손으로 짠 구간. 멈춰서 열어 보는 날(3.16)과, 찾던 날(1.13 — 전시 뒤 카페)이 들어 있다
  const FIXED = {
    '2024-03-23': { cat: 'food', n: 5 },
    '2024-03-16': { names: ['cafe_041', 'cafe_004', 'cafe_044', 'cafe_003', 'cafe_024', 'cafe_036'], loc: '서울 성동구' },
    '2024-03-09': { cat: 'city', n: 3 },
    '2024-03-02': { names: ['cafe_009', 'cafe_026', 'cafe_043', 'cafe_028'], loc: '서울 마포구' },
    '2024-02-17': { cat: 'food', n: 4 },
    '2024-02-03': { cat: 'pet', n: 6 },
    '2024-01-27': { cat: 'night', n: 2 },
    '2024-01-13': { names: ['museum_005', 'museum_001', 'museum_006', 'museum_002', 'museum_008', 'cafe_013', 'cafe_042', 'cafe_038', 'cafe_023'], loc: '서울 서초구' },
  };
  const HERO = 'cafe_004', STOP = '2024-03-16';
  const FIX_FROM = Date.UTC(2024, 0, 14), FIX_TO = Date.UTC(2024, 2, 26);

  const sections = [];
  const add = (ms, photos, loc) => sections.push({ ms, key: dkey(ms), photos, loc });
  add(TODAY, fromCat('food', 3), '');
  for (let ms = TODAY - DAY; ms > Date.UTC(2022, 1, 1); ms -= DAY * (1 + Math.floor(rnd() * rnd() * 7))) {
    const r = rnd();
    const n = r < .45 ? 1 + Math.floor(rnd() * 3) : r < .82 ? 4 + Math.floor(rnd() * 6) : 10 + Math.floor(rnd() * 14);
    const photos = fromCat(pickCat(), n), loc = rnd() < .35 ? LOCS[Math.floor(rnd() * LOCS.length)] : '';
    if (FIXED[dkey(ms)] || (ms >= FIX_FROM && ms <= FIX_TO)) continue;
    add(ms, photos, loc);
  }
  for (const [k, f] of Object.entries(FIXED)) {
    add(Date.parse(k + 'T00:00:00Z'), f.names ? f.names.map(n => idx[n] ?? 0) : fromCat(f.cat, f.n), f.loc || '');
  }
  sections.sort((a, b) => b.ms - a.ms);

  let total = 0;
  for (const s of sections) {
    const d = new Date(s.ms), ago = Math.round((TODAY - s.ms) / DAY);
    const md = (d.getUTCMonth() + 1) + '월 ' + d.getUTCDate() + '일';
    s.label = ago === 0 ? '오늘' : ago === 1 ? '어제' : d.getUTCFullYear() === 2026 ? md : d.getUTCFullYear() + '년 ' + md;
    s.month = d.getUTCFullYear() + '년 ' + (d.getUTCMonth() + 1) + '월';
    s.rows = Math.ceil(s.photos.length / 4);
    s.y = total;
    s.h = HEAD + s.rows * PITCH + GAP;
    total += s.h;
  }
  const MAXY = total - VH;
  const sec = k => sections.find(s => s.key === k);

  /* ── 화면 요소 ── */
  const list = $('[data-gp-list]');
  const pool = (cls, n, inner = '') => Array.from({ length: n }, () => {
    const e = document.createElement('div');
    e.className = cls; e.innerHTML = inner; list.appendChild(e);
    return e;
  });
  const cells = pool('gp__c', 40), heads = pool('gp__h', 8, '<b></b><span></span>');
  const spritePos = i => (i % GP.cols) / (GP.cols - 1) * 100 + '% ' + Math.floor(i / GP.cols) / Math.max(1, GP.rows - 1) * 100 + '%';
  const put = (e, x, y) => { e.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)'; };
  const hide = e => { e.style.transform = 'translate3d(-999px,0,0)'; };

  // 보이는 줄만 그린다. 맨 위에 걸린 묶음을 돌려준다
  const drawList = y => {
    let lo = 0, hi = sections.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (sections[m].y + sections[m].h > y) hi = m; else lo = m + 1; }
    let ci = 0, hn = 0;
    for (let i = lo; i < sections.length && sections[i].y < y + VH; i++) {
      const s = sections[i], top = s.y - y;
      if (top > -HEAD && hn < heads.length) {
        const h = heads[hn++];
        if (h._s !== s) { h.firstChild.textContent = s.label; h.lastChild.textContent = s.loc; h._s = s; }
        put(h, 0, top);
      }
      for (let r = Math.max(0, Math.floor((y - s.y - HEAD) / PITCH)); r < s.rows; r++) {
        const cy = top + HEAD + r * PITCH;
        if (cy > VH) break;
        for (let c = 0; c < 4 && ci < cells.length; c++) {
          const p = s.photos[r * 4 + c];
          if (p === undefined) break;
          const e = cells[ci++];
          if (e._p !== p) { e.style.backgroundPosition = spritePos(p); e._p = p; }
          put(e, c * PITCH, cy);
        }
      }
    }
    for (; ci < cells.length; ci++) hide(cells[ci]);
    for (; hn < heads.length; hn++) hide(heads[hn]);
    return sections[lo];
  };

  /* ── 키보드 ── */
  const ICON = {
    shift: '<svg viewBox="0 0 24 24"><path d="M12 4.500 4.500 12.500H9V19h6v-6.500h4.500L12 4.500Z"/></svg>',
    del: '<svg viewBox="0 0 24 24"><path d="M9 6h11v12H9l-5-6 5-6Zm3.500 3.500 4.500 5m0-5-4.500 5"/></svg>',
    go: '<svg viewBox="0 0 24 24"><circle cx="10.500" cy="10.500" r="6"/><path d="m15 15 5 5"/></svg>',
    smile: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.500"/><path d="M8.500 14a4.300 4.300 0 0 0 7 0M9 9.800v.2M15 9.800v.2"/></svg>',
    sticker: '<svg viewBox="0 0 24 24"><path d="M5 5h14v9l-5 5H5V5Zm9 14v-5h5"/></svg>',
    mic: '<svg viewBox="0 0 24 24"><path d="M12 4a2.500 2.500 0 0 0-2.500 2.500v5a2.500 2.500 0 0 0 5 0v-5A2.500 2.500 0 0 0 12 4Zm-5.500 7.500a5.500 5.500 0 0 0 11 0M12 17v3"/></svg>',
    tune: '<svg viewBox="0 0 24 24"><path d="M5 8h8m6 0h0M5 16h0m6 0h8"/><circle cx="16" cy="8" r="2.200"/><circle cx="8" cy="16" r="2.200"/></svg>',
    more: '<svg viewBox="0 0 24 24"><circle cx="5.500" cy="12" r=".9"/><circle cx="12" cy="12" r=".9"/><circle cx="18.500" cy="12" r=".9"/></svg>',
  };
  const key = (label, cls = '', flex = 1, face = label) =>
    '<div class="gp__k ' + cls + '" data-k="' + label + '" style="flex-grow:' + flex + '">' + face + '</div>';
  const row = (cls, html) => '<div class="gp__kr ' + cls + '">' + html + '</div>';
  const kb = $('[data-gp-kb]');
  kb.innerHTML =
    '<div class="gp__kt">' + ICON.smile + ICON.sticker + '<b>GIF</b>' + ICON.mic + ICON.tune + ICON.more + '</div>' +
    row('gp__kr--num', [...'1234567890'].map(c => key(c)).join('')) +
    row('', [...'ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ'].map(c => key(c)).join('')) +
    row('gp__kr--in', [...'ㅁㄴㅇㄹㅎㅗㅓㅏㅣ'].map(c => key(c)).join('')) +
    row('', key('shift', 'gp__k--fn', 1.5, ICON.shift) + [...'ㅋㅌㅊㅍㅠㅜㅡ'].map(c => key(c)).join('') + key('del', 'gp__k--fn', 1.5, ICON.del)) +
    row('', key('sym', 'gp__k--fn', 1.5, '!#1') + key('lang', 'gp__k--fn', 1.3, '한/영') + key(',') + key(' ', '', 4.4, '') + key('.') + key('go', 'gp__k--go', 1.6, ICON.go));
  const keys = {};
  kb.querySelectorAll('.gp__k').forEach(k => { keys[k.dataset.k] = k; });
  const keyPos = label => {
    const k = keys[label];
    return [k.offsetLeft + k.offsetWidth / 2, 780 - KB_H + k.offsetTop + k.offsetHeight / 2];
  };

  /* ── 타임라인 ── */
  // 두벌식으로 치는 순서 그대로 — [누르는 키, 그때 검색창에 보이는 글자]
  const TYPED = [['ㄷ', 'ㄷ'], ['ㅡ', '드'], ['ㄹ', '들'], ['ㄹ', '들ㄹ'], ['ㅏ', '들라'], ['ㅋ', '들랔'], ['ㅡ', '들라크'], ['ㄹ', '들라클'],
    ['ㅜ', '들라크루'], ['ㅇ', '들라크룽'], ['ㅏ', '들라크루아'], [' ', '들라크루아 '], ['ㅈ', '들라크루아 ㅈ'], ['ㅓ', '들라크루아 저'],
    ['ㄴ', '들라크루아 전'], ['ㅅ', '들라크루아 전ㅅ'], ['ㅣ', '들라크루아 전시']];
  const KEY_T0 = .30, KEY_DT = .068;
  const T_GO = 1.56;                    // 검색 키
  const T_KB = [1.60, 1.82];            // 키보드가 내려간다
  const T_NONE = [1.86, 2.02];          // '검색 결과가 없습니다'
  const T_BACK1 = 2.45, T_OUT = [2.50, 2.76];   // 뒤로 → 사진 탭
  const H0 = 3.90, H1 = 4.96, H2 = 5.12, H3 = 5.74, H4 = 5.82;   // 스크롤 핸들을 잡고 끄는 구간
  const S0 = 6.00, S1 = 6.44;           // 손으로 천천히 되짚는다
  const T_TAP = 6.62, T_OPEN = [6.66, 6.90];
  const T_BACK2 = 7.60, T_CLOSE = [7.64, 7.86];
  const T_LOOP = 9.72;                  // 처음 화면으로 넘어가기 시작

  const scr = [];                       // 스크롤 구간 {a,b,y0,y1,e}
  const ptr = [];                       // 손가락이 닿아 있는 구간
  let y = 0;
  const tap = (t, x, yy, d = .09) => ptr.push({ a: t, b: t + d, x0: x, y0: yy, x1: x, y1: yy });
  // 튕겨 넘기기 — d>0 이면 과거 쪽. 손가락이 끄는 동안은 1:1, 떼면 관성으로 미끄러진다
  const flick = (t, d) => {
    const drag = d * .36, a = t, b = t + .13, c = b + .46, fy = d > 0 ? 610 : 250;
    ptr.push({ a, b, x0: 190, y0: fy, x1: 197, y1: fy - drag, e: accel });
    scr.push({ a, b, y0: y, y1: y + drag, e: accel }, { a: b, b: c, y0: y + drag, y1: y + d, e: outCubic });
    y += d;
  };

  TYPED.forEach(([k], i) => tap(KEY_T0 + i * KEY_DT, ...keyPos(k), .045));
  tap(T_GO, ...keyPos('go'));
  tap(T_BACK1, 27, 66);

  flick(2.92, 430);
  flick(3.38, 660);
  const stop = sec(STOP);
  const yStop = stop.y - 150;                                   // 멈춰서 살펴보는 자리
  const yOver = sections.find(s => s.ms <= Date.UTC(2023, 4, 20)).y - 90;   // 너무 내려간 자리 (2023년 5월)
  const yBack = yStop + 260;
  scr.push({ a: H0 + .06, b: H1, y0: y, y1: yOver, e: smooth }, { a: H2, b: H3, y0: yOver, y1: yBack, e: smooth });
  ptr.push({ a: H0, b: H4, handle: true });
  scr.push({ a: S0, b: S1, y0: yBack, y1: yStop, e: smooth });
  ptr.push({ a: S0, b: S1, x0: 204, y0: 300, x1: 210, y1: 300 + (yBack - yStop), e: smooth });
  y = yStop;

  // 열어 보는 사진 — 3월 16일 묶음의 둘째 칸
  const heroAt = stop.photos.indexOf(idx[HERO]);
  const R0 = { x: (heroAt % 4) * PITCH, y: LIST_TOP + (stop.y - yStop) + HEAD + Math.floor(heroAt / 4) * PITCH, w: CELL, h: CELL };
  const heroH = 360 / GP.heroAspect;
  const R1 = { x: 0, y: 92 + (644 - 92 - heroH) / 2, w: 360, h: heroH };
  tap(T_TAP, R0.x + CELL / 2, R0.y + CELL / 2);
  tap(T_BACK2, 27, 66);

  flick(8.02, 560);
  flick(8.44, 780);      // 여기서 찾던 날(2024년 1월 13일)이 화면 위쪽을 스쳐 지나간다
  flick(8.94, -700);
  flick(9.36, -520);

  const scrollAt = t => {
    let v = 0;
    for (const s of scr) {
      if (t >= s.b) v = s.y1;
      else if (t >= s.a) return lerp(s.y0, s.y1, s.e(prog(t, s.a, s.b)));
      else break;
    }
    return v;
  };
  const handleY = yv => 8 + clamp(yv / MAXY) * (VH - 16 - 44);   // 목록 영역 안에서의 위치
  const pointerAt = t => {
    for (const s of ptr) {
      if (t < s.a - .02 || t > s.b + .07) continue;
      const p = (s.e || smooth)(prog(t, s.a, s.b));
      const a = t < s.a ? prog(t, s.a - .02, s.a) : t > s.b ? 1 - prog(t, s.b, s.b + .07) : 1;
      if (s.handle) return { x: 342, y: LIST_TOP + handleY(scrollAt(clamp(t, s.a, s.b))) + 22, a };
      return { x: lerp(s.x0, s.x1, p), y: lerp(s.y0, s.y1, p), a };
    }
    return null;
  };

  /* ── 그리기 ── */
  const el = {
    clock: $('[data-gp-clock]'), search: $('[data-gp-search]'), q: $('[data-gp-q]'), caret: $('[data-gp-caret]'),
    clear: $('[data-gp-clear]'), recent: $('[data-gp-recent]'), spin: $('[data-gp-spin]'), none: $('[data-gp-none]'),
    handle: $('[data-gp-handle]'), bubble: $('[data-gp-bubble]'), vbg: $('[data-gp-vbg]'), vimg: $('[data-gp-vimg]'),
    vui: $('[data-gp-vui]'), touch: $('[data-gp-touch]'),
  };
  // 크게 보기 아래의 작은 사진 줄
  const before = sec('2024-03-23').photos.slice(-3).concat(stop.photos.slice(0, heroAt));
  const after = stop.photos.slice(heroAt + 1).concat(sec('2024-03-09').photos);
  $('[data-gp-strip]').innerHTML = before.slice(-4).map(p => '<i style="background-position:' + spritePos(p) + '"></i>').join('') +
    '<i class="is-on"></i>' + after.slice(0, 4).map(p => '<i style="background-position:' + spritePos(p) + '"></i>').join('');

  let lastText = null, lastKey = null, lastStep = 0, lastClock = '', lastMonth = '';
  const render = t => {
    t = ((t % T) + T) % T;
    const looping = t >= T_LOOP;
    const ts = looping ? 0 : t;          // 끝에서는 검색 화면이 처음 상태로 다시 떠오른다

    // 검색 화면
    const n = ts < KEY_T0 ? 0 : Math.min(TYPED.length, Math.floor((ts - KEY_T0) / KEY_DT) + 1);
    const text = n ? TYPED[n - 1][1] : '';
    if (text !== lastText) {
      el.q.textContent = text || '검색';
      el.q.classList.toggle('gp__ph', !text);
      el.clear.style.opacity = text ? 1 : 0;
      el.recent.style.opacity = text ? 0 : 1;
      lastText = text;
    }
    const pressed = ts >= T_GO && ts < T_GO + .1 ? 'go'
      : n && n <= TYPED.length && ts < KEY_T0 + (n - 1) * KEY_DT + .05 ? TYPED[n - 1][0] : null;
    if (pressed !== lastKey) {
      if (lastKey !== null) keys[lastKey].classList.remove('is-on');
      if (pressed !== null) keys[pressed].classList.add('is-on');
      lastKey = pressed;
    }
    el.caret.style.opacity = ts < T_KB[0] ? 1 : 0;
    kb.style.transform = 'translate3d(0,' + smooth(prog(ts, T_KB[0], T_KB[1])) * KB_H + 'px,0)';
    el.spin.style.opacity = ts > T_KB[0] + .02 && ts < T_NONE[0] ? 1 : 0;
    el.spin.style.transform = 'rotate(' + ts * 720 + 'deg)';
    el.none.style.opacity = smooth(prog(ts, T_NONE[0], T_NONE[1]));
    const out = smooth(prog(t, T_OUT[0], T_OUT[1]));
    const sOp = looping ? smooth(prog(t, T_LOOP, T - .04)) : 1 - out;
    el.search.style.opacity = sOp;
    el.search.style.visibility = sOp > 0 ? 'visible' : 'hidden';
    el.search.style.transform = looping ? 'none' : 'translate3d(' + out * 36 + 'px,0,0)';

    // 상태바 시계 — 찾는 동안 시간이 흐른다
    const clock = '9:' + (41 + (looping && sOp > .5 ? 0 : Math.round(17 * prog(t, T_OUT[1], 9.6))));
    if (clock !== lastClock) { el.clock.textContent = clock; lastClock = clock; }

    // 사진 목록
    const ys = scrollAt(t);
    const first = drawList(ys);
    const dragging = t >= H0 && t <= H4;
    const hOp = Math.max(Math.min(prog(t, 2.95, 3.10), 1 - prog(t, 6.55, 6.75)), prog(t, 8.05, 8.20));
    el.handle.style.opacity = hOp;
    el.handle.classList.toggle('is-on', dragging);
    put(el.handle, 0, LIST_TOP + handleY(ys));
    el.bubble.style.opacity = clamp(Math.min((t - H0) / .1, (H4 + .16 - t) / .16));
    put(el.bubble, 0, LIST_TOP + handleY(ys) + 2);
    if (first.month !== lastMonth) { el.bubble.textContent = first.month; lastMonth = first.month; }

    // 사진 크게 보기
    const vp = t < T_CLOSE[0] ? outCubic(prog(t, T_OPEN[0], T_OPEN[1])) : 1 - outCubic(prog(t, T_CLOSE[0], T_CLOSE[1]));
    el.vbg.style.opacity = vp;
    el.vui.style.opacity = clamp(vp * 1.7 - .7);
    el.vimg.style.opacity = vp > 0 ? 1 : 0;
    if (vp > 0) {
      el.vimg.style.left = lerp(R0.x, R1.x, vp) + 'px';
      el.vimg.style.top = lerp(R0.y, R1.y, vp) + 'px';
      el.vimg.style.width = lerp(R0.w, R1.w, vp) + 'px';
      el.vimg.style.height = lerp(R0.h, R1.h, vp) + 'px';
    }

    // 손가락
    const p = pointerAt(t);
    el.touch.style.opacity = p ? p.a : 0;
    if (p) el.touch.style.transform = 'translate3d(' + p.x + 'px,' + p.y + 'px,0) scale(' + (.86 + .14 * p.a) + ')';

    // 바깥 설명(슬라이드의 단계 목록)이 따라오도록 지금 단계를 알린다
    const step = t < T_OUT[0] ? 1 : t < T_TAP - .12 ? 2 : t < T_CLOSE[1] + .04 ? 3 : 4;
    if (step !== lastStep) {
      root.dataset.gpStep = step;
      if (slide) slide.dataset.gpStep = step;
      lastStep = step;
    }
  };
  window.__gpRender = render;

  /* ── 재생 ── */
  const STILL = 4.6;                    // 멈춰 있을 때 보여 줄 장면 (인쇄 · 전체 장표 썸네일)
  const frozen = new URLSearchParams(location.search).get('gpt');
  if (frozen !== null) { render(parseFloat(frozen) || 0); return; }
  let t0 = 0, playing = false;
  const frame = now => {
    const active = !slide || slide.classList.contains('is-active');
    if (active) {
      if (!playing) t0 = now + (slide ? 700 : 0);   // 슬라이드가 다 뜬 뒤에 시작
      render(Math.max(0, now - t0) / 1000);
    } else if (playing) render(STILL);
    playing = active;
    requestAnimationFrame(frame);
  };
  render(STILL);
  requestAnimationFrame(frame);
})();
