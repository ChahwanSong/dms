"""sync 진행률 계약(frontend/src/lib/progressContract.json, 2026-11-26)의 형식 검사 -- 구현 없이 초록이다.

계약 파일은 러너(dms_job_runner.progress 가 <phase>/progress.json 을 쓴다)·API(dms.job_progress · GET
/api/user/jobs/{job_id}/progress)·포탈(lib/types.ts JobProgressResponse·ProgressSnapshot) 셋의 단일 원천이다(reasonCodes.json
과 같은 원리 -- 계약이 바뀌면 양쪽 시험이 함께 빨개진다). 여기서 보는 것은 셋이다:

1. 파일 자체의 모양 -- 필드 서술자(type·범위·required·tools·mode), phase 목록, 불변식, 남은 시간 금지 규칙.
2. 예(examples)가 그 표를 만족하는가 -- 예 스냅숏은 실측 로그에서 만들었고, 구현 레인 시험(러너 재생·API 멱등·포탈 msw)이
   그대로 가져다 쓴다. 표의 모든 키가 어느 예에든 한 번은 나온다(예가 계약 전체를 덮는다).
3. 이미 있는 코드와의 접점 -- 아티팩트 이름 규칙·phase 화이트리스트(dms.artifact_files), 사유 코드 목록(새 코드 없음), 포탈
   타입(lib/types.ts)의 키 집합.

러너·API 의 상수(FILE_NAME·SCHEMA_VERSION·PHASES·READ_MAX_BYTES)와 Tracker 출력·sanitize 의 대조는 그 모듈을 만드는 구현
레인의 시험이 덧붙인다 -- 이 파일은 아직 없는 모듈을 import 하지 않는다."""
import json
import math
import re
from pathlib import Path

import pytest

from dms.artifact_files import NAME_RE, PHASES as ARTIFACT_PHASES

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "frontend" / "src" / "lib"
INT_MAX = 2**63 - 1
TYPES = {"int", "float", "bool", "str", "enum", "obj"}
DESCRIPTOR_KEYS = {"type", "min", "max", "const", "values", "pattern", "fields", "required", "tools", "mode", "doc"}
EXAMPLE_NAMES = ["workers", "dsync_walk_src", "dsync_create", "dsync_copy_overall", "dsync_ended", "nsync_scan",
                 "nsync_batches_exec", "nsync_batches_dry", "quiet_start"]
TS_TYPE = {"int": "number", "float": "number", "bool": "boolean", "str": "string", "enum": "string"}


def _no_duplicate_keys(pairs):
    # 손으로 고치는 JSON 이라 같은 키가 두 번 나오면 뒤의 것이 조용히 이긴다 -- 읽을 때 막는다.
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ValueError(f"progressContract.json 중복 키: {dup}")
    return dict(pairs)


