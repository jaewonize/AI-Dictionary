/* 실시간 채팅 화면. POST /api/ask 와 POST /api/answer 의 응답 본문을 이어 읽으며 이벤트를 그린다.
   브라우저의 EventSource 는 GET 만 보내므로 쓰지 않고 fetch 로 읽는다.
   이벤트 하나는 'data: ' 로 시작하는 JSON 한 줄과 빈 줄이다. */
(function () {
  'use strict';

  var chat = document.getElementById('chat');
  var thread = document.getElementById('thread');
  var empty = document.getElementById('empty');
  var form = document.getElementById('form');
  var input = document.getElementById('q');
  var sendBtn = document.getElementById('send');

  var runId = null;    // 지금 실행의 run_id. 다른 run_id 의 이벤트는 버린다
  var streamNo = 0;    // 가장 최근에 연 응답 번호
  var streaming = false;
  var queued = null;   // 응답을 다 읽기 전에 누른 선택지. 응답이 끝나면 보낸다

  var R = AIDicRender.createRenderer(thread, {
    scrollEl: chat,
    photoUrl: function (id) { return '/api/photo?id=' + encodeURIComponent(id); },
    onOption: function (optionId) {
      if (!runId) return false;
      if (streaming) { queued = optionId; return true; }
      answer(optionId);
      return true;
    },
    onDone: function () { setBusy(false); }
  });

  function setBusy(busy) {
    input.disabled = busy;
    sendBtn.disabled = busy;
  }

  function fail(message) {
    R.handle({ type: 'error', message: message });
  }

  // 빈 줄로 나눈 덩어리 하나를 이벤트로 읽어 그린다. done 이면 true
  function dispatch(chunk, fresh) {
    var data = chunk.split('\n')
      .filter(function (l) { return l.indexOf('data:') === 0; })
      .map(function (l) { return l.slice(5).replace(/^ /, ''); })
      .join('\n');
    if (!data) return false;
    var ev;
    try { ev = JSON.parse(data); } catch (e) { return false; }
    if (!ev || typeof ev !== 'object') return false;
    if (ev.run_id != null) {
      if (fresh && runId == null) runId = ev.run_id;
      else if (ev.run_id !== runId) return false;
    }
    R.handle(ev);
    return ev.type === 'done';
  }

  async function post(url, body, fresh) {
    var my = ++streamNo;
    var gotDone = false;
    streaming = true;
    setBusy(true);
    try {
      var res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (res.status === 409) {
        fail(fresh
          ? '다른 검색이 아직 진행 중이에요. 끝난 뒤에 다시 물어봐 주세요.'
          : '이 질문은 지금 답을 기다리고 있지 않아요. 다시 물어봐 주세요.');
        return;
      }
      if (!res.ok || !res.body) {
        fail('서버가 요청을 처리하지 못했어요. (' + res.status + ')');
        return;
      }
      var reader = res.body.getReader();
      var dec = new TextDecoder();
      var buf = '';
      for (;;) {
        var r = await reader.read();
        if (r.done) break;
        buf = (buf + dec.decode(r.value, { stream: true })).replace(/\r\n/g, '\n');
        var i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          if (dispatch(buf.slice(0, i), fresh)) gotDone = true;
          buf = buf.slice(i + 2);
        }
      }
      buf += dec.decode();
      if (buf.trim() && dispatch(buf, fresh)) gotDone = true;
      if (!gotDone && my === streamNo) fail('응답이 중간에 끊겼어요. 다시 물어봐 주세요.');
    } catch (e) {
      if (my === streamNo) fail('서버에 연결하지 못했어요. 노트북 서버가 켜져 있는지 확인해 주세요.');
    } finally {
      if (my === streamNo) {
        streaming = false;
        if (!gotDone) setBusy(false);
        if (queued != null) {
          var q = queued;
          queued = null;
          answer(q);
        }
      }
    }
  }

  function answer(optionId) {
    post('/api/answer', { run_id: runId, option_id: optionId }, false);
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q || input.disabled) return;
    input.value = '';
    input.blur(); // 폰 자판을 내려 카드가 보이게 한다
    empty.hidden = true;
    runId = null;
    queued = null;
    R.userBubble(q);
    post('/api/ask', { query: q }, true);
  });
})();
