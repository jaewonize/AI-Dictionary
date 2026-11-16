// 맨 아래 '내 실제 데이터' 절. 서버가 var/bali/ 결과를 그 자리에서 읽어 주는 /api/bali 를 그린다.
// 개인 사진이라 데이터는 저장소에 넣지 않았고, 이 파일에도 지명·날짜 같은 내용은 적지 않는다(모두 데이터에서 온다).
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);

  function h(tag, props = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props ?? {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : String(v));
    }
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      n.append(c instanceof Node ? c : String(c));
    }
    return n;
  }

  const WD = ['일', '월', '화', '수', '목', '금', '토'];
  const dayOf = (t) => (t || '').slice(0, 10);
  function dayLabel(d) {
    if (!d) return '날짜 없음';
    const [y, m, dd] = d.split('-').map(Number);
    return `${m}/${dd}(${WD[new Date(Date.UTC(y, m - 1, dd)).getUTCDay()]})`;
  }
  const hm = (t) => (t || '').slice(11, 16);
  const timeLabel = (t) => (t ? `${dayLabel(dayOf(t))} ${hm(t)}` : '시각 없음');
  const placeLabel = (p) => (p.area_ko ? (p.near_ko ? `${p.area_ko} · ${p.near_ko} 부근` : p.area_ko) : '찍은 곳 없음');
  const usd = (v, d = 2) => (v === null || v === undefined || Number.isNaN(+v) ? '—' : `$${Number(v).toFixed(d)}`);
  const int = (v) => (v === null || v === undefined || Number.isNaN(+v) ? '—' : Math.round(v).toLocaleString('ko-KR'));
  const secs = (ms) => (ms === null || ms === undefined ? '—' : `${(ms / 1000).toFixed(1)}초`);
  const avg = (xs) => { const v = xs.map(Number).filter(Number.isFinite); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const byTime = (a, b) => (a.time || '').localeCompare(b.time || '') || a.id.localeCompare(b.id);
  const srcOf = (p) => p.badge || p.source || '출처 없음';
  function countBy(xs, f) {
    const m = new Map();
    for (const x of xs) { const k = f(x); m.set(k, (m.get(k) || 0) + 1); }
    return m;
  }
  const TYPE_KO = { scene: '장면 사진', document: '문서 사진', screenshot: '화면 캡처' };
  const BASIS_KO = { taken: '사진 파일에 적힌 찍은 시각', filename: '파일 이름의 시각', saved: '저장한 시각' };
  const PURPOSE_KO = {
    describe: '사진 설명', places: '지명 붙이기', link: '카카오톡 사진 연계', link_match: '그림으로 짝 찾기',
    loop: '에이전트 루프', pick_candidates: '설명에서 후보 고르기', judge_photos: '사진 다시 보기',
  };
  const KIND_KO = {
    sequence: '순서', meal_time: '시각·식사', sign_text: '간판·글자', animal: '동물',
    object_scene: '장면·사물', shopping: '기념품·쇼핑', menu_text: '메뉴판',
    dinner_food: '저녁 식사', object_drink: '음료·소품', airport_return: '귀국·공항',
  };
  const VIEW_SAMPLE = 'sample';
  const VIEW_RANDOM = 'random';
  const VIEW_ALL = 'all';
  const RANDOM_N = 12;

  const S = { data: null, byId: new Map(), view: VIEW_SAMPLE, day: '', group: '', source: '', type: '', q: '', list: [], sel: null, ctl: {} };

  // ---------- 위 요약 ----------
  function card(title, maker, stats) {
    return h('div', { class: 'card' }, h('h3', { text: title }), maker ? h('div', { class: 'maker', text: maker }) : null,
      stats.map(([k, v]) => h('div', { class: 'stat' }, h('span', { text: k }), h('b', { text: v }))));
  }

  function drawCards() {
    const d = S.data;
    const ph = d.photos;
    const ok = ph.filter((p) => p.desc && !p.desc.error).length;
    const errs = ph.filter((p) => p.desc?.error).length;
    const srcs = [...countBy(ph, srcOf)].map(([k, n]) => `${k} ${n}장`).join(' · ');
    const areas = new Set(d.groups.map((g) => g.area_ko).filter(Boolean));
    const visits = new Set(ph.map((p) => p.visit).filter(Boolean));
    const idx = d.index;
    const ev = d.eval;
    const hs = d.hindsight;
    const hsEv = hs?.eval;
    const hsTotalUsd = Number(hs?.eval?.llm_usage?.total?.cost_usd ?? hs?.retain?.bank_total_llm_usage?.cost_usd ?? 0);
    const hsTotalCalls = Number(hs?.eval?.llm_usage?.total?.calls ?? hs?.retain?.bank_total_llm_usage?.calls ?? 0);
    const ak = d.answer_key;
    const evalMaker = ev ? `${ev.model ?? 'Claude'} · 시험 비용 ${usd(ev.cost_usd)}` : (ak ? '정답표 초안 검토 대기' : '시험 전');
    const evalStat = ev ? ['without hindsight', `${ev.passed} / ${ev.total}개`] : ['질의 초안', ak ? `${ak.length}개` : '없음'];
    const cardsList = [
      card('모은 사진', srcs, [['사진', `${ph.length}장`], ['건너뛴 동영상', `${d.catalog.videos}개`]]),
      card('Claude 사진 설명', `${d.describe.model} · effort ${d.describe.effort} · 지시문 ${d.describe.prompt_version}`,
        [['설명한 사진', `${ok} / ${ph.length}장`], ['실패', `${errs}장`]]),
      card('찍은 곳 묶음과 지명', `같은 방문 ${visits.size}번`, [['같은 자리 묶음', `${d.groups.length}개`], ['붙인 지역 이름', `${areas.size}곳`]]),
      card('검색 색인 · 기본 시험', evalMaker, [
        ['색인 줄 수', idx ? `${idx.rows}줄` : '미생성'],
        evalStat,
      ]),
    ];
    if (hsEv) {
      cardsList.push(
        card('질의 시험 (with hindsight)', `은행 ${hsEv.bank} · 시험 비용 ${usd(hsEv.total_cost_usd)}`, [
          ['reflect 통과', `${hsEv.passed} / ${hsEv.total}개`],
          ['recall 상위10 적중', `${hsEv.recall_summary?.top10_hit_queries ?? 0} / ${hsEv.total}개`],
        ]),
      );
    }
    cardsList.push(
      card('Claude 총 비용', hsTotalCalls ? `장부 ${int(d.calls.count)}번 + hindsight ${int(hsTotalCalls)}번` : `${d.dir}/claude_calls.jsonl 장부 합계`, [
        ['전체 합계', usd(d.calls.total_usd + hsTotalUsd)],
        ['장부 / hindsight', `${usd(d.calls.total_usd)} / ${usd(hsTotalUsd)}`],
      ]),
    );
    $('bali-cards').replaceChildren(...cardsList);
  }

  // ---------- 분석 과정 ----------
  function fact(k, v) { return h('span', {}, `${k} `, h('b', { text: v })); }
  function preBlock(title, text) {
    if (!text) return null;
    return h('details', { class: 'det' }, h('summary', { text: title }), h('pre', { class: 'pre', text }));
  }

  function drawSteps() {
    const d = S.data;
    const ph = d.photos;
    const srcs = [...countBy(ph, srcOf)].map(([k, n]) => `${k} ${n}장`).join(', ');
    const bases = [...countBy(ph, (p) => p.time_basis || '없음')].map(([k, n]) => `${BASIS_KO[k] ?? k} ${n}장`).join(', ');
    const descs = ph.map((p) => p.desc).filter(Boolean);
    const dCalls = d.calls.purposes.find((x) => x.purpose === 'describe');
    const pCalls = d.calls.purposes.find((x) => x.purpose === 'places');
    const longEdge = Math.max(0, ...ph.map((p) => Math.max(p.model_width || 0, p.model_height || 0)));
    const visits = new Set(ph.map((p) => p.visit).filter(Boolean));
    const idx = d.index;
    const ev = d.eval;
    const ak = d.answer_key;

    const steps = [
      h('li', { class: 'bstep' },
        h('h4', { text: '① 모으기 (pipeline.collect)' }),
        h('div', { class: 'who', text: '노트북 안에서 처리했고 Claude는 쓰지 않았다' }),
        h('p', { text: `사진 ${ph.length}장(${srcs})을 목록(${d.dir}/catalog.jsonl)에 올렸다. 동영상 ${d.catalog.videos}개는 목록에만 적고 분석하지 않았다.` }),
        h('p', { text: `찍은 시각은 ${bases}에서 읽었다. 카카오톡 사진은 파일 이름이 받은 시각이라 찍은 시각으로 쓰지 않고 받은 시각으로 따로 남겼다.` }),
        h('p', { text: `사진마다 회전만 반영해 숨은 정보(EXIF·XMP)를 지운 사본 두 벌을 만들었다. Claude용은 긴 변 ${longEdge}픽셀 이하, 화면용은 이 페이지에서 보는 사본이다. 원본 폴더는 고치지 않았다.` }),
      ),
      h('li', { class: 'bstep' },
        h('h4', { text: '② 사진 설명 (pipeline.describe)' }),
        h('div', { class: 'who', text: `Claude ${d.describe.model} · effort ${d.describe.effort} · 지시문 ${d.describe.prompt_version} · 사진 한 장에 한 번씩` }),
        h('p', { text: `보낸 것은 숨은 정보를 지운 Claude용 사본 한 장, 지시문(system), 사용자 글 "${d.describe.user_prompt ?? '?'}" 한 줄뿐이다. 찍은 시각과 찍은 곳은 보내지 않았다.` }),
        h('p', { text: '답은 정해진 형식으로 받았다: place(장소 종류 한두 낱말), scene(장면 설명 3~6문장), text_in_photo(사진 속 글자를 원문 그대로), photo_type(장면·문서·화면 캡처).' }),
        h('div', { class: 'facts' },
          fact('설명', `${descs.filter((x) => !x.error).length}/${ph.length}장`),
          fact('평균 입력', `${int(avg(descs.map((x) => x.input_tokens)))}토큰`),
          fact('평균 출력', `${int(avg(descs.map((x) => x.output_tokens)))}토큰`),
          fact('사진당', usd(avg(descs.map((x) => x.cost_usd)), 4)),
          fact('응답 중앙값', secs(dCalls?.ms_median)),
          fact('합계', usd(dCalls?.cost_usd))),
        preBlock(`지시문 전문 (app/prompts/${d.describe.prompt_version}.md)`, d.describe.system),
        preBlock(`실행 기록 (${d.dir}/describe.log)`, d.describe.log),
      ),
      d.places ? h('li', { class: 'bstep' },
        h('h4', { text: '③ 찍은 곳에 지명 붙이기 (pipeline.places)' }),
        h('div', { class: 'who', text: `노트북 안에서 묶고, Claude ${d.places.model} 한 번(effort ${pCalls?.effort ?? '?'} · 지시문 ${d.places.prompt_version})으로 이름을 받았다` }),
        h('p', { text: `찍은 곳이 150m 안으로 이어진 사진을 같은 자리 묶음으로(${d.groups.length}개), 같은 날 같은 자리에서 90분 안쪽 간격으로 이어 찍은 사진을 같은 방문으로(${visits.size}번) 묶었다(pipeline/places.py 기준).` }),
        h('p', { text: '묶음 가운데점 근처의 마을·행정 구역·명소 이름을 공개 지명 데이터(GeoNames)에서 거리와 함께 찾고, 묶음마다 이 목록만 Claude에 한 번에 보내 사람이 흔히 부르는 지명(한국어·영어)을 받았다. 좌표와 사진은 보내지 않았다.' }),
        h('div', { class: 'facts' },
          fact('묶음', `${d.groups.length}개`),
          fact('입력', `${int(pCalls?.input_tokens)}토큰`),
          fact('출력', `${int(pCalls?.output_tokens)}토큰`),
          fact('걸린 시간', secs(pCalls?.ms_median)),
          fact('비용', usd(pCalls?.cost_usd, 4))),
        preBlock(`지시문 전문 (app/prompts/${d.places.prompt_version}.md)`, d.places.system),
        preBlock('Claude에 보낸 글 전문 (places.json 값으로 다시 만든 것)', d.places.request),
      ) : null,
      idx ? h('li', { class: 'bstep' },
        h('h4', { text: '④ 색인 만들기 (pipeline.build_index)' }),
        h('div', { class: 'who', text: '노트북 안에서 합쳤고 Claude는 쓰지 않았다' }),
        h('p', { text: `카탈로그(catalog.jsonl), 사진 설명(descriptions.jsonl), 묶음 지명(places.json)을 id 기준으로 합쳐 검색 색인(${d.dir}/index.jsonl)을 만들었다.` }),
        h('div', { class: 'facts' },
          fact('색인 줄 수', `${idx.rows}줄 (사진 ${idx.photos}장 · 문서 ${idx.documents}건)`),
          fact('시각 빠진 사진', `${idx.missing_time}장`),
          fact('지명 빠진 사진', `${idx.missing_place}장`),
          fact('가까운 명소 포함', `${idx.with_near}장`),
          fact('글자 있는 사진', `${idx.with_text}장`)),
        preBlock(`실행 기록 (${d.dir}/logs/build_index.log)`, idx.log),
      ) : null,
      ev ? h('li', { class: 'bstep' },
        h('h4', { text: '⑤ 에이전트 루프 질의 시험 without hindsight (eval.run)' }),
        h('div', { class: 'who', text: `Claude ${ev.model ?? 'claude-opus-5-5'} · plan_query / find_candidates / judge_photos / show_answer` }),
        h('p', { text: `정답표(${d.dir}/answer_key.json)의 질의 ${ev.total}개를 hindsight 없이 기본 색인 경로(에이전트 루프)로 실행해 채점했다. 순서 질의는 plan_query의 relation과 find_candidates의 after·before로 풀었다.` }),
        h('div', { class: 'facts' },
          fact('맞음', `${ev.passed}/${ev.total}개`),
          fact('틀림', `${ev.failed}개`),
          fact('필수만 정확 일치', `${ev.exact_match}개`),
          fact('호출', `${int(ev.calls)}번`),
          fact('시험 비용', usd(ev.cost_usd)),
          fact('걸린 시간', `${Number(ev.elapsed_s || 0).toFixed(1)}초`)),
      ) : h('li', { class: 'bstep bstep-todo' },
        h('h4', { text: '⑤ 에이전트 루프 질의 시험 without hindsight (채점 대기 중)' }),
        h('p', { text: ak
          ? `사진·시각·지명만 보고 만든 정답표 초안(${d.dir}/answer_key.json, ${ak.length}개 질의)이 준비되었다. 사용자 확인과 실행 승인 뒤 eval.run으로 채점하면 맞음·틀림과 고른 사진이 아래 표에 채워진다.`
          : '정답표와 에이전트 질의 시험 결과가 아직 없다.' }),
      ),
      d.hindsight?.retain ? h('li', { class: 'bstep' },
        h('h4', { text: '⑥ hindsight 은행 적재 (eval.hindsight retain)' }),
        h('div', { class: 'who', text: `은행 ${d.hindsight.retain.bank} · LLM ${d.hindsight.retain.settings?.llm_provider}/${d.hindsight.retain.settings?.llm_model} · 임베딩 ${d.hindsight.retain.settings?.embeddings_model} · 검색 ${d.hindsight.retain.settings?.text_search_extension} · 관찰=${d.hindsight.retain.settings?.observations ? '켜짐' : '꺼짐'}` }),
        h('p', { text: '사진마다 기억 하나씩(장면 설명 + 사진 속 글자 + 묶음 지명 + 찍은 시각 +08:00 + 출처)을 넣었다. 먼저 10장 시범 적재와 recall 점검을 거친 뒤 나머지를 적재했으며, 좌표와 받은 시각은 보내지 않았다.' }),
        h('div', { class: 'facts' },
          fact('넣은 기억', `${d.hindsight.retain.inserted_memories}/${d.hindsight.retain.total_index_photos}장`),
          fact('실패', `${d.hindsight.retain.failed_count}건`),
          fact('추출 사실(nodes)', `${int(d.hindsight.retain.stats?.total_nodes)}개`),
          fact('LLM 호출', `${int(d.hindsight.retain.llm_usage?.calls)}번`),
          fact('적재 비용', usd(d.hindsight.retain.llm_usage?.cost_usd))),
        preBlock(`10장 시범 적재 기록 (${d.dir}/logs/hs_pilot.log)`, d.hindsight.logs?.pilot),
        preBlock(`전체 적재 기록 (${d.dir}/logs/hs_retain.log)`, d.hindsight.logs?.retain),
      ) : null,
      d.hindsight?.eval ? h('li', { class: 'bstep' },
        h('h4', { text: '⑦ hindsight 질의 시험 with hindsight (eval.hindsight eval)' }),
        h('div', { class: 'who', text: `은행 ${d.hindsight.eval.bank} · recall(로컬 임베딩+pgroonga+리랭커, LLM 0회) + reflect(Claude ${d.hindsight.eval.model})` }),
        h('p', { text: `같은 정답표(${d.dir}/answer_key.json)의 질의 ${d.hindsight.eval.total}개로 recall 상위 10장과 reflect 추론 답을 돌려 첫 정답 순위와 맞음·틀림을 without hindsight 경로와 비교했다.` }),
        h('div', { class: 'facts' },
          fact('reflect 맞음', `${d.hindsight.eval.passed}/${d.hindsight.eval.total}개`),
          fact('recall 상위10 적중', `${d.hindsight.eval.recall_summary?.top10_hit_queries ?? 0}/${d.hindsight.eval.total}개`),
          fact('recall 1위', `${d.hindsight.eval.recall_summary?.rank1_queries ?? 0}개`),
          fact('reflect 호출', `${int(d.hindsight.eval.total_calls)}번`),
          fact('reflect 비용', usd(d.hindsight.eval.total_cost_usd))),
        preBlock(`질의 시험 기록 (${d.dir}/logs/hs_eval.log)`, d.hindsight.logs?.eval),
      ) : null,
    ];
    $('bali-steps').replaceChildren(...steps.filter(Boolean));
  }

  // ---------- 날짜별 ----------
  function drawDays() {
    const ph = S.data.photos;
    const srcs = [...countBy(ph, srcOf).keys()];
    const days = [...countBy(ph, (p) => dayOf(p.time))].sort((a, b) => a[0].localeCompare(b[0]));
    const head = h('tr', {}, h('th', { text: '날짜' }), srcs.map((s) => h('th', { class: 'n', text: s })), h('th', { class: 'n', text: '합계' }),
      h('th', { text: '그날 찍은 곳(사진 수)' }), h('th', { class: 'n', text: '설명 비용' }), h('th', { text: '' }));
    const rows = days.map(([day]) => {
      const xs = ph.filter((p) => dayOf(p.time) === day);
      const areas = [...countBy(xs, (p) => p.area_ko || '찍은 곳 없음')].sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k}(${n})`).join(', ');
      return h('tr', {}, h('td', { class: 'n', text: dayLabel(day) }),
        srcs.map((s) => h('td', { class: 'n', text: String(xs.filter((p) => srcOf(p) === s).length) })),
        h('td', { class: 'n', text: String(xs.length) }), h('td', { text: areas }),
        h('td', { class: 'n', text: usd(xs.reduce((a, p) => a + (Number(p.desc?.cost_usd) || 0), 0)) }),
        h('td', {}, h('button', { type: 'button', class: 'btn btn-s', onclick: () => focusBrowse({ day, group: '' }) }, '이날 사진 보기')));
    });
    $('bali-days').replaceChildren(h('thead', {}, head), h('tbody', {}, rows));
  }

  // ---------- 묶음 표 ----------
  function drawGroups() {
    const head = h('tr', {}, ['묶음', '사진', '날짜', '반경', '붙인 지역 이름', '가까운 명소', 'Claude가 적은 근거', 'Claude가 받은 근처 지명', ''].map((t, i) =>
      h('th', { class: i === 1 || i === 3 ? 'n' : null, text: t })));
    const rows = S.data.groups.map((g) => h('tr', {},
      h('td', { text: g.group }),
      h('td', { class: 'n', text: String(g.photos) }),
      h('td', { class: 'n', text: g.days.map(dayLabel).join(', ') }),
      h('td', { class: 'n', text: `${int(g.spread_m)}m` }),
      h('td', {}, h('b', { text: g.area_ko || '—' }), g.area_en ? h('span', { class: 'muted', text: ` ${g.area_en}` }) : null),
      h('td', {}, g.near_ko || '—', g.near_en ? h('span', { class: 'muted', text: ` ${g.near_en}` }) : null),
      h('td', { class: 'wide', text: g.note || '—' }),
      h('td', { class: 'wide' }, h('details', { class: 'det' }, h('summary', { text: '목록' }),
        h('div', { class: 'small' }, h('div', { text: `마을·동네: ${g.nearby.towns}` }), h('div', { text: `행정 구역: ${g.nearby.districts}` }),
          h('div', { text: `명소: ${g.nearby.spots}` })))),
      h('td', {}, h('button', { type: 'button', class: 'btn btn-s', onclick: () => focusBrowse({ group: g.group, day: '' }) }, '사진 보기')),
    ));
    $('bali-groups').replaceChildren(h('thead', {}, head), h('tbody', {}, rows));
  }

  // ---------- 비용 표 (without / with hindsight) ----------
  const PREP_PURPOSES = new Set(['describe', 'places', 'link', 'link_match']);

  function sumPurposes(ps) {
    return {
      calls: ps.reduce((a, p) => a + (Number(p.calls) || 0), 0),
      inTok: ps.reduce((a, p) => a + (Number(p.input_tokens) || 0), 0),
      outTok: ps.reduce((a, p) => a + (Number(p.output_tokens) || 0), 0),
      crTok: ps.reduce((a, p) => a + (Number(p.cache_read_input_tokens) || 0), 0),
      usd: ps.reduce((a, p) => a + (Number(p.cost_usd) || 0), 0),
    };
  }

  function subtotalRow(label, s, bold = false) {
    return h('tr', { class: 'total' },
      h('td', {}, bold ? h('b', { text: label }) : label),
      h('td'), h('td'),
      h('td', { class: 'n', text: int(s.calls) }),
      h('td', { class: 'n', text: int(s.inTok) }),
      h('td', { class: 'n', text: int(s.outTok) }),
      h('td', { class: 'n', text: int(s.crTok) }),
      h('td', { class: 'n' }, bold ? h('b', { text: usd(s.usd) }) : usd(s.usd)),
      h('td', { class: 'n', text: s.calls ? usd(s.usd / s.calls, 4) : '—' }),
      h('td'));
  }

  function drawCost() {
    const c = S.data.calls;
    const ev = S.data.eval;
    const hs = S.data.hindsight;
    const prepPs = c.purposes.filter((p) => PREP_PURPOSES.has(p.purpose));
    const evalPs = c.purposes.filter((p) => !PREP_PURPOSES.has(p.purpose));
    const prepSum = sumPurposes(prepPs);
    const evalLedgerSum = sumPurposes(evalPs);

    // 1) without hindsight 비용 표
    const head = h('tr', {}, ['구분 · 단계', '모델', 'effort', '호출', '입력 토큰', '출력 토큰', '캐시 읽기 토큰', '합계', '호출당', '응답 중앙값'].map((t, i) =>
      h('th', { class: i >= 3 ? 'n' : null, text: t })));
    const pRow = (p, prefix = '') => h('tr', {},
      h('td', { text: `${prefix}${PURPOSE_KO[p.purpose] ?? p.purpose}` }),
      h('td', { text: p.model }),
      h('td', { text: p.effort }),
      h('td', { class: 'n', text: int(p.calls) }),
      h('td', { class: 'n', text: int(p.input_tokens) }),
      h('td', { class: 'n', text: int(p.output_tokens) }),
      h('td', { class: 'n', text: int(p.cache_read_input_tokens) }),
      h('td', { class: 'n', text: usd(p.cost_usd) }),
      h('td', { class: 'n', text: p.calls ? usd(p.cost_usd / p.calls, 4) : '—' }),
      h('td', { class: 'n', text: secs(p.ms_median) }));

    const woRows = [
      ...prepPs.map((p) => pRow(p, '[사전 준비] ')),
      subtotalRow('소계 ① 공통 사전 준비 (1회)', prepSum),
      ...evalPs.map((p) => pRow(p, '[질의 시험] ')),
      subtotalRow('소계 ② 질의 시험 장부 누적 (without hindsight)', evalLedgerSum),
    ];
    if (ev && Math.abs((ev.cost_usd || 0) - evalLedgerSum.usd) > 0.01) {
      woRows.push(h('tr', {},
        h('td', { class: 'muted small', text: `참고: 현재 15문항 채점표(eval_results.json) 1세트 기준 질의 시험 비용` }),
        h('td', { class: 'muted small', text: ev.model || 'claude-opus-5-5' }),
        h('td', { class: 'muted small', text: '루프+도구' }),
        h('td', { class: 'n muted small', text: int(ev.calls) }),
        h('td', { class: 'n muted small', text: '—' }),
        h('td', { class: 'n muted small', text: '—' }),
        h('td', { class: 'n muted small', text: '—' }),
        h('td', { class: 'n muted small', text: usd(ev.cost_usd) }),
        h('td', { class: 'n muted small', text: ev.calls ? usd(ev.cost_usd / ev.calls, 4) : '—' }),
        h('td', { class: 'n muted small', text: '—' })));
    }
    woRows.push(subtotalRow('합계 (without hindsight 장부 전체)', {
      calls: prepSum.calls + evalLedgerSum.calls,
      inTok: prepSum.inTok + evalLedgerSum.inTok,
      outTok: prepSum.outTok + evalLedgerSum.outTok,
      crTok: prepSum.crTok + evalLedgerSum.crTok,
      usd: c.total_usd,
    }, true));
    $('bali-cost').replaceChildren(h('thead', {}, head), h('tbody', {}, woRows));

    // 2) with hindsight 비용 비교 카드 및 표
    const hsSec = $('bali-cost-hs-sec');
    const hsCardsBox = $('bali-cost-hs-cards');
    const hsTbl = $('bali-cost-hs');
    if (!hsSec || !hsCardsBox || !hsTbl) return;
    if (!hs || (!hs.retain && !hs.eval)) {
      hsSec.hidden = true;
      return;
    }
    hsSec.hidden = false;

    const uRet = hs.eval?.llm_usage?.retain ?? hs.retain?.llm_usage ?? { calls: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, cost_usd: 0, model: 'claude-opus-5-5' };
    const uRef = hs.eval?.llm_usage?.reflect ?? null;
    const uTot = hs.eval?.llm_usage?.total ?? hs.retain?.bank_total_llm_usage ?? uRet;

    const retUncached = Math.max(0, (uRet.input_tokens || 0) - (uRet.cached_tokens || 0));
    const refCalls = uRef ? (uRef.calls || 0) : 0;
    const refUsd = uRef ? (uRef.cost_usd || 0) : 0;
    const refUncached = uRef ? Math.max(0, (uRef.input_tokens || 0) - (uRef.cached_tokens || 0)) : 0;
    const refOut = uRef ? (uRef.output_tokens || 0) : 0;
    const refCached = uRef ? (uRef.cached_tokens || 0) : 0;

    const wo15Calls = ev ? ev.calls : evalLedgerSum.calls;
    const wo15Usd = ev ? ev.cost_usd : evalLedgerSum.usd;
    const qCount = hs.eval?.total || ev?.total || 15;
    const grandSpent = c.total_usd + (uTot.cost_usd || 0);

    hsCardsBox.replaceChildren(
      card('15문항 질의 시점 비용 비교', '질문할 때마다 발생하는 Claude 호출', [
        ['without hindsight', `${int(wo15Calls)}회 · ${usd(wo15Usd)}`],
        ['with hindsight (reflect)', hs.eval ? `${int(refCalls)}회 · ${usd(refUsd)}` : '채점 대기 중'],
      ]),
      card('질의 1건당 평균 비용 · recall 비용', 'recall 단독 검색은 로컬 처리($0)', [
        ['without / reflect (문항당)', hs.eval ? `${usd(wo15Usd / qCount, 3)} → ${usd(refUsd / qCount, 3)}` : `${usd(wo15Usd / qCount, 3)} → 측정 중`],
        ['hindsight recall 단독', '0회 · $0.000'],
      ]),
      card('초기 1회 준비 비용 비교', '사진 307장 설명·지명 + 은행 적재', [
        ['without hindsight (설명+지명)', `${int(prepSum.calls)}회 · ${usd(prepSum.usd)}`],
        ['with hindsight (+은행 적재)', `${int(prepSum.calls + (uRet.calls || 0))}회 · ${usd(prepSum.usd + (uRet.cost_usd || 0))}`],
      ]),
      card('실제 총 누적 지출 (한도 $100)', '장부(claude_calls.jsonl) + hindsight 은행', [
        ['총 실지출 합계', usd(grandSpent)],
        ['남은 한도', usd(Math.max(0, 100 - grandSpent))],
      ]),
    );

    const hsHead = h('tr', {}, ['구분 · 단계', '모델 · 엔진', '비고', '호출', '입력 토큰(비캐시)', '출력 토큰', '캐시 읽기 토큰', '합계', '호출당', '전후 차이'].map((t, i) =>
      h('th', { class: i >= 3 && i <= 8 ? 'n' : null, text: t })));

    const hsRows = [
      ...prepPs.map((p) => h('tr', {},
        h('td', { text: `[사전 준비 · 재사용] ${PURPOSE_KO[p.purpose] ?? p.purpose}` }),
        h('td', { text: p.model }),
        h('td', { class: 'muted small', text: '기존 결과 그대로 재사용(추가 지출 $0)' }),
        h('td', { class: 'n', text: int(p.calls) }),
        h('td', { class: 'n', text: int(p.input_tokens) }),
        h('td', { class: 'n', text: int(p.output_tokens) }),
        h('td', { class: 'n', text: int(p.cache_read_input_tokens) }),
        h('td', { class: 'n', text: usd(p.cost_usd) }),
        h('td', { class: 'n', text: p.calls ? usd(p.cost_usd / p.calls, 4) : '—' }),
        h('td', { class: 'small muted', text: '동일' }))),
      h('tr', {},
        h('td', { text: '[초기 1회] hindsight 기억 적재 (retain)' }),
        h('td', { text: uRet.model || 'claude-opus-5-5' }),
        h('td', { class: 'small muted', text: `은행 ${hs.retain?.bank || 'bali-trip'} (${hs.retain?.inserted_memories ?? 0}/${hs.retain?.total_index_photos ?? 307}장)` }),
        h('td', { class: 'n', text: int(uRet.calls) }),
        h('td', { class: 'n', text: int(retUncached) }),
        h('td', { class: 'n', text: int(uRet.output_tokens || 0) }),
        h('td', { class: 'n', text: int(uRet.cached_tokens || 0) }),
        h('td', { class: 'n', text: usd(uRet.cost_usd) }),
        h('td', { class: 'n', text: uRet.calls ? usd(uRet.cost_usd / uRet.calls, 4) : '—' }),
        h('td', { class: 'small muted', text: `+${usd(uRet.cost_usd)} (초기 1회)` })),
      h('tr', {},
        h('td', { text: '[질의 시험] hindsight recall (상위 10장 검색)' }),
        h('td', { text: 'bge-m3 + pgroonga + hs-rerank-1' }),
        h('td', { class: 'small muted', text: '로컬 임베딩·전문검색·리랭커 (Claude 미호출)' }),
        h('td', { class: 'n', text: '0' }),
        h('td', { class: 'n', text: '0' }),
        h('td', { class: 'n', text: '0' }),
        h('td', { class: 'n', text: '0' }),
        h('td', { class: 'n', text: '$0.00' }),
        h('td', { class: 'n', text: '$0.0000' }),
        h('td', { class: 'small good', text: `기존 후보 검색 대비 호출 0회` })),
      h('tr', {},
        h('td', { text: '[질의 시험] hindsight reflect (추론 답 채점)' }),
        h('td', { text: (uRef?.model || hs.eval?.model || 'claude-opus-5-5') }),
        h('td', { class: 'small muted', text: hs.eval ? `${hs.eval.total}문항 추론 + 구조화 추출` : '적재 완료 후 실행 예정' }),
        h('td', { class: 'n', text: hs.eval ? int(refCalls) : '—' }),
        h('td', { class: 'n', text: hs.eval ? int(refUncached) : '—' }),
        h('td', { class: 'n', text: hs.eval ? int(refOut) : '—' }),
        h('td', { class: 'n', text: hs.eval ? int(refCached) : '—' }),
        h('td', { class: 'n', text: hs.eval ? usd(refUsd) : '측정 대기' }),
        h('td', { class: 'n', text: hs.eval && refCalls ? usd(refUsd / refCalls, 4) : '—' }),
        h('td', {
          class: `small ${hs.eval ? (refUsd <= wo15Usd ? 'good' : 'bad') : 'muted'}`,
          text: hs.eval ? `기존 15문항(${usd(wo15Usd)}) 대비 ${refUsd <= wo15Usd ? '' : '+'}${usd(refUsd - wo15Usd)}` : '대기 중',
        })),
    ];

    const withTotalCalls = prepSum.calls + (uRet.calls || 0) + refCalls;
    const withTotalIn = prepSum.inTok + retUncached + refUncached;
    const withTotalOut = prepSum.outTok + (uRet.output_tokens || 0) + refOut;
    const withTotalCr = prepSum.crTok + (uRet.cached_tokens || 0) + refCached;
    const withTotalUsd = prepSum.usd + (uRet.cost_usd || 0) + refUsd;
    const woOneSetTotalCalls = prepSum.calls + wo15Calls;
    const woOneSetTotalUsd = prepSum.usd + wo15Usd;

    hsRows.push(
      h('tr', { class: 'total' },
        h('td', {}, h('b', { text: '합계 (with hindsight 경로: 사전 준비 + 적재 + 15문항 시험)' })),
        h('td'), h('td'),
        h('td', { class: 'n' }, h('b', { text: int(withTotalCalls) })),
        h('td', { class: 'n', text: int(withTotalIn) }),
        h('td', { class: 'n', text: int(withTotalOut) }),
        h('td', { class: 'n', text: int(withTotalCr) }),
        h('td', { class: 'n' }, h('b', { text: usd(withTotalUsd) })),
        h('td'),
        h('td', {
          class: `small ${hs.eval ? (withTotalUsd <= woOneSetTotalUsd ? 'good' : 'bad') : 'muted'}`,
          text: hs.eval ? `${withTotalUsd <= woOneSetTotalUsd ? '' : '+'}${usd(withTotalUsd - woOneSetTotalUsd)} (1세트 전체 기준)` : '적재 진행 중',
        })),
      h('tr', {},
        h('td', { class: 'muted small', text: '비교 기준: without hindsight 1세트 경로 (사전 준비 + 15문항 시험)' }),
        h('td'), h('td'),
        h('td', { class: 'n muted small', text: int(woOneSetTotalCalls) }),
        h('td', { class: 'n muted small', text: '—' }),
        h('td', { class: 'n muted small', text: '—' }),
        h('td', { class: 'n muted small', text: '—' }),
        h('td', { class: 'n muted small', text: usd(woOneSetTotalUsd) }),
        h('td'),
        h('td', { class: 'small muted', text: '기준선' })),
    );

    hsTbl.replaceChildren(h('thead', {}, hsHead), h('tbody', {}, hsRows));
  }

  // ---------- 표본 확인 ----------
  function select(label, key, options) {
    const s = h('select', { id: `bali-${key}`, onchange: () => { S[key] = s.value; drawBrowse(); } },
      options.map(([v, t]) => h('option', { value: v, text: t })));
    s.value = S[key];
    S.ctl[key] = s;
    return [h('label', { for: s.id, text: label }), s];
  }

  function drawControls() {
    const ph = S.data.photos;
    const days = [...countBy(ph, (p) => dayOf(p.time))].sort((a, b) => a[0].localeCompare(b[0]));
    const srcs = [...countBy(ph, srcOf)];
    const types = [...countBy(ph, (p) => p.desc?.photo_type || '')].filter(([k]) => k);
    const q = h('input', { type: 'search', id: 'bali-q', placeholder: '설명·사진 속 글자·지명에서 찾기', oninput: () => { S.q = q.value; drawBrowse(); } });
    S.ctl.q = q;
    $('bali-controls').replaceChildren(
      ...select('보기', 'view', [[VIEW_SAMPLE, '묶음마다 1장(표본)'], [VIEW_RANDOM, `무작위 ${RANDOM_N}장`], [VIEW_ALL, '조건에 맞는 사진 전부']]),
      h('button', { type: 'button', class: 'btn btn-s', onclick: () => { S.view = VIEW_RANDOM; S.ctl.view.value = VIEW_RANDOM; drawBrowse(); } }, '무작위로 다시 뽑기'),
      ...select('날짜', 'day', [['', '전체'], ...days.map(([d, n]) => [d, `${dayLabel(d)} · ${n}장`])]),
      ...select('묶음', 'group', [['', '전체'], ...S.data.groups.map((g) => [g.group, `${g.group} ${g.area_ko}${g.near_ko ? ` · ${g.near_ko}` : ''} · ${g.photos}장`])]),
      ...select('출처', 'source', [['', '전체'], ...srcs.map(([s, n]) => [s, `${s} · ${n}장`])]),
      ...select('종류', 'type', [['', '전체'], ...types.map(([t, n]) => [t, `${TYPE_KO[t] ?? t} · ${n}장`])]),
      q,
    );
  }

  function filtered() {
    const q = S.q.trim().toLowerCase();
    return S.data.photos.filter((p) => (!S.day || dayOf(p.time) === S.day)
      && (!S.group || p.place_group === S.group)
      && (!S.source || srcOf(p) === S.source)
      && (!S.type || p.desc?.photo_type === S.type)
      && (!q || [p.id, p.area_ko, p.area_en, p.near_ko, p.near_en, p.desc?.place, p.desc?.scene, p.desc?.text_in_photo]
        .some((s) => (s || '').toLowerCase().includes(q)))).sort(byTime);
  }

  function pickList(all) {
    if (S.view === VIEW_ALL) return all;
    if (S.view === VIEW_RANDOM) {
      const xs = all.slice();
      for (let i = xs.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [xs[i], xs[j]] = [xs[j], xs[i]]; }
      return xs.slice(0, RANDOM_N).sort(byTime);
    }
    const groups = new Map();
    for (const p of all) {
      const k = p.place_group || `-${p.id}`;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(p);
    }
    return [...groups.values()].map((xs) => xs[Math.floor((xs.length - 1) / 2)]).sort(byTime);
  }

  function thumb(p) {
    const d = p.desc;
    return h('button', { type: 'button', class: 'bcard', 'data-id': p.id, 'aria-pressed': String(p.id === S.sel), onclick: () => showDetail(p.id) },
      h('img', { src: p.img, alt: '', loading: 'lazy', decoding: 'async' }),
      h('span', { class: 't', text: timeLabel(p.time) }),
      h('span', { class: 'w', text: `${placeLabel(p)} · ${srcOf(p)}` }),
      d ? h('span', { class: 's' }, h('span', { class: 'kind', text: d.place || TYPE_KO[d.photo_type] || '—' }), d.scene) : h('span', { class: 's err', text: '설명 없음' }));
  }

  function drawBrowse() {
    const all = filtered();
    S.list = pickList(all);
    const total = S.data.photos.length;
    const what = S.view === VIEW_ALL ? '' : S.view === VIEW_RANDOM ? ` 가운데 무작위 ${S.list.length}장` : ` 가운데 묶음마다 가운데 시각의 1장씩 ${S.list.length}장`;
    $('bali-count').textContent = `조건에 맞는 사진 ${all.length}장${what}을 보는 중이다(전체 ${total}장). 카드를 누르면 사진과 Claude가 돌려준 설명 전체, 호출 비용이 나온다.`;
    $('bali-grid').replaceChildren(...S.list.map(thumb));
    if (S.sel && !S.list.some((p) => p.id === S.sel)) closeDetail();
  }

  function focusBrowse(next) {
    Object.assign(S, next, { view: VIEW_ALL, q: '' });
    for (const k of ['view', 'day', 'group', 'source', 'type', 'q']) if (S.ctl[k]) S.ctl[k].value = S[k];
    drawBrowse();
    $('bali-browse').scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  function closeDetail() {
    S.sel = null;
    $('bali-detail').hidden = true;
    for (const b of $('bali-grid').querySelectorAll('.bcard')) b.setAttribute('aria-pressed', 'false');
  }

  function dl(pairs) {
    return h('dl', {}, pairs.filter(Boolean).map(([k, v]) => [h('dt', { text: k }), h('dd', {}, v ?? '—')]));
  }

  function showDetail(id) {
    const p = S.byId.get(id);
    if (!p) return;
    S.sel = id;
    for (const b of $('bali-grid').querySelectorAll('.bcard')) b.setAttribute('aria-pressed', String(b.dataset.id === id));
    const d = p.desc ?? {};
    const i = S.list.findIndex((x) => x.id === id);
    const go = (k) => { const n = S.list[i + k]; if (n) showDetail(n.id); };
    const longEdge = Math.max(p.model_width || 0, p.model_height || 0);
    const box = $('bali-detail');
    box.hidden = false;
    box.replaceChildren(
      h('figure', { class: 'bfig' },
        h('a', { href: p.img, target: '_blank', rel: 'noopener', title: '새 탭에서 크게 보기' }, h('img', { src: p.img, alt: d.scene || p.id })),
        h('figcaption', { class: 'muted small', text: `${p.id} · 화면용 사본이다. Claude에는 같은 사진을 긴 변 ${longEdge}픽셀로 줄인 사본(원본 ${p.width}×${p.height})을 보냈다.` })),
      h('div', { class: 'binfo' },
        h('h4', { text: '사진 목록에 적힌 것' }),
        dl([
          ['찍은 시각', `${timeLabel(p.time)} (${BASIS_KO[p.time_basis] ?? p.time_basis ?? '기준 없음'})`],
          ['찍은 곳', placeLabel(p)],
          ['묶음·방문', `${p.place_group ?? '—'} · ${p.visit ?? '—'}`],
          ['출처', srcOf(p)],
          p.received_at ? ['받은 시각', timeLabel(p.received_at)] : null,
        ]),
        h('h4', { text: 'Claude가 돌려준 설명' }),
        d.error ? h('p', { class: 'err', text: `실패: ${d.error}` }) : null,
        dl([
          ['장소 종류 (place)', d.place],
          ['사진 종류 (photo_type)', d.photo_type ? `${TYPE_KO[d.photo_type] ?? d.photo_type} (${d.photo_type})` : null],
          ['장면 (scene)', d.scene],
        ]),
        h('div', { class: 'small muted', text: '사진 속 글자 (text_in_photo)' }),
        h('pre', { class: 'pre', text: d.text_in_photo || '(글자 없음)' }),
        h('h4', { text: '호출' }),
        h('p', { class: 'small', text: `${d.model ?? '—'} · effort ${d.effort ?? '—'} · 지시문 ${d.prompt_version ?? '—'} · 입력 ${int(d.input_tokens)}토큰 · 출력 ${int(d.output_tokens)}토큰 · ${usd(d.cost_usd, 4)} · ${secs(d.ms)}` }),
        h('p', { class: 'small muted', text: `보낸 것: 사진 1장 + "${S.data.describe.user_prompt ?? '?'}" + 지시문. 찍은 시각과 찍은 곳은 보내지 않았다.` }),
        h('div', { class: 'bnav' },
          h('button', { type: 'button', class: 'btn btn-s', disabled: i <= 0, onclick: () => go(-1) }, '← 이전'),
          h('span', { class: 'muted small', text: i >= 0 ? `${i + 1} / ${S.list.length}` : '' }),
          h('button', { type: 'button', class: 'btn btn-s', disabled: i < 0 || i >= S.list.length - 1, onclick: () => go(1) }, '다음 →'),
          h('button', { type: 'button', class: 'btn btn-s', onclick: closeDetail }, '닫기'))),
    );
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function jumpToPhoto(id) {
    if (!S.byId.has(id)) return;
    Object.assign(S, { view: VIEW_ALL, day: '', group: '', source: '', type: '', q: '' });
    for (const k of ['view', 'day', 'group', 'source', 'type', 'q']) if (S.ctl[k]) S.ctl[k].value = S[k];
    drawBrowse();
    showDetail(id);
  }

  function miniThumbs(ids, clsFn, compact = false) {
    if (!ids?.length) return h('span', { class: 'muted small', text: '없음' });
    return h('div', { class: compact ? 'beval-thumbs compact' : 'beval-thumbs' }, ids.map((id) => {
      const p = S.byId.get(id);
      const cls = clsFn ? clsFn(id) : '';
      const title = p ? `${id} · ${timeLabel(p.time)} · ${placeLabel(p)}` : id;
      return h('button', { type: 'button', class: cls, title, onclick: () => jumpToPhoto(id) },
        p ? h('img', { src: p.img, alt: id, loading: 'lazy', decoding: 'async' }) : h('span', { class: 'small', text: id }));
    }));
  }

  function rankBadge(rk) {
    if (!rk) return h('span', { class: 'rank bad', text: '없음' });
    const cls = rk === 1 ? 'good' : (rk <= 3 ? 'mid' : '');
    return h('span', { class: `rank ${cls}`.trim(), text: `${rk}위` });
  }

  function drawIndexAndEval() {
    const d = S.data;
    const idx = d.index;
    const ev = d.eval;
    const ak = d.answer_key;
    const cardsBox = $('bali-index-cards');
    const wrap = $('bali-eval-wrap');
    const tbl = $('bali-eval');
    const sub = $('bali-eval-sub');
    if (!cardsBox || !wrap || !tbl) return;

    const idxCards = [];
    if (idx) {
      const typeSummary = Object.entries(idx.photo_types || {}).map(([k, n]) => `${TYPE_KO[k] ?? k} ${n}`).join(' · ') || '—';
      idxCards.push(
        card('검색 색인 줄 수', `${d.dir}/index.jsonl`, [['전체 줄 수', `${idx.rows}줄`], ['사진 · 문서', `${idx.photos}장 · ${idx.documents}건`]]),
        card('시각·지명 누락 검사', '수집 시각 + 묶음 지명 결합', [['시각 빠진 사진', `${idx.missing_time}장`], ['지명 빠진 사진', `${idx.missing_place}장`]]),
        card('색인 세부 분포', typeSummary, [['가까운 명소 포함', `${idx.with_near}장`], ['글자 있는 사진', `${idx.with_text}장`]]),
      );
    }
    if (ev) {
      idxCards.push(
        card('질의 시험 채점', `${ev.model ?? 'Claude'} · 호출 ${int(ev.calls)}번`,
          [['맞음 · 틀림', `${ev.passed}맞음 · ${ev.failed}틀림`], ['시험 비용', usd(ev.cost_usd)]]),
      );
    } else if (ak) {
      const seqN = ak.filter((t) => t.kind === 'sequence').length;
      idxCards.push(
        card('질의 정답표 초안', `${d.dir}/answer_key.json · 채점 전`,
          [['전체 질의', `${ak.length}개`], ['순서 질의', `${seqN}개`]]),
      );
    }
    cardsBox.replaceChildren(...idxCards);

    if (ev && ev.results?.length) {
      if (sub) {
        sub.textContent = `에이전트 루프 채점 결과(${ev.passed}/${ev.total} 통과, 필수만 정확 일치 ${ev.exact_match}개, 비용 ${usd(ev.cost_usd)}). 고른 사진 테두리: 초록=필수 정답, 파랑=선택 허용, 빨강=여분·함정. 썸네일을 누르면 위 표본 확인에서 열린다.`;
      }
      wrap.hidden = false;
      const head = h('tr', {}, ['번호', '종류', '질의와 에이전트 답', '판정', '고른 사진', '정답 사진(초록=필수, 파랑=선택)', '빠짐 · 여분 · 함정', '호출 · 비용']
        .map((t, i) => h('th', { class: i === 7 ? 'n' : null, text: t })));
      const rows = ev.results.map((r) => {
        const mustSet = new Set(r.answer_ids || []);
        const optSet = new Set(r.optional_ids || []);
        const gotCls = (id) => (mustSet.has(id) ? 'lab-answer' : (optSet.has(id) ? 'lab-exhibition' : 'lab-trap'));
        const gtAll = [...(r.answer_ids || []), ...(r.optional_ids || [])];
        const gtCls = (id) => (mustSet.has(id) ? 'lab-answer' : 'lab-exhibition');
        const diffs = [];
        if (r.missing_ids?.length) diffs.push(`빠짐 ${r.missing_ids.length}장: ${r.missing_ids.join(', ')}`);
        if (r.extra_ids?.length) diffs.push(`여분 ${r.extra_ids.length}장: ${r.extra_ids.join(', ')}`);
        if (r.trap_hits?.length) diffs.push(`함정 ${r.trap_hits.length}장: ${r.trap_hits.join(', ')}`);
        if (r.error) diffs.push(`오류: ${r.error}`);
        return h('tr', {},
          h('td', {}, h('b', { text: r.id })),
          h('td', {}, h('span', { class: 'kind', text: KIND_KO[r.kind] ?? (r.kind || '일반') })),
          h('td', { class: 'wide' },
            h('div', {}, h('b', { text: r.query })),
            r.result_sentence ? h('div', { class: 'small muted', text: `답: ${r.result_sentence}` }) : null,
            r.cutoffs?.length ? h('div', { class: 'small muted', text: `기준 시각: ${r.cutoffs.join(', ')}` }) : null),
          h('td', {}, h('span', { class: `badge-pill ${r.passed ? 'good' : 'bad'}`, text: r.passed ? '맞음' : '틀림' })),
          h('td', {}, miniThumbs(r.got_ids, gotCls)),
          h('td', {}, miniThumbs(gtAll, gtCls)),
          h('td', { class: 'small' }, diffs.length ? diffs.map((line) => h('div', { class: 'err', text: line })) : h('span', { class: 'muted', text: '없음' })),
          h('td', { class: 'n small', text: `${int(r.calls)}회 · ${usd(r.cost_usd, 3)}` }),
        );
      });
      tbl.replaceChildren(h('thead', {}, head), h('tbody', {}, rows));
    } else if (ak?.length) {
      if (sub) {
        sub.textContent = `채점 실행 전이라 사진·시각·지명만 보고 만든 정답표 초안(${ak.length}개 질의)을 미리 보여 준다. 채점을 돌리면 이 표가 질의별 맞음·틀림과 고른 사진 결과로 바뀐다. 썸네일을 누르면 위 표본 확인에서 열린다.`;
      }
      wrap.hidden = false;
      const head = h('tr', {}, ['번호', '종류', '질의 (초안)', '상태', '필수 정답 사진', '선택 허용 사진', '함정 사진', '비고']
        .map((t) => h('th', { text: t })));
      const rows = ak.map((t) => h('tr', {},
        h('td', {}, h('b', { text: t.id })),
        h('td', {}, h('span', { class: 'kind', text: KIND_KO[t.kind] ?? (t.kind || '일반') })),
        h('td', { class: 'wide' }, h('b', { text: t.query })),
        h('td', {}, h('span', { class: 'badge-pill mid', text: '채점 대기' })),
        h('td', {}, miniThumbs(t.answer_ids, () => 'lab-answer')),
        h('td', {}, miniThumbs(t.optional_ids, () => 'lab-exhibition')),
        h('td', {}, miniThumbs(t.trap_ids, () => 'lab-trap')),
        h('td', { class: 'wide small muted', text: t.note || '—' }),
      ));
      tbl.replaceChildren(h('thead', {}, head), h('tbody', {}, rows));
    } else {
      wrap.hidden = true;
    }
  }

  function drawHindsight() {
    const hs = S.data.hindsight;
    const sec = $('bali-hindsight-eval');
    const cardsBox = $('bali-hs-cards');
    const sub = $('bali-hs-sub');
    const evalWrap = $('bali-hs-eval-wrap');
    const evalTbl = $('bali-hs-eval');
    const cmpHead = $('bali-hs-cmp-head');
    const cmpSub = $('bali-hs-cmp-sub');
    const cmpWrap = $('bali-hs-cmp-wrap');
    const cmpTbl = $('bali-hs-cmp');
    if (!sec || !cardsBox || !evalWrap || !evalTbl || !cmpWrap || !cmpTbl) return;

    if (!hs || (!hs.retain && !hs.eval)) {
      sec.hidden = true;
      return;
    }
    sec.hidden = false;

    const ret = hs.retain;
    const ev = hs.eval;
    const wo = S.data.eval;
    const cfg = ev?.settings ?? ret?.settings ?? {};
    const bankName = ev?.bank ?? ret?.bank ?? 'bali-trip';
    const bStats = ev?.bank_stats ?? ret?.stats ?? {};
    const uRet = ev?.llm_usage?.retain ?? ret?.llm_usage ?? { calls: 0, cost_usd: 0 };
    const uTot = ev?.llm_usage?.total ?? ret?.bank_total_llm_usage ?? { calls: 0, cost_usd: 0 };

    const hsCards = [];
    const insertedN = ret ? ret.inserted_memories : (bStats.total_documents ?? 0);
    const totalIdxN = ret ? ret.total_index_photos : (S.data.index?.photos ?? S.data.photos.length);
    const failedN = ret ? ret.failed_count : (bStats.failed_operations ?? 0);
    hsCards.push(
      card('hindsight 은행 적재', `은행 ${bankName} · 추출 사실 ${int(bStats.total_nodes ?? 0)}개`, [
        ['넣은 기억 수', `${insertedN} / ${totalIdxN}장`],
        ['실패 수', `${failedN}건`],
      ]),
      card('hindsight 설정', `LLM ${cfg.llm_provider || 'anthropic'} / ${cfg.llm_model || 'claude-opus-5-5'}`, [
        ['임베딩 · 전문검색', `${(cfg.embeddings_model || 'bge-m3').replace('BAAI/', '')} · ${cfg.text_search_extension || 'pgroonga'}`],
        ['관찰(자동 요약)', cfg.observations ? '켜짐' : '꺼짐 (사실 기억만)'],
      ]),
    );

    if (ev) {
      const rs = ev.recall_summary || {};
      hsCards.push(
        card('질의 시험 채점 (with hindsight)', `reflect · 호출 ${int(ev.total_calls)}번${wo ? ` (기존 ${int(wo.calls)}번)` : ''}`, [
          ['맞음 · 틀림', `${ev.passed}맞음 · ${ev.failed}틀림`],
          ['시험 비용 (reflect)', `${usd(ev.total_cost_usd)}${wo ? ` (기존 ${usd(wo.cost_usd)})` : ''}`],
        ]),
        card('hindsight recall 상위 10장', '로컬 임베딩+pgroonga+리랭커 · LLM 0회 ($0)', [
          ['상위 10장 정답 적중', `${rs.top10_hit_queries ?? 0} / ${ev.total}개`],
          ['첫 정답 1위 · MRR', `1위 ${rs.rank1_queries ?? 0}개 · ${Number(rs.mrr ?? 0).toFixed(2)}`],
        ]),
      );
    }

    hsCards.push(
      card('hindsight 비용 요약', `은행 전체 LLM 호출 ${int(uTot.calls)}번`, [
        ['기억 적재 비용(1회)', usd(uRet.cost_usd)],
        ['적재 + 시험 합계', usd(uTot.cost_usd)],
      ]),
    );
    cardsBox.replaceChildren(...hsCards);

    if (ev && ev.results?.length) {
      if (sub) {
        sub.textContent = `hindsight reflect 채점 결과(${ev.passed}/${ev.total} 통과, 필수만 정확 일치 ${ev.exact}개, 시험 비용 ${usd(ev.total_cost_usd)} · 기억 적재 1회 비용 ${usd(uRet.cost_usd)} 별도). 위 'without hindsight' 표와 동일한 15개 항목·기준으로 채점했다.`;
      }
      evalWrap.hidden = false;
      const head = h('tr', {}, ['번호', '종류', '질의와 hindsight 답 (reflect)', '판정', '고른 사진', '정답 사진(초록=필수, 파랑=선택)', '빠짐 · 여분 · 함정', '호출 · 비용']
        .map((t, i) => h('th', { class: i === 7 ? 'n' : null, text: t })));
      const rows = ev.results.map((r) => {
        const mustSet = new Set(r.answer_ids || []);
        const optSet = new Set(r.optional_ids || []);
        const gotCls = (id) => (mustSet.has(id) ? 'lab-answer' : (optSet.has(id) ? 'lab-exhibition' : 'lab-trap'));
        const gtAll = [...(r.answer_ids || []), ...(r.optional_ids || [])];
        const gtCls = (id) => (mustSet.has(id) ? 'lab-answer' : 'lab-exhibition');
        const diffs = [];
        if (r.missing_ids?.length) diffs.push(`빠짐 ${r.missing_ids.length}장: ${r.missing_ids.join(', ')}`);
        if (r.extra_ids?.length) diffs.push(`여분 ${r.extra_ids.length}장: ${r.extra_ids.join(', ')}`);
        if (r.trap_hits?.length) diffs.push(`함정 ${r.trap_hits.length}장: ${r.trap_hits.join(', ')}`);
        if (r.error) diffs.push(`오류: ${r.error}`);
        const woInfo = r.without_hindsight;
        let deltaTag = null;
        if (woInfo?.available && r.passed !== woInfo.passed) {
          deltaTag = h('div', {
            class: `small ${r.passed ? 'good' : 'bad'}`,
            text: r.passed ? '↑ without hindsight: 틀림→맞음' : '↓ without hindsight: 맞음→틀림',
          });
        }
        return h('tr', {},
          h('td', {}, h('b', { text: r.id })),
          h('td', {}, h('span', { class: 'kind', text: KIND_KO[r.kind] ?? (r.kind || '일반') })),
          h('td', { class: 'wide' },
            h('div', {}, h('b', { text: r.query })),
            r.result_sentence ? h('div', { class: 'small muted', text: `답: ${r.result_sentence}` }) : null,
            h('div', { class: 'small muted', text: `내부 recall 검색 ${int(r.recall_tool_calls)}회 · 첫 정답 순위 ${r.first_must_rank ? `${r.first_must_rank}위` : '없음'}` })),
          h('td', {},
            h('span', { class: `badge-pill ${r.passed ? 'good' : 'bad'}`, text: r.passed ? '맞음' : '틀림' }),
            deltaTag),
          h('td', {}, miniThumbs(r.got_ids, gotCls)),
          h('td', {}, miniThumbs(gtAll, gtCls)),
          h('td', { class: 'small' }, diffs.length ? diffs.map((line) => h('div', { class: 'err', text: line })) : h('span', { class: 'muted', text: '없음' })),
          h('td', { class: 'n small', text: `${int(r.calls)}회 · ${usd(r.cost_usd, 3)}` }),
        );
      });
      evalTbl.replaceChildren(h('thead', {}, head), h('tbody', {}, rows));

      if (cmpHead) cmpHead.hidden = false;
      if (cmpSub) cmpSub.hidden = false;
      cmpWrap.hidden = false;
      const cHead = h('tr', {}, [
        '번호',
        '질의 · 정답',
        'without hindsight (기본 색인 경로)',
        'hindsight recall (상위 10장 · LLM 0회)',
        'hindsight reflect (추론 답)',
      ].map((t) => h('th', { text: t })));

      const cRows = ev.results.map((r) => {
        const mustSet = new Set(r.answer_ids || []);
        const optSet = new Set(r.optional_ids || []);
        const trapSet = new Set(r.trap_ids || []);
        const clsStrict = (id) => (mustSet.has(id) ? 'lab-answer' : (optSet.has(id) ? 'lab-exhibition' : 'lab-trap'));
        const clsRecall = (id) => (mustSet.has(id) ? 'lab-answer' : (optSet.has(id) ? 'lab-exhibition' : (trapSet.has(id) ? 'lab-trap' : 'lab-other')));
        const gtAll = [...(r.answer_ids || []), ...(r.optional_ids || [])];
        const gtCls = (id) => (mustSet.has(id) ? 'lab-answer' : 'lab-exhibition');

        const woR = r.without_hindsight || {};
        const recC = r.recall_channel || {};
        const mustN = (r.answer_ids || []).length;

        const woCell = woR.available ? [
          h('div', { class: 'cmp-meta' },
            h('span', { class: `badge-pill ${woR.passed ? 'good' : 'bad'}`, text: woR.passed ? '맞음' : '틀림' }),
            h('span', { class: 'muted', text: '첫 정답' }),
            rankBadge(woR.first_must_rank),
            h('span', { class: 'muted', text: `${int(woR.calls)}회 · ${usd(woR.cost_usd, 3)}` })),
          miniThumbs(woR.got_ids, clsStrict, true),
        ] : [h('span', { class: 'muted small', text: '결과 없음' })];

        const recHitN = (recC.matched_must_top10 || []).length;
        const recTrapN = (recC.trap_hits_top10 || []).length;
        const recPillCls = recHitN === mustN && !recTrapN ? 'good' : (recHitN > 0 ? 'mid' : 'bad');
        const recCell = [
          h('div', { class: 'cmp-meta' },
            h('span', { class: `badge-pill ${recPillCls}`, text: `상위10 정답 ${recHitN}/${mustN}${recTrapN ? ` · 함정 ${recTrapN}` : ''}` }),
            h('span', { class: 'muted', text: '첫 정답' }),
            rankBadge(recC.first_must_rank),
            h('span', { class: 'muted', text: '0회 · $0.000' })),
          miniThumbs(recC.top10_ids, clsRecall, true),
        ];

        const rfCell = [
          h('div', { class: 'cmp-meta' },
            h('span', { class: `badge-pill ${r.passed ? 'good' : 'bad'}`, text: r.passed ? '맞음' : '틀림' }),
            h('span', { class: 'muted', text: '첫 정답' }),
            rankBadge(r.first_must_rank),
            h('span', { class: 'muted', text: `${int(r.calls)}회 · ${usd(r.cost_usd, 3)}` })),
          miniThumbs(r.got_ids, clsStrict, true),
        ];

        return h('tr', {},
          h('td', {}, h('b', { text: r.id }), h('div', { class: 'small muted', text: KIND_KO[r.kind] ?? r.kind })),
          h('td', { class: 'wide' },
            h('div', {}, h('b', { text: r.query })),
            h('div', { class: 'small muted', text: `필수 ${mustN}장${r.optional_ids?.length ? ` · 선택 ${r.optional_ids.length}장` : ''}${r.trap_ids?.length ? ` · 함정 ${r.trap_ids.length}장` : ''}` }),
            miniThumbs(gtAll, gtCls, true)),
          h('td', {}, ...woCell),
          h('td', {}, ...recCell),
          h('td', {}, ...rfCell),
        );
      });
      cmpTbl.replaceChildren(h('thead', {}, cHead), h('tbody', {}, cRows));
    } else {
      if (sub) {
        sub.textContent = `hindsight 은행(${bankName})에 사진 기억 ${insertedN}/${totalIdxN}장을 적재하는 중이다. 질의 시험(eval.hindsight eval)이 돌면 채점표와 비교표가 여기에 채워진다.`;
      }
      evalWrap.hidden = true;
      if (cmpHead) cmpHead.hidden = true;
      if (cmpSub) cmpSub.hidden = true;
      cmpWrap.hidden = true;
    }
  }

  // ---------- 불러오기 ----------
  async function load() {
    const msg = $('bali-msg');
    const body = $('bali-body');
    if (!msg || !body) return;
    msg.hidden = false;
    msg.textContent = '실데이터 결과를 불러오는 중…';
    let j = null;
    try {
      const res = await fetch('/api/bali', { cache: 'no-store' });
      j = await res.json().catch(() => null);
      if (!res.ok || !j) throw new Error(j?.error || `서버 응답 ${res.status}`);
    } catch (err) {
      body.hidden = true;
      msg.textContent = `실데이터 결과를 불러오지 못했다. ${err.message}`;
      return;
    }
    S.data = j;
    S.byId = new Map(j.photos.map((p) => [p.id, p]));
    msg.hidden = true;
    body.hidden = false;
    $('bali-dir').textContent = j.dir;
    drawCards();
    drawSteps();
    drawDays();
    drawControls();
    drawBrowse();
    drawGroups();
    drawIndexAndEval();
    drawHindsight();
    drawCost();
    if (S.sel) showDetail(S.sel);
  }

  document.addEventListener('DOMContentLoaded', () => {
    load();
    $('reload')?.addEventListener('click', load);
  });
})();