CONTRACT = json.loads((LIB / "progressContract.json").read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
SNAP = CONTRACT["snapshot"]
FIELDS = SNAP["fields"]
EXAMPLES = CONTRACT["examples"]
RESPONSE = CONTRACT["api"]["response"]


def _value_problems(desc: dict, value, where: str, snap: dict) -> list[str]:
    """서술자 하나와 값 하나 -- 틀린 곳의 설명 목록(빈 목록 = 맞음). snap 은 tools·mode 판정용 스냅숏 전체."""
    t = desc["type"]
    if t == "int" and type(value) is not int:                      # bool 은 int 의 하위형이라 type() 으로 가른다
        return [f"{where}: 정수가 아니다 ({value!r})"]
    if t == "float" and (type(value) not in (int, float) or not math.isfinite(value)):
        return [f"{where}: 유한한 수가 아니다 ({value!r})"]
    if t == "bool" and type(value) is not bool:
        return [f"{where}: bool 이 아니다 ({value!r})"]
    if t == "str" and not (isinstance(value, str) and re.fullmatch(desc["pattern"], value)):
        return [f"{where}: pattern 밖 ({value!r})"]
    if t == "enum" and not (isinstance(value, str) and value in desc["values"]):
        return [f"{where}: 열거 밖 ({value!r})"]
    out = []
    if t in ("int", "float"):
        if "const" in desc:
            if value != desc["const"]:
                out.append(f"{where}: const {desc['const']!r} 가 아니다 ({value!r})")
        elif not desc["min"] <= value <= desc["max"]:
            out.append(f"{where}: 범위 밖 ({value!r})")
    if t == "obj":
        if not isinstance(value, dict) or not value:
            return [f"{where}: 비지 않은 객체가 아니다 ({value!r})"]
        for k, v in value.items():
            sub = desc["fields"].get(k)
            out += [f"{where}.{k}: 계약에 없는 키"] if sub is None else _value_problems(sub, v, f"{where}.{k}", snap)
    if t == "snapshot":
        out += _snapshot_problems(value, where)
    if "tools" in desc and snap.get("tool") not in desc["tools"]:
        out.append(f"{where}: {desc['tools']} 도구의 스냅숏에만 ({snap.get('tool')!r})")
    if "mode" in desc and snap.get("dryrun") is not (desc["mode"] == "preview"):
        out.append(f"{where}: {desc['mode']} 스냅숏에만 (dryrun={snap.get('dryrun')!r})")
    return out


def _snapshot_problems(snap, where: str = "snapshot") -> list[str]:
    """snapshot.fields 표(API sanitize 가 보는 것 + tools·mode)."""
    if not isinstance(snap, dict):
        return [f"{where}: 객체가 아니다"]
    out = []
    for k, v in snap.items():
        out += [f"{where}.{k}: 계약에 없는 키"] if k not in FIELDS else _value_problems(FIELDS[k], v, f"{where}.{k}", snap)
    out += [f"{where}.{k}: required 인데 없다" for k, d in FIELDS.items() if d.get("required") and k not in snap]
    return out


def _runner_shape_problems(snap: dict) -> list[str]:
    """러너가 지키는 출력 모양 -- runner_always·도구별 phase·불변식·크기·남은 시간 금지."""
    out = [f"runner_always {k} 없음" for k in SNAP["runner_always"] if k not in snap]
    by_tool = SNAP["phases_by_tool"]
    if snap.get("phase") not in by_tool["common"] + by_tool.get(snap.get("tool"), []):
        out.append(f"phase {snap.get('phase')!r} 는 {snap.get('tool')!r} 가 내는 단계가 아니다")
    inv = SNAP["invariants"]
    present = [snap[k] for k in inv["seconds_order"] if k in snap]
    if present != sorted(present):
        out.append(f"seconds_order 위반 {[(k, snap[k]) for k in inv['seconds_order'] if k in snap]}")
    if inv["since_line_iff_lines"] and ("since_line_s" in snap) != (snap.get("lines", 0) > 0):
        out.append("since_line_s 가 있다 ⇔ lines > 0 위반")
    compact = json.dumps(snap, separators=(",", ":"), ensure_ascii=False)
    if len(compact.encode("utf-8")) > SNAP["max_bytes"]:
        out.append(f"직렬화 {len(compact.encode('utf-8'))} B > max_bytes")
    out += [f"금지 키 {k}" for k in SNAP["forbidden_keys"] if f'"{k}":' in compact]
    out += [f"금지 글자 {s!r}" for s in SNAP["forbidden_substrings"] if s in compact.lower()]
    return out


def _descriptors(fields: dict, prefix: str = ""):
    for name, desc in fields.items():
        yield f"{prefix}{name}", desc, prefix == ""
        if desc.get("type") == "obj":
            yield from _descriptors(desc["fields"], f"{prefix}{name}.")


def _ts_interface(name: str) -> dict[str, tuple[bool, str]]:
    """lib/types.ts 의 평평한 interface 본문 -- {키: (선택인가, 타입 글자)}."""
    src = (LIB / "types.ts").read_text(encoding="utf-8")
    m = re.search(rf"^export interface {name} \{{\n(.*?)^\}}", src, re.S | re.M)
    assert m, f"lib/types.ts 에 export interface {name} 가 없다"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return {k: (opt == "?", typ.strip()) for k, opt, typ in re.findall(r"(\w+)(\?)?:\s*([^;]+);", body)}


# --- 1. 파일 자체의 모양 ---------------------------------------------------------------------------------------------

def test_top_level_shape():
    assert set(CONTRACT) == {"_doc", "schema_version", "file_name", "phases_param", "api", "snapshot",
                             "ui_forbidden_substrings", "examples", "summary_keys_added", "poll_ms"}
    assert type(CONTRACT["schema_version"]) is int and CONTRACT["schema_version"] == 1
    assert set(EXAMPLES) == {"_doc", "snapshots", "sources", "responses"}


def test_file_name_and_phase_param_meet_the_existing_artifact_rules():
    # 목록·열기 API(artifact_files)가 받는 이름이어야 진행 파일이 칩·라우트에 보인다. 임시 파일 이름(러너 몫)은 반대로 밖이어야 한다.
    name = CONTRACT["file_name"]
    assert name == "progress.json" and NAME_RE.fullmatch(name) and not set(name) <= {"."}
    assert CONTRACT["phases_param"] == ["preview", "execution"]
    assert set(CONTRACT["phases_param"]) <= set(ARTIFACT_PHASES)


def test_field_descriptors_are_well_formed():
    tools = FIELDS["tool"]["values"]
    for path, desc, top in _descriptors(FIELDS):
        assert set(desc) <= DESCRIPTOR_KEYS, (path, set(desc) - DESCRIPTOR_KEYS)
        assert isinstance(desc.get("doc"), str) and desc["doc"].strip(), path
        t = desc["type"]
        assert t in TYPES, (path, t)
        if t in ("int", "float"):
            if "const" in desc:
                assert type(desc["const"]) is int and "min" not in desc and "max" not in desc, path
            else:
                lo, hi = desc["min"], desc["max"]
                kinds = (int,) if t == "int" else (int, float)
                assert type(lo) in kinds and type(hi) in kinds and math.isfinite(lo) and math.isfinite(hi), path
                assert 0 <= lo <= hi, path
                if t == "int":
                    assert hi <= INT_MAX, path
        else:
            assert not {"min", "max", "const"} & set(desc), path
        if t == "enum":
            vals = desc["values"]
            assert vals and len(set(vals)) == len(vals) and all(isinstance(v, str) for v in vals), path
        if t == "str":
            p = desc["pattern"]
            assert p.startswith("^") and p.endswith("$"), path
            re.compile(p)
        assert ("pattern" in desc) == (t == "str") and ("values" in desc) == (t == "enum"), path
        assert ("fields" in desc) == (t == "obj"), path
        if t == "obj":
            # 한 단계 깊이만(sanitize·포탈 타입이 평평한 하위 객체를 전제한다), 빈 표 금지.
            assert top and desc["fields"], path
            assert all(sub["type"] != "obj" for sub in desc["fields"].values()), path
        if "tools" in desc:
            assert top and desc["tools"] and set(desc["tools"]) <= set(tools), path
        if "mode" in desc:
            assert not top and desc["mode"] in ("execution", "preview"), path
        if "required" in desc:
            assert top and desc["required"] is True, path


def test_required_version_and_runner_always():
    assert {k for k, d in FIELDS.items() if d.get("required")} == {"v", "tool", "phase"}
    assert FIELDS["v"] == {**FIELDS["v"], "type": "int", "const": CONTRACT["schema_version"]}
    assert FIELDS["tool"]["values"] == ["dsync", "nsync"]
    always = SNAP["runner_always"]
    assert len(set(always)) == len(always) and set(always) <= set(FIELDS)
    assert {k for k, d in FIELDS.items() if d.get("required")} <= set(always)
    # 하위 객체는 「모르면 키를 뺀다」라 늘 쓰는 키에 들지 않는다.
    assert all(FIELDS[k]["type"] != "obj" for k in always)


def test_phase_list():
    phases = SNAP["phases"]
    assert len(phases) == 18 and len(set(phases)) == 18
    assert all(re.fullmatch(FIELDS["phase"]["pattern"], p) for p in phases)
    by_tool = SNAP["phases_by_tool"]
    assert set(by_tool) == {"common", *FIELDS["tool"]["values"]}
    for name, lst in by_tool.items():
        assert lst and len(set(lst)) == len(lst) and set(lst) <= set(phases), name
    assert set().union(*by_tool.values()) == set(phases)
    # 러너가 붙이는 단계(run_job 맨 앞 workers, mpirun 직전 start)는 도구 줄에서 나오지 않는다.
    assert by_tool["common"] == ["workers", "start"]
    assert all(not set(by_tool["common"]) & set(lst) for name, lst in by_tool.items() if name != "common")


def test_invariants_refer_to_real_fields():
    inv = SNAP["invariants"]
    assert set(inv) == {"_doc", "seconds_order", "since_line_iff_lines"}
    assert inv["seconds_order"] == ["since_line_s", "phase_since_s", "elapsed_s"]
    assert all(FIELDS[k]["type"] == "float" for k in inv["seconds_order"])
    assert inv["since_line_iff_lines"] is True and FIELDS["lines"]["type"] == "int"


def test_remaining_time_is_never_carried():
    # 사용자 결정(남은 시간은 표시하지 않는다) + 설계 D4(도구 원문 줄 `line` 은 싣지 않는다 -- 원문의 「N secs left」가 칩으로 샌다).
    assert "line" in SNAP["forbidden_keys"]
    names = [path.rsplit(".", 1)[-1] for path, _, _ in _descriptors(FIELDS)]
    assert not set(SNAP["forbidden_keys"]) & set(names)
    assert not [n for n in names for s in SNAP["forbidden_substrings"] if s in n.lower()]
    assert {"left", "remaining"} <= set(SNAP["forbidden_substrings"])
    assert {"남은", "left"} <= set(CONTRACT["ui_forbidden_substrings"])


def test_api_section():
    api = CONTRACT["api"]
    assert set(api) == {"_doc", "path", "query_param", "default_phase", "headers", "read_max_bytes", "response"}
    assert api["path"] == "/api/user/jobs/{job_id}/progress" and api["query_param"] == "phase"
    assert api["default_phase"] in CONTRACT["phases_param"]
    assert api["headers"] == {"Cache-Control": "no-store"}
    # API 읽기 상한은 러너 직렬화 상한보다 넉넉하다(앞으로 늘 필드 여유).
    assert type(api["read_max_bytes"]) is int and type(SNAP["max_bytes"]) is int
    assert 0 < SNAP["max_bytes"] < api["read_max_bytes"]


def test_response_table():
    fields = RESPONSE["fields"]
    assert list(fields) == RESPONSE["available_true_keys"]
    assert set(RESPONSE["available_false_keys"]) < set(RESPONSE["available_true_keys"])
    assert fields["phase"]["values"] == CONTRACT["phases_param"]
    assert fields["progress"]["type"] == "snapshot" and fields["available"]["type"] == "bool"
    assert fields["age_seconds"]["type"] == "int" and fields["age_seconds"]["min"] == 0


def test_summary_keys_and_poll_ms():
    assert CONTRACT["summary_keys_added"] == ["output_truncated"]
    poll = CONTRACT["poll_ms"]
    assert set(poll) == {"normal", "slow"} and all(type(v) is int for v in poll.values())
    assert (poll["normal"], poll["slow"]) == (5000, 15000)


# --- 2. 예가 표를 만족한다 -------------------------------------------------------------------------------------------

def test_example_names_and_sources():
    assert list(EXAMPLES["snapshots"]) == EXAMPLE_NAMES
    assert list(EXAMPLES["sources"]) == EXAMPLE_NAMES
    assert all(isinstance(s, str) and s.strip() for s in EXAMPLES["sources"].values())


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_example_snapshot_satisfies_the_table(name):
    snap = EXAMPLES["snapshots"][name]
    assert _snapshot_problems(snap, name) == []
    assert _runner_shape_problems(snap) == []


def test_examples_cover_every_field_tool_and_mode():
    # 포탈·API 시험이 예만으로 모든 키를 지나가게 -- 표에 키를 더하면 그 키를 담은 예도 더해야 한다.
    snaps = list(EXAMPLES["snapshots"].values())
    for path, desc, top in _descriptors(FIELDS):
        if top:
            seen = any(path in s for s in snaps)
        else:
            parent, key = path.split(".")
            seen = any(key in s.get(parent, {}) for s in snaps)
        assert seen, f"{path} 를 담은 예가 없다"
    assert {s["tool"] for s in snaps} == set(FIELDS["tool"]["values"])
    assert {(s["tool"], s["dryrun"]) for s in snaps} >= {("dsync", False), ("dsync", True), ("nsync", False),
                                                          ("nsync", True)}
    assert any(s["quiet"] for s in snaps) and any(s["ended"] for s in snaps)
    assert {s["phase"] for s in snaps} >= {"workers", "start"}


def test_example_responses():
    resp = EXAMPLES["responses"]
    assert set(resp) == {"available", "available_preview", "unavailable", "errors"}
    snaps = list(EXAMPLES["snapshots"].values())
    for key in ("available", "available_preview"):
        body = resp[key]
        assert list(body) == RESPONSE["available_true_keys"], key
        assert body["available"] is True
        problems = [p for k, v in body.items() for p in _value_problems(RESPONSE["fields"][k], v, f"{key}.{k}", {})]
        assert problems == []
        # 응답의 progress 는 예 스냅숏을 그대로 펼친 것이다(두 벌이 갈라지지 않게).
        assert body["progress"] in snaps, key
    assert resp["available"]["phase"] == "execution" and resp["available_preview"]["phase"] == "preview"
    assert resp["available_preview"]["progress"]["dryrun"] is True
    off = resp["unavailable"]
    assert list(off) == RESPONSE["available_false_keys"] and off["available"] is False
    assert off["phase"] in CONTRACT["phases_param"]


def test_example_errors_use_existing_reason_codes_only():
    # 진행 라우트는 새 사유 코드를 내지 않는다(설계 §4.2) -- 쓰는 detail 은 모두 사유 코드 목록(양방향 계약)에 이미 있다.
    # dms-ssc 는 결과 파일 만료가 없어 410 artifact_expired 가 없다(dms-k8s 계약과 다른 곳).
    codes = set(json.loads((LIB / "reasonCodes.json").read_text(encoding="utf-8")))
    errors = EXAMPLES["responses"]["errors"]
    assert {e["status"] for e in errors} == {404, 422, 503}
    for e in errors:
        assert set(e) == {"status", "body"} and set(e["body"]) == {"detail"}, e
        assert e["body"]["detail"] in codes, e
    assert {e["body"]["detail"] for e in errors} == {"job_not_found", "artifact_not_found", "invalid_phase",
                                                     "artifact_base_unavailable"}


# --- 3. 포탈 타입(lib/types.ts)이 계약과 같은 키를 든다 ---------------------------------------------------------------

def test_portal_snapshot_types_match_the_contract():
    snap_ts = _ts_interface("ProgressSnapshot")
    assert set(snap_ts) == set(FIELDS)
    assert all(optional for optional, _ in snap_ts.values()), "ProgressSnapshot 의 키는 전부 선택(키 없음 = 모름)"
    for name, desc in FIELDS.items():
        typ = snap_ts[name][1]
        if desc["type"] == "obj":
            sub_ts = _ts_interface(typ)
            assert set(sub_ts) == set(desc["fields"]), name
            assert all(optional for optional, _ in sub_ts.values()), typ
            assert {k: t for k, (_, t) in sub_ts.items()} == {k: TS_TYPE[d["type"]] for k, d in desc["fields"].items()}
        else:
            assert typ == TS_TYPE[desc["type"]], (name, typ)


def test_portal_response_type_matches_the_contract():
    resp_ts = _ts_interface("JobProgressResponse")
    assert set(resp_ts) == set(RESPONSE["fields"])
    assert {k for k, (optional, _) in resp_ts.items() if not optional} == set(RESPONSE["available_false_keys"])
    assert {k: t for k, (_, t) in resp_ts.items()} == {"phase": "string", "available": "boolean",
                                                         "age_seconds": "number", "progress": "ProgressSnapshot"}


def test_portal_tool_summary_has_the_added_keys():
    src = (LIB / "types.ts").read_text(encoding="utf-8")
    m = re.search(r"^export interface ToolSummary \{\n(.*?)^\}", src, re.S | re.M)
    assert m
    body = re.sub(r"//[^\n]*", "", m.group(1))
    for key in CONTRACT["summary_keys_added"]:
        assert re.search(rf"\b{key}\?: boolean;", body), key
