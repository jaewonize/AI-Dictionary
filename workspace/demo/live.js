/* 서버 실시간 연결: /api/health 확인과 /api/ask · /api/answer SSE 수신 */
(function (global) {
  'use strict';

  var Bus = global.__AIDicBus = global.__AIDicBus || {};

  Bus.createLive = function createLive(callbacks) {
    callbacks = callbacks || {};
    var runId = null;
    var streamNo = 0;
    var streaming = false;
    var queuedOption = null;

    function isHttpOrigin() {
      var p = global.location && global.location.protocol;
      return p === 'http:' || p === 'https:';
    }

    async function checkHealth() {
      if (!isHttpOrigin() || typeof global.fetch !== 'function') {
        if (callbacks.onHealth) callbacks.onHealth(null);
        return null;
      }
      var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timer = setTimeout(function () {
        if (ctrl) ctrl.abort();
      }, 2000);
      try {
        var res = await global.fetch('/api/health', {
          method: 'GET',
          signal: ctrl ? ctrl.signal : undefined
        });
        clearTimeout(timer);
        if (!res.ok) {
          if (callbacks.onHealth) callbacks.onHealth(null);
          return null;
        }
        var info = await res.json();
        if (!info || typeof info !== 'object') {
          if (callbacks.onHealth) callbacks.onHealth(null);
          return null;
        }
        if (callbacks.onHealth) callbacks.onHealth(info);
        return info;
      } catch (e) {
        clearTimeout(timer);
        if (callbacks.onHealth) callbacks.onHealth(null);
        return null;
      }
    }

    function dispatchChunk(chunk, fresh) {
      var dataLines = chunk.split('\n')
        .filter(function (l) { return l.indexOf('data:') === 0; })
        .map(function (l) { return l.slice(5).replace(/^ /, ''); })
        .join('\n');
      if (!dataLines) return false;
      var ev;
      try {
        ev = JSON.parse(dataLines);
      } catch (e) {
        return false;
      }
      if (!ev || typeof ev !== 'object') return false;
      if (ev.run_id != null) {
        if (fresh && runId == null) {
          runId = ev.run_id;
        } else if (runId != null && ev.run_id !== runId) {
          return false;
        }
      }
      if (callbacks.onEvent) callbacks.onEvent(ev, runId);
      return ev.type === 'done';
    }

    async function postStream(url, body, fresh) {
      var my = ++streamNo;
      var gotDone = false;
      streaming = true;
      if (callbacks.onStreamingChange) callbacks.onStreamingChange(true);
      try {
        var res = await global.fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        if (res.status === 409) {
          if (my === streamNo && callbacks.onNotice) {
            callbacks.onNotice(
              fresh
                ? '다른 검색이 아직 진행 중이에요. 끝난 뒤에 다시 물어봐 주세요.'
                : '이 질문은 지금 답을 기다리고 있지 않아요. 다시 물어봐 주세요.'
            );
          }
          return;
        }
        if (!res.ok || !res.body) {
          if (my === streamNo && callbacks.onNotice) {
            callbacks.onNotice('서버가 요청을 처리하지 못했어요. (' + res.status + ')');
          }
          return;
        }
        var reader = res.body.getReader();
        var dec = new TextDecoder();
        var buf = '';
        for (;;) {
          var r = await reader.read();
          if (r.done) break;
          if (my !== streamNo) return;
          buf = (buf + dec.decode(r.value, { stream: true })).replace(/\r\n/g, '\n');
          var idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            if (dispatchChunk(buf.slice(0, idx), fresh)) gotDone = true;
            buf = buf.slice(idx + 2);
          }
        }
        buf += dec.decode();
        if (buf.trim() && dispatchChunk(buf, fresh)) gotDone = true;
        if (!gotDone && my === streamNo && callbacks.onNotice) {
          callbacks.onNotice('응답이 중간에 끊겼어요. 다시 물어봐 주세요.');
        }
      } catch (e) {
        if (my === streamNo && callbacks.onNotice) {
          callbacks.onNotice('서버에 연결하지 못했어요. 노트북 서버가 켜져 있는지 확인해 주세요.');
        }
      } finally {
        if (my === streamNo) {
          streaming = false;
          if (callbacks.onStreamingChange) callbacks.onStreamingChange(false);
          if (queuedOption != null && runId != null) {
            var nextOpt = queuedOption;
            queuedOption = null;
            answer(nextOpt);
          }
        }
      }
    }

    function ask(query) {
      runId = null;
      queuedOption = null;
      postStream('/api/ask', { query: query }, true);
    }

    function answer(optionId) {
      if (!runId) return false;
      if (streaming) {
        queuedOption = optionId;
        return true;
      }
      postStream('/api/answer', { run_id: runId, option_id: optionId }, false);
      return true;
    }

    function abort() {
      streamNo++;
      streaming = false;
      runId = null;
      queuedOption = null;
    }

    function isStreaming() {
      return streaming;
    }

    function currentRunId() {
      return runId;
    }

    return {
      checkHealth: checkHealth,
      ask: ask,
      answer: answer,
      abort: abort,
      isStreaming: isStreaming,
      currentRunId: currentRunId
    };
  };
})(window);
