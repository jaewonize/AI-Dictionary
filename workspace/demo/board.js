/* 대시보드 ① 자료가 글자가 되는 과정, ② 에이전트 파이프라인, ④ 질문에 답하는 과정, 마무리 표 */
(function (global) {
  'use strict';

  var Bus = global.__AIDicBus = global.__AIDicBus || {};

  var BASE_TOOLS = [
    ['plan_query', '질의 정리'],
    ['search_web', '웹 검색'],
    ['ask_user', '되묻기'],
    ['recall_memory', '기억 꺼내기'],
    ['reflect_memory', '기억 되짚기'],
    ['find_candidates', '후보 찾기'],
    ['judge_photos', '사진 다시 보기'],
    ['show_answer', '답 보이기']
  ];

  var DOC_EXTRA_TOOLS = [
    ['read_documents', '문서 읽기'],
    ['calc', '계산']
  ];

  var TOOL_KO = {
    plan_query: '질의 정리',
    search_web: '웹 검색',
    ask_user: '되묻기',
    recall_memory: '기억 꺼내기',
    reflect_memory: '기억 되짚기',
    find_candidates: '후보 찾기',
    judge_photos: '사진 다시 보기',
    show_answer: '답 보이기',
    read_documents: '문서 읽기',
    calc: '계산'
  };

  var CALL_PURPOSE_KO = {
    loop: '다음 할 일 고르기',
    search_web: '웹 검색',
    search_web_extract: '검색 결과 정리',
    pick_candidates: '설명에서 후보 고르기',
    judge_photos: '사진 다시 보기'
  };

  function extractHostname(urlStr) {
    if (!urlStr || typeof urlStr !== 'string') return '';
    try {
      var u = new URL(urlStr);
      return u.hostname || '';
    } catch (e) {
      var m = /^https?:\/\/([^/?#]+)/i.exec(urlStr);
      return m ? m[1] : '';
    }
  }

  function fmtRange(a, b) {
    var sa = Bus.fmtShortDate(a);
    var sb = Bus.fmtShortDate(b);
    return sa && sb ? sa + ' – ' + sb : (sa || sb || '');
  }

  Bus.createBoard = function createBoard(data, callbacks) {
    callbacks = callbacks || {};

    // ① 요소
    var sub1El = document.getElementById('db-sub-1');
    var p1CountsEl = document.getElementById('db-p1-counts');
    var p1NarrowEl = document.getElementById('db-p1-narrow');
    var p1StatusEl = document.getElementById('db-p1-status');
    var p1SpotEl = document.getElementById('db-p1-spotlight');
    var p1GridEl = document.getElementById('db-p1-grid');
    var p1FootEl = document.getElementById('db-p1-footer');
    var p1DrawerEl = document.getElementById('db-p1-drawer');
    var replayPrepBtn = document.getElementById('db-replay-prep');

    // ② 요소
    var p2RowsEl = document.getElementById('db-p2-rows');
    var p2FootEl = document.getElementById('db-p2-foot');
    var p2PopEl = document.getElementById('db-p2-popover');

    // ④ 요소
    var p4ToolsEl = document.getElementById('db-p4-tools');
    var p4LogEl = document.getElementById('db-p4-log');
    var p4FootEl = document.getElementById('db-p4-foot');
    var p4CallsEl = document.getElementById('db-p4-calls');

    // 마무리 표 요소
    var outroCardEl = document.getElementById('demo-outro-card');

    var catalog = data.catalog || { total_photos: 0, badges: [], described_count: 0, index_count: null, photos: [] };
    var docCatalog = data.catalog_docs || { total_docs: 0, badges: [], docs: [] };
    var docSvgs = data.doc_svgs || {};
    var smallPhotos = data.photos_small || {};
    var largePhotos = data.photos_large || {};
    var pipeData = data.pipeline || {};
    var noServerCount = (data.no_server_photo_ids || []).length;

    var currentMode = 'auto';
    var currentCase = null;
    var isDocCase = false;
    var activePhotos = catalog.photos || [];
    var photoMap = {};
    var cellMap = {};
    var describedPhotos = [];

    var narrowActive = false;
    var narrowRules = [];
    var narrowStepsState = [];
    var narrowHadEvidenceRule = false;
    var activeDayFilter = null;

    // ④ 상태
    var usedTools = {};
    var toolChipMap = {};
    var currentTurnEl = null;
    var currentTurnCostEl = null;
    var currentTurnReflectSubEl = null;
    var turnGroupCardMap = {};
    var cardDomCounter = 0;
    var currentDotLine = null;
    var currentRecordKind = '대본';
    var currentRunObj = null;
    var liveRunId = null;
    var lastLiveProgress = null;
    var receivedReflects = [];

    // ---------------------------------------------------------------- ① 격자 만들기
    function rebuildPhotoGrid(photosList, noPhotosFlag) {
      activePhotos = photosList || [];
      photoMap = {};
      cellMap = {};
      describedPhotos = [];
      p1GridEl.textContent = '';

      activePhotos.forEach(function (p) {
        photoMap[p.id] = p;
        if (p.has_desc) describedPhotos.push(p.id);
        var btn = Bus.el('button', 'db-p1-cell');
        btn.type = 'button';
        btn.dataset.id = p.id;
        var src = !noPhotosFlag ? (smallPhotos[p.id] || largePhotos[p.id] || '') : '';
        if (src) {
          var img = Bus.el('img');
          img.alt = '';
          img.decoding = 'async';
          img.src = src;
          btn.appendChild(img);
        }
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          if (currentMode !== 'try') return;
          openDrawer(p.id);
        });
        cellMap[p.id] = btn;
        p1GridEl.appendChild(btn);
      });

      sub1El.textContent = '사진 ' + activePhotos.length + '장 · 미리 분석에서 AI 모델이 사진마다 설명을 한 번 적어 둡니다';
      var parts = [];
      (catalog.badges || []).forEach(function (b) {
        parts.push(b.badge + ' ' + b.count);
      });
      parts.push('모두 ' + activePhotos.length + '장');
      p1CountsEl.textContent = parts.join(' · ');
    }

    function rebuildDocGrid(caseObj) {
      var docList = docCatalog.docs || [];
      var docPhotoIds = (caseObj && caseObj.scene && caseObj.scene.doc_photos) || [];
      var catPhotoById = {};
      (catalog.photos || []).forEach(function (p) { catPhotoById[p.id] = p; });

      activePhotos = [];
      photoMap = {};
      cellMap = {};
      describedPhotos = [];
      p1GridEl.textContent = '';

      docList.forEach(function (d) {
        var item = {
          id: d.id,
          badge: d.badge || '',
          ext: d.ext || 'DOC',
          chars: d.chars || 0,
          isDoc: true,
          has_desc: true
        };
        activePhotos.push(item);
        photoMap[item.id] = item;
        describedPhotos.push(item.id);

        var cls = 'db-p1-cell is-doc' + (item.badge === '카톡 받은 파일' ? ' is-kakao-doc' : '');
        var btn = Bus.el('button', cls, item.ext);
        btn.type = 'button';
        btn.dataset.id = item.id;
        btn.dataset.ext = item.ext;
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          if (currentMode !== 'try') return;
          openDrawer(item.id);
        });
        cellMap[item.id] = btn;
        p1GridEl.appendChild(btn);
      });

      docPhotoIds.forEach(function (pid) {
        var cp = catPhotoById[pid] || { id: pid, badge: '', time_dot: '' };
        var item = {
          id: pid,
          badge: cp.badge || '',
          time_dot: cp.time_dot || '',
          isDoc: false,
          isDocPhoto: true,
          has_desc: false
        };
        activePhotos.push(item);
        photoMap[pid] = item;

        var btn = Bus.el('button', 'db-p1-cell');
        btn.type = 'button';
        btn.dataset.id = pid;
        var src = smallPhotos[pid] || largePhotos[pid] || '';
        if (src) {
          var img = Bus.el('img');
          img.alt = '';
          img.decoding = 'async';
          img.src = src;
          btn.appendChild(img);
        }
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          if (currentMode !== 'try') return;
          openDrawer(pid);
        });
        cellMap[pid] = btn;
        p1GridEl.appendChild(btn);
      });

      sub1El.textContent = '문서 ' + docList.length + '건 · 노트북의 코드가 문서에서 글자를 뽑습니다';
      var dParts = [];
      (docCatalog.badges || []).forEach(function (b) {
        dParts.push(b.badge + ' ' + b.count);
      });
      dParts.push('모두 ' + docList.length + '건');
      dParts.push('문서 사진 ' + docPhotoIds.length + '장');
      p1CountsEl.textContent = dParts.join(' · ');
    }

    function setGridChecks(count) {
      var targetSet = {};
      for (var i = 0; i < count && i < describedPhotos.length; i++) {
        targetSet[describedPhotos[i]] = true;
      }
      activePhotos.forEach(function (p) {
        var cell = cellMap[p.id];
        if (!cell) return;
        var existing = cell.querySelector('.db-p1-check');
        if (targetSet[p.id]) {
          if (!existing) cell.appendChild(Bus.el('span', 'db-p1-check', '✓'));
        } else if (existing) {
          existing.remove();
        }
      });
    }

    function clearGridHighlights() {
      activePhotos.forEach(function (p) {
        var c = cellMap[p.id];
        if (!c) return;
        c.classList.remove('is-dim', 'is-evidence');
        c.hidden = false;
      });
    }

    function highlightBrightSet(brightSet) {
      activePhotos.forEach(function (p) {
        var c = cellMap[p.id];
        if (!c) return;
        if (brightSet[p.id]) c.classList.remove('is-dim');
        else c.classList.add('is-dim');
      });
    }

    function markEvidencePhotos(ids) {
      activePhotos.forEach(function (p) {
        var c = cellMap[p.id];
        if (c) c.classList.remove('is-evidence');
      });
      (ids || []).forEach(function (id) {
        if (cellMap[id]) cellMap[id].classList.add('is-evidence');
      });
    }

    function openDrawer(photoId) {
      var p = photoMap[photoId];
      if (!p) return;
      p1DrawerEl.textContent = '';
      var head = Bus.el('div', 'db-drawer-head');
      var metaLeft = isDocCase
        ? (p.isDoc ? (p.chars ? (p.chars + '자') : p.ext) : p.time_dot)
        : p.time_dot;
      var metaStr = [metaLeft, p.badge].filter(Boolean).join(' · ');
      head.appendChild(Bus.el('span', null, metaStr));
      var closeBtn = Bus.el('button', 'db-drawer-close', '닫기');
      closeBtn.type = 'button';
      closeBtn.addEventListener('click', function () { closeDrawer(); });
      head.appendChild(closeBtn);
      p1DrawerEl.appendChild(head);

      var src = docSvgs[photoId] || largePhotos[photoId] || smallPhotos[photoId] || '';
      if (src) {
        var img = Bus.el('img', 'db-drawer-img');
        img.alt = '';
        img.src = src;
        p1DrawerEl.appendChild(img);
      }
      if (isDocCase) {
        var dLines = (currentCase && currentCase.scene && currentCase.scene.doc_lines && currentCase.scene.doc_lines[photoId]) || [];
        p1DrawerEl.appendChild(Bus.el('div', 'db-drawer-label', '뽑은 글자 가운데 일부'));
        p1DrawerEl.appendChild(
          Bus.el('div', 'db-drawer-body', dLines.length ? dLines.join('\n') : ('뽑은 글자 ' + (p.chars || 0) + '자'))
        );
      } else {
        p1DrawerEl.appendChild(Bus.el('div', 'db-drawer-label', p.has_desc ? '미리 적어 둔 설명' : '설명 없음'));
        if (p.scene) {
          p1DrawerEl.appendChild(Bus.el('div', 'db-drawer-body', p.scene));
        }
        if (p.text) {
          p1DrawerEl.appendChild(Bus.el('div', 'db-drawer-label', '사진 속 글자'));
          p1DrawerEl.appendChild(Bus.el('div', 'db-drawer-body', p.text));
        }
      }
      p1DrawerEl.hidden = false;
    }

    function closeDrawer() {
      p1DrawerEl.hidden = true;
    }

    function renderSpotlightCard(photoId, localElapsedMs, slotDurationMs) {
      var p = photoMap[photoId] || { id: photoId, badge: '', time_dot: '', scene: '', text: '', has_desc: false };
      p1SpotEl.textContent = '';
      p1SpotEl.hidden = false;
      p1SpotEl.onclick = function (e) {
        e.stopPropagation();
        if (currentMode === 'try') openDrawer(photoId);
      };

      var imgBox = Bus.el('div', 'db-p1-spot-img');
      var src = docSvgs[photoId] || largePhotos[photoId] || smallPhotos[photoId] || '';
      if (src) {
        var im = Bus.el('img');
        im.alt = '';
        im.src = src;
        imgBox.appendChild(im);
      }
      p1SpotEl.appendChild(imgBox);
      p1SpotEl.appendChild(Bus.el('div', 'db-p1-spot-arrow', '→'));

      var body = Bus.el('div', 'db-p1-spot-body');
      var metaLeft = isDocCase
        ? (p.isDoc ? (p.chars ? (p.chars + '자') : (p.ext || '')) : p.time_dot)
        : p.time_dot;
      body.appendChild(Bus.el('div', 'db-p1-spot-meta', [metaLeft, p.badge].filter(Boolean).join(' · ')));

      if (isDocCase) {
        var docLines = (currentCase && currentCase.scene && currentCase.scene.doc_lines && currentCase.scene.doc_lines[photoId]) || [];
        var docFull = docLines.join('\n');
        var docChars = Array.from(docFull);
        var dInterval = Math.min(18, Math.max(0.5, (slotDurationMs - 500) / Math.max(1, docChars.length)));
        var dShown = Math.min(docChars.length, Math.max(0, Math.floor(localElapsedMs / dInterval)));
        body.appendChild(Bus.el('div', 'db-p1-spot-head', '뽑은 글자 가운데 일부'));
        body.appendChild(Bus.el('div', 'db-p1-spot-text', docChars.slice(0, dShown).join('')));
      } else if (!p.has_desc) {
        body.appendChild(Bus.el('div', 'db-p1-spot-head', '설명 없음'));
      } else {
        var sceneChars = Array.from(p.scene || '');
        var textChars = Array.from(p.text || '');
        var totalChars = sceneChars.length + textChars.length;
        var interval = Math.min(18, Math.max(0.5, (slotDurationMs - 500) / Math.max(1, totalChars)));
        var shown = Math.min(totalChars, Math.max(0, Math.floor(localElapsedMs / interval)));
        var shownScene = sceneChars.slice(0, Math.min(sceneChars.length, shown)).join('');
        body.appendChild(Bus.el('div', 'db-p1-spot-head', '미리 적어 둔 설명'));
        body.appendChild(Bus.el('div', 'db-p1-spot-text', shownScene));
        if (textChars.length > 0 && shown > sceneChars.length) {
          var shownTxt = textChars.slice(0, shown - sceneChars.length).join('');
          body.appendChild(Bus.el('div', 'db-p1-spot-head', '사진 속 글자'));
          body.appendChild(Bus.el('div', 'db-p1-spot-text', shownTxt));
        }
      }
      p1SpotEl.appendChild(body);
    }

    function showIndexFooter(show) {
      p1FootEl.textContent = '';
      if (!show) {
        p1FootEl.hidden = true;
        return;
      }
      if (isDocCase) {
        p1FootEl.appendChild(Bus.el('div', null, '노트북의 코드가 문서에서 글자를 뽑습니다. AI 모델을 부르지 않습니다.'));
        p1FootEl.appendChild(Bus.el('div', null, '질문에 답하는 문서 단계(문서 읽기, 계산)는 아직 만들지 않았습니다.'));
        p1FootEl.hidden = false;
      } else if (catalog.index_count != null) {
        p1FootEl.textContent = '찾을 자료 목록 ' + catalog.index_count + '건 · 에이전트는 이 목록에서 찾습니다';
        p1FootEl.hidden = false;
      } else {
        p1FootEl.hidden = true;
      }
    }

    // ① 장면별 상태 설정
    function setPanel1Intro() {
      closeDrawer();
      p1CountsEl.hidden = true;
      p1NarrowEl.hidden = true;
      p1StatusEl.hidden = true;
      p1SpotEl.hidden = true;
      showIndexFooter(false);
      activePhotos.forEach(function (p) {
        if (cellMap[p.id]) cellMap[p.id].hidden = true;
      });
    }

    function setPanel1Collect(progress01) {
      closeDrawer();
      p1CountsEl.hidden = false;
      p1NarrowEl.hidden = true;
      p1StatusEl.hidden = true;
      p1SpotEl.hidden = true;
      showIndexFooter(false);
      setGridChecks(0);
      var visibleCnt = Math.min(activePhotos.length, Math.max(1, Math.floor(activePhotos.length * progress01)));
      activePhotos.forEach(function (p, i) {
        var c = cellMap[p.id];
        if (!c) return;
        c.hidden = i >= visibleCnt;
        c.classList.remove('is-dim', 'is-evidence');
      });
    }

    function setPanel1Describe(localMs, durationMs, spotlightIds) {
      closeDrawer();
      p1CountsEl.hidden = false;
      p1NarrowEl.hidden = true;
      clearGridHighlights();
      var ratio = durationMs > 0 ? Math.min(1, Math.max(0, localMs / durationMs)) : 1;
      var checkedNow = Math.round(describedPhotos.length * ratio);
      setGridChecks(checkedNow);
      p1StatusEl.textContent = isDocCase
        ? ('글자를 뽑은 문서 ' + checkedNow + ' / ' + describedPhotos.length)
        : ('설명을 적은 사진 ' + checkedNow + ' / ' + activePhotos.length);
      p1StatusEl.hidden = false;

      var spots = (spotlightIds && spotlightIds.length) ? spotlightIds : (activePhotos[0] ? [activePhotos[0].id] : []);
      if (spots.length) {
        var slotDur = durationMs / spots.length;
        var sIdx = Math.min(spots.length - 1, Math.max(0, Math.floor(localMs / slotDur)));
        renderSpotlightCard(spots[sIdx], localMs - sIdx * slotDur, slotDur);
      } else {
        p1SpotEl.hidden = true;
      }
      showIndexFooter(isDocCase || ratio >= 0.98);
    }

    function setPanel1Ready() {
      narrowActive = false;
      activeDayFilter = null;
      p1CountsEl.hidden = false;
      p1NarrowEl.hidden = true;
      p1SpotEl.hidden = true;
      clearGridHighlights();
      setGridChecks(describedPhotos.length);
      p1StatusEl.textContent = isDocCase
        ? ('글자를 뽑은 문서 ' + describedPhotos.length + ' / ' + describedPhotos.length)
        : ('설명을 적은 사진 ' + describedPhotos.length + ' / ' + activePhotos.length);
      p1StatusEl.hidden = false;
      showIndexFooter(true);
    }

    // ① 좁히기
    function formatNarrowLabel(tpl, ev) {
      var out = String(tpl || '');
      var evid = (ev && ev.evidence) || {};
      if (out.indexOf('{date}') >= 0) {
        var d = evid.date;
        var dStr = Array.isArray(d) ? d.map(Bus.fmtKoDate).join(', ') : Bus.fmtKoDate(d || '');
        out = out.replace('{date}', dStr);
      }
      if (out.indexOf('{cutoff}') >= 0) {
        out = out.replace('{cutoff}', Bus.fmtTimeHM(evid.cutoff || ''));
      }
      return out;
    }

    function updateNarrowDisplay() {
      var activeParts = narrowStepsState.filter(function (s) { return s.matched; });
      if (!activeParts.length) {
        p1NarrowEl.hidden = true;
        return;
      }
      p1NarrowEl.textContent = activeParts.map(function (s) {
        return s.text + ' ' + s.count;
      }).join(' → ');
      p1NarrowEl.hidden = false;
    }

    function startQuestionNarrow(rules) {
      setPanel1Ready();
      narrowActive = true;
      narrowHadEvidenceRule = false;
      narrowRules = rules || [];
      narrowStepsState = narrowRules.map(function (r) {
        if (r.on === 'start') {
          var allCnt = isDocCase ? (docCatalog.total_docs || describedPhotos.length) : activePhotos.length;
          return { rule: r, matched: true, text: r.label, count: allCnt };
        }
        return { rule: r, matched: false, text: r.label, count: 0 };
      });
      updateNarrowDisplay();
    }

    function doesEventMatchRule(cond, ev, isFirstEvent, hasAnyAnswerInRun) {
      if (!cond || !ev) return false;
      if (cond === 'first_event') return Boolean(isFirstEvent);
      if (cond === 'first_answer') {
        if (ev.type === 'answer') return true;
        if (!hasAnyAnswerInRun && ev.type === 'result') return true;
        return false;
      }
      if (cond === 'result') return ev.type === 'result';
      if (typeof cond === 'object' && cond.card) {
        if (ev.type !== 'card') return false;
        if (cond.card !== '*' && ev.kind !== cond.card) return false;
        if (cond.has) {
          var evid = ev.evidence;
          if (!evid || typeof evid !== 'object') return false;
          var val = evid[cond.has];
          if (val == null) return false;
          if (Array.isArray(val) && val.length === 0) return false;
          if (val === '') return false;
        }
        return true;
      }
      return false;
    }

    function handleNarrowEvent(ev, isBranch, isFirstEvent, hasAnyAnswerInRun) {
      if (isBranch) return;
      if (ev.type === 'card' && ev.evidence && Array.isArray(ev.evidence.photo_ids)) {
        markEvidencePhotos(ev.evidence.photo_ids);
      }
      narrowStepsState.forEach(function (st) {
        if (st.matched) return;
        var r = st.rule;
        if (!doesEventMatchRule(r.on, ev, isFirstEvent, hasAnyAnswerInRun)) return;
        st.matched = true;
        st.text = formatNarrowLabel(r.label, ev);
        var bright = {};
        if (r.set === 'day') {
          var rawD = (ev.evidence || {}).date;
          var dList = Array.isArray(rawD) ? rawD : (rawD ? [rawD] : []);
          var dSet = {};
          dList.forEach(function (x) { dSet[String(x)] = true; });
          var cnt = 0;
          activePhotos.forEach(function (p) {
            if (p.date && dSet[p.date]) { bright[p.id] = true; cnt++; }
          });
          st.count = cnt;
          highlightBrightSet(bright);
        } else if (r.set === 'evidence') {
          var evid = ev.evidence || {};
          var pids = evid.photo_ids || [];
          pids.forEach(function (id) { bright[id] = true; });
          st.count = evid.to_count != null ? evid.to_count : pids.length;
          narrowHadEvidenceRule = true;
          highlightBrightSet(bright);
        } else if (r.set === 'result') {
          var items = ev.items || [];
          items.forEach(function (it) {
            var id = typeof it === 'string' ? it : (it && it.id);
            if (id) bright[id] = true;
          });
          st.count = items.length;
          highlightBrightSet(bright);
        }
      });

      // evidence 단계가 켜진 뒤 result 가 오면 밝게 둔 자료를 result 의 items 로 바꾼다
      if (ev.type === 'result' && narrowHadEvidenceRule) {
        var resBright = {};
        (ev.items || []).forEach(function (it) {
          var id = typeof it === 'string' ? it : (it && it.id);
          if (id) resBright[id] = true;
        });
        highlightBrightSet(resBright);
      }
      updateNarrowDisplay();
    }

    // ---------------------------------------------------------------- ② 에이전트 파이프라인
    var p2RowSpecs = [
      {
        key: 'groups',
        name: '같은 자리 묶기',
        desc: '찍은 곳이 150m 안으로 이어진 사진을 한 자리로 묶어요',
        clickable: true
      },
      {
        key: 'visits',
        name: '같은 방문 묶기',
        desc: '같은 날 같은 자리에서 90분 안쪽 간격으로 찍은 사진을 한 방문으로 묶어요',
        clickable: false
      },
      {
        key: 'named',
        name: '지명 붙이기',
        desc: '자리마다 사람들이 흔히 부르는 지명을 붙여요',
        clickable: true
      },
      {
        key: 'links',
        name: '받은 사진 잇기',
        desc: '받은 사진을 같은 자리에서 찍은 사진과 이어요',
        clickable: false
      },
      {
        key: 'timeline',
        name: '하루 타임라인',
        desc: '날짜마다 그날 찍은 사진을 시각 순서로 한 줄씩 적어요',
        clickable: true
      }
    ];

    var p2RowEls = [];

    function buildPanel2Rows() {
      p2RowsEl.textContent = '';
      p2RowEls = p2RowSpecs.map(function (sp) {
        var row = Bus.el('div', 'db-p2-row' + (sp.clickable ? ' is-clickable' : ''));
        row.appendChild(Bus.el('span', 'db-p2-name', sp.name));
        row.appendChild(Bus.el('span', 'db-p2-desc', sp.desc));
        var val = Bus.el('span', 'db-p2-val');
        row.appendChild(val);
        if (sp.clickable) {
          row.addEventListener('click', function (e) {
            e.stopPropagation();
            if (currentMode !== 'try') return;
            togglePanel2Popover(sp.key);
          });
        }
        p2RowsEl.appendChild(row);
        return { spec: sp, el: row, valEl: val };
      });
      p2FootEl.textContent = '↓ 사진 설명과 함께 Hindsight에 기억으로 넣어요';
    }

    function formatPanel2Value(key, ratio01) {
      var r = Math.min(1, Math.max(0, ratio01));
      if (key === 'groups') {
        if (pipeData.groups == null) return { text: '지명 파일이 없어 건너뛰었어요', muted: true };
        var g = Math.round(pipeData.groups * r);
        var p = Math.round((pipeData.photos_in_groups || 0) * r);
        return { text: g + '곳 · 사진 ' + p + '장', muted: false };
      }
      if (key === 'visits') {
        if (pipeData.visits == null) return { text: '지명 파일이 없어 건너뛰었어요', muted: true };
        return { text: Math.round(pipeData.visits * r) + '번', muted: false };
      }
      if (key === 'named') {
        if (pipeData.named == null) return { text: '지명 파일이 없어 건너뛰었어요', muted: true };
        return { text: Math.round(pipeData.named * r) + '곳', muted: false };
      }
      if (key === 'links') {
        if (pipeData.links == null) return { text: '이번에는 돌리지 않았어요', muted: true };
        return { text: Math.round(pipeData.links * r) + '장', muted: false };
      }
      if (key === 'timeline') {
        if (!pipeData.timeline_days) return { text: '기억 백업에 하루 타임라인이 없어요', muted: true };
        return { text: Math.round(pipeData.timeline_days * r) + '일', muted: false };
      }
      return { text: '', muted: true };
    }

    function updatePanel2FootText() {
      p2FootEl.textContent = isDocCase
        ? '사진 쪽 준비 단계예요. 문서는 이 차례를 거치지 않고 글자를 뽑아 바로 기억 저장소에 넣어요.'
        : '↓ 사진 설명과 함께 Hindsight에 기억으로 넣어요';
    }

    function setPanel2Empty() {
      if (isDocCase) {
        setPanel2Done();
        return;
      }
      p2PopEl.hidden = true;
      p2RowEls.forEach(function (item) {
        item.el.hidden = true;
        item.el.classList.add('is-off');
      });
      p2FootEl.hidden = true;
    }

    function setPanel2Progress(localMs, durationMs) {
      p2PopEl.hidden = true;
      updatePanel2FootText();
      var stepDur = durationMs / 5;
      p2RowEls.forEach(function (item, idx) {
        var rowStart = idx * stepDur;
        if (localMs < rowStart) {
          item.el.hidden = true;
          item.el.classList.add('is-off');
          return;
        }
        item.el.hidden = false;
        item.el.classList.remove('is-off');
        var countRatio = Math.min(1, Math.max(0, (localMs - rowStart) / 800));
        var fv = formatPanel2Value(item.spec.key, countRatio);
        item.valEl.textContent = fv.text;
        item.valEl.classList.toggle('is-muted', fv.muted);
      });
      p2FootEl.hidden = localMs < durationMs * 0.9;
    }

    function setPanel2Done() {
      p2PopEl.hidden = true;
      updatePanel2FootText();
      p2RowEls.forEach(function (item) {
        item.el.hidden = false;
        item.el.classList.remove('is-off');
        var fv = formatPanel2Value(item.spec.key, 1);
        item.valEl.textContent = fv.text;
        item.valEl.classList.toggle('is-muted', fv.muted);
      });
      p2FootEl.hidden = false;
    }

    function togglePanel2Popover(kindKey) {
      if (!p2PopEl.hidden && p2PopEl.dataset.kind === kindKey) {
        p2PopEl.hidden = true;
        return;
      }
      p2PopEl.textContent = '';
      p2PopEl.dataset.kind = kindKey;
      if (kindKey === 'groups' || kindKey === 'named') {
        var groups = pipeData.top_groups || [];
        if (!groups.length) {
          p2PopEl.appendChild(Bus.el('div', 'db-pop-item', '지명 파일이 없어 건너뛰었어요'));
        } else {
          groups.forEach(function (g) {
            p2PopEl.appendChild(
              Bus.el('div', 'db-pop-item', g.label + ' · 사진 ' + g.photos + '장 · ' + g.days + '일')
            );
          });
        }
      } else if (kindKey === 'timeline') {
        var days = pipeData.days || [];
        if (!days.length) {
          p2PopEl.appendChild(Bus.el('div', 'db-pop-item', '기억 백업에 하루 타임라인이 없어요'));
        } else {
          days.forEach(function (d) {
            var btn = Bus.el('button', 'db-pop-item is-btn', Bus.fmtKoDate(d.date) + ' · 사진 ' + d.photos + '장');
            btn.type = 'button';
            if (activeDayFilter === d.date) btn.classList.add('is-on');
            btn.addEventListener('click', function () {
              if (narrowActive) return;
              if (activeDayFilter === d.date) {
                activeDayFilter = null;
                clearGridHighlights();
                btn.classList.remove('is-on');
              } else {
                activeDayFilter = d.date;
                var allBtns = p2PopEl.querySelectorAll('.db-pop-item');
                for (var i = 0; i < allBtns.length; i++) allBtns[i].classList.remove('is-on');
                btn.classList.add('is-on');
                var bright = {};
                activePhotos.forEach(function (p) {
                  if (p.date === d.date) bright[p.id] = true;
                });
                highlightBrightSet(bright);
              }
            });
            p2PopEl.appendChild(btn);
          });
        }
      }
      p2PopEl.hidden = false;
    }

    // ---------------------------------------------------------------- ④ 질문에 답하는 과정
    function renderToolStrip(sceneKind) {
      p4ToolsEl.textContent = '';
      toolChipMap = {};
      var list = BASE_TOOLS.slice();
      if (sceneKind === 'document') {
        DOC_EXTRA_TOOLS.forEach(function (t) { list.push([t[0], t[1], true]); });
      }
      list.forEach(function (item) {
        var code = item[0];
        var ko = item[1];
        var isDoc = item[2];
        var chip = Bus.el('span', 'db-tool-chip', ko + ' · ' + code);
        if (isDoc) {
          chip.appendChild(document.createTextNode(' '));
          chip.appendChild(Bus.el('span', 'db-tool-tag', '문서 단계'));
        }
        if (usedTools[code]) chip.classList.add('used');
        toolChipMap[code] = chip;
        p4ToolsEl.appendChild(chip);
      });
    }

    function markToolUsed(code) {
      usedTools[code] = true;
      if (toolChipMap[code]) toolChipMap[code].classList.add('used');
    }

    function setCurrentLine(lineEl) {
      if (currentDotLine) currentDotLine.classList.remove('is-current');
      currentDotLine = lineEl;
      if (lineEl) {
        lineEl.classList.add('is-current');
        p4LogEl.scrollTop = p4LogEl.scrollHeight;
      }
    }

    function ensureTurn() {
      if (currentTurnEl) return currentTurnEl;
      var t = Bus.el('div', 'db-p4-turn');
      var head = Bus.el('div', 'db-p4-turn-head');
      var left = Bus.el('span', null, 'AI 모델이 다음 할 일을 골라요');
      var costSpan = Bus.el('span', null, '');
      head.appendChild(left);
      head.appendChild(costSpan);
      t.appendChild(head);
      p4LogEl.appendChild(t);
      currentTurnEl = t;
      currentTurnCostEl = costSpan;
      currentTurnReflectSubEl = null;
      return t;
    }

    function openNewTurn() {
      currentTurnEl = null;
      currentTurnCostEl = null;
      currentTurnReflectSubEl = null;
      return ensureTurn();
    }

    function appendTurnLine(text, extraCls) {
      var turn = ensureTurn();
      var ln = Bus.el('div', 'db-p4-line' + (extraCls ? ' ' + extraCls : ''));
      ln.appendChild(Bus.el('span', 'db-p4-dot'));
      var txtEl = Bus.el('span', 'db-p4-txt', text);
      ln.appendChild(txtEl);
      turn.appendChild(ln);
      setCurrentLine(ln);
      return { line: ln, textEl: txtEl };
    }

    function buildWebDetailBlock(ev) {
      var box = Bus.el('div', 'db-p4-web');
      var d = (ev.data && typeof ev.data === 'object') ? ev.data : {};
      var evs = Array.isArray(d.events) ? d.events : [];
      var headLine = Bus.el('div');

      if (!ev.cached) {
        headLine.appendChild(Bus.el('span', 'db-p4-web-tag', '실제 검색'));
        var seenHost = {};
        var hosts = [];
        evs.forEach(function (item) {
          var h = extractHostname(item && item.url);
          if (h && !seenHost[h]) { seenHost[h] = true; hosts.push(h); }
        });
        var msg = '이 실행에서 웹을 실제로 검색했어요 · 찾은 행사 ' + evs.length + '건 · 출처 ' + hosts.length + '곳';
        if (hosts.length) {
          msg += '(' + hosts.slice(0, 3).join(', ') + ')';
        }
        if (currentRecordKind === '저장한 실행' && currentRunObj && Array.isArray(currentRunObj.calls)) {
          var reqSum = 0;
          currentRunObj.calls.forEach(function (c) {
            if (c && c.purpose === 'search_web' && c.web_search_requests) {
              reqSum += Number(c.web_search_requests) || 0;
            }
          });
          if (reqSum > 0) msg += ' · 검색 ' + reqSum + '번';
        }
        headLine.appendChild(document.createTextNode(msg));
      } else {
        headLine.appendChild(Bus.el('span', 'db-p4-web-tag', '미리 찾아 둔 결과'));
        headLine.appendChild(document.createTextNode('미리 찾아 둔 웹 결과로 답했어요 · 행사 ' + evs.length + '건'));
      }
      box.appendChild(headLine);

      evs.forEach(function (item) {
        item = item || {};
        var nm = item.name || item.venue || '';
        var rg = fmtRange(item.start, item.end);
        var parts = [nm, rg].filter(Boolean);
        var lineTxt = parts.join(' · ');
        if (item.status === 'upcoming') {
          lineTxt = lineTxt ? (lineTxt + ' · 예정') : '예정';
        }
        if (lineTxt) box.appendChild(Bus.el('div', null, lineTxt));
      });
      return box;
    }

    function computeReflectCostSuffix(reflectList) {
      var withUsage = (reflectList || []).filter(function (r) { return r && r.usage != null; });
      if (!withUsage.length) return '';
      var hasUnknown = withUsage.some(function (r) { return r.usage.cost_usd == null; });
      if (hasUnknown) return ' · Hindsight 되짚기 비용 모름';
      var sum = 0;
      withUsage.forEach(function (r) { sum += Number(r.usage.cost_usd) || 0; });
      return ' · Hindsight 되짚기 $' + sum.toFixed(2);
    }

    function updatePanel4Footer() {
      p4FootEl.textContent = '';
      if (currentRecordKind === '대본') {
        p4FootEl.textContent = '대본이라 걸린 시간과 비용이 없어요';
        return;
      }
      if (currentRecordKind === '설계 미리보기') {
        p4FootEl.textContent = '설계 미리보기라 걸린 시간과 비용이 없어요';
        return;
      }
      if (currentRecordKind === '저장한 실행' && currentRunObj && currentRunObj.summary) {
        var s = currentRunObj.summary;
        var sec = ((Number(currentRunObj.elapsed_ms) || 0) / 1000).toFixed(1);
        var cost = Number(s.cost_usd || 0).toFixed(2);
        var runReflects = (currentRunObj.events || []).filter(function (e) { return e && e.type === 'reflect'; });
        var txt = 'AI 모델 호출 ' + s.calls + '번 · ' + sec + '초 · $' + cost +
          computeReflectCostSuffix(runReflects) +
          ' · 실행 ' + (currentRunObj.run_id || '');
        if (currentRunObj.today) {
          txt += ' · 기준일 ' + currentRunObj.today;
        }
        var btn = Bus.el('button', 'db-p4-foot-btn', txt);
        btn.type = 'button';
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          toggleCallsTable();
        });
        p4FootEl.appendChild(btn);
        return;
      }
      if (currentRecordKind === '실시간') {
        if (!lastLiveProgress || lastLiveProgress.calls == null || lastLiveProgress.cost_usd == null) {
          p4FootEl.textContent = liveRunId ? ('실행 ' + liveRunId) : '';
          return;
        }
        var lSec = ((Number(lastLiveProgress.elapsed_ms) || 0) / 1000).toFixed(1);
        var lCost = Number(lastLiveProgress.cost_usd || 0).toFixed(2);
        p4FootEl.textContent =
          'AI 모델 호출 ' + lastLiveProgress.calls + '번 · ' + lSec + '초 · $' + lCost +
          computeReflectCostSuffix(receivedReflects) +
          ' · 마지막 진행 표시까지 · 실행 ' + (liveRunId || '');
      }
    }

    function toggleCallsTable() {
      if (!p4CallsEl.hidden) {
        p4CallsEl.hidden = true;
        return;
      }
      if (!currentRunObj) return;
      p4CallsEl.textContent = '';
      var tbl = Bus.el('table', 'db-calls-table');
      var thead = Bus.el('thead');
      var hr = Bus.el('tr');
      hr.appendChild(Bus.el('th', null, '쓰임새'));
      hr.appendChild(Bus.el('th', null, '시간'));
      hr.appendChild(Bus.el('th', null, '비용'));
      thead.appendChild(hr);
      tbl.appendChild(thead);

      var tbody = Bus.el('tbody');
      (currentRunObj.calls || []).forEach(function (c) {
        var tr = Bus.el('tr');
        var pLabel = CALL_PURPOSE_KO[c.purpose] || c.purpose || '';
        tr.appendChild(Bus.el('td', null, pLabel));
        tr.appendChild(Bus.el('td', null, ((Number(c.ms) || 0) / 1000).toFixed(1) + '초'));
        tr.appendChild(Bus.el('td', null, '$' + Number(c.cost_usd || 0).toFixed(4)));
        tbody.appendChild(tr);
      });

      var runReflects = (currentRunObj.events || []).filter(function (e) {
        return e && e.type === 'reflect' && e.usage != null;
      });
      runReflects.forEach(function (rf) {
        var u = rf.usage;
        var tr = Bus.el('tr');
        var pTxt = u.cost_known === false
          ? '기억 되짚기(Hindsight 안의 AI 모델 호출 수 모름)'
          : '기억 되짚기(Hindsight 안의 AI 모델 ' + u.calls + '번)';
        var tTxt = ((Number(u.ms) || 0) / 1000).toFixed(1) + '초';
        var cTxt = u.cost_usd == null ? '비용을 재지 못했어요' : ('$' + Number(u.cost_usd).toFixed(2));
        tr.appendChild(Bus.el('td', null, pTxt));
        tr.appendChild(Bus.el('td', null, tTxt));
        tr.appendChild(Bus.el('td', null, cTxt));
        tbody.appendChild(tr);
      });

      tbl.appendChild(tbody);
      p4CallsEl.appendChild(tbl);
      p4CallsEl.hidden = false;
    }

    function resetPanel4(recordKind, runObj) {
      usedTools = {};
      currentTurnEl = null;
      currentTurnCostEl = null;
      currentTurnReflectSubEl = null;
      turnGroupCardMap = {};
      cardDomCounter = 0;
      currentDotLine = null;
      currentRecordKind = recordKind || '대본';
      currentRunObj = runObj || null;
      liveRunId = null;
      lastLiveProgress = null;
      receivedReflects = [];
      p4LogEl.textContent = '';
      p4FootEl.textContent = '';
      p4CallsEl.hidden = true;
      renderToolStrip(currentCase ? currentCase.scene.kind : 'photo');
    }

    function handlePanel4Event(ev, liveIdOverride) {
      if (!ev || typeof ev !== 'object') return;
      if (liveIdOverride) liveRunId = liveIdOverride;
      else if (ev.run_id && !liveRunId) liveRunId = ev.run_id;

      var t = ev.type;
      if (t === 'progress') {
        if (ev.elapsed_ms != null && ev.calls != null && ev.cost_usd != null) {
          lastLiveProgress = ev;
        }
        var step = String(ev.step || '');
        if (step === '질문을 살펴보고 다음 단계를 고르는 중이에요') {
          var newTurn = openNewTurn();
          if (ev.elapsed_ms != null && ev.cost_usd != null && currentTurnCostEl) {
            currentTurnCostEl.textContent = (ev.elapsed_ms / 1000).toFixed(1) + '초 · $' + Number(ev.cost_usd).toFixed(2);
          }
          setCurrentLine(newTurn);
          updatePanel4Footer();
          return;
        }
        if (ev.elapsed_ms != null && ev.cost_usd != null) {
          ensureTurn();
          if (currentTurnCostEl) {
            currentTurnCostEl.textContent = (ev.elapsed_ms / 1000).toFixed(1) + '초 · $' + Number(ev.cost_usd).toFixed(2);
          }
        }
        var mTool = /^([a-z0-9_]+)\s실행 중$/.exec(step);
        if (mTool) {
          var code = mTool[1];
          markToolUsed(code);
          var koName = TOOL_KO[code] || code;
          appendTurnLine(koName + ' · ' + code);
          updatePanel4Footer();
          return;
        }
        if (step === '기억 저장소에서 찾는 중이에요') {
          appendTurnLine('도구 안에서 Hindsight가 은행에서 기억을 꺼내요', 'is-sub');
        } else if (step.indexOf('기억을 되짚어 보는 중이에요') === 0) {
          var mSec = /\((\d+)초\)/.exec(step);
          var rfTxt = '도구 안에서 Hindsight가 은행의 AI 모델로 기억을 되짚어요' +
            (mSec ? ' · ' + mSec[1] + '초 지남' : '');
          if (currentTurnReflectSubEl) {
            currentTurnReflectSubEl.textEl.textContent = rfTxt;
            setCurrentLine(currentTurnReflectSubEl.line);
          } else {
            currentTurnReflectSubEl = appendTurnLine(rfTxt, 'is-sub');
          }
        } else if (step === '웹을 검색하는 중이에요') {
          appendTurnLine('도구 안에서 AI 모델이 웹을 검색해요', 'is-sub');
        } else if (step === '검색 결과를 정리하는 중이에요') {
          appendTurnLine('검색한 글에서 이름, 장소, 기간을 정리해요', 'is-sub');
        } else if (step === '설명에서 후보를 고르는 중이에요') {
          appendTurnLine('도구 안에서 AI 모델이 설명을 읽고 후보를 골라요', 'is-sub');
        } else {
          var mJudge = /^후보 사진 (\d+)장을 다시 확인하는 중이에요$/.exec(step);
          if (mJudge) {
            appendTurnLine('도구 안에서 AI 모델이 사진 ' + mJudge[1] + '장을 다시 봐요', 'is-sub');
          } else {
            appendTurnLine(step, 'is-sub');
          }
        }
        updatePanel4Footer();
        return;
      }

      if (t === 'memory') {
        var memTxt;
        if (ev.error) {
          memTxt = '기억 저장소를 쓰지 못했어요 · ' + ev.error;
        } else if (Array.isArray(ev.hits) && ev.hits.length > 0) {
          memTxt = '기억 ' + ev.hits.length + '개를 받았어요 · ' + ((Number(ev.elapsed_ms) || 0) / 1000).toFixed(1) + '초';
        } else {
          memTxt = '꺼낸 기억이 없어요';
        }
        appendTurnLine(memTxt, 'is-sub');
        return;
      }

      if (t === 'reflect') {
        receivedReflects.push(ev);
        var rfResTxt;
        var rfSec = ((Number(ev.elapsed_ms) || 0) / 1000).toFixed(1);
        if (ev.error) {
          rfResTxt = '기억 되짚기를 하지 못했어요 · ' + ev.error;
        } else {
          var pLen = Array.isArray(ev.photo_ids) ? ev.photo_ids.length : 0;
          rfResTxt = pLen > 0
            ? '사진 ' + pLen + '장을 골랐어요 · ' + rfSec + '초'
            : '되짚기가 고른 사진이 없어요 · ' + rfSec + '초';
        }
        if (ev.usage != null) {
          if (ev.usage.cost_usd != null) {
            rfResTxt += ' · Hindsight 안의 AI 모델 ' + ev.usage.calls + '번 · $' + Number(ev.usage.cost_usd).toFixed(2);
          } else {
            rfResTxt += ' · 비용을 재지 못했어요';
          }
        }
        appendTurnLine(rfResTxt, 'is-sub');
        updatePanel4Footer();
        return;
      }

      if (t === 'card' || t === 'result') {
        var keyNo = t + ':' + ev.no;
        var cardText = '카드 ' + ev.no + ' · ' + ev.title;
        var evidIds = (ev.evidence && ev.evidence.photo_ids) ? ev.evidence.photo_ids.slice() : [];
        if (turnGroupCardMap[keyNo]) {
          turnGroupCardMap[keyNo].textEl.textContent = cardText;
          turnGroupCardMap[keyNo].evidIds = evidIds;
          setCurrentLine(turnGroupCardMap[keyNo].line);
        } else {
          var domIdx = cardDomCounter++;
          var created = appendTurnLine(cardText, 'is-card');
          created.evidIds = evidIds;
          created.domIdx = domIdx;
          created.line.addEventListener('click', function () {
            if (currentMode !== 'try') return;
            if (callbacks.onFocusPhoneCard) callbacks.onFocusPhoneCard(created.domIdx);
            if (created.evidIds && created.evidIds.length) markEvidencePhotos(created.evidIds);
          });
          turnGroupCardMap[keyNo] = created;
          if (t === 'card' && ev.kind === 'web') {
            ensureTurn().appendChild(buildWebDetailBlock(ev));
          }
        }
        updatePanel4Footer();
        return;
      }

      if (t === 'ask') {
        markToolUsed('ask_user');
        appendTurnLine('되묻기 · ask_user');
        var askDomIdx = cardDomCounter++;
        var askCreated = appendTurnLine('카드 ' + ev.no + ' · ' + ev.question, 'is-card');
        askCreated.line.addEventListener('click', function () {
          if (currentMode !== 'try') return;
          if (callbacks.onFocusPhoneCard) callbacks.onFocusPhoneCard(askDomIdx);
        });
        return;
      }

      if (t === 'done') {
        if (ev.status === 'waiting') {
          appendTurnLine('사용자의 답을 기다려요');
        } else if (ev.status === 'answered') {
          appendTurnLine('끝났어요');
        } else {
          appendTurnLine('답을 내지 못하고 끝났어요');
        }
        updatePanel4Footer();
        return;
      }

      if (t === 'answer') {
        turnGroupCardMap = {};
        currentTurnEl = null;
        var ansRow = Bus.el('div', 'db-p4-turn');
        ansRow.appendChild(Bus.el('div', 'db-p4-line', '사용자가 골랐어요: ' + (ev.label || '')));
        p4LogEl.appendChild(ansRow);
        setCurrentLine(ansRow);
        return;
      }

      if (t === 'error') {
        appendTurnLine('문제가 생겼어요: ' + (ev.message || ''));
        updatePanel4Footer();
      }
    }

    // ---------------------------------------------------------------- 마무리 표
    function buildOutroTable(recordKind, runObj) {
      outroCardEl.textContent = '';
      outroCardEl.appendChild(
        Bus.el('div', 'demo-outro-title', '질문 처리는 실시간이고, 사진 설명은 미리 만들어 둡니다')
      );
      var kindDesc = '설계 문서의 값으로 꾸민 대본';
      if (recordKind === '저장한 실행') kindDesc = '시험에서 통과한 실제 실행을 저장한 것';
      else if (recordKind === '설계 미리보기') kindDesc = '설계 미리보기(문서 단계는 아직 없습니다)';

      var leftRows = [
        '질문 이해, 도구 고르기, 사진 다시 보기(AI 모델)',
        '기억 꺼내기(에이전트가 질문마다 Hindsight 은행에서 꺼냅니다. AI 모델 비용은 들지 않습니다)',
        '후보 찾기, 거리와 시각 계산(노트북의 코드)',
        '되묻기와 사용자의 선택',
        '기억 되짚기(시간·순서 질문 등에서 에이전트가 Hindsight에 맡깁니다. Hindsight 안의 AI 모델 비용이 듭니다)'
      ];
      var rightRows = [
        currentCase && currentCase.scene.kind === 'document'
          ? '문서 글자: 노트북의 코드가 미리 뽑아 둔 것'
          : '사진마다 적어 둔 설명',
        'Hindsight 기억 저장소에 넣어 둔 기억(사진 설명과 하루 타임라인)',
        '같은 자리와 방문 묶음, 지명, 하루 타임라인(에이전트 파이프라인)',
        '이 화면의 실행: ' + kindDesc,
        '폰 폴더 자료는 가상 자료입니다. 관람객이 찍힌 실제 사진 ' + noServerCount + '장은 얼굴을 흐렸습니다'
      ];

      var webCard = null;
      if (runObj && Array.isArray(runObj.events)) {
        for (var i = 0; i < runObj.events.length; i++) {
          if (runObj.events[i].type === 'card' && runObj.events[i].kind === 'web') {
            webCard = runObj.events[i];
            break;
          }
        }
      }
      if (webCard) {
        if (!webCard.cached) leftRows.push('웹 검색(이 실행에서 실제로 검색했습니다)');
        else rightRows.push('웹 결과(미리 찾아 둔 결과로 답했습니다)');
      }

      var tbl = Bus.el('table', 'demo-outro-table');
      var thead = Bus.el('thead');
      var hr = Bus.el('tr');
      hr.appendChild(Bus.el('th', null, '질문할 때마다 실시간으로 도는 것'));
      hr.appendChild(Bus.el('th', null, '미리 만들어 둔 것'));
      thead.appendChild(hr);
      tbl.appendChild(thead);

      var tbody = Bus.el('tbody');
      var maxLen = Math.max(leftRows.length, rightRows.length);
      for (var r = 0; r < maxLen; r++) {
        var tr = Bus.el('tr');
        tr.appendChild(Bus.el('td', null, leftRows[r] || ''));
        tr.appendChild(Bus.el('td', null, rightRows[r] || ''));
        tbody.appendChild(tr);
      }
      tbl.appendChild(tbody);
      outroCardEl.appendChild(tbl);
    }

    // 바깥 누름·Esc 닫기
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        closeDrawer();
        p2PopEl.hidden = true;
        p4CallsEl.hidden = true;
        if (activeDayFilter && !narrowActive) {
          activeDayFilter = null;
          clearGridHighlights();
        }
      }
    });

    document.addEventListener('click', function (e) {
      if (!p1DrawerEl.hidden && !p1DrawerEl.contains(e.target)) {
        closeDrawer();
      }
      if (!p2PopEl.hidden && !p2PopEl.contains(e.target)) {
        p2PopEl.hidden = true;
      }
      if (!p4CallsEl.hidden && !p4CallsEl.contains(e.target)) {
        p4CallsEl.hidden = true;
      }
    });

    replayPrepBtn.addEventListener('click', function () {
      if (replayPrepBtn.disabled) return;
      if (callbacks.onReplayPrep) callbacks.onReplayPrep();
    });

    buildPanel2Rows();

    return {
      setCase: function (caseObj, mode) {
        currentCase = caseObj;
        currentMode = mode;
        isDocCase = Boolean(caseObj && caseObj.scene && caseObj.scene.kind === 'document');
        if (isDocCase) {
          rebuildDocGrid(caseObj);
        } else {
          var photosForCase = (caseObj.scene && caseObj.scene.only_photos)
            ? caseObj.scene.only_photos
            : catalog.photos;
          rebuildPhotoGrid(photosForCase, Boolean(caseObj.no_photos));
        }
        updatePanel2FootText();
        replayPrepBtn.hidden = (mode !== 'try') || Boolean(caseObj.scene && caseObj.scene.skip_prep);
      },
      setMode: function (mode) {
        currentMode = mode;
        replayPrepBtn.hidden = (mode !== 'try') || Boolean(currentCase && currentCase.scene && currentCase.scene.skip_prep);
      },
      setReplayPrepDisabled: function (dis) {
        replayPrepBtn.disabled = Boolean(dis);
      },
      openPhotoDrawer: openDrawer,
      setPanel1Intro: setPanel1Intro,
      setPanel1Collect: setPanel1Collect,
      setPanel1Describe: setPanel1Describe,
      setPanel1Ready: setPanel1Ready,
      startQuestionNarrow: startQuestionNarrow,
      handleNarrowEvent: handleNarrowEvent,
      setPanel2Empty: setPanel2Empty,
      setPanel2Progress: setPanel2Progress,
      setPanel2Done: setPanel2Done,
      resetPanel4: resetPanel4,
      handlePanel4Event: handlePanel4Event,
      updatePanel4Footer: updatePanel4Footer,
      buildOutroTable: buildOutroTable
    };
  };
})(window);
