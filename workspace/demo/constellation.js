/* 대시보드 ③ 메모리 별자리와 에이전트가 꺼낸 기억·기억 되짚기 표시 */
(function (global) {
  'use strict';

  var Bus = global.__AIDicBus = global.__AIDicBus || {};

  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function splitDocId(docId) {
    if (!docId || typeof docId !== 'string') return { kind: null, id: null };
    if (docId.indexOf('photo:') === 0) return { kind: 'photo', id: docId.slice(6) };
    if (docId.indexOf('doc:') === 0) return { kind: 'doc', id: docId.slice(4) };
    if (docId.indexOf('timeline:') === 0) return { kind: 'timeline', id: docId.slice(9) };
    return { kind: 'other', id: docId };
  }

  function normalizeHit(h) {
    h = h || {};
    var kind = h.kind || null;
    var id;
    if (h.id !== undefined) {
      id = h.id;
    } else if (h.source_id !== undefined) {
      id = h.source_id;
    } else if (h.document_id) {
      var sp = splitDocId(h.document_id);
      if (!kind) kind = sp.kind;
      id = sp.id;
    } else {
      id = null;
    }
    if (!kind && h.document_id) {
      kind = splitDocId(h.document_id).kind;
    }
    var key = (typeof h.key === 'string' && h.text === undefined)
      ? h.key
      : Bus.makeMatchKey(h.text);
    return {
      rank: h.rank,
      kind: kind || 'other',
      id: id == null ? null : String(id),
      key: key
    };
  }

  function dedupNormalizedHits(rawHits) {
    var seen = {};
    var out = [];
    (rawHits || []).forEach(function (raw) {
      var nh = normalizeHit(raw);
      var sig = nh.kind + '\u0000' + (nh.id == null ? '__null__' : nh.id) + '\u0000' + nh.key;
      if (seen[sig]) return;
      seen[sig] = true;
      out.push(nh);
    });
    return out;
  }

  Bus.createConstellation = function createConstellation(data, callbacks) {
    callbacks = callbacks || {};
    var emptyConst = {
      bank: '',
      has_doc_memories: false,
      stars: [],
      words: [],
      labels: [],
      time_links: [],
      entity_links: [],
      causal_links: []
    };

    var statsEl = document.getElementById('db-p3-stats');
    var stageEl = document.getElementById('db-p3-stage');
    var canvas = document.getElementById('db-p3-canvas');
    var tooltipEl = document.getElementById('db-p3-tooltip');
    var legendEl = document.getElementById('db-p3-legend');
    var captionEl = document.getElementById('db-p3-caption');
    var ctx = canvas.getContext('2d');
    var W = canvas.width;
    var H = canvas.height;

    var photoDateMap = {};
    ((data.catalog && data.catalog.photos) || []).forEach(function (p) {
      if (p && p.id && p.date) photoDateMap[p.id] = p.date;
    });

    function buildPack(rawConst) {
      var cd = rawConst || emptyConst;
      var stList = cd.stars || [];
      var wList = cd.words || [];
      var bLabels = cd.labels || [];
      var tLinks = cd.time_links || [];
      var eLinks = cd.entity_links || [];
      var cLinks = cd.causal_links || [];
      var bName = cd.bank || '';

      var sByN = {};
      var sByDoc = {};
      var sOrder = stList.slice().sort(function (a, b) {
        return (a.order - b.order) || (a.n - b.n);
      });
      stList.forEach(function (s) {
        sByN[s.n] = s;
        var dk = s.kind + ':' + s.id;
        if (!sByDoc[dk]) sByDoc[dk] = [];
        sByDoc[dk].push(s.n);
      });
      Object.keys(sByDoc).forEach(function (dk) {
        sByDoc[dk].sort(function (a, b) { return a - b; });
      });

      var nLen = stList.length;
      var pxArr = new Float64Array(nLen);
      var pyArr = new Float64Array(nLen);

      if (nLen > 0) {
        var rng = mulberry32(7);
        var vx = new Float64Array(nLen);
        var vy = new Float64Array(nLen);
        var padX = 36;
        var padY = 24;
        var spanW = W - padX * 2;
        var spanH = H - padY * 2;

        for (var i = 0; i < nLen; i++) {
          pxArr[i] = padX + rng() * spanW;
          pyArr[i] = padY + rng() * spanH;
        }

        var allEdges = [];
        tLinks.forEach(function (e) { allEdges.push([e[0], e[1], 0.025]); });
        eLinks.forEach(function (e) { allEdges.push([e[0], e[1], 0.018]); });
        cLinks.forEach(function (e) { allEdges.push([e[0], e[1], 0.035]); });

        var cx = W / 2;
        var cy = H / 2;
        var repulse = nLen > 600 ? 180 : 280;
        var useGrid = nLen > 1500;

        for (var iter = 0; iter < 300; iter++) {
          var damp = 0.82 * (1 - iter / 360);
          for (var k = 0; k < nLen; k++) {
            vx[k] = (cx - pxArr[k]) * 0.012;
            vy[k] = (cy - pyArr[k]) * 0.016;
          }

          if (!useGrid) {
            for (var a = 0; a < nLen; a++) {
              var ax = pxArr[a];
              var ay = pyArr[a];
              for (var b = a + 1; b < nLen; b++) {
                var dx = ax - pxArr[b];
                var dy = ay - pyArr[b];
                var d2 = dx * dx + dy * dy + 16;
                if (d2 > 22500) continue;
                var f = repulse / d2;
                var fx = dx * f;
                var fy = dy * f;
                vx[a] += fx;
                vy[a] += fy;
                vx[b] -= fx;
                vy[b] -= fy;
              }
            }
          } else {
            var cellSize = 60;
            var cols = Math.ceil(W / cellSize) + 2;
            var buckets = {};
            for (var gi = 0; gi < nLen; gi++) {
              var gx = Math.floor(pxArr[gi] / cellSize);
              var gy = Math.floor(pyArr[gi] / cellSize);
              var gkey = gy * cols + gx;
              if (!buckets[gkey]) buckets[gkey] = [];
              buckets[gkey].push(gi);
            }
            for (var ai = 0; ai < nLen; ai++) {
              var cxi = Math.floor(pxArr[ai] / cellSize);
              var cyi = Math.floor(pyArr[ai] / cellSize);
              for (var oy = -1; oy <= 1; oy++) {
                for (var ox = -1; ox <= 1; ox++) {
                  var cell = buckets[(cyi + oy) * cols + (cxi + ox)];
                  if (!cell) continue;
                  for (var bi = 0; bi < cell.length; bi++) {
                    var bj = cell[bi];
                    if (bj <= ai) continue;
                    var dxg = pxArr[ai] - pxArr[bj];
                    var dyg = pyArr[ai] - pyArr[bj];
                    var d2g = dxg * dxg + dyg * dyg + 16;
                    var fg = repulse / d2g;
                    vx[ai] += dxg * fg;
                    vy[ai] += dyg * fg;
                    vx[bj] -= dxg * fg;
                    vy[bj] -= dyg * fg;
                  }
                }
              }
            }
          }

          for (var ei = 0; ei < allEdges.length; ei++) {
            var ed = allEdges[ei];
            var u = ed[0];
            var v = ed[1];
            var st = ed[2];
            var ex = pxArr[v] - pxArr[u];
            var ey = pyArr[v] - pyArr[u];
            var dist = Math.sqrt(ex * ex + ey * ey) || 1;
            var pull = (dist - 28) * st;
            var px = (ex / dist) * pull;
            var py = (ey / dist) * pull;
            vx[u] += px;
            vy[u] += py;
            vx[v] -= px;
            vy[v] -= py;
          }

          for (var m = 0; m < nLen; m++) {
            pxArr[m] = Math.min(W - padX, Math.max(padX, pxArr[m] + Math.max(-8, Math.min(8, vx[m] * damp))));
            pyArr[m] = Math.min(H - padY, Math.max(padY, pyArr[m] + Math.max(-8, Math.min(8, vy[m] * damp))));
          }
        }
      }

      return {
        cData: cd,
        stars: stList,
        words: wList,
        baseLabels: bLabels,
        timeLinks: tLinks,
        entityLinks: eLinks,
        causalLinks: cLinks,
        bankName: bName,
        starByN: sByN,
        starsByDoc: sByDoc,
        saveOrderStars: sOrder,
        N: nLen,
        posX: pxArr,
        posY: pyArr
      };
    }

    var photoPack = buildPack(data.constellation);
    var docPack = data.constellation_docs ? buildPack(data.constellation_docs) : null;

    var cData = photoPack.cData;
    var stars = photoPack.stars;
    var words = photoPack.words;
    var baseLabels = photoPack.baseLabels;
    var timeLinks = photoPack.timeLinks;
    var entityLinks = photoPack.entityLinks;
    var causalLinks = photoPack.causalLinks;
    var bankName = photoPack.bankName;
    var starByN = photoPack.starByN;
    var starsByDoc = photoPack.starsByDoc;
    var saveOrderStars = photoPack.saveOrderStars;
    var N = photoPack.N;
    var posX = photoPack.posX;
    var posY = photoPack.posY;

    function activatePack(pack) {
      cData = pack.cData;
      stars = pack.stars;
      words = pack.words;
      baseLabels = pack.baseLabels;
      timeLinks = pack.timeLinks;
      entityLinks = pack.entityLinks;
      causalLinks = pack.causalLinks;
      bankName = pack.bankName;
      starByN = pack.starByN;
      starsByDoc = pack.starsByDoc;
      saveOrderStars = pack.saveOrderStars;
      N = pack.N;
      posX = pack.posX;
      posY = pack.posY;
    }

    // ---------------------------------------------------------------- 맞추기 함수 (AIDicDemo 공개 API)
    function matchNormalizedHits(dedupHits, recallBank) {
      if (recallBank && bankName && recallBank !== bankName) {
        return {
          stars: dedupHits.map(function () { return null; }),
          missing: 0,
          bankMismatch: true
        };
      }
      var matchedStars = [];
      var missing = 0;
      dedupHits.forEach(function (h) {
        if (!h.id || !h.kind || h.kind === 'other') {
          matchedStars.push(null);
          missing++;
          return;
        }
        var docList = starsByDoc[h.kind + ':' + h.id];
        if (!docList || !docList.length) {
          matchedStars.push(null);
          missing++;
          return;
        }
        var exact = docList.filter(function (sn) {
          return starByN[sn] && starByN[sn].key === h.key;
        });
        matchedStars.push(exact.length ? exact.slice() : docList.slice());
      });
      return { stars: matchedStars, missing: missing, bankMismatch: false };
    }

    function matchRecall(recallObj) {
      if (!recallObj || typeof recallObj !== 'object') {
        return { stars: [], missing: 0 };
      }
      var dedup = dedupNormalizedHits(recallObj.hits || []);
      var res = matchNormalizedHits(dedup, recallObj.bank || '');
      return { stars: res.stars, missing: res.missing };
    }

    // ---------------------------------------------------------------- 상태 변수
    var currentMode = 'auto';
    var currentCase = null;
    var currentRecordKind = '대본';
    var currentQuery = '';
    var compareRecallForCase = null;

    var phase = 'empty'; // 'empty' | 'saving' | 'ready' | 'question'
    var unlockedSet = {};
    var unlockedCount = 0;

    var zoom = 1.0;
    var panX = 0;
    var panY = 0;
    var dragging = false;
    var dragStartX = 0;
    var dragStartY = 0;

    // 질문하기 상태
    var querySent = false;
    var recallLoading = false;
    var compareLoading = false;
    var reflectPending = false;
    var reflectProgressSec = null;

    var memoryEvents = [];
    var reflectEvents = [];
    var seenCards = [];
    var resultItems = null;
    var doneReceived = false;
    var compareShown = false;

    var rankBadgeEls = [];

    function clearRankBadges() {
      rankBadgeEls.forEach(function (el) { if (el.parentNode) el.remove(); });
      rankBadgeEls = [];
    }

    function validCompareRecall() {
      if (!compareRecallForCase || !currentQuery) return null;
      if (compareRecallForCase.bank && bankName && compareRecallForCase.bank !== bankName) return null;
      if (Bus.normSpace(compareRecallForCase.query) !== Bus.normSpace(currentQuery)) return null;
      return compareRecallForCase;
    }

    function computeRecallDisplayState() {
      var goodMemEvents = memoryEvents.filter(function (e) {
        return !e.error && Array.isArray(e.hits) && e.hits.length > 0;
      });
      if (goodMemEvents.length > 0) {
        var combinedRaw = [];
        goodMemEvents.forEach(function (e) {
          (e.hits || []).forEach(function (h) { combinedRaw.push(h); });
        });
        var dedup = dedupNormalizedHits(combinedRaw);
        var lastBank = goodMemEvents[goodMemEvents.length - 1].bank || '';
        var matched = matchNormalizedHits(dedup, lastBank);
        return {
          source: 'agent',
          calls: memoryEvents.length,
          hits: dedup,
          stars: matched.stars,
          missing: matched.missing,
          bankMismatch: matched.bankMismatch,
          bank: lastBank
        };
      }
      if (memoryEvents.length === 0 && compareShown && validCompareRecall()) {
        var cmp = validCompareRecall();
        var cmpDedup = dedupNormalizedHits(cmp.hits || []);
        var cmpMatched = matchNormalizedHits(cmpDedup, cmp.bank || '');
        return {
          source: 'compare',
          calls: 0,
          hits: cmpDedup,
          stars: cmpMatched.stars,
          missing: cmpMatched.missing,
          bankMismatch: cmpMatched.bankMismatch,
          bank: cmp.bank || ''
        };
      }
      return {
        source: null,
        calls: memoryEvents.length,
        hits: [],
        stars: [],
        missing: 0,
        bankMismatch: false,
        bank: ''
      };
    }

    function shownRecall() {
      var st = computeRecallDisplayState();
      return {
        source: st.source,
        calls: st.calls,
        stars: st.stars,
        missing: st.missing
      };
    }

    function shownReflect() {
      if (!reflectEvents.length) return null;
      var lastRf = reflectEvents[reflectEvents.length - 1];
      var pids = Array.isArray(lastRf.photo_ids) ? lastRf.photo_ids.slice() : [];
      var bankDiff = Boolean(lastRf.bank && bankName && lastRf.bank !== bankName);
      var rfStars = pids.map(function (pid) {
        if (lastRf.error || bankDiff) return null;
        var list = starsByDoc['photo:' + pid];
        return (list && list.length) ? list.slice() : null;
      });
      return {
        calls: reflectEvents.length,
        photo_ids: pids,
        stars: rfStars,
        error: lastRf.error || null
      };
    }

    global.AIDicDemo = {
      matchRecall: matchRecall,
      shownRecall: shownRecall,
      shownReflect: shownReflect
    };

    // ---------------------------------------------------------------- 범례 및 자막 줄 그리기
    function renderLegend(recSource) {
      legendEl.textContent = '';
      var row = Bus.el('div', 'db-p3-leg-row');
      [
        ['#009296', '시간이 가까운 기억'],
        ['#f59e0b', '같은 낱말이 나온 기억'],
        ['#8b5cf6', '원인과 결과로 이어진 기억']
      ].forEach(function (pair) {
        var sw = Bus.el('span', 'db-p3-swatch');
        var bar = Bus.el('i');
        bar.style.background = pair[0];
        sw.appendChild(bar);
        sw.appendChild(document.createTextNode(pair[1]));
        row.appendChild(sw);
      });
      legendEl.appendChild(row);
      legendEl.appendChild(
        Bus.el('div', null, '시간과 낱말 연결은 백업에 든 기억으로 다시 계산했습니다. 뜻이 닮은 기억끼리의 연결은 이번 화면에 없습니다.')
      );
      if (phase === 'question') {
        var prefix = recSource === 'compare'
          ? '밝은 별과 숫자: 비교용으로 따로 꺼낸 기억과 그 순위'
          : '밝은 별과 숫자: 에이전트가 꺼낸 기억과 그 순위';
        legendEl.appendChild(
          Bus.el(
            'div',
            null,
            prefix + ' · 점선 고리: 하루 타임라인 · 파란 고리: 폰의 답에 나온 사진 · 흰 두 겹 고리: 되짚기가 고른 사진'
          )
        );
      }
    }

    function addCapLine(text, cls) {
      captionEl.appendChild(Bus.el('div', cls || 'db-p3-cap-sub', text));
    }

    function findDateLinkedCard(agentHits) {
      var recDates = {};
      agentHits.forEach(function (h) {
        if (h.kind === 'timeline' && h.id) recDates[h.id] = true;
        else if (h.kind === 'photo' && h.id && photoDateMap[h.id]) recDates[photoDateMap[h.id]] = true;
      });
      for (var i = 0; i < seenCards.length; i++) {
        var c = seenCards[i];
        var evid = c.evidence;
        if (!evid || !evid.date) continue;
        var ds = Array.isArray(evid.date) ? evid.date : [evid.date];
        for (var j = 0; j < ds.length; j++) {
          var d = String(ds[j]);
          if (recDates[d]) {
            return { date: d, no: c.no, title: c.title };
          }
        }
      }
      return null;
    }

    function renderCaption(recState) {
      captionEl.textContent = '';
      if (phase === 'empty') return;
      if (phase === 'saving') {
        var isDocSc = Boolean(currentCase && currentCase.scene && currentCase.scene.kind === 'document');
        var saveMsg = (isDocSc && cData.has_doc_memories)
          ? 'Hindsight가 사진 설명, 하루 타임라인, 문서 글자를 기억으로 나눠 저장한 순서대로 보여 줍니다. 시간은 줄였습니다.'
          : 'Hindsight가 사진 설명과 하루 타임라인을 기억으로 나눠 저장한 순서대로 보여 줍니다. 시간은 줄였습니다.';
        addCapLine(saveMsg, 'db-p3-cap-sub');
        if (isDocSc && !cData.has_doc_memories) {
          addCapLine('문서는 아직 기억 저장소에 넣지 않아 문서 사진 2장의 기억만 표시합니다.', 'db-p3-cap-sub');
        }
        return;
      }
      if (phase === 'ready') {
        return;
      }

      // phase === 'question'
      if (!querySent) return;

      var hasRecallShown = recState.source === 'agent' || recState.source === 'compare';
      var hasMemErrorsOnly = memoryEvents.length > 0 && memoryEvents.every(function (e) { return Boolean(e.error); });
      var hasMemEmptyOnly = memoryEvents.length > 0 && !hasMemErrorsOnly && !hasRecallShown;

      if (!hasRecallShown && !hasMemErrorsOnly && !hasMemEmptyOnly && !resultItems && !doneReceived) {
        if (compareLoading) {
          addCapLine('같은 질의로 따로 꺼내 둔 기억을 불러오는 중이에요', 'db-p3-cap-main');
        } else if (recallLoading) {
          addCapLine('에이전트가 Hindsight 은행에서 기억을 꺼내는 중이에요', 'db-p3-cap-main');
        } else {
          addCapLine('질문에 답하는 동안 에이전트가 꺼낸 기억을 여기에 밝혀요', 'db-p3-cap-sub');
        }
      } else if (recState.source === 'agent') {
        var photoSet = {};
        var tlSet = {};
        var docSet = {};
        recState.hits.forEach(function (h) {
          if (h.kind === 'photo' && h.id) photoSet[h.id] = true;
          else if (h.kind === 'timeline' && h.id) tlSet[h.id] = true;
          else if (h.kind === 'doc' && h.id) docSet[h.id] = true;
        });
        var totalElapsedMs = 0;
        var queries = [];
        memoryEvents.forEach(function (e) {
          totalElapsedMs += Number(e.elapsed_ms) || 0;
          if (e.query) queries.push(e.query);
        });
        var numLine =
          '에이전트가 Hindsight 은행에서 꺼낸 기억 ' + recState.hits.length + '개 · 사진 ' +
          Object.keys(photoSet).length + '장 · 하루 타임라인 ' + Object.keys(tlSet).length + '개';
        var docCnt = Object.keys(docSet).length;
        if (docCnt > 0) numLine += ' · 문서 ' + docCnt + '건';
        numLine += ' · 꺼내는 데 ' + (totalElapsedMs / 1000).toFixed(1) + '초';
        if (!recState.bankMismatch && recState.missing > 0) {
          numLine += ' · 별자리에 없는 기억 ' + recState.missing + '개';
        }
        if (memoryEvents.length >= 2) {
          numLine += ' (꺼내기 2번)';
        }
        addCapLine(numLine, 'db-p3-cap-main');

        if (recState.bankMismatch) {
          addCapLine(
            '에이전트가 꺼낸 은행(' + recState.bank + ')이 별자리의 백업과 달라 별에 맞추지 않았어요',
            'db-p3-cap-hl'
          );
        }
        var wherePrefix = currentRecordKind === '실시간' ? '실시간으로' : '저장한 실행에서';
        addCapLine(
          wherePrefix + ' 에이전트가 받은 기억입니다. 꺼내기는 Hindsight 안의 검색과 순위 매기기만 하고 비용이 드는 AI 모델 호출이 없습니다. 에이전트 루프가 이 기억을 받아 후보 찾기와 사진 다시 보기를 이어 갔습니다.',
          'db-p3-cap-sub'
        );
        if (queries.length) {
          addCapLine('에이전트가 쓴 질의: ' + queries.join(' / '), 'db-p3-cap-sub');
        }
      } else if (recState.source === 'compare') {
        var cmp = validCompareRecall();
        var cPhotoSet = {};
        var cTlSet = {};
        var cDocSet = {};
        recState.hits.forEach(function (h) {
          if (h.kind === 'photo' && h.id) cPhotoSet[h.id] = true;
          else if (h.kind === 'timeline' && h.id) cTlSet[h.id] = true;
          else if (h.kind === 'doc' && h.id) cDocSet[h.id] = true;
        });
        var cSec = (((cmp && Number(cmp.elapsed_ms)) || 0) / 1000).toFixed(1);
        var cDocCnt = Object.keys(cDocSet).length;
        var cNumLine =
          '같은 질의로 따로 꺼내 둔 기억 ' + recState.hits.length + '개 · 사진 ' +
          Object.keys(cPhotoSet).length + '장 · 하루 타임라인 ' + Object.keys(cTlSet).length + '개';
        if (cDocCnt > 0) cNumLine += ' · 문서 ' + cDocCnt + '건';
        cNumLine += ' · 꺼내는 데 ' + cSec + '초';
        if (recState.missing > 0) {
          cNumLine += ' · 별자리에 없는 기억 ' + recState.missing + '개';
        }
        addCapLine(cNumLine, 'db-p3-cap-main');

        var ansOrigin;
        if (currentRecordKind === '대본') {
          ansOrigin = '폰의 답은 대본으로 꾸민 것입니다.';
        } else if (currentRecordKind === '설계 미리보기') {
          ansOrigin = '폰의 답은 설계 미리보기로 꾸민 것입니다.';
        } else if (reflectEvents.length > 0) {
          ansOrigin = '이번 실행에서 에이전트는 기억 꺼내기를 부르지 않았습니다.';
        } else {
          ansOrigin = '이번 실행에서 에이전트는 기억 저장소를 부르지 않았습니다.';
        }
        addCapLine(
          '비교용으로 같은 질의로 따로 꺼내 둔 기억입니다. 에이전트가 받은 기억이 아닙니다. ' + ansOrigin,
          'db-p3-cap-sub'
        );
      } else {
        // 규칙 8: 꺼낸 기억이 없을 때
        var fallbackMsg;
        if (hasMemErrorsOnly) {
          var lastErr = memoryEvents[memoryEvents.length - 1].error || '';
          fallbackMsg = '기억 저장소를 쓰지 못했어요(' + lastErr + '). 에이전트는 기억 없이 이어 찾았습니다.';
        } else if (hasMemEmptyOnly) {
          fallbackMsg = '에이전트가 꺼낸 기억이 없어요(빈 결과)';
        } else if ((currentRecordKind === '실시간' || currentRecordKind === '저장한 실행') && reflectEvents.length > 0) {
          fallbackMsg = '이번 실행에서 에이전트가 기억 꺼내기를 부르지 않았어요';
        } else if (currentRecordKind === '실시간' || currentRecordKind === '저장한 실행') {
          fallbackMsg = '이번 실행에서 에이전트가 기억 저장소를 부르지 않았어요';
        } else {
          fallbackMsg = '이 기록에는 꺼낸 기억이 없어요';
        }
        addCapLine(fallbackMsg, 'db-p3-cap-main');

        if (resultItems && resultItems.length > 0) {
          var anyInBackup = resultItems.some(function (id) {
            return Boolean(starsByDoc['photo:' + id] || starsByDoc['doc:' + id]);
          });
          if (!anyInBackup) {
            addCapLine('이 사진들은 기억 저장소 백업에 없어요.', 'db-p3-cap-sub');
          }
        }
      }

      // 되짚기 진행 중 또는 완료 줄
      if (reflectPending) {
        var pendTxt = '에이전트가 Hindsight에 기억 되짚기를 맡겼어요. 은행 안의 AI 모델이 기억을 다시 꺼내 보며 답을 정리하는 중이에요';
        if (reflectProgressSec != null) pendTxt += ' (' + reflectProgressSec + '초)';
        addCapLine(pendTxt, 'db-p3-cap-hl');
      } else if (reflectEvents.length > 0) {
        var rf = reflectEvents[reflectEvents.length - 1];
        if (rf.error) {
          addCapLine('기억 되짚기를 하지 못했어요(' + rf.error + '). 에이전트는 되짚기 없이 이어 찾았습니다.', 'db-p3-cap-hl');
        } else {
          var pCount = Array.isArray(rf.photo_ids) ? rf.photo_ids.length : 0;
          var rfSec = ((Number(rf.elapsed_ms) || 0) / 1000).toFixed(1);
          var rfHead = pCount > 0
            ? 'Hindsight가 기억을 되짚어 사진 ' + pCount + '장을 골랐어요 · ' + rfSec + '초'
            : 'Hindsight가 기억을 되짚었지만 고른 사진이 없어요 · ' + rfSec + '초';
          if (rf.usage && rf.usage.cost_usd != null) {
            rfHead += ' · Hindsight 안의 AI 모델 ' + rf.usage.calls + '번 · $' + Number(rf.usage.cost_usd).toFixed(2);
          }
          addCapLine(rfHead, 'db-p3-cap-main');

          var rsn = String(rf.reasoning || '').trim();
          if (rsn) {
            var rChars = Array.from(rsn);
            var rShort = rChars.length > 160 ? (rChars.slice(0, 160).join('') + '…') : rsn;
            addCapLine('되짚기의 근거: ' + rShort, 'db-p3-cap-sub');
          }
          addCapLine(
            '되짚기는 Hindsight 안에서 AI 모델이 돌아 비용이 듭니다. 고른 사진은 후보이고, 에이전트가 사진 다시 보기로 확인한 것만 답에 들어갑니다.',
            'db-p3-cap-sub'
          );
        }
      }

      // 날짜를 잇는 줄 (에이전트가 받은 기억이 있을 때)
      if (recState.source === 'agent') {
        var linked = findDateLinkedCard(recState.hits);
        if (linked) {
          addCapLine(
            '꺼낸 기억의 날짜(' + Bus.fmtKoDate(linked.date) + ')가 카드 ' + linked.no + " '" + linked.title + "'에도 나와요",
            'db-p3-cap-hl'
          );
        }
      }

      // 답이 나왔을 때의 줄들
      if (resultItems && resultItems.length > 0 && !compareLoading) {
        if (hasRecallShown) {
          var who = recState.source === 'agent' ? '에이전트가 꺼낸 기억' : '비교용 기억';
          var isDocCaseAns = Boolean(currentCase && currentCase.scene && currentCase.scene.kind === 'document');
          var hitItemRanks = {};
          recState.hits.forEach(function (h, idx) {
            var matchable = isDocCaseAns ? (h.kind === 'doc' || h.kind === 'photo') : (h.kind === 'photo');
            if (matchable && h.id) {
              var rk = h.rank != null ? Number(h.rank) : (idx + 1);
              if (hitItemRanks[h.id] == null || rk < hitItemRanks[h.id]) {
                hitItemRanks[h.id] = rk;
              }
            }
          });
          var mCnt = 0;
          var bestRank = null;
          resultItems.forEach(function (pid) {
            if (hitItemRanks[pid] != null) {
              mCnt++;
              if (bestRank == null || hitItemRanks[pid] < bestRank) bestRank = hitItemRanks[pid];
            }
          });
          if (isDocCaseAns) {
            if (mCnt > 0) {
              addCapLine(
                '답에 쓴 문서 ' + resultItems.length + '건 가운데 ' + mCnt + '건이 ' + who + '에 있어요 · 가장 높은 순위 ' + bestRank + '위',
                'db-p3-cap-hl'
              );
            } else {
              addCapLine(
                '답에 쓴 문서 ' + resultItems.length + '건은 ' + who + '에 없어요',
                'db-p3-cap-sub'
              );
            }
          } else if (mCnt > 0) {
            addCapLine(
              '답에 나온 사진 ' + resultItems.length + '장 가운데 ' + mCnt + '장이 ' + who + '에 있어요 · 가장 높은 순위 ' + bestRank + '위',
              'db-p3-cap-hl'
            );
          } else {
            addCapLine(
              '답에 나온 사진 ' + resultItems.length + '장은 ' + who + '에 없어요',
              'db-p3-cap-sub'
            );
          }
        }

        if (reflectEvents.length > 0) {
          var lastRf2 = reflectEvents[reflectEvents.length - 1];
          var rfIds = Array.isArray(lastRf2.photo_ids) ? lastRf2.photo_ids : [];
          if (!lastRf2.error && rfIds.length > 0) {
            var rfSet = {};
            rfIds.forEach(function (id) { rfSet[id] = true; });
            var m2 = 0;
            resultItems.forEach(function (id) { if (rfSet[id]) m2++; });
            addCapLine('되짚기가 고른 사진 ' + rfIds.length + '장 가운데 ' + m2 + '장이 답에 나왔어요', 'db-p3-cap-hl');
          }
        }
      }

      // 비교 줄 (에이전트가 받은 기억 + 비교용 기억 둘 다 있을 때)
      if (recState.source === 'agent' && resultItems && validCompareRecall()) {
        var cmpObj = validCompareRecall();
        var cmpDedup2 = dedupNormalizedHits(cmpObj.hits || []);
        var agentSigSet = {};
        recState.hits.forEach(function (h) {
          agentSigSet[h.kind + '\u0000' + (h.id || '') + '\u0000' + h.key] = true;
        });
        var overlapCnt = 0;
        cmpDedup2.forEach(function (h) {
          if (agentSigSet[h.kind + '\u0000' + (h.id || '') + '\u0000' + h.key]) overlapCnt++;
        });
        addCapLine(
          '같은 질의로 따로 꺼내 둔 기억(비교용) ' + cmpDedup2.length + '개 가운데 ' + overlapCnt + '개가 에이전트가 받은 기억과 같아요',
          'db-p3-cap-sub'
        );
      }
    }

    // ---------------------------------------------------------------- 캔버스 그리기
    function screenCoords(sn) {
      var cx = W / 2;
      var cy = H / 2;
      return {
        x: (posX[sn] - cx) * zoom + cx + panX,
        y: (posY[sn] - cy) * zoom + cy + panY
      };
    }

    function draw() {
      ctx.clearRect(0, 0, W, H);
      clearRankBadges();

      var recState = computeRecallDisplayState();
      renderLegend(recState.source);
      renderCaption(recState);

      if (phase === 'empty' || unlockedCount === 0) {
        statsEl.textContent = '0개 메모리 · 0개 표시 · 0개 레이블 · 0개 링크 · 줌 ' + zoom.toFixed(1) + 'x';
        canvas.dataset.blueRingCount = '0';
        canvas.dataset.reflectRingCount = '0';
        return;
      }

      var hasHighlightFilter = false;
      var brightStarSet = {};
      var rankByStar = {};
      var blueRingSet = {};
      var reflectRingSet = {};

      if (phase === 'question') {
        if (recState.source && !recState.bankMismatch) {
          hasHighlightFilter = true;
          recState.stars.forEach(function (sList, idx) {
            if (!sList || !sList.length) return;
            sList.forEach(function (sn) { brightStarSet[sn] = true; });
            var rk = recState.hits[idx] && recState.hits[idx].rank != null
              ? Number(recState.hits[idx].rank)
              : (idx + 1);
            if (rk >= 1 && rk <= 10) {
              var smallest = sList[0];
              if (rankByStar[smallest] == null || rk < rankByStar[smallest]) {
                rankByStar[smallest] = rk;
              }
            }
          });
        }

        if (reflectEvents.length > 0) {
          var lastRf = reflectEvents[reflectEvents.length - 1];
          var rfBankOk = !(lastRf.bank && bankName && lastRf.bank !== bankName);
          if (!lastRf.error && rfBankOk && Array.isArray(lastRf.photo_ids)) {
            lastRf.photo_ids.forEach(function (pid) {
              var list = starsByDoc['photo:' + pid];
              if (list && list.length) {
                hasHighlightFilter = true;
                list.forEach(function (sn) {
                  reflectRingSet[sn] = true;
                  brightStarSet[sn] = true;
                });
              }
            });
          }
        }

        if (resultItems && resultItems.length > 0) {
          var matchedResStars = [];
          resultItems.forEach(function (pid) {
            var list = starsByDoc['photo:' + pid] || starsByDoc['doc:' + pid];
            if (list && list.length) {
              list.forEach(function (sn) { matchedResStars.push(sn); });
            }
          });
          if (matchedResStars.length > 0) {
            if (!recState.source && Object.keys(reflectRingSet).length === 0) {
              hasHighlightFilter = true;
            }
            matchedResStars.forEach(function (sn) {
              blueRingSet[sn] = true;
              if (hasHighlightFilter) brightStarSet[sn] = true;
            });
          }
        }
      }

      canvas.dataset.blueRingCount = String(Object.keys(blueRingSet).length);
      canvas.dataset.reflectRingCount = String(Object.keys(reflectRingSet).length);

      // 선 그리기 (두 끝 별이 모두 켜졌을 때)
      var drawnLinks = 0;

      function drawEdgeList(edges, color, weighted) {
        ctx.strokeStyle = color;
        for (var i = 0; i < edges.length; i++) {
          var e = edges[i];
          var u = e[0];
          var v = e[1];
          if (!unlockedSet[u] || !unlockedSet[v]) continue;
          drawnLinks++;
          var bothBright = !hasHighlightFilter || (brightStarSet[u] && brightStarSet[v]);
          ctx.globalAlpha = bothBright ? 0.36 : 0.08;
          ctx.lineWidth = weighted ? Math.max(0.6, (e[2] || 0.5) * 1.6) : 0.9;
          var pu = screenCoords(u);
          var pv = screenCoords(v);
          ctx.beginPath();
          ctx.moveTo(pu.x, pu.y);
          ctx.lineTo(pv.x, pv.y);
          ctx.stroke();
        }
      }

      drawEdgeList(timeLinks, '#009296', true);
      drawEdgeList(entityLinks, '#f59e0b', false);
      drawEdgeList(causalLinks, '#8b5cf6', false);
      ctx.globalAlpha = 1;

      // 별과 고리 그리기
      var isDocDraw = Boolean(currentCase && currentCase.scene && currentCase.scene.kind === 'document');
      var docPhotoSet = {};
      if (isDocDraw && currentCase && Array.isArray(currentCase.scene.doc_photos)) {
        currentCase.scene.doc_photos.forEach(function (pid) { docPhotoSet[pid] = true; });
      }
      var visibleCount = 0;
      for (var si = 0; si < N; si++) {
        if (!unlockedSet[si]) continue;
        var st = stars[si];
        var isBright = !hasHighlightFilter || Boolean(brightStarSet[si]);
        var starAlpha = isBright ? 1.0 : 0.25;
        if (phase === 'saving' && isDocDraw) {
          if (cData.has_doc_memories) {
            starAlpha = st.kind === 'doc' ? 1.0 : 0.60;
            isBright = true;
          } else {
            isBright = Boolean(st.kind === 'photo' && docPhotoSet[st.id]);
            starAlpha = isBright ? 1.0 : 0.25;
          }
        } else if (phase === 'ready' && isDocDraw && !cData.has_doc_memories) {
          isBright = Boolean(st.kind === 'photo' && docPhotoSet[st.id]);
          starAlpha = isBright ? 1.0 : 0.25;
        }
        if (isBright) visibleCount++;

        var p = screenCoords(si);
        ctx.globalAlpha = starAlpha;
        ctx.fillStyle = '#e8ecff';
        ctx.beginPath();
        ctx.arc(p.x, p.y, isBright && hasHighlightFilter ? 3.4 : 2.3, 0, Math.PI * 2);
        ctx.fill();

        // 하루 타임라인 점선 고리
        if (st.kind === 'timeline' && isBright && phase === 'question') {
          ctx.save();
          ctx.strokeStyle = '#9ee6e8';
          ctx.lineWidth = 1.2;
          ctx.setLineDash([2, 2]);
          ctx.beginPath();
          ctx.arc(p.x, p.y, 6.2, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
        }

        // 답에 나온 사진 파란 고리
        if (blueRingSet[si]) {
          ctx.save();
          ctx.globalAlpha = 1.0;
          ctx.strokeStyle = '#4c74ff';
          ctx.lineWidth = 2.2;
          ctx.beginPath();
          ctx.arc(p.x, p.y, 7.2, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
        }

        // 되짚기가 고른 사진 흰 두 겹 고리
        if (reflectRingSet[si]) {
          ctx.save();
          ctx.globalAlpha = 1.0;
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 1.4;
          ctx.beginPath();
          ctx.arc(p.x, p.y, 9.8, 0, Math.PI * 2);
          ctx.stroke();
          ctx.lineWidth = 1.1;
          ctx.strokeStyle = 'rgba(255,255,255,0.85)';
          ctx.beginPath();
          ctx.arc(p.x, p.y, 12.4, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
        }
      }
      ctx.globalAlpha = 1;

      // 레이블 계산 및 그리기
      var activeLabelWordIndices = [];
      if (phase === 'ready' || (phase === 'saving' && unlockedCount >= N) || (phase === 'question' && !hasHighlightFilter)) {
        activeLabelWordIndices = baseLabels;
      } else if (phase === 'question' && hasHighlightFilter && !(currentCase && currentCase.scene.kind === 'document')) {
        var wordCounts = {};
        Object.keys(brightStarSet).forEach(function (snStr) {
          var sObj = starByN[Number(snStr)];
          if (!sObj) return;
          (sObj.words || []).forEach(function (wi) {
            wordCounts[wi] = (wordCounts[wi] || 0) + 1;
          });
        });
        activeLabelWordIndices = Object.keys(wordCounts)
          .map(Number)
          .sort(function (a, b) {
            var diff = wordCounts[b] - wordCounts[a];
            if (diff !== 0) return diff;
            return String(words[a] || '').localeCompare(String(words[b] || ''), 'ko');
          })
          .slice(0, 12);
      }

      if (activeLabelWordIndices.length > 0) {
        ctx.save();
        ctx.font = '600 9.5px "Pretendard Variable", sans-serif';
        ctx.fillStyle = 'rgba(218, 226, 255, 0.72)';
        activeLabelWordIndices.forEach(function (wi) {
          var wText = words[wi];
          if (!wText) return;
          var sumX = 0;
          var sumY = 0;
          var cnt = 0;
          for (var j = 0; j < N; j++) {
            if (!unlockedSet[j]) continue;
            if (hasHighlightFilter && !brightStarSet[j]) continue;
            if (stars[j].words && stars[j].words.indexOf(wi) >= 0) {
              var sp = screenCoords(j);
              sumX += sp.x;
              sumY += sp.y;
              cnt++;
            }
          }
          if (cnt > 0) {
            ctx.fillText(wText, sumX / cnt + 5, sumY / cnt - 4);
          }
        });
        ctx.restore();
      }

      // 순위 숫자 (1..10) 표시
      Object.keys(rankByStar).forEach(function (snStr) {
        var sn = Number(snStr);
        var rk = rankByStar[sn];
        var pt = screenCoords(sn);
        var badge = Bus.el('span', null, String(rk));
        badge.style.position = 'absolute';
        badge.style.left = Math.round(pt.x + 6) + 'px';
        badge.style.top = Math.round(pt.y - 12) + 'px';
        badge.style.color = '#ffd166';
        badge.style.fontSize = '10.5px';
        badge.style.fontWeight = '800';
        badge.style.pointerEvents = 'none';
        badge.style.textShadow = '0 1px 3px rgba(0,0,0,.9)';
        stageEl.appendChild(badge);
        rankBadgeEls.push(badge);
      });

      statsEl.textContent =
        unlockedCount + '개 메모리 · ' + visibleCount + '개 표시 · ' +
        activeLabelWordIndices.length + '개 레이블 · ' + drawnLinks + '개 링크 · 줌 ' + zoom.toFixed(1) + 'x';
    }

    // ---------------------------------------------------------------- 마우스 상호작용 (직접 해 보기 전용)
    function findStarNear(clientX, clientY) {
      var rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      var mx = ((clientX - rect.left) / rect.width) * W;
      var my = ((clientY - rect.top) / rect.height) * H;
      var best = null;
      var bestD2 = 100;
      for (var i = 0; i < N; i++) {
        if (!unlockedSet[i]) continue;
        var p = screenCoords(i);
        var dx = mx - p.x;
        var dy = my - p.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < bestD2) {
          bestD2 = d2;
          best = stars[i];
        }
      }
      return best;
    }

    canvas.addEventListener('mousemove', function (e) {
      if (currentMode !== 'try') {
        tooltipEl.hidden = true;
        return;
      }
      if (dragging) {
        var rect = canvas.getBoundingClientRect();
        var sx = W / (rect.width || W);
        var sy = H / (rect.height || H);
        panX += (e.clientX - dragStartX) * sx;
        panY += (e.clientY - dragStartY) * sy;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        draw();
        return;
      }
      var hit = findStarNear(e.clientX, e.clientY);
      if (!hit) {
        tooltipEl.hidden = true;
        return;
      }
      tooltipEl.textContent = '';
      var recState = computeRecallDisplayState();
      if (recState.source && !recState.bankMismatch) {
        for (var rIdx = 0; rIdx < recState.stars.length; rIdx++) {
          var arr = recState.stars[rIdx];
          if (arr && arr.indexOf(hit.n) >= 0) {
            var rk = recState.hits[rIdx].rank != null ? recState.hits[rIdx].rank : (rIdx + 1);
            var rkPrefix = recState.source === 'agent' ? '에이전트가 꺼낸 순위 ' : '비교용 순위 ';
            tooltipEl.appendChild(Bus.el('div', 'db-p3-tip-rank', rkPrefix + rk + '위'));
            break;
          }
        }
      }
      var rfInfo = shownReflect();
      if (rfInfo && !rfInfo.error && rfInfo.stars) {
        var inRf = rfInfo.stars.some(function (arr) { return arr && arr.indexOf(hit.n) >= 0; });
        if (inRf) {
          tooltipEl.appendChild(Bus.el('div', 'db-p3-tip-rank', '되짚기가 고른 사진'));
        }
      }

      var isDocRestricted = cData.has_doc_memories && (
        hit.kind === 'doc' ||
        (hit.kind === 'photo' && currentCase && (currentCase.scene.doc_photos || []).indexOf(hit.id) >= 0)
      );
      if (isDocRestricted) {
        tooltipEl.appendChild(Bus.el('div', null, '문서의 기억'));
      } else {
        tooltipEl.appendChild(Bus.el('div', null, hit.short || ''));
        var wNames = (hit.words || []).slice(0, 6).map(function (wi) { return words[wi]; }).filter(Boolean);
        if (wNames.length) {
          tooltipEl.appendChild(Bus.el('div', 'db-p3-tip-words', wNames.join(' · ')));
        }
      }
      var sRect = stageEl.getBoundingClientRect();
      var scaleX = (sRect.width || W) / stageEl.offsetWidth;
      var scaleY = (sRect.height || H) / stageEl.offsetHeight;
      var lx = (e.clientX - sRect.left) / scaleX + 10;
      var ly = (e.clientY - sRect.top) / scaleY + 10;
      tooltipEl.style.left = Math.min(stageEl.offsetWidth - 260, Math.max(6, lx)) + 'px';
      tooltipEl.style.top = Math.min(stageEl.offsetHeight - 90, Math.max(6, ly)) + 'px';
      tooltipEl.hidden = false;
    });

    canvas.addEventListener('mouseleave', function () {
      dragging = false;
      tooltipEl.hidden = true;
    });

    canvas.addEventListener('mousedown', function (e) {
      if (currentMode !== 'try') return;
      dragging = true;
      dragStartX = e.clientX;
      dragStartY = e.clientY;
    });

    global.addEventListener('mouseup', function () {
      dragging = false;
    });

    canvas.addEventListener('click', function (e) {
      if (currentMode !== 'try') return;
      var hit = findStarNear(e.clientX, e.clientY);
      if (hit && hit.kind === 'photo' && hit.id && callbacks.onSelectPhoto) {
        callbacks.onSelectPhoto(hit.id);
      }
    });

    canvas.addEventListener('wheel', function (e) {
      if (currentMode !== 'try') return;
      e.preventDefault();
      var delta = e.deltaY < 0 ? 0.15 : -0.15;
      zoom = Math.min(3.0, Math.max(0.6, Math.round((zoom + delta) * 10) / 10));
      draw();
    }, { passive: false });

    // ---------------------------------------------------------------- 외부 제어 API
    function resetQuestionVars() {
      querySent = false;
      recallLoading = false;
      compareLoading = false;
      reflectPending = false;
      reflectProgressSec = null;
      memoryEvents = [];
      reflectEvents = [];
      seenCards = [];
      resultItems = null;
      doneReceived = false;
      compareShown = false;
      tooltipEl.hidden = true;
    }

    function unlockCount(cnt) {
      unlockedSet = {};
      var c = Math.max(0, Math.min(N, cnt));
      for (var i = 0; i < c; i++) {
        unlockedSet[saveOrderStars[i].n] = true;
      }
      unlockedCount = c;
    }

    return {
      setCaseAndMode: function (caseObj, mode, recordKind) {
        currentCase = caseObj;
        currentMode = mode;
        currentRecordKind = recordKind || '대본';
        var wantDocPack = Boolean(caseObj && caseObj.scene && caseObj.scene.kind === 'document' && docPack);
        activatePack(wantDocPack ? docPack : photoPack);
        compareRecallForCase = (caseObj && caseObj.recall) || null;
        zoom = 1.0;
        panX = 0;
        panY = 0;
      },
      setRecordKind: function (recordKind) {
        currentRecordKind = recordKind || '대본';
        draw();
      },
      setEmpty: function () {
        phase = 'empty';
        resetQuestionVars();
        unlockCount(0);
        draw();
      },
      setSavingProgress: function (ratio01) {
        phase = 'saving';
        resetQuestionVars();
        var cnt = ratio01 >= 1 ? N : Math.max(1, Math.floor(N * Math.max(0, ratio01)));
        unlockCount(cnt);
        draw();
      },
      setReadyAll: function () {
        phase = 'ready';
        resetQuestionVars();
        unlockCount(N);
        draw();
      },
      startQuestion: function (query, recordKind, willWaitCompareOnly) {
        phase = 'question';
        resetQuestionVars();
        unlockCount(N);
        currentQuery = query || '';
        if (recordKind) currentRecordKind = recordKind;
        querySent = true;
        compareLoading = Boolean(willWaitCompareOnly && validCompareRecall());
        draw();
      },
      revealCompareRecall: function () {
        compareLoading = false;
        if (validCompareRecall() && memoryEvents.length === 0) {
          compareShown = true;
        }
        draw();
      },
      handleEvent: function (ev) {
        if (!ev || typeof ev !== 'object') return;
        var t = ev.type;
        if (t === 'progress') {
          var step = String(ev.step || '');
          if (step === 'recall_memory 실행 중' || step === '기억 저장소에서 찾는 중이에요') {
            recallLoading = true;
            if (reflectPending) {
              reflectPending = false;
              reflectProgressSec = null;
            }
          } else if (step === 'reflect_memory 실행 중') {
            reflectPending = true;
            reflectProgressSec = null;
          } else if (step.indexOf('기억을 되짚어 보는 중이에요') === 0) {
            reflectPending = true;
            var m = /\((\d+)초\)/.exec(step);
            if (m) reflectProgressSec = Number(m[1]);
          } else if (reflectPending) {
            reflectPending = false;
            reflectProgressSec = null;
          }
          draw();
          return;
        }
        if (t === 'memory') {
          recallLoading = false;
          compareLoading = false;
          compareShown = false;
          memoryEvents.push(ev);
          draw();
          return;
        }
        if (t === 'reflect') {
          reflectPending = false;
          reflectProgressSec = null;
          reflectEvents.push(ev);
          draw();
          return;
        }
        if (t === 'card') {
          seenCards.push(ev);
          draw();
          return;
        }
        if (t === 'result') {
          if (reflectPending) {
            reflectPending = false;
            reflectProgressSec = null;
          }
          resultItems = (ev.items || []).map(function (it) {
            return typeof it === 'string' ? it : (it && it.id);
          }).filter(Boolean);
          draw();
          return;
        }
        if (t === 'done') {
          if (reflectPending) {
            reflectPending = false;
            reflectProgressSec = null;
          }
          doneReceived = true;
          if (currentRecordKind === '실시간' && memoryEvents.length === 0 && validCompareRecall()) {
            compareLoading = false;
            compareShown = true;
          }
          draw();
        }
      }
    };
  };

  if (typeof Bus.boot === 'function') {
    Bus.boot();
  }
})(window);
