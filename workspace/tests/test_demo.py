"""2부 브라우저 데모(workspace/demo/) 단위 및 통합 검사."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import WS, settings
from app.events import CARD_TITLES
from demo.build import (
    HINDSIGHT_DIR,
    RUNS_DIR,
    SCENES_DIR,
    build_constellation_payload,
    build_pipeline_summary,
    check_scene_conditions,
    collect_run_photo_ids,
    compute_narrow_counts,
    load_old_backup_quotes,
    sanitize_compare_recall,
    sanitize_run_for_page,
    validate_run_record,
    validate_scene_config,
)
from demo.export_bank import export_bank
from pipeline.common import read_jsonl

DEMO_DIR = WS / "demo"
OLD_BACKUP_SHA256 = "8f888ed9452ad93fb4a3ec0b4b29754dc14edb653c57744da00eac101fea3627"

FORBIDDEN_KO_PLAIN = (
    "메타데이터",
    "백엔드",
    "엔드포인트",
    "프롬프트",
    "토큰",
    "스키마",
    "임베딩",
    "벡터",
)
FORBIDDEN_EN_BOUNDED = ("EXIF", "GPS", "XMP", "LLM", "RAG", "GPT")
FORBIDDEN_EN_PLAIN = ("ChatGPT", "Gemini", "OpenAI")
LEGACY_FORBIDDEN_VALUES = (
    "2024.1.13",
    "2024년 1월 13일",
    "48장",
    "270장",
    "세 차례",
    "국내 전시가 세 번",
    "오후 4시 이후",
    "벽에 걸린 그림",
    "인스타그램",
    "A업체",
    "C업체",
)


def _iter_json_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_json_strings(item)
    elif isinstance(obj, dict):
        for val in obj.values():
            yield from _iter_json_strings(val)


def _find_jargon_violations(text: str) -> list[str]:
    found: list[str] = []
    for w in FORBIDDEN_KO_PLAIN:
        if w in text:
            found.append(w)
    for w in FORBIDDEN_EN_PLAIN:
        if w in text:
            found.append(w)
    for w in FORBIDDEN_EN_BOUNDED:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", text):
            found.append(w)
    if re.search(r"(?<![가-힣])색인", text):
        found.append("색인")
    if re.search(r"(?<!에이전트 )파이프라인", text):
        found.append("파이프라인")
    return found


def _make_synthetic_backup_zip(zip_path: Path, bank_id: str = "synth-bank") -> None:
    manifest = {
        "schema_version": 1,
        "source_bank_id": bank_id,
        "document_count": 2,
        "fact_count": 3,
    }
    long_body = (
        "실내 탁자 위에 놓인 커피 잔과 작은 접시를\n\t근접 촬영한 장면이다   "
        + ("추가설명글자" * 20)
        + " | When: 2025-01-02 14:00 | Where: 서울 | Involving: user"
    )
    doc_photo = {
        "id": "photo:folder/sample_01.jpg",
        "original_text": "[사진] 사진 id: folder/sample_01.jpg\n실내 탁자 장면",
        "retain_params": {"event_date": "2025-01-02T14:00:00+09:00"},
        "facts": [
            {
                "text": long_body,
                "occurred_start": "2025-01-02T05:00:00Z",
                "created_at": "2025-01-10T01:00:00Z",
                "entities": ["커피", "접시", "user", "갤러리", "folder/sample_01.jpg", "010-1234-5678"],
                "causal_relations": [{"relation_type": "caused_by", "target_fact_index": 1}],
            },
            {
                "text": "같은 자리에서 디저트 접시를 함께 찍었다. | When: 2025-01-02 14:05",
                "occurred_start": "2025-01-02T05:05:00Z",
                "created_at": "2025-01-10T01:00:01Z",
                "entities": ["접시", "디저트"],
                "causal_relations": [],
            },
        ],
    }
    doc_tl = {
        "id": "timeline:2025-01-02",
        "original_text": "[하루 타임라인] 하루 기록\n- 14:00 · 카페\n- 14:05 · 카페",
        "retain_params": {"event_date": "2025-01-02T14:00:00+09:00"},
        "facts": [
            {
                "text": "오후에 카페에서 커피와 디저트를 마셨다. | When: 2025-01-02",
                "occurred_start": "2025-01-02T05:00:00Z",
                "created_at": "2025-01-10T01:00:02Z",
                "entities": ["커피", "카페"],
                "causal_relations": [],
            }
        ],
    }
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("documents/000000.json", json.dumps(doc_photo, ensure_ascii=False))
        zf.writestr("documents/000001.json", json.dumps(doc_tl, ensure_ascii=False))


# 1. 대본과 장면 설정의 모양 검사 (카탈로그 불필요)
def test_1_scripts_and_scenes_schema():
    assert CARD_TITLES["plan"] and CARD_TITLES["result"]
    scene_files = sorted(SCENES_DIR.glob("*.json"))
    run_files = sorted(RUNS_DIR.glob("*.json"))
    assert scene_files, "demo/scenes/ 에 파일이 없습니다"
    assert run_files, "demo/runs/ 에 파일이 없습니다"

    for sf in scene_files:
        sc = json.loads(sf.read_text(encoding="utf-8"))
        validate_scene_config(sc, label=sf.name)

    for rf in run_files:
        rec = json.loads(rf.read_text(encoding="utf-8"))
        validate_run_record(rec, label=rf.name, check_catalog=False, check_quotes=False)


# 2. 장면 설정의 on 조건과 케이스 A 좁히기 숫자 검사
def test_2_scene_conditions_and_narrow_counts():
    for sf in sorted(SCENES_DIR.glob("*.json")):
        sc = json.loads(sf.read_text(encoding="utf-8"))
        rf = RUNS_DIR / f"{sc['run']}.json"
        rec = json.loads(rf.read_text(encoding="utf-8"))
        unmatched = check_scene_conditions(sc, rec)
        assert unmatched == [], f"{sf.name} 에서 켜지지 않는 단계: {unmatched}"

    sc_a = json.loads((SCENES_DIR / "A.json").read_text(encoding="utf-8"))
    rec_a = json.loads((RUNS_DIR / "A.json").read_text(encoding="utf-8"))
    no_cat_counts = compute_narrow_counts(sc_a, rec_a, None)
    assert no_cat_counts[2] == 6

    if not settings.catalog.is_file():
        pytest.skip("카탈로그가 없어 272 -> 19 -> 6 검사를 건너뜁니다")
    photo_rows = [r for r in read_jsonl(settings.catalog) if r.get("kind") == "photo"]
    full_counts = compute_narrow_counts(sc_a, rec_a, photo_rows)
    assert full_counts == [272, 19, 6]


# 3. 카탈로그 id·badge·시각 표시 및 옛 백업 인용 검사
def test_3_catalog_and_old_backup_quotes():
    if not settings.catalog.is_file():
        pytest.skip("카탈로그 파일이 없어 건너뜁니다")
    catalog_rows = read_jsonl(settings.catalog)
    catalog_by_id = {r["id"]: r for r in catalog_rows if r.get("id")}

    scenario_zip = HINDSIGHT_DIR / "aidic-scenario.zip"
    old_quotes = load_old_backup_quotes(scenario_zip if scenario_zip.is_file() else None)

    for rf in sorted(RUNS_DIR.glob("*.json")):
        rec = json.loads(rf.read_text(encoding="utf-8"))
        validate_run_record(
            rec,
            label=rf.name,
            catalog_by_id=catalog_by_id,
            old_quote_map=old_quotes,
            check_catalog=True,
            check_quotes=old_quotes is not None,
        )


# 4. export_bank 검사
def test_4_export_bank():
    candidates = [
        HINDSIGHT_DIR / "aidic-mobile.zip",
        HINDSIGHT_DIR / "aidic-scenario.zip",
    ]
    present = [p for p in candidates if p.is_file()]
    if not present:
        pytest.skip("백업 zip이 없어 건너뜁니다")

    for zp in present:
        sha = hashlib.sha256(zp.read_bytes()).hexdigest()
        res1 = export_bank(zp)
        res2 = export_bank(zp)
        assert res1 == res2
        st = res1["stats"]
        if sha == OLD_BACKUP_SHA256:
            assert (
                st["memories"],
                st["time_links"],
                st["entity_links"],
                st["causal_links"],
                st["words"],
            ) == (365, 1112, 622, 4, 814)
        else:
            assert st["memories"] == res1["fact_count_manifest"]


# 5. 화면 문구 검사 (금지어, 물결표, 옛 값)
def test_5_ui_wording_and_legacy_values():
    for path in sorted(DEMO_DIR.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or "recall" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for legacy in LEGACY_FORBIDDEN_VALUES:
            assert legacy not in text, f"{path.relative_to(WS)} 에 옛 값 '{legacy}' 가 있습니다"

        if path.suffix in (".js", ".html", ".css"):
            bad = _find_jargon_violations(text)
            assert not bad, f"{path.relative_to(WS)} 에 금지어가 있습니다: {bad}"

    for folder in (RUNS_DIR, SCENES_DIR):
        for jf in sorted(folder.glob("*.json")):
            obj = json.loads(jf.read_text(encoding="utf-8"))
            for s in _iter_json_strings(obj):
                bad = _find_jargon_violations(s)
                assert not bad, f"{jf.name} 문자열에 금지어가 있습니다: {bad} ({s!r})"
                assert "~" not in s, f"{jf.name} 문자열에 물결표(~)가 있습니다: {s!r}"


# 6. 시나리오 값 검사 (JSON 에서 동적으로 읽어 코드 파일에 없는지 확인)
def test_6_no_scenario_literals_in_code():
    literals: set[str] = set()
    for rf in sorted(RUNS_DIR.glob("*.json")):
        rec = json.loads(rf.read_text(encoding="utf-8"))
        if rec.get("query"):
            literals.add(str(rec["query"]).strip())
        for ev in rec.get("events") or []:
            if ev.get("type") == "ask":
                for opt in ev.get("options") or []:
                    if isinstance(opt, dict) and opt.get("label"):
                        literals.add(str(opt["label"]).strip())
        for pid in collect_run_photo_ids(rec):
            stem = Path(pid).stem
            if len(stem) >= 4:
                literals.add(stem)

    for sf in sorted(SCENES_DIR.glob("*.json")):
        sc = json.loads(sf.read_text(encoding="utf-8"))
        for pid in sc.get("spotlight") or []:
            stem = Path(pid).stem
            if len(stem) >= 4:
                literals.add(stem)

    assert literals, "검사할 시나리오 값이 비었습니다"
    for code_path in sorted(DEMO_DIR.rglob("*")):
        if not code_path.is_file() or code_path.suffix not in (".py", ".js", ".html", ".css"):
            continue
        src = code_path.read_text(encoding="utf-8")
        for lit in sorted(literals):
            assert lit not in src, f"{code_path.relative_to(WS)} 에 시나리오 값 '{lit}' 가 적혀 있습니다"


# 7. /demo 길 검사
def test_7_demo_route(tmp_path, monkeypatch):
    missing_file = tmp_path / "no_demo.html"
    monkeypatch.setattr(main, "DEMO_PAGE", missing_file)
    client = TestClient(main.app)
    r404 = client.get("/demo")
    assert r404.status_code == 404
    assert "demo.build" in r404.text

    present_file = tmp_path / "demo.html"
    present_file.write_text("<!doctype html><title>ok</title>", encoding="utf-8")
    monkeypatch.setattr(main, "DEMO_PAGE", present_file)
    r200 = client.get("/demo")
    assert r200.status_code == 200
    assert "text/html" in r200.headers.get("content-type", "")
    assert "<title>ok</title>" in r200.text


# 8. 좌표 빼기 검사
def test_8_strip_web_card_coordinates():
    base_rec = json.loads((RUNS_DIR / "A.json").read_text(encoding="utf-8"))
    synth = json.loads(json.dumps(base_rec))
    for ev in synth["events"]:
        if ev["type"] == "card" and ev["kind"] == "web":
            ev["data"]["events"][0]["lat"] = 37.4791
            ev["data"]["events"][0]["lon"] = 127.0118
            ev["data"]["events"][0]["name"] = "샘플행사명"
            break

    cleaned = sanitize_run_for_page(synth)
    dumped = json.dumps(cleaned, ensure_ascii=False)
    assert '"lat"' not in dumped
    assert '"lon"' not in dumped
    assert "37.4791" not in dumped
    assert "127.0118" not in dumped
    web_ev = next(e for e in cleaned["events"] if e["type"] == "card" and e["kind"] == "web")
    assert web_ev["data"]["events"][0]["name"] == "샘플행사명"
    assert "status" in web_ev["data"]["events"][0]


# 9. memory 와 reflect 이벤트 넣기 검사
def test_9_memory_and_reflect_sanitization():
    raw_mem_text_1 = "비밀본문원문_남으면안됨_첫번째 | When: 2025-03-01"
    raw_mem_text_2 = "비밀본문원문_남으면안됨_두번째 | When: 2025-03-02"
    synth_run = {
        "run_id": "synth_01",
        "test": "S",
        "query": "테스트 질문",
        "started_at": "2026-11-16T00:00:00+00:00",
        "elapsed_ms": 12300,
        "today": "2026-11-16",
        "status": "answered",
        "error": None,
        "answers": [],
        "result_ids": ["folder/p1.jpg"],
        "result_sentence": "한 장 찾았어요.",
        "calls": [
            {
                "purpose": "loop",
                "ms": 1500,
                "cost_usd": 0.05,
                "model": "secret-loop-model-name",
                "input_tokens": 1234,
                "output_tokens": 567,
            }
        ],
        "summary": {
            "calls": 1,
            "cost_usd": 0.05,
            "input_tokens": 1234,
            "output_tokens": 567,
        },
        "tools": [{"name": "recall_memory", "ok": True}],
        "hindsight": {"reflect_calls": 1, "cost_usd": 0.42},
        "events": [
            {
                "type": "memory",
                "run_id": "synth_01",
                "seq": 1,
                "n": 1,
                "query": "테스트 질문",
                "bank": "synth-bank",
                "elapsed_ms": 800,
                "count": 2,
                "hits": [
                    {
                        "rank": 1,
                        "document_id": "photo:folder/p1.jpg",
                        "kind": "photo",
                        "source_id": "folder/p1.jpg",
                        "text": raw_mem_text_1,
                    },
                    {
                        "rank": 2,
                        "document_id": None,
                        "kind": "other",
                        "source_id": None,
                        "text": raw_mem_text_2,
                    },
                ],
                "error": None,
            },
            {
                "type": "memory",
                "run_id": "synth_01",
                "seq": 2,
                "n": 2,
                "query": "두 번째 질의",
                "bank": "synth-bank",
                "elapsed_ms": 120,
                "count": 0,
                "hits": [],
                "error": "연결 시간 초과",
            },
            {
                "type": "reflect",
                "run_id": "synth_01",
                "seq": 3,
                "n": 1,
                "query": "테스트 질문",
                "known": "확인 사실",
                "bank": "synth-bank",
                "elapsed_ms": 95000,
                "count": 1,
                "photo_ids": ["folder/p1.jpg"],
                "left_out": 0,
                "reasoning": "시각과 장소가 맞는 후보 사진입니다.",
                "inner_calls": 2,
                "inner_queries": ["하위 질의"],
                "usage": {
                    "bank": "synth-bank",
                    "model": "secret-reflect-model-xyz",
                    "calls": 6,
                    "input_tokens": 8888,
                    "output_tokens": 777,
                    "cache_read_input_tokens": 6666,
                    "cost_usd": 0.64,
                    "cost_known": True,
                    "ms": 95000,
                    "ok": True,
                },
                "error": None,
            },
        ],
    }

    cleaned = sanitize_run_for_page(synth_run)
    dumped = json.dumps(cleaned, ensure_ascii=False)

    assert cleaned["today"] == "2026-11-16"
    assert "tools" not in cleaned and '"tools"' not in dumped
    assert "hindsight" not in cleaned and '"hindsight"' not in dumped

    m1, m2, rf = cleaned["events"]
    assert set(m1.keys()) == {"type", "run_id", "seq", "n", "query", "bank", "elapsed_ms", "count", "error", "hits"}
    assert set(m1["hits"][0].keys()) == {"rank", "kind", "id", "key"}
    assert m1["hits"][1]["id"] is None
    assert m2["error"] == "연결 시간 초과" and m2["hits"] == []
    assert '"document_id"' not in dumped
    assert raw_mem_text_1 not in dumped and "When:" not in dumped

    assert set(rf.keys()) == {
        "type",
        "run_id",
        "seq",
        "n",
        "query",
        "known",
        "bank",
        "elapsed_ms",
        "count",
        "photo_ids",
        "left_out",
        "reasoning",
        "inner_calls",
        "inner_queries",
        "usage",
        "error",
    }
    assert set(rf["usage"].keys()) == {"calls", "cost_usd", "cost_known", "ms", "ok"}
    assert "secret-reflect-model-xyz" not in dumped
    assert "secret-loop-model-name" not in dumped
    assert "input_tokens" not in dumped and "output_tokens" not in dumped

    # memory, reflect, today, tools, hindsight 가 없는 대본 기록도 문제없이 처리
    script_rec = json.loads((RUNS_DIR / "A.json").read_text(encoding="utf-8"))
    cleaned_script = sanitize_run_for_page(script_rec, default_kind="대본")
    assert cleaned_script["demo"]["kind"] == "대본"


# 10. 비교용 꺼낸 기억 넣기 검사
def test_10_compare_recall_sanitization(tmp_path):
    zp = tmp_path / "synth.zip"
    _make_synthetic_backup_zip(zp, bank_id="synth-bank")
    bank_exp = export_bank(zp)
    const_payload = build_constellation_payload(bank_exp)
    star0 = const_payload["stars"][0]

    assert "When:" not in star0["key"]
    assert "\n" not in star0["key"] and "\t" not in star0["key"] and "  " not in star0["key"]
    assert len(star0["key"]) == 100

    same_raw_text = (
        "실내 탁자 위에 놓인 커피 잔과 작은 접시를\n\t근접 촬영한 장면이다   "
        + ("추가설명글자" * 20)
        + " | When: 2025-01-02 14:00 | Where: 서울 | Involving: user"
    )
    raw_recall = {
        "bank": "synth-bank",
        "run": "S",
        "query": "커피   접시\n질문",
        "budget": "mid",
        "max_tokens": 8192,
        "elapsed_ms": 420,
        "hits": [
            {
                "rank": 1,
                "document_id": "photo:folder/sample_01.jpg",
                "kind": "photo",
                "source_id": "folder/sample_01.jpg",
                "text": same_raw_text,
                "type": "world",
                "occurred_start": "2025-01-02T05:00:00Z",
            }
        ],
    }

    clean_rec, skip_reason = sanitize_compare_recall(
        raw_recall,
        expected_bank="synth-bank",
        expected_query="  커피 접시 질문 ",
    )
    assert skip_reason is None
    assert clean_rec is not None
    assert clean_rec["hits"][0]["key"] == star0["key"]
    dumped = json.dumps(clean_rec, ensure_ascii=False)
    for forbidden in ('"text"', '"type"', '"occurred_start"', '"document_id"', '"budget"', '"max_tokens"'):
        assert forbidden not in dumped

    wrong_bank, reason_bank = sanitize_compare_recall(
        raw_recall,
        expected_bank="other-bank",
        expected_query="커피 접시 질문",
    )
    assert wrong_bank is None and reason_bank and "은행" in reason_bank

    wrong_q, reason_q = sanitize_compare_recall(
        raw_recall,
        expected_bank="synth-bank",
        expected_query="전혀 다른 질문",
    )
    assert wrong_q is None and reason_q and "질의" in reason_q


# 11. 대시보드 ②의 숫자 검사
def test_11_pipeline_summary_counts(tmp_path):
    zp = tmp_path / "synth.zip"
    _make_synthetic_backup_zip(zp, bank_id="synth-bank")
    bank_exp = export_bank(zp)

    synth_places = {
        "groups": [
            {
                "group": "G01",
                "photos": 5,
                "days": ["2025-01-01", "2025-01-02"],
                "spread_m": 42,
                "lat": 37.5,
                "lon": 127.0,
                "nearby": {"towns": [{"name": "TestTown"}]},
                "area_en": "AreaOne",
                "area_ko": "가동네",
                "near_en": "SpotOne",
                "near_ko": "가명소",
            },
            {
                "group": "G02",
                "photos": 8,
                "days": ["2025-01-03"],
                "spread_m": 15,
                "lat": 35.1,
                "lon": 129.1,
                "nearby": {},
                "area_en": "AreaTwo",
                "area_ko": "",
                "near_en": "SpotTwo",
                "near_ko": "나근처",
            },
            {
                "group": "G03",
                "photos": 5,
                "days": ["2025-01-04"],
                "spread_m": 10,
                "lat": 36.0,
                "lon": 128.0,
                "nearby": {},
                "area_en": "AreaThree",
                "area_ko": "",
                "near_en": "",
                "near_ko": "",
            },
        ],
        "photos": {
            "p1.jpg": {"place_group": "G01", "visit": "G01_V1"},
            "p2.jpg": {"place_group": "G01", "visit": "G01_V2"},
            "p3.jpg": {"place_group": "G02", "visit": "G02_V1"},
            "p4.jpg": {"place_group": "G02", "visit": "G02_V1"},
            "p5.jpg": {"place_group": "G03", "visit": "G03_V1"},
        },
    }

    summary = build_pipeline_summary(synth_places, None, bank_exp["timeline_days"])
    assert summary["groups"] == 3
    assert summary["photos_in_groups"] == 18
    assert summary["visits"] == 4
    assert summary["named"] == 2
    assert summary["links"] is None
    assert summary["timeline_days"] == 1
    assert [g["label"] for g in summary["top_groups"]] == ["나근처", "가동네", "이름 없는 자리"]

    dumped = json.dumps(summary, ensure_ascii=False)
    for forbidden in ('"lat"', '"lon"', '"spread_m"', '"nearby"', "AreaOne", "AreaTwo", "SpotOne"):
        assert forbidden not in dumped

    empty_summary = build_pipeline_summary(None, None, bank_exp["timeline_days"])
    assert empty_summary["groups"] is None
    assert empty_summary["photos_in_groups"] is None
    assert empty_summary["visits"] is None
    assert empty_summary["named"] is None
    assert empty_summary["timeline_days"] == 1


# 12. 문서 케이스 B 대본·장면 설정·좁히기·인용 검사
def test_12_document_case_b_and_narrow_counts():
    sc_b_path = SCENES_DIR / "B.json"
    run_b_path = RUNS_DIR / "B.json"
    if not sc_b_path.is_file() or not run_b_path.is_file():
        pytest.skip("케이스 B 파일이 없어 건너뜁니다")

    sc_b = json.loads(sc_b_path.read_text(encoding="utf-8"))
    rec_b = json.loads(run_b_path.read_text(encoding="utf-8"))
    validate_scene_config(sc_b, label="B.json")
    assert sc_b["kind"] == "document"
    assert rec_b["demo"]["kind"] == "설계 미리보기"
    assert check_scene_conditions(sc_b, rec_b) == []

    counts_127 = compute_narrow_counts(sc_b, rec_b, None, catalog_docs_count=127)
    assert counts_127 == [127, 5, 3]

    if settings.catalog.is_file() and settings.docs.is_file():
        cat_rows = read_jsonl(settings.catalog)
        cat_by_id = {r["id"]: r for r in cat_rows if r.get("id")}
        doc_map = {r["id"]: str(r.get("body") or "") for r in read_jsonl(settings.docs) if r.get("id")}
        scenario_zip = HINDSIGHT_DIR / "aidic-scenario.zip"
        old_quotes = load_old_backup_quotes(scenario_zip if scenario_zip.is_file() else None)
        validate_run_record(
            rec_b,
            label="B.json",
            catalog_by_id=cat_by_id,
            old_quote_map=old_quotes,
            doc_text_map=doc_map,
            scene_doc_lines=sc_b.get("doc_lines") or {},
            check_catalog=True,
            check_quotes=True,
        )


# 13. 문서 백업 선택 및 문서/문서사진 전용 낱말의 레이블 제외 검사
def test_13_doc_bank_selection_and_label_exclusion(tmp_path):
    zip_path = tmp_path / "synth-docs-bank.zip"
    manifest = {"source_bank_id": "synth-docs-bank", "fact_count": 3}
    d_photo_normal = {
        "id": "photo:DCIM/Camera/normal.jpg",
        "original_text": "[사진] normal",
        "retain_params": {"event_date": "2025-01-02T10:00:00+09:00"},
        "facts": [
            {
                "text": "일반 사진 기억",
                "occurred_start": "2025-01-02T10:00:00+09:00",
                "created_at": "2025-01-10T01:00:00Z",
                "entities": ["일반풍경낱말"],
                "causal_relations": [],
            }
        ],
    }
    d_photo_doc = {
        "id": "photo:DCIM/Camera/doc_photo.jpg",
        "original_text": "[사진] doc photo",
        "retain_params": {"event_date": "2025-01-03T10:00:00+09:00"},
        "facts": [
            {
                "text": "문서 사진 기억",
                "occurred_start": "2025-01-03T10:00:00+09:00",
                "created_at": "2025-01-10T01:01:00Z",
                "entities": ["문서사진전용이름"],
                "causal_relations": [],
            }
        ],
    }
    d_doc = {
        "id": "doc:Download/estimate.pdf",
        "original_text": "[문서] estimate",
        "retain_params": {"event_date": "2025-01-04T09:00:00+09:00"},
        "facts": [
            {
                "text": "문서 본문 기억",
                "occurred_start": "2025-01-04T09:00:00+09:00",
                "created_at": "2025-01-10T01:02:00Z",
                "entities": ["문서본문전용이름"],
                "causal_relations": [],
            }
        ],
    }
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("documents/000000.json", json.dumps(d_photo_normal, ensure_ascii=False))
        zf.writestr("documents/000001.json", json.dumps(d_photo_doc, ensure_ascii=False))
        zf.writestr("documents/000002.json", json.dumps(d_doc, ensure_ascii=False))

    exp = export_bank(zip_path, exclude_label_photo_ids={"DCIM/Camera/doc_photo.jpg"})
    assert exp["has_doc_memories"] is True
    label_words = {exp["words"][idx] for idx in exp["labels"]}
    assert "일반풍경낱말" in label_words
    assert "문서본문전용이름" not in label_words
    assert "문서사진전용이름" not in label_words

    # 실제 aidic-mobile-docs.zip 이 있을 때도 doc: / doc_photos 전용 낱말이 labels 에 없는지 검증
    real_docs_zip = HINDSIGHT_DIR / "aidic-mobile-docs.zip"
    sc_b_path = SCENES_DIR / "B.json"
    if real_docs_zip.is_file() and sc_b_path.is_file():
        sc_b = json.loads(sc_b_path.read_text(encoding="utf-8"))
        excl_pids = set(sc_b.get("doc_photos") or [])
        real_exp = export_bank(real_docs_zip, exclude_label_photo_ids=excl_pids)
        assert real_exp["has_doc_memories"] is True
        allowed_word_indices: set[int] = set()
        for st in real_exp["stars"]:
            if st["kind"] != "doc" and not (st["kind"] == "photo" and st["id"] in excl_pids):
                allowed_word_indices.update(st["words"])
        for l_wi in real_exp["labels"]:
            assert l_wi in allowed_word_indices, (
                f"문서/문서사진에서만 나온 낱말({real_exp['words'][l_wi]})이 labels 에 들어갔습니다"
            )
