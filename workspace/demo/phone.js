/* 폰 화면 조종기: web/render.js 를 화면 01 틀 안에서 돌리고 진행 문구·크게 보기·누름 표시를 맡는다. */
(function (global) {
  'use strict';

  var Bus = global.__AIDicBus = global.__AIDicBus || {};

  var TOOL_PROGRESS_KO = {
    plan_query: '질문을 정리하는 중이에요',
    search_web: '웹에서 찾아보는 중이에요',
    recall_memory: '기억을 꺼내는 중이에요',
    reflect_memory: '기억을 되짚는 중이에요',
    find_candidates: '후보를 찾는 중이에요',
    judge_photos: '사진을 다시 보는 중이에요',
    show_answer: '답을 정리하는 중이에요',
    read_documents: '문서를 읽는 중이에요',
    calc: '금액을 계산하는 중이에요'
  };

  function mapPhoneProgressStep(step) {
    var s = String(step || '');
    var m = /^([a-z0-9_]+)\s실행 중$/.exec(s);
    if (!m) {
      if (/\s실행 중$/.test(s)) return '찾는 중이에요';
      return s;
    }
    var tool = m[1];
    return Object.prototype.hasOwnProperty.call(TOOL_PROGRESS_KO, tool)
      ? TOOL_PROGRESS_KO[tool]
      : '찾는 중이에요';
  }

  Bus.createPhone = function createPhone(data, callbacks) {
    callbacks = callbacks || {};
    var phoneEl = document.getElementById('demo-phone');
    var stripEl = document.getElementById('demo-phone-strip');
    var chatEl = document.getElementById('demo-chat');
    var emptyEl = document.getElementById('demo-empty');
    var threadEl = document.getElementById('demo-thread');
    var suggestWrap = document.getElementById('demo-suggest-wrap');
    var suggestBtn = document.getElementById('demo-suggest-btn');
    var formEl = document.getElementById('demo-form');
    var inputEl = document.getElementById('demo-q');
    var sendBtn = document.getElementById('demo-send');
    var stageEl = document.getElementById('demo-stage');

    var noServerSet = {};
    (data.no_server_photo_ids || []).forEach(function (id) { noServerSet[id] = true; });
    var largeMap = data.photos_large || {};
    var smallMap = data.photos_small || {};

    var noPhotosMode = false;
    var isLiveRun = false;
    var seenResultInTurn = false;
    var currentMode = 'auto';
    var busy = false;

    function resolvePhotoUrl(id) {
      if (!id || noPhotosMode) return '';
      if (largeMap[id]) return largeMap[id];
      if (isLiveRun && !noServerSet[id]) {
        return '/api/photo?id=' + encodeURIComponent(id);
      }
      if (smallMap[id]) return smallMap[id];
      return '';
    }

    var R = global.AIDicRender.createRenderer(threadEl, {
      scrollEl: chatEl,
      photoUrl: resolvePhotoUrl,
      onOption: function (optId, optLabel) {
        if (callbacks.onOption) {
          return callbacks.onOption(optId, optLabel);
        }
        return false;
      },
      onDone: function (status, ev) {
        if (callbacks.onDone) callbacks.onDone(status, ev);
      }
    });

    // body 에 붙는 크게 보기 창(.lightbox)을 폰 안으로 옮긴다
    function adoptLightboxes() {
      var boxes = document.body.querySelectorAll(':scope > .lightbox');
      for (var i = 0; i < boxes.length; i++) {
        phoneEl.appendChild(boxes[i]);
      }
    }
    adoptLightboxes();
    if (typeof MutationObserver !== 'undefined') {
      var mo = new MutationObserver(function () { adoptLightboxes(); });
      mo.observe(document.body, { childList: true });
    }

    function clearTaps() {
      var taps = phoneEl.querySelectorAll('.demo-tap');
      for (var i = 0; i < taps.length; i++) taps[i].remove();
    }

    function showTapOn(targetEl) {
      if (!targetEl || stageEl.classList.contains('demo-noanim')) return;
      var pr = phoneEl.getBoundingClientRect();
      var tr = targetEl.getBoundingClientRect();
      if (!pr.width || !tr.width) return;
      var scaleX = pr.width / phoneEl.offsetWidth || 1;
      var scaleY = pr.height / phoneEl.offsetHeight || 1;
      var x = (tr.left - pr.left + tr.width / 2) / scaleX;
      var y = (tr.top - pr.top + tr.height / 2) / scaleY;
      var tap = Bus.el('div', 'demo-tap');
      tap.style.left = x + 'px';
      tap.style.top = y + 'px';
      phoneEl.appendChild(tap);
      setTimeout(function () { if (tap.parentNode) tap.remove(); }, 750);
    }

    function reset() {
      R.reset();
      clearTaps();
      emptyEl.hidden = false;
      inputEl.value = '';
      seenResultInTurn = false;
    }

    function setCase(caseObj, liveFlag) {
      noPhotosMode = Boolean(caseObj && caseObj.no_photos);
      isLiveRun = Boolean(liveFlag);
      var q = (caseObj && caseObj.run && caseObj.run.query) || '';
      suggestBtn.textContent = q;
      suggestBtn.title = q;
    }

    function setLiveRun(flag) {
      isLiveRun = Boolean(flag);
    }

    function setModeAndView(mode, view, recordKind) {
      currentMode = mode;
      suggestWrap.hidden = (mode !== 'try');
      updateInputDisabled();
      if (view === 'phone') {
        stripEl.hidden = false;
        if (recordKind === '저장한 실행') {
          stripEl.textContent = '저장한 실행 · 실제 실행을 다시 보여 줍니다';
        } else if (recordKind === '설계 미리보기') {
          stripEl.textContent = '설계 미리보기 · 실제 실행이 아닙니다';
        } else {
          stripEl.textContent = '대본 · 실제 실행이 아닙니다';
        }
      } else {
        stripEl.hidden = true;
      }
    }

    function setBusy(flag) {
      busy = Boolean(flag);
      updateInputDisabled();
    }

    function updateInputDisabled() {
      var dis = (currentMode === 'auto') || busy;
      inputEl.disabled = dis;
      sendBtn.disabled = dis;
      suggestBtn.disabled = dis;
    }

    function setTypingText(text) {
      inputEl.value = text || '';
    }

    function tapSend() {
      showTapOn(sendBtn);
    }

    function tapOption(optionId) {
      var asks = threadEl.querySelectorAll('.ask');
      if (!asks.length) return;
      var lastAsk = asks[asks.length - 1];
      var btns = lastAsk.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        if (String(btns[i].dataset.id) === String(optionId)) {
          showTapOn(btns[i]);
          return;
        }
      }
    }

    function openFirstRelated(withTap) {
      var results = threadEl.querySelectorAll('.result');
      if (!results.length) return;
      var lastRes = results[results.length - 1];
      var firstBtn = lastRes.querySelector('.follow button');
      if (!firstBtn) return;
      if (withTap) showTapOn(firstBtn);
      if (!firstBtn.classList.contains('on')) {
        firstBtn.click();
      }
    }

    function userBubble(text) {
      emptyEl.hidden = true;
      inputEl.value = '';
      seenResultInTurn = false;
      R.userBubble(text);
    }

    function handleEvent(ev) {
      if (!ev || typeof ev !== 'object') return;
      var t = ev.type;
      // memory 와 reflect 이벤트는 폰에 넘기지 않는다
      if (t === 'memory' || t === 'reflect') return;
      if (t === 'answer') {
        seenResultInTurn = false;
        R.handle(ev);
        return;
      }
      if (t === 'result') {
        seenResultInTurn = true;
        R.handle(ev);
        return;
      }
      if (t === 'progress') {
        // 같은 차례에서 result 뒤에 오는 progress 는 폰에 넘기지 않는다
        if (seenResultInTurn) return;
        var mapped = mapPhoneProgressStep(ev.step);
        R.handle({
          type: 'progress',
          run_id: ev.run_id,
          seq: ev.seq,
          step: mapped
        });
        return;
      }
      R.handle(ev);
    }

    function systemNotice(message) {
      emptyEl.hidden = true;
      var n = Bus.el('div', 'demo-sys-note', message);
      n.setAttribute('role', 'status');
      threadEl.appendChild(n);
      requestAnimationFrame(function () { chatEl.scrollTop = chatEl.scrollHeight; });
    }

    function focusCardByIndex(cardDomIndex) {
      var children = threadEl.querySelectorAll('.step, .result');
      var node = children[cardDomIndex];
      if (!node) return;
      node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      node.classList.add('demo-card-flash');
      setTimeout(function () { node.classList.remove('demo-card-flash'); }, 900);
    }

    suggestBtn.addEventListener('click', function () {
      if (suggestBtn.disabled) return;
      inputEl.value = suggestBtn.textContent || '';
      inputEl.focus();
    });

    formEl.addEventListener('submit', function (e) {
      e.preventDefault();
      if (currentMode !== 'try' || busy) return;
      var q = inputEl.value.trim();
      if (!q) return;
      inputEl.value = '';
      inputEl.blur();
      if (callbacks.onSubmit) callbacks.onSubmit(q);
    });

    return {
      reset: reset,
      setCase: setCase,
      setLiveRun: setLiveRun,
      setModeAndView: setModeAndView,
      setBusy: setBusy,
      setTypingText: setTypingText,
      tapSend: tapSend,
      tapOption: tapOption,
      openFirstRelated: openFirstRelated,
      userBubble: userBubble,
      handleEvent: handleEvent,
      systemNotice: systemNotice,
      focusCardByIndex: focusCardByIndex
    };
  };
})(window);
