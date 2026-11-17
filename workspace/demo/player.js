/* 재생기와 전체 조종기: 시계 하나(demoTimeline), 자동 재생, 직접 해 보기(실시간·저장한 실행) */
(function (global) {
  'use strict';

  var Bus = global.__AIDicBus = global.__AIDicBus || {};

  Bus.el = function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null && text !== '') node.textContent = String(text);
    return node;
  };

  Bus.normSpace = function normSpace(s) {
    return String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  };

  Bus.fmtKoDate = function fmtKoDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''));
    return m ? (m[1] + '년 ' + Number(m[2]) + '월 ' + Number(m[3]) + '일') : String(s || '');
  };

  Bus.fmtShortDate = function fmtShortDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''));
    return m ? (m[1] + '.' + Number(m[2]) + '.' + Number(m[3])) : String(s || '');
  };

  Bus.fmtTimeHM = function fmtTimeHM(s) {
    var m = /(?:^|T|\s)(\d{1,2}):(\d{2})/.exec(String(s || ''));
    if (!m) return String(s || '');
    var hh = m[1].length === 1 ? ('0' + m[1]) : m[1];
    return hh + ':' + m[2];
  };

  Bus.makeMatchKey = function makeMatchKey(text) {
    var head = String(text == null ? '' : text).split(' | When:')[0];
    var norm = head.replace(/\s+/g, ' ').trim();
    return Array.from(norm).slice(0, 100).join('');
  };

  var DELAY = {
    progress: 650,
    card: 1100,
    ask: 1100,
    answer: 2600,
    result: 1200,
    error: 700,
    done: 300,
    memory: 1100,
    reflect: 1100
  };

  var STEP_CIRCLES = ['①', '②', '③', '④', '⑤'];

  function makePlaylist(run) {
    var evs = (run.events || []).filter(function (e) { return e && typeof e === 'object'; });
    var answers = run.answers || [];
    if (!answers.length || evs.some(function (e) { return e.type === 'answer'; })) {
      return evs;
    }
    var out = [];
    var k = 0;
    for (var i = 0; i < evs.length; i++) {
      out.push(evs[i]);
      if (evs[i].type === 'ask' && answers[k] != null) {
        if (evs[i + 1] && evs[i + 1].type === 'done') out.push(evs[++i]);
        var ansItem = answers[k++];
        if (typeof ansItem === 'object' && ansItem) {
          out.push({ type: 'answer', run_id: run.run_id, seq: evs[i].seq, option_id: ansItem.option_id, label: ansItem.label });
        }
      }
    }
    return out;
  }

  function findFirstWebCard(events) {
    var evs = events || [];
    for (var i = 0; i < evs.length; i++) {
      if (evs[i] && evs[i].type === 'card' && evs[i].kind === 'web') return evs[i];
    }
    return null;
  }

  function parseUrlParams() {
    var sp = new URLSearchParams(global.location ? global.location.search : '');
    var mode = sp.get('mode') === 'try' ? 'try' : 'auto';
    var rawSpeed = Number(sp.get('speed'));
    var speed = [0.5, 1, 2, 4].indexOf(rawSpeed) >= 0 ? rawSpeed : 1;
    var rawT = sp.get('t');
    var seekT = null;
    if (mode === 'auto' && rawT !== null && rawT !== '' && /^\d+(\.\d+)?$/.test(rawT)) {
      seekT = Math.max(0, Math.round(Number(rawT)));
    }
    var rawView = sp.get('view');
    var view = 'all';
    if (mode === 'auto' && (rawView === 'phone' || rawView === 'board')) {
      view = rawView;
    }
    var rawFit = sp.get('fit');
    var fitSec = null;
    if (rawFit !== null && rawFit !== '') {
      var fn = Number(rawFit);
      if (!isNaN(fn) && fn >= 10 && fn <= 60) {
        fitSec = fn;
      }
    }
    var clean = sp.get('clean') === '1';
    var liveOff = sp.get('live') === '0';
    return {
      sceneKey: sp.get('scene') || '',
      mode: mode,
      speed: speed,
      seekT: seekT,
      view: view,
      fitSec: fitSec,
      clean: clean,
      liveOff: liveOff
    };
  }

  Bus.boot = function boot() {
    var dataEl = document.getElementById('aidic-demo-data');
    if (!dataEl) return;
    var DATA = JSON.parse(dataEl.textContent || '{}');
    var cases = DATA.cases || [];
    if (!cases.length) return;

    var urlCfg = parseUrlParams();

    // 주요 DOM 요소
    var stageEl = document.getElementById('demo-stage');
    var caseSelectEl = document.getElementById('demo-case-select');
    var modeAutoBtn = document.getElementById('demo-mode-auto');
    var modeTryBtn = document.getElementById('demo-mode-try');
    var restartBtn = document.getElementById('demo-restart');
    var playPauseBtn = document.getElementById('demo-playpause');
    var speedsWrap = document.getElementById('demo-speeds');
    var sceneBarEl = document.getElementById('demo-scene-bar');
    var serverBadgeEl = document.getElementById('demo-server-badge');
    var kindBadgeEl = document.getElementById('demo-kind-badge');
    var noticeEl = document.getElementById('demo-notice');
    var stepsEl = document.getElementById('demo-steps');

    var livePanelEl = document.getElementById('demo-live-panel');
    var liveToggleEl = document.getElementById('demo-live-toggle');
    var liveSubEl = document.getElementById('demo-live-sub');
    var liveSpentEl = document.getElementById('demo-live-spent');

    var introOverlayEl = document.getElementById('demo-intro-overlay');
    var introQueryEl = document.getElementById('demo-intro-query');
    var outroOverlayEl = document.getElementById('demo-outro-overlay');

    // 상태 변수
    var currentCaseIdx = 0;
    for (var ci = 0; ci < cases.length; ci++) {
      if (cases[ci].scene.key === urlCfg.sceneKey) {
        currentCaseIdx = ci;
        break;
      }
    }
    var currentMode = urlCfg.mode;
    var currentView = urlCfg.view;
    var currentSpeed = urlCfg.speed;
    var serverInfo = null;

    // 실시간 누적 비용 상태
    var liveRunSeq = 0;
    var liveRunCosts = {};
    var totalLiveReflectCost = 0;
    var hasUnknownReflectCost = false;

    // 자동 재생 시계 상태
    var timeline = null;
    var playing = false;
    var currentTimeMs = 0;
    var lastFrameWall = 0;
    var rafId = null;
    var appliedStateKey = '';
    var prepReplayActive = false;
    var prepReplayEndMs = 0;

    // 저장한 실행 직접 재생 상태
    var tryReplayToken = 0;
    var tryWaitingAsk = null;
    var tryActiveSteps = [];
    var tryActiveNarrow = [];
    var trySeenFirstEvent = false;
    var tryHasAnswerInRun = false;

    // 무대 크기 조절
    function updateScale() {
      var baseW = currentView === 'phone' ? 360 : 1920;
      var baseH = currentView === 'phone' ? 780 : 1080;
      var w = global.innerWidth || baseW;
      var h = global.innerHeight || baseH;
      var s = Math.min(w / baseW, h / baseH);
      stageEl.style.transform = 'scale(' + s.toFixed(5) + ')';
    }
    global.addEventListener('resize', updateScale);

    // 하위 모듈 생성
    var board = Bus.createBoard(DATA, {
      onFocusPhoneCard: function (domIdx) {
        phone.focusCardByIndex(domIdx);
      },
      onReplayPrep: function () {
        startTryPrepReplay();
      }
    });

    var constellation = Bus.createConstellation(DATA, {
      onSelectPhoto: function (photoId) {
        board.openPhotoDrawer(photoId);
      }
    });

    var phone = Bus.createPhone(DATA, {
      onSubmit: function (queryText) {
        handleTrySubmit(queryText);
      },
      onOption: function (optId, optLabel) {
        return handleTryOption(optId, optLabel);
      },
      onDone: function () { }
    });

    var live = Bus.createLive({
      onHealth: function (info) {
        serverInfo = info;
        updateServerBadge();
        updateLivePanelVisibility();
      },
      onStreamingChange: function (streaming) {
        setStreamingControlsLocked(streaming);
        if (!streaming) {
          updateLiveSpentText();
        }
      },
      onEvent: function (ev, runId) {
        handleLiveEvent(ev, runId);
      },
      onNotice: function (msg) {
        phone.systemNotice(msg);
      }
    });

    // ---------------------------------------------------------------- 위 막대·알림 줄·단계 표시
    function getCase() {
      return cases[currentCaseIdx];
    }

    function baseRecordKind(caseObj) {
      return (caseObj && caseObj.run && caseObj.run.demo && caseObj.run.demo.kind) || '저장한 실행';
    }

    function updateServerBadge() {
      if (!serverInfo) {
        serverBadgeEl.textContent = '서버 없음 · 저장해 둔 기록만 보여 줍니다';
        serverBadgeEl.classList.remove('live');
        return;
      }
      var idxPart = serverInfo.index && Number(serverInfo.index) > 0
        ? ('찾을 자료 ' + serverInfo.index + '건')
        : '찾을 자료 없음';
      var webPart = serverInfo.web_search === 'live' ? '웹 검색 실제 검색' : '웹 검색 미리 찾아 둔 결과';
      serverBadgeEl.textContent = '서버 연결됨 · ' + idxPart + ' · ' + webPart;
      serverBadgeEl.classList.add('live');
    }

    function updateNoticeAndKind(recordKind, webCardOrNull, isLiveBeforeWeb) {
      kindBadgeEl.textContent = recordKind;
      var baseMsg;
      if (recordKind === '실시간') {
        baseMsg = '지금 서버에서 실제로 실행하고 있습니다. 질문마다 AI 모델 비용이 듭니다. 사진 설명은 미리 만들어 둔 것입니다.';
      } else if (recordKind === '저장한 실행') {
        baseMsg = '시험에서 통과한 실제 실행을 저장해 다시 보여 주는 화면입니다. 사진 설명은 미리 만들어 둔 것입니다.';
      } else if (recordKind === '설계 미리보기') {
        baseMsg = '설계 미리보기입니다. 문서를 읽고 계산하는 단계는 아직 만들지 않아, 설계 문서의 값과 실제 문서 글자로 꾸몄습니다.';
      } else {
        baseMsg = '대본으로 꾸민 화면입니다. 값은 설계 문서와 폰 폴더 자료에서 가져왔고, 실제로 돌린 실행이 아닙니다. 사진 설명은 미리 만들어 둔 것입니다.';
      }

      if (webCardOrNull && !isLiveBeforeWeb) {
        baseMsg += webCardOrNull.cached
          ? ' 웹 결과는 미리 찾아 둔 것입니다.'
          : ' 웹 검색은 이 실행에서 실제로 했습니다.';
      }
      noticeEl.textContent = baseMsg;
      noticeEl.classList.toggle('is-live', recordKind === '실시간');
    }

    function updateLiveSpentText() {
      var progSum = Object.keys(liveRunCosts).reduce(function (acc, k) {
        return acc + (liveRunCosts[k] || 0);
      }, 0);
      var sum = progSum + totalLiveReflectCost;
      var txt = '이 화면에서 쓴 비용 약 $' + sum.toFixed(2);
      if (hasUnknownReflectCost) txt += ' · 되짚기 비용 일부 모름';
      liveSpentEl.textContent = txt;
    }

    function updateLivePanelVisibility() {
      var c = getCase();
      var isDoc = c && c.scene && c.scene.kind === 'document';
      if (currentMode !== 'try') {
        livePanelEl.hidden = true;
        return;
      }
      if (isDoc) {
        livePanelEl.hidden = false;
        liveToggleEl.checked = false;
        liveToggleEl.disabled = true;
        liveSubEl.textContent = '문서를 읽고 계산하는 단계가 아직 없어, 이 케이스는 설계 미리보기로 보여 줘요.';
        liveSpentEl.hidden = true;
        return;
      }
      if (serverInfo) {
        livePanelEl.hidden = false;
        liveToggleEl.disabled = live.isStreaming();
        liveSubEl.textContent = '질문마다 AI 모델 비용이 들어요';
        liveSpentEl.hidden = false;
        updateLiveSpentText();
      } else {
        livePanelEl.hidden = true;
      }
    }

    function renderLeftSteps(stepDefs, activeIdx) {
      stepsEl.textContent = '';
      if (!stepDefs || !stepDefs.length || activeIdx < 0) return;
      stepDefs.forEach(function (st, idx) {
        if (idx > 0) {
          stepsEl.appendChild(Bus.el('span', 'demo-step-arrow', '→'));
        }
        var num = STEP_CIRCLES[idx] || String(idx + 1);
        var pill = Bus.el('span', 'demo-step-pill' + (idx === activeIdx ? ' on' : ''), num + ' ' + st.label);
        stepsEl.appendChild(pill);
      });
    }

    function computeActiveStepIndex(stepDefs, seenStart, eventsProcessed, hasAnyAnswer) {
      if (!seenStart || !stepDefs || !stepDefs.length) return -1;
      var active = -1;
      for (var i = 0; i < stepDefs.length; i++) {
        var cond = stepDefs[i].on;
        if (cond === 'start') {
          active = i;
          continue;
        }
        for (var j = 0; j < eventsProcessed.length; j++) {
          var ev = eventsProcessed[j];
          var isFirst = (j === 0);
          if (cond === 'first_event' && isFirst) { active = i; break; }
          if (cond === 'first_answer') {
            if (ev.type === 'answer' || (!hasAnyAnswer && ev.type === 'result')) { active = i; break; }
          }
          if (cond === 'result' && ev.type === 'result') { active = i; break; }
          if (typeof cond === 'object' && cond.card && ev.type === 'card') {
            if (cond.card === '*' || ev.kind === cond.card) {
              if (!cond.has || (ev.evidence && ev.evidence[cond.has] != null)) {
                active = i;
                break;
              }
            }
          }
        }
      }
      return active;
    }

    // ---------------------------------------------------------------- 자동 재생 타임라인 만들기
    function buildCaseTimeline(caseObj) {
      var sc = caseObj.scene;
      var run = caseObj.run;
      var isDoc = sc.kind === 'document';
      var skipPrep = (currentView === 'phone') || Boolean(sc.skip_prep);

      var scenes = [];
      var cursor = 0;

      if (!skipPrep) {
        scenes.push({ name: '시작', start: cursor, end: cursor + 3000 });
        cursor += 3000;
        var colDur = isDoc ? 4000 : 5000;
        scenes.push({ name: '자료 모으기', start: cursor, end: cursor + colDur });
        cursor += colDur;
        var descDur = isDoc ? 12000 : 14000;
        scenes.push({ name: isDoc ? '글자 뽑기' : '미리 분석', start: cursor, end: cursor + descDur });
        cursor += descDur;
        if (!isDoc) {
          scenes.push({ name: '맥락 잇기', start: cursor, end: cursor + 6000 });
          cursor += 6000;
        }
        var saveDur = isDoc ? 6000 : 10000;
        scenes.push({ name: '기억 저장', start: cursor, end: cursor + saveDur });
        cursor += saveDur;
      }

      var qStart = cursor;
      var qChars = Array.from(run.query || '');
      var evs = makePlaylist(run);

      var rawTypingTotal = 700 + qChars.length * 55 + 650;
      var rawEvTotal = 0;
      evs.forEach(function (e) { rawEvTotal += (DELAY[e.type] || 600); });
      var rawQuestionDur = rawTypingTotal + rawEvTotal + 400;

      var ratio = 1.0;
      if (urlCfg.fitSec != null && rawQuestionDur > 0) {
        var cand = (urlCfg.fitSec * 1000) / rawQuestionDur;
        if (cand >= 0.6 && cand <= 1.6) {
          ratio = cand;
        } else if (global.console && console.info) {
          console.info('fit 비율이 허용 범위(0.6–1.6)를 벗어나 기본 길이를 씁니다:', cand.toFixed(2));
        }
      }

      var typeStartAt = qStart + Math.round(700 * ratio);
      var bubbleAt = qStart + Math.round((700 + qChars.length * 55 + 650) * ratio);
      var tapSendAt = Math.max(qStart, bubbleAt - Math.round(250 * ratio));

      var scheduledEvents = [];
      var evCursor = bubbleAt;
      var answerTaps = [];

      evs.forEach(function (ev) {
        var wait = Math.round((DELAY[ev.type] || 600) * ratio);
        if (ev.type === 'answer') {
          var tapAt = evCursor + Math.round(1600 * ratio);
          answerTaps.push({ t: tapAt, option_id: ev.option_id });
        }
        evCursor += wait;
        scheduledEvents.push({
          seq: ev.seq,
          type: ev.type,
          kind: ev.kind || null,
          t: evCursor,
          ev: ev
        });
      });

      var hasAgentMem = evs.some(function (e) { return e.type === 'memory'; });
      var firstGoodMem = null;
      for (var mi = 0; mi < scheduledEvents.length; mi++) {
        var mev = scheduledEvents[mi].ev;
        if (mev.type === 'memory' && !mev.error && Array.isArray(mev.hits) && mev.hits.length > 0) {
          firstGoodMem = scheduledEvents[mi].t;
          break;
        }
      }

      var hasValidCompare = false;
      if (!hasAgentMem && caseObj.recall) {
        var activeConstObj = (isDoc && DATA.constellation_docs) ? DATA.constellation_docs : DATA.constellation;
        var bankMatch = !caseObj.recall.bank || !activeConstObj.bank || caseObj.recall.bank === activeConstObj.bank;
        var qMatch = Bus.normSpace(caseObj.recall.query) === Bus.normSpace(run.query);
        hasValidCompare = bankMatch && qMatch;
      }

      var recallTime = null;
      var compareRevealAt = null;
      if (firstGoodMem != null) {
        recallTime = firstGoodMem;
      } else if (hasValidCompare) {
        compareRevealAt = bubbleAt + Math.round(1500 * ratio);
        recallTime = compareRevealAt;
      }

      var qEnd = evCursor + Math.round(400 * ratio);
      if (compareRevealAt != null && compareRevealAt + 300 > qEnd) {
        qEnd = compareRevealAt + 300;
      }
      scenes.push({ name: '질문하기', start: qStart, end: qEnd });
      cursor = qEnd;

      var outroStart = null;
      var outroRelTapAt = null;
      var outroTableAt = null;
      if (currentView !== 'phone') {
        var outroDur = isDoc ? 6000 : 8000;
        outroStart = cursor;
        outroRelTapAt = outroStart + 1500;
        outroTableAt = outroStart + outroDur - 4000;
        scenes.push({ name: '마무리', start: outroStart, end: outroStart + outroDur });
        cursor += outroDur;
      }

      global.demoTimeline = {
        scenes: scenes.map(function (s) { return { name: s.name, start: s.start, end: s.end }; }),
        events: scheduledEvents.map(function (se) {
          return { seq: se.seq, type: se.type, kind: se.kind, t: se.t };
        }),
        recall: recallTime
      };

      return {
        scenes: scenes,
        totalDuration: cursor,
        qStart: qStart,
        qEnd: qEnd,
        typeStartAt: typeStartAt,
        charStepMs: 55 * ratio,
        queryChars: qChars,
        tapSendAt: tapSendAt,
        bubbleAt: bubbleAt,
        compareRevealAt: compareRevealAt,
        hasValidCompare: hasValidCompare,
        hasAgentMem: hasAgentMem,
        scheduledEvents: scheduledEvents,
        answerTaps: answerTaps,
        outroStart: outroStart,
        outroRelTapAt: outroRelTapAt,
        outroTableAt: outroTableAt
      };
    }

    function buildSceneButtons() {
      sceneBarEl.textContent = '';
      if (!timeline) return;
      timeline.scenes.forEach(function (s) {
        var btn = Bus.el('button', 'demo-scene-btn', s.name);
        btn.type = 'button';
        btn.dataset.sceneName = s.name;
        btn.addEventListener('click', function () {
          if (currentMode !== 'auto' || btn.disabled) return;
          seekAutoTo(s.start, playing);
        });
        sceneBarEl.appendChild(btn);
      });
    }

    function updateSceneButtonHighlight(ms) {
      var btns = sceneBarEl.querySelectorAll('.demo-scene-btn');
      if (!timeline) return;
      var activeName = timeline.scenes.length ? timeline.scenes[timeline.scenes.length - 1].name : '';
      for (var i = 0; i < timeline.scenes.length; i++) {
        var s = timeline.scenes[i];
        if (ms >= s.start && ms < s.end) {
          activeName = s.name;
          break;
        }
      }
      for (var j = 0; j < btns.length; j++) {
        btns[j].classList.toggle('on', btns[j].dataset.sceneName === activeName);
      }
    }

    // ---------------------------------------------------------------- 자동 재생 시각 t 상태 적용 (완전 결정적)
    var lastRenderedEventCount = 0;
    var lastSentBubble = false;
    var lastRevealedCompare = false;
    var lastOpenedOutroRel = false;
    var lastTapSendFired = false;
    var lastAnswerTapCount = 0;
    var currentPhaseTag = '';

    function resetForFullDeterministicApply() {
      var c = getCase();
      var rk = baseRecordKind(c);
      phone.reset();
      phone.setCase(c, false);
      phone.setModeAndView(currentMode, currentView, rk);
      board.resetPanel4(rk, c.run);
      board.buildOutroTable(rk, c.run);
      introOverlayEl.hidden = true;
      outroOverlayEl.hidden = true;
      lastRenderedEventCount = 0;
      lastSentBubble = false;
      lastRevealedCompare = false;
      lastOpenedOutroRel = false;
      lastTapSendFired = false;
      lastAnswerTapCount = 0;
      currentPhaseTag = '';
    }

    function findSceneByName(name) {
      if (!timeline) return null;
      for (var i = 0; i < timeline.scenes.length; i++) {
        if (timeline.scenes[i].name === name) return timeline.scenes[i];
      }
      return null;
    }

    function renderAutoAt(ms, isInstantSeek) {
      if (!timeline) return;
      var c = getCase();
      var sc = c.scene;
      var run = c.run;
      var rk = baseRecordKind(c);
      var clamped = Math.max(0, Math.min(timeline.totalDuration, ms));
      currentTimeMs = clamped;

      if (prepReplayActive && clamped >= prepReplayEndMs) {
        stopLoop();
        prepReplayActive = false;
        enterTryIdleState();
        return;
      }

      updateSceneButtonHighlight(clamped);

      var sIntro = findSceneByName('시작');
      var sCollect = findSceneByName('자료 모으기');
      var sDesc = findSceneByName('미리 분석') || findSceneByName('글자 뽑기');
      var sPipe = findSceneByName('맥락 잇기');
      var sSave = findSceneByName('기억 저장');

      if (isInstantSeek) {
        stageEl.classList.add('demo-noanim');
        resetForFullDeterministicApply();
      }

      // 1) 시작 장면
      if (sIntro && clamped < sIntro.end) {
        if (currentPhaseTag !== 'intro') {
          currentPhaseTag = 'intro';
          phone.reset();
          renderLeftSteps(sc.steps, -1);
          board.setPanel1Intro();
          board.setPanel2Empty();
          constellation.setEmpty();
          board.resetPanel4(rk, run);
          outroOverlayEl.hidden = true;
        }
        introQueryEl.textContent = run.query || '';
        introOverlayEl.hidden = clamped >= (sIntro.start + 2000);
        if (isInstantSeek) restoreAnimNextFrame();
        return;
      }
      introOverlayEl.hidden = true;

      // 2) 자료 모으기 장면
      if (sCollect && clamped >= sCollect.start && clamped < sCollect.end) {
        currentPhaseTag = 'collect';
        renderLeftSteps(sc.steps, -1);
        var colProg = (clamped - sCollect.start) / (sCollect.end - sCollect.start);
        board.setPanel1Collect(colProg);
        board.setPanel2Empty();
        constellation.setEmpty();
        outroOverlayEl.hidden = true;
        if (isInstantSeek) restoreAnimNextFrame();
        return;
      }

      // 3) 미리 분석 장면
      if (sDesc && clamped >= sDesc.start && clamped < sDesc.end) {
        currentPhaseTag = 'desc';
        renderLeftSteps(sc.steps, -1);
        board.setPanel1Describe(clamped - sDesc.start, sDesc.end - sDesc.start, sc.spotlight);
        board.setPanel2Empty();
        constellation.setEmpty();
        outroOverlayEl.hidden = true;
        if (isInstantSeek) restoreAnimNextFrame();
        return;
      }

      // 4) 맥락 잇기 장면
      if (sPipe && clamped >= sPipe.start && clamped < sPipe.end) {
        if (currentPhaseTag !== 'pipe') {
          currentPhaseTag = 'pipe';
          renderLeftSteps(sc.steps, -1);
          board.setPanel1Ready();
          constellation.setEmpty();
          outroOverlayEl.hidden = true;
        }
        board.setPanel2Progress(clamped - sPipe.start, sPipe.end - sPipe.start);
        if (isInstantSeek) restoreAnimNextFrame();
        return;
      }

      // 5) 기억 저장 장면
      if (sSave && clamped >= sSave.start && clamped < sSave.end) {
        if (currentPhaseTag !== 'save') {
          currentPhaseTag = 'save';
          renderLeftSteps(sc.steps, -1);
          board.setPanel1Ready();
          board.setPanel2Done();
          outroOverlayEl.hidden = true;
        }
        var saveProg = (clamped - sSave.start) / (sSave.end - sSave.start);
        constellation.setSavingProgress(saveProg);
        if (isInstantSeek) restoreAnimNextFrame();
        return;
      }

      // 6) 질문하기 & 7) 마무리 장면
      if (currentPhaseTag !== 'question') {
        currentPhaseTag = 'question';
        board.startQuestionNarrow(sc.narrow);
        board.setPanel2Done();
        constellation.setReadyAll();
        board.resetPanel4(rk, run);
      }

      // 타자 및 보내기
      if (clamped < timeline.bubbleAt) {
        if (clamped >= timeline.typeStartAt) {
          var typedCnt = Math.min(
            timeline.queryChars.length,
            Math.max(1, Math.floor((clamped - timeline.typeStartAt) / Math.max(1, timeline.charStepMs)) + 1)
          );
          phone.setTypingText(timeline.queryChars.slice(0, typedCnt).join(''));
        } else {
          phone.setTypingText('');
        }
        if (!isInstantSeek && !lastTapSendFired && clamped >= timeline.tapSendAt) {
          lastTapSendFired = true;
          phone.tapSend();
        }
      } else if (!lastSentBubble) {
        lastSentBubble = true;
        if (!isInstantSeek && !lastTapSendFired) {
          lastTapSendFired = true;
          phone.tapSend();
        }
        phone.userBubble(run.query || '');
        constellation.startQuestion(run.query || '', rk, !timeline.hasAgentMem && timeline.hasValidCompare);
      }

      // 되묻기 선택 누름 표시
      if (!isInstantSeek) {
        while (lastAnswerTapCount < timeline.answerTaps.length && clamped >= timeline.answerTaps[lastAnswerTapCount].t) {
          phone.tapOption(timeline.answerTaps[lastAnswerTapCount].option_id);
          lastAnswerTapCount++;
        }
      }

      var hasAnyAnswer = timeline.scheduledEvents.some(function (se) { return se.ev.type === 'answer'; });

      // 비교용 기억 표시 시각이 이벤트 사이에 올 수 있으므로 시간 순서대로 처리
      while (lastRenderedEventCount < timeline.scheduledEvents.length &&
        clamped >= timeline.scheduledEvents[lastRenderedEventCount].t) {
        var nextEvItem = timeline.scheduledEvents[lastRenderedEventCount];
        if (!lastRevealedCompare && timeline.compareRevealAt != null && timeline.compareRevealAt <= nextEvItem.t && clamped >= timeline.compareRevealAt) {
          lastRevealedCompare = true;
          constellation.revealCompareRecall();
        }
        var evObj = nextEvItem.ev;
        var isFirstEv = (lastRenderedEventCount === 0);
        lastRenderedEventCount++;
        phone.handleEvent(evObj);
        board.handleNarrowEvent(evObj, false, isFirstEv, hasAnyAnswer);
        board.handlePanel4Event(evObj);
        constellation.handleEvent(evObj);
      }

      if (!lastRevealedCompare && timeline.compareRevealAt != null && clamped >= timeline.compareRevealAt) {
        lastRevealedCompare = true;
        constellation.revealCompareRecall();
      }

      // 왼쪽 단계 표시 갱신
      var processedEvs = timeline.scheduledEvents.slice(0, lastRenderedEventCount).map(function (x) { return x.ev; });
      var activeStepIdx = computeActiveStepIndex(sc.steps, clamped >= timeline.qStart, processedEvs, hasAnyAnswer);
      renderLeftSteps(sc.steps, activeStepIdx);

      // 마무리 장면: 첫 연관 사진 단추 열기 + 마지막 4초 표 덮개
      if (timeline.outroStart != null && clamped >= timeline.outroStart) {
        if (timeline.outroRelTapAt != null && clamped >= timeline.outroRelTapAt && !lastOpenedOutroRel) {
          lastOpenedOutroRel = true;
          phone.openFirstRelated(!isInstantSeek);
        }
        outroOverlayEl.hidden = !(timeline.outroTableAt != null && clamped >= timeline.outroTableAt);
      } else {
        outroOverlayEl.hidden = true;
      }

      if (isInstantSeek) restoreAnimNextFrame();
    }

    function restoreAnimNextFrame() {
      requestAnimationFrame(function () {
        stageEl.classList.remove('demo-noanim');
      });
    }

    function tickLoop(now) {
      if (!playing) return;
      if (!lastFrameWall) lastFrameWall = now;
      var dt = Math.min(120, Math.max(0, now - lastFrameWall));
      lastFrameWall = now;
      var effSpeed = prepReplayActive ? 2 : currentSpeed;
      var nextMs = currentTimeMs + dt * effSpeed;

      if (timeline && nextMs >= timeline.totalDuration) {
        renderAutoAt(timeline.totalDuration, false);
        stopLoop();
        return;
      }
      renderAutoAt(nextMs, false);
      if (playing) {
        rafId = requestAnimationFrame(tickLoop);
      }
    }

    function startLoop() {
      if (playing) return;
      playing = true;
      lastFrameWall = 0;
      playPauseBtn.textContent = '멈춤';
      rafId = requestAnimationFrame(tickLoop);
    }

    function stopLoop() {
      playing = false;
      lastFrameWall = 0;
      if (rafId != null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      playPauseBtn.textContent = '재생';
    }

    function seekAutoTo(targetMs, keepPlaying) {
      stopLoop();
      appliedStateKey = '';
      renderAutoAt(targetMs, true);
      if (keepPlaying && timeline && targetMs < timeline.totalDuration) {
        startLoop();
      }
    }

    // ---------------------------------------------------------------- 직접 해 보기 모드
    function cancelTryReplay() {
      tryReplayToken++;
      tryWaitingAsk = null;
    }

    function enterTryIdleState() {
      stopLoop();
      prepReplayActive = false;
      cancelTryReplay();
      live.abort();

      var c = getCase();
      var rk = baseRecordKind(c);
      var webCard = findFirstWebCard(c.run.events);

      introOverlayEl.hidden = true;
      outroOverlayEl.hidden = true;
      updateNoticeAndKind(rk, webCard, false);

      phone.reset();
      phone.setCase(c, false);
      phone.setModeAndView('try', 'all', rk);
      phone.setBusy(false);

      board.setCase(c, 'try');
      board.setPanel1Ready();
      board.setPanel2Done();
      board.resetPanel4(rk, c.run);
      board.updatePanel4Footer();

      constellation.setCaseAndMode(c, 'try', rk);
      constellation.setReadyAll();

      renderLeftSteps(c.scene.steps, -1);
      updateLivePanelVisibility();
      setStreamingControlsLocked(false);
    }

    function startTryPrepReplay() {
      var c = getCase();
      if (!c || c.scene.skip_prep || live.isStreaming()) return;
      cancelTryReplay();
      timeline = buildCaseTimeline(c);
      var sSave = findSceneByName('기억 저장');
      if (!sSave) return;
      prepReplayActive = true;
      prepReplayEndMs = sSave.end;
      seekAutoTo(3000, true);
    }

    function setStreamingControlsLocked(locked) {
      var dis = Boolean(locked);
      phone.setBusy(dis);
      caseSelectEl.disabled = dis;
      restartBtn.disabled = dis;
      modeAutoBtn.disabled = dis;
      modeTryBtn.disabled = dis;
      playPauseBtn.disabled = dis;
      board.setReplayPrepDisabled(dis);

      if (cIsDocument()) {
        liveToggleEl.disabled = true;
      } else {
        liveToggleEl.disabled = dis;
      }

      var spBtns = speedsWrap.querySelectorAll('button');
      for (var i = 0; i < spBtns.length; i++) spBtns[i].disabled = dis;
      var scBtns = sceneBarEl.querySelectorAll('button');
      for (var j = 0; j < scBtns.length; j++) scBtns[j].disabled = dis;
    }

    function cIsDocument() {
      var c = getCase();
      return Boolean(c && c.scene && c.scene.kind === 'document');
    }

    function shouldRunLive() {
      return Boolean(serverInfo && liveToggleEl.checked && !cIsDocument());
    }

    function handleTrySubmit(queryText) {
      if (prepReplayActive) {
        stopLoop();
        prepReplayActive = false;
      }
      cancelTryReplay();
      var c = getCase();

      if (shouldRunLive()) {
        live.abort();
        var sameQuery = Bus.normSpace(queryText) === Bus.normSpace(c.run.query);
        tryActiveSteps = sameQuery ? c.scene.steps : (DATA.default_steps || []);
        tryActiveNarrow = sameQuery ? c.scene.narrow : (DATA.default_narrow || []);
        trySeenFirstEvent = false;
        tryHasAnswerInRun = true;
        liveRunSeq += 1;

        phone.reset();
        phone.setCase(c, true);
        phone.setModeAndView('try', 'all', '실시간');
        updateNoticeAndKind('실시간', null, true);

        board.startQuestionNarrow(tryActiveNarrow);
        board.setPanel2Done();
        board.resetPanel4('실시간', null);
        constellation.setCaseAndMode(c, 'try', '실시간');
        constellation.startQuestion(queryText, '실시간', true);
        renderLeftSteps(tryActiveSteps, 0);

        phone.userBubble(queryText);
        live.ask(queryText);
        return;
      }

      // 저장한 실행 / 대본 재생 경로
      var matchesSaved = Bus.normSpace(queryText) === Bus.normSpace(c.run.query);
      if (!matchesSaved) {
        phone.userBubble(queryText);
        var msg = serverInfo
          ? '실시간으로 묻기가 꺼져 있어 저장해 둔 질문에만 답해요. 아래 질문을 눌러 보세요.'
          : '서버가 꺼져 있어 저장해 둔 질문에만 답해요. 아래 질문을 눌러 보세요.';
        phone.systemNotice(msg);
        return;
      }

      startSavedInteractiveReplay(c, queryText);
    }

    var liveProcessedEvents = [];

    function handleLiveEvent(ev, runId) {
      var isFirst = !trySeenFirstEvent;
      trySeenFirstEvent = true;
      if (isFirst) liveProcessedEvents = [];
      liveProcessedEvents.push(ev);

      if (ev.type === 'card' && ev.kind === 'web') {
        updateNoticeAndKind('실시간', ev, false);
      }
      if (ev.type === 'progress' && ev.cost_usd != null) {
        var runCostKey = runId || ('live_' + liveRunSeq);
        liveRunCosts[runCostKey] = Number(ev.cost_usd) || 0;
        updateLiveSpentText();
      }
      if (ev.type === 'reflect' && ev.usage != null) {
        if (ev.usage.cost_usd == null) {
          hasUnknownReflectCost = true;
        } else {
          totalLiveReflectCost += Number(ev.usage.cost_usd) || 0;
        }
        updateLiveSpentText();
      }

      phone.handleEvent(ev);
      board.handleNarrowEvent(ev, false, isFirst, true);
      board.handlePanel4Event(ev, runId);
      constellation.handleEvent(ev);

      var stepIdx = computeActiveStepIndex(tryActiveSteps, true, liveProcessedEvents, true);
      renderLeftSteps(tryActiveSteps, stepIdx);
    }

    function sleepScaled(ms, myToken) {
      return new Promise(function (resolve) {
        var dur = Math.max(10, Math.round(ms / currentSpeed));
        setTimeout(function () {
          resolve(myToken === tryReplayToken);
        }, dur);
      });
    }

    async function startSavedInteractiveReplay(c, queryText) {
      var myToken = ++tryReplayToken;
      var rk = baseRecordKind(c);
      var evs = makePlaylist(c.run);
      var webCard = findFirstWebCard(evs);
      var hasAgentMem = evs.some(function (e) { return e.type === 'memory'; });
      var hasAnyAns = evs.some(function (e) { return e.type === 'answer'; });

      phone.reset();
      phone.setCase(c, false);
      phone.setModeAndView('try', 'all', rk);
      updateNoticeAndKind(rk, webCard, false);

      board.startQuestionNarrow(c.scene.narrow);
      board.setPanel2Done();
      board.resetPanel4(rk, c.run);
      constellation.setCaseAndMode(c, 'try', rk);
      constellation.startQuestion(queryText, rk, !hasAgentMem && Boolean(c.recall));
      renderLeftSteps(c.scene.steps, 0);

      phone.userBubble(queryText);

      if (!hasAgentMem && c.recall) {
        setTimeout(function () {
          if (myToken !== tryReplayToken) return;
          constellation.revealCompareRecall();
        }, Math.round(1500 / currentSpeed));
      }

      await runEventSlice(c, evs, 0, false, myToken, hasAnyAns, []);
    }

    async function runEventSlice(c, evs, startIndex, isBranch, myToken, hasAnyAns, accEvents) {
      for (var i = startIndex; i < evs.length; i++) {
        var ev = evs[i];
        var delayMs = ev.type === 'answer' ? 0 : (DELAY[ev.type] || 600);
        if (delayMs > 0) {
          var ok = await sleepScaled(delayMs, myToken);
          if (!ok || myToken !== tryReplayToken) return;
        }
        if (myToken !== tryReplayToken) return;

        var isFirst = (accEvents.length === 0 && !isBranch);
        accEvents.push(ev);

        phone.handleEvent(ev);
        board.handleNarrowEvent(ev, isBranch, isFirst, hasAnyAns);
        board.handlePanel4Event(ev);
        constellation.handleEvent(ev);

        if (!isBranch) {
          var stIdx = computeActiveStepIndex(c.scene.steps, true, accEvents, hasAnyAns);
          renderLeftSteps(c.scene.steps, stIdx);
        }

        if (ev.type === 'done' && ev.status === 'waiting') {
          // 본래 기록의 answer 자리 찾기
          var mainEvs = makePlaylist(c.run);
          var ansIdx = -1;
          for (var k = 0; k < mainEvs.length; k++) {
            if (mainEvs[k].type === 'answer') { ansIdx = k; break; }
          }
          var expectedAns = ansIdx >= 0 ? mainEvs[ansIdx] : null;
          tryWaitingAsk = {
            caseObj: c,
            mainEvs: mainEvs,
            ansIdx: ansIdx,
            expectedOptionId: expectedAns ? String(expectedAns.option_id) : null,
            expectedLabel: expectedAns ? (expectedAns.label || '') : '',
            myToken: myToken,
            hasAnyAns: hasAnyAns,
            accEvents: accEvents
          };
          return;
        }
      }
    }

    function handleTryOption(optId) {
      if (currentMode !== 'try') return false;
      if (shouldRunLive() && live.currentRunId()) {
        return live.answer(optId);
      }
      if (!tryWaitingAsk) return false;

      var ctxWait = tryWaitingAsk;
      var chosen = String(optId);
      var c = ctxWait.caseObj;

      if (ctxWait.expectedOptionId && chosen === ctxWait.expectedOptionId) {
        tryWaitingAsk = null;
        Promise.resolve().then(function () {
          if (ctxWait.myToken !== tryReplayToken) return;
          runEventSlice(c, ctxWait.mainEvs, ctxWait.ansIdx, false, ctxWait.myToken, ctxWait.hasAnyAns, ctxWait.accEvents);
        });
        return true;
      }

      var branches = c.run.branches || {};
      if (branches[chosen] && Array.isArray(branches[chosen].events)) {
        tryWaitingAsk = null;
        var brEvs = branches[chosen].events;
        Promise.resolve().then(function () {
          if (ctxWait.myToken !== tryReplayToken) return;
          runEventSlice(c, brEvs, 0, true, ctxWait.myToken, ctxWait.hasAnyAns, ctxWait.accEvents);
        });
        return true;
      }

      var note = '이 기록에는 이 선택지를 고른 흐름이 없어요. ‘' + ctxWait.expectedLabel + '’ 쪽을 골라 보세요.';
      if (serverInfo && c.scene.kind === 'photo') {
        note += ' 실시간으로 묻기를 켜면 다른 선택지도 실제로 돌려 볼 수 있어요.';
      }
      phone.systemNotice(note);
      return false;
    }

    // ---------------------------------------------------------------- 모드·케이스 전환 및 조작 단추 연결
    function applyViewClasses() {
      stageEl.classList.toggle('demo-clean', urlCfg.clean);
      stageEl.classList.toggle('demo-view-phone', currentMode === 'auto' && currentView === 'phone');
      stageEl.classList.toggle('demo-view-board', currentMode === 'auto' && currentView === 'board');
      updateScale();
    }

    function enterMode(newMode, initialSeekMs) {
      stopLoop();
      cancelTryReplay();
      live.abort();
      prepReplayActive = false;

      currentMode = newMode;
      if (currentMode === 'try') {
        currentView = 'all';
      } else {
        currentView = urlCfg.view;
      }
      applyViewClasses();

      modeAutoBtn.classList.toggle('on', currentMode === 'auto');
      modeTryBtn.classList.toggle('on', currentMode === 'try');

      var c = getCase();
      var rk = baseRecordKind(c);
      var webCard = findFirstWebCard(c.run.events);
      updateNoticeAndKind(rk, webCard, false);

      board.setCase(c, currentMode);
      constellation.setCaseAndMode(c, currentMode, rk);
      timeline = buildCaseTimeline(c);
      buildSceneButtons();

      if (currentMode === 'try') {
        enterTryIdleState();
      } else {
        updateLivePanelVisibility();
        setStreamingControlsLocked(false);
        if (initialSeekMs != null) {
          seekAutoTo(initialSeekMs, false);
        } else {
          seekAutoTo(0, true);
        }
      }
    }

    // 고르기 칸 채우기
    cases.forEach(function (c, idx) {
      var opt = Bus.el('option', null, c.scene.menu_label || c.scene.key);
      opt.value = String(idx);
      if (idx === currentCaseIdx) opt.selected = true;
      caseSelectEl.appendChild(opt);
    });

    caseSelectEl.addEventListener('change', function () {
      if (caseSelectEl.disabled) return;
      currentCaseIdx = Number(caseSelectEl.value) || 0;
      enterMode(currentMode, null);
    });

    modeAutoBtn.addEventListener('click', function () {
      if (modeAutoBtn.disabled || currentMode === 'auto') return;
      enterMode('auto', null);
    });

    modeTryBtn.addEventListener('click', function () {
      if (modeTryBtn.disabled || currentMode === 'try') return;
      enterMode('try', null);
    });

    restartBtn.addEventListener('click', function () {
      if (restartBtn.disabled) return;
      if (currentMode === 'try') {
        enterTryIdleState();
      } else {
        seekAutoTo(0, true);
      }
    });

    playPauseBtn.addEventListener('click', function () {
      if (playPauseBtn.disabled || currentMode !== 'auto') return;
      if (playing) {
        stopLoop();
      } else {
        if (timeline && currentTimeMs >= timeline.totalDuration) {
          seekAutoTo(0, true);
        } else {
          startLoop();
        }
      }
    });

    var speedBtns = speedsWrap.querySelectorAll('button[data-speed]');
    function updateSpeedButtons() {
      for (var i = 0; i < speedBtns.length; i++) {
        var sp = Number(speedBtns[i].dataset.speed);
        speedBtns[i].classList.toggle('on', sp === currentSpeed);
      }
    }
    updateSpeedButtons();
    for (var si = 0; si < speedBtns.length; si++) {
      speedBtns[si].addEventListener('click', function (e) {
        var btn = e.currentTarget;
        if (btn.disabled) return;
        currentSpeed = Number(btn.dataset.speed) || 1;
        updateSpeedButtons();
      });
    }

    if (urlCfg.liveOff) {
      liveToggleEl.checked = false;
    }

    // 키보드 단축키 (자동 재생에서만, 입력창 초점 아닐 때)
    document.addEventListener('keydown', function (e) {
      if (currentMode !== 'auto') return;
      var tag = e.target && e.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.code === 'Space' || e.key === ' ') {
        e.preventDefault();
        playPauseBtn.click();
        return;
      }
      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        seekAutoTo(0, true);
        return;
      }
      if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && timeline && timeline.scenes.length) {
        e.preventDefault();
        var curIdx = 0;
        for (var i = 0; i < timeline.scenes.length; i++) {
          if (currentTimeMs >= timeline.scenes[i].start && currentTimeMs < timeline.scenes[i].end) {
            curIdx = i;
            break;
          }
          if (currentTimeMs >= timeline.scenes[i].end) curIdx = i;
        }
        var nextIdx = e.key === 'ArrowRight'
          ? Math.min(timeline.scenes.length - 1, curIdx + 1)
          : Math.max(0, (currentTimeMs - timeline.scenes[curIdx].start > 800 ? curIdx : curIdx - 1));
        seekAutoTo(timeline.scenes[nextIdx].start, playing);
      }
    });

    // 초기 부팅 실행
    updateServerBadge();
    enterMode(currentMode, urlCfg.seekT);
    live.checkHealth();
  };
})(window);
