/* AI ( ) Dictionary 채팅 화면 그리기. 실시간 화면(live.js)과 재생 페이지가 같은 코드를 쓴다.
   이벤트 모양은 app/events.py 를 따른다. 서버와 모델이 쓴 글은 모두 textContent 로만 넣는다.

   AIDicRender.createRenderer(root, opts) -> { userBubble(text), handle(event), reset() }
     opts.photoUrl(id)            사진 주소. 빈 문자열이면 회색 칸만 그린다
     opts.onOption(id, label)     선택지를 누를 때. false 를 돌려주면 누름을 무시한다
     opts.onDone(status, event)   done 이벤트가 오면. 입력창을 다시 여는 데 쓴다
     opts.scrollEl                내려 보낼 스크롤 칸(없으면 root)
   AIDicRender.openLightbox(src, caption) 사진 크게 보기 */
(function (global) {
  'use strict';

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null && text !== '') e.textContent = String(text);
    return e;
  }

  function asList(v) {
    if (v == null || v === '') return [];
    return Array.isArray(v) ? v : [v];
  }

  function toText(v) {
    if (v == null) return '';
    if (Array.isArray(v)) return v.map(toText).filter(Boolean).join(', ');
    if (typeof v === 'object') return Object.keys(v).map(function (k) { return toText(v[k]); }).filter(Boolean).join(' · ');
    return String(v);
  }

  function fmtDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
    return m ? m[1] + '년 ' + Number(m[2]) + '월 ' + Number(m[3]) + '일' : String(s);
  }

  function fmtShortDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''));
    return m ? m[1] + '.' + Number(m[2]) + '.' + Number(m[3]) : String(s || '');
  }

  function fmtTime(s) {
    var m = /(?:^|T|\s)(\d{1,2}):(\d{2})/.exec(String(s));
    return m ? m[1] + ':' + m[2] : String(s);
  }

  function range(a, b) {
    a = fmtShortDate(a); b = fmtShortDate(b);
    return a && b ? a + ' – ' + b : a || b;
  }

  // ---------------------------------------------------------------- 크게 보기
  var lb = null;

  function closeLightbox() {
    if (lb) { lb.hidden = true; lb.querySelector('img').removeAttribute('src'); }
  }

  function openLightbox(src, caption) {
    if (!src) return;
    if (!lb) {
      lb = el('div', 'lightbox');
      lb.setAttribute('role', 'dialog');
      lb.setAttribute('aria-modal', 'true');
      var img = el('img'); img.alt = '';
      var close = el('button', 'lb-close', '닫기'); close.type = 'button';
      lb.appendChild(img); lb.appendChild(el('div', 'lb-cap')); lb.appendChild(close);
      lb.addEventListener('click', closeLightbox);
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeLightbox(); });
      document.body.appendChild(lb);
    }
    lb.querySelector('img').src = src;
    lb.querySelector('.lb-cap').textContent = caption || '';
    lb.hidden = false;
  }

  // ---------------------------------------------------------------- 그리기
  var RELATION = { before: '기준보다 앞', after: '기준 뒤', same_day: '기준과 같은 날', none: '' };
  var PLAN_ROWS = [['대상', 'target'], ['기준', 'anchor'], ['관계', 'relation'], ['아는 것', 'known'], ['모르는 것', 'unknown']];

  function createRenderer(root, opts) {
    opts = opts || {};
    var scrollEl = opts.scrollEl || root;
    var keyed = {};          // 이번 차례(사용자 말 뒤)에 그린 카드. 같은 no가 다시 오면 바꾼다
    var progressEl = null;
    var pendingAnswer = null; // 화면에서 눌러 말풍선을 먼저 그린 선택지 id

    function photoUrl(id) { return id && opts.photoUrl ? opts.photoUrl(id) || '' : ''; }

    function scroll() {
      requestAnimationFrame(function () { scrollEl.scrollTop = scrollEl.scrollHeight; });
    }

    function clearProgress() {
      if (progressEl) { progressEl.remove(); progressEl = null; }
    }

    function setLive(node) {
      var lives = root.querySelectorAll('.dot.live');
      for (var i = 0; i < lives.length; i++) lives[i].classList.remove('live');
      var d = node && node.querySelector('.dot');
      if (d) d.classList.add('live');
    }

    function place(key, node) {
      var old = key && keyed[key];
      if (old && old.parentNode === root) root.replaceChild(node, old);
      else root.appendChild(node);
      if (key) keyed[key] = node;
    }

    function newTurn() { keyed = {}; }

    function bubble(text) {
      var w = el('div', 'me');
      w.appendChild(el('div', 'b', text));
      root.appendChild(w);
    }

    function closeAsk(box, chosenId) {
      var bs = box.querySelectorAll('button');
      for (var i = 0; i < bs.length; i++) {
        bs[i].disabled = true;
        if (chosenId != null && bs[i].dataset.id === String(chosenId)) bs[i].classList.add('on');
      }
    }

    function closeAllAsks() {
      var boxes = root.querySelectorAll('.ask');
      for (var i = 0; i < boxes.length; i++) closeAsk(boxes[i], null);
    }

    function thumb(id, cls, caption) {
      var box = el('button', cls); box.type = 'button';
      var src = photoUrl(id);
      if (src) {
        var img = el('img'); img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.src = src;
        box.appendChild(img);
      }
      box.addEventListener('click', function () { openLightbox(src, caption); });
      return box;
    }

    function grid(items) {
      var g = el('div', 'grid');
      asList(items).forEach(function (it) {
        var o = typeof it === 'string' ? { id: it } : (it || {});
        var t = thumb(o.id, 'tile', [o.badge, o.time_label].filter(Boolean).join(' · '));
        if (o.badge) t.appendChild(el('span', 'badge', o.badge));
        if (o.time_label) t.appendChild(el('span', 'time', o.time_label));
        g.appendChild(t);
      });
      return g;
    }

    function stepFrame(title, sentence) {
      var node = el('div', 'step');
      node.appendChild(el('div', 'dot'));
      var body = el('div', 'sbody');
      body.appendChild(el('div', 't', title));
      if (sentence) body.appendChild(el('div', 'd', sentence));
      node.appendChild(body);
      return { node: node, body: body };
    }

    function planList(d) {
      var box = el('div', 'facts');
      PLAN_ROWS.forEach(function (row) {
        var v = d[row[1]];
        var text = row[1] === 'relation' && Object.prototype.hasOwnProperty.call(RELATION, v) ? RELATION[v] : toText(v);
        if (!text) return;
        var f = el('div', 'f');
        f.appendChild(el('i', null, row[0]));
        f.appendChild(el('span', null, text));
        box.appendChild(f);
      });
      return box;
    }

    function webBlock(d, cached) {
      var box = el('div', 'web');
      if (d.summary) box.appendChild(el('div', 'sum', d.summary));
      var evs = asList(d.events);
      if (evs.length) {
        var ul = el('ul', 'evlist');
        evs.forEach(function (e) {
          e = e || {};
          var li = el('li');
          li.appendChild(el('b', null, e.name || ''));
          var meta = [e.venue, range(e.start, e.end)].filter(Boolean).join(' · ');
          if (meta) li.appendChild(el('span', 'meta', meta));
          if (e.status === 'upcoming') li.appendChild(el('span', 'tag', '예정'));
          ul.appendChild(li);
        });
        box.appendChild(ul);
      } else if (d.found === false) {
        box.appendChild(el('div', 'muted', '찾은 결과가 없어요'));
      }
      if (cached) box.appendChild(el('div', 'note', '미리 찾아 둔 결과예요'));
      return box;
    }

    function quoteText(x) {
      if (x == null) return '';
      if (typeof x === 'object') return toText(x.text != null ? x.text : x.read_text);
      return String(x).trim();
    }

    function evidenceRow(ev) {
      if (!ev || typeof ev !== 'object') return null;
      var box = el('div', 'evid');

      var ids = asList(ev.photo_ids);
      if (ids.length) {
        var strip = el('div', 'strip');
        ids.forEach(function (id) { strip.appendChild(thumb(id, 'th', '')); });
        box.appendChild(strip);
      }

      var seen = {}, quotes = [];
      asList(ev.read_text).forEach(function (x) {
        var t = quoteText(x);
        if (t && !seen[t]) { seen[t] = 1; quotes.push(t); }
      });
      if (quotes.length) {
        var q = el('div', 'quotes');
        quotes.slice(0, 6).forEach(function (t) { q.appendChild(el('span', 'quote', '“' + t + '”')); });
        box.appendChild(q);
      }

      var chips = el('div', 'chips');
      var dates = asList(ev.date).map(fmtDate).join(', ');
      if (dates) chips.appendChild(el('span', 'chip', dates));
      var from = ev.from_count, to = ev.to_count;
      if (from != null || to != null) {
        var c = el('span', 'chip');
        if (from != null) { c.appendChild(el('span', 'big', from)); c.appendChild(document.createTextNode('장')); }
        if (from != null && to != null) c.appendChild(document.createTextNode(' → '));
        if (to != null) { c.appendChild(el('span', 'big hit', to)); c.appendChild(document.createTextNode('장')); }
        chips.appendChild(c);
      }
      if (ev.cutoff) chips.appendChild(el('span', 'chip', '찍은 시각 ' + fmtTime(ev.cutoff) + ' 뒤'));
      if (chips.childNodes.length) box.appendChild(chips);

      return box.childNodes.length ? box : null;
    }

    function drawCard(ev) {
      var f = stepFrame(ev.title, ev.sentence);
      var data = ev.data && typeof ev.data === 'object' ? ev.data : {};
      if (ev.kind === 'plan') f.body.appendChild(planList(data));
      else if (ev.kind === 'web') f.body.appendChild(webBlock(data, ev.cached));
      else if (ev.cached) f.body.appendChild(el('div', 'note', '미리 찾아 둔 결과예요'));
      var evid = evidenceRow(ev.evidence);
      if (evid) f.body.appendChild(evid);
      return f.node;
    }

    function drawAsk(ev) {
      var f = stepFrame(ev.title, ev.question);
      var box = el('div', 'ask');
      asList(ev.options).forEach(function (o) {
        o = o || {};
        var b = el('button'); b.type = 'button';
        b.dataset.id = String(o.id);
        b.appendChild(el('span', 'lbl', o.label));
        if (o.detail) b.appendChild(el('small', null, o.detail));
        b.addEventListener('click', function () {
          if (b.disabled) return;
          if (opts.onOption && opts.onOption(o.id, o.label) === false) return;
          closeAsk(box, o.id);
          pendingAnswer = String(o.id);
          clearProgress();
          bubble(o.label);
          newTurn();
          scroll();
        });
        box.appendChild(b);
      });
      f.body.appendChild(box);
      return f.node;
    }

    function drawResult(ev) {
      var node = el('div', 'result');
      var items = asList(ev.items);
      var head = el('div', 'reshead');
      head.appendChild(el('div', 't', ev.title || items.length + '장 찾았어요'));
      node.appendChild(head);
      if (ev.sentence) node.appendChild(el('div', 'd', ev.sentence));
      if (items.length) node.appendChild(grid(items));

      var groups = asList(ev.related).filter(function (g) { return g && asList(g.items).length; });
      if (groups.length) {
        var rel = el('div', 'related');
        if (ev.related_title) rel.appendChild(el('div', 'rel-t', ev.related_title));
        var btns = el('div', 'follow');
        var slot = el('div', 'rel-slot');
        groups.forEach(function (g) {
          var b = el('button', null, g.label); b.type = 'button';
          b.appendChild(el('span', 'n', asList(g.items).length + '장'));
          b.addEventListener('click', function () {
            // 결과에 든 사진 목록을 그대로 펼친다. 서버를 다시 부르지 않는다
            var wasOpen = b.classList.contains('on');
            var all = btns.querySelectorAll('button');
            for (var i = 0; i < all.length; i++) all[i].classList.remove('on');
            slot.textContent = '';
            if (wasOpen) return;
            b.classList.add('on');
            var sec = el('div', 'relgroup');
            sec.appendChild(el('div', 'rel-h', g.label));
            sec.appendChild(grid(g.items));
            slot.appendChild(sec);
            sec.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          });
          btns.appendChild(b);
        });
        rel.appendChild(btns);
        rel.appendChild(slot);
        node.appendChild(rel);
      }
      return node;
    }

    function drawError(ev) {
      var n = el('div', 'errcard');
      n.setAttribute('role', 'alert');
      n.appendChild(el('div', 't', '문제가 생겼어요'));
      n.appendChild(el('div', 'd', ev.message || '알 수 없는 오류가 났어요.'));
      return n;
    }

    function showProgress(ev) {
      if (!progressEl) {
        progressEl = el('div', 'working');
        progressEl.setAttribute('aria-live', 'polite');
        var hop = el('span', 'hop');
        hop.appendChild(el('i')); hop.appendChild(el('i')); hop.appendChild(el('i'));
        progressEl.appendChild(hop);
        progressEl.appendChild(el('span', 'w-t'));
      }
      progressEl.querySelector('.w-t').textContent = ev.step || '찾는 중이에요';
      root.appendChild(progressEl); // 늘 맨 아래에 하나만 둔다
    }

    function handle(ev) {
      if (!ev || typeof ev !== 'object') return;
      var t = ev.type, node;
      if (t !== 'progress') clearProgress();
      switch (t) {
        case 'card':
          node = drawCard(ev);
          place(ev.no != null ? 'card:' + ev.no : null, node);
          setLive(node);
          break;
        case 'ask':
          node = drawAsk(ev);
          place(ev.no != null ? 'ask:' + ev.no : null, node);
          setLive(node);
          break;
        case 'answer': {
          var asks = root.querySelectorAll('.ask');
          if (asks.length) closeAsk(asks[asks.length - 1], ev.option_id);
          var already = pendingAnswer != null && pendingAnswer === String(ev.option_id);
          pendingAnswer = null;
          if (!already) { bubble(ev.label || ''); newTurn(); }
          setLive(null);
          break;
        }
        case 'result':
          place(ev.no != null ? 'result:' + ev.no : null, drawResult(ev));
          setLive(null);
          break;
        case 'progress':
          showProgress(ev);
          break;
        case 'error':
          root.appendChild(drawError(ev));
          break;
        case 'done':
          setLive(null);
          if (opts.onDone) opts.onDone(ev.status, ev);
          break;
        default:
          return; // 모르는 이벤트는 그리지 않는다
      }
      scroll();
    }

    function userBubble(text) {
      clearProgress();
      closeAllAsks();
      pendingAnswer = null;
      newTurn();
      bubble(text);
      scroll();
    }

    function reset() {
      root.textContent = '';
      keyed = {};
      progressEl = null;
      pendingAnswer = null;
      closeLightbox();
    }

    return { userBubble: userBubble, handle: handle, reset: reset };
  }

  global.AIDicRender = { createRenderer: createRenderer, openLightbox: openLightbox };
})(window);
