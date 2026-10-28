"""배치 항목 CSV 해석·내보내기·템플릿(batch_csv, 2026-10-26 BACKLOG C) -- 순수 함수. 라우트는 test_api_batch_csv.py.

계약: 포탈 lib/csv.ts 시절의 열(scan·rm = target|path 1열, sync = source,destination 2열)과 헤더 규칙을 그대로 받고,
BOM·따옴표·CRLF·빈 줄·내보내기 파일의 정보 열·수식 방어(')를 더 받는다. 줄 판정은 생성과 같은 함수(build_data_payload ·
validate_batch · rm_target_overlaps)다 -- 오류는 줄 번호(line = 물리 줄, row = 데이터 행)와 사유 코드로."""
import pytest

from dms.batch_csv import (MAX_CSV_ROWS, export_items_csv, parse_items_csv, template_csv)
from dms.domain import DomainValidationError, rm_target_overlaps, validate_batch
from dms.repositories.request_purges import MAX_BATCH_DELETE_ITEMS


def _codes(r):
    return [(e["line"], e["row"], e["reason_code"]) for e in r["errors"]]


# --- 예전 포탈 파서(lib/csv.ts)와 같은 결과 ------------------------------------------------------------------------

def test_scan_headerless_path_list():
    r = parse_items_csv("scan", "a/b\nc")
    assert r["errors"] == [] and r["items"] == [{"target": "a/b"}, {"target": "c"}]
    assert r["rows"] == 2 and r["header"] is None


@pytest.mark.parametrize("head", ["target", "path", "Target", " PATH "])
def test_scan_header_tokens_case_insensitive(head):
    r = parse_items_csv("scan", f"{head}\na")
    assert r["items"] == [{"target": "a"}] and r["header"] == ["target"]


def test_first_line_that_is_not_a_header_is_data():
    assert parse_items_csv("scan", "data/target1\nb")["items"] == [{"target": "data/target1"}, {"target": "b"}]


def test_sync_two_columns_with_or_without_header():
    assert parse_items_csv("sync", "a,b\nc,d")["items"] == [{"source": "a", "destination": "b"},
                                                           {"source": "c", "destination": "d"}]
    r = parse_items_csv("sync", "SOURCE,Destination\na,b")
    assert r["items"] == [{"source": "a", "destination": "b"}] and r["header"] == ["source", "destination"]


def test_blank_lines_skipped_cells_trimmed_line_numbers_are_physical():
    r = parse_items_csv("sync", "\n a , b \n\nc\n")
    assert r["items"] == [{"source": "a", "destination": "b"}]
    # 물리 4번째 줄(빈 줄 포함), 데이터 2번째 행
    assert _codes(r) == [(4, 2, "batch_csv_bad_row")]


@pytest.mark.parametrize("text", ["a,\n", ",b\n", "a\n"])
def test_sync_wrong_shape_is_a_row_error(text):
    r = parse_items_csv("sync", text)
    assert _codes(r) == [(1, 1, "batch_csv_bad_row")] and r["items"] == []


def test_scan_comma_row_is_an_error_not_a_silent_split():
    assert _codes(parse_items_csv("scan", "a,b")) == [(1, 1, "batch_csv_bad_row")]
    assert _codes(parse_items_csv("rm", "a,b", storages={"storage": "s1"})) == [(1, 1, "batch_csv_bad_row")]


@pytest.mark.parametrize("text", ["", "  \n ", "target\n", "﻿target\r\n\r\n"])
def test_no_data_rows_is_empty_batch(text):
    r = parse_items_csv("scan", text)
    assert _codes(r) == [(None, None, "empty_batch")] and r["rows"] == 0


# --- 새로 받는 것: BOM·따옴표·CRLF·내보내기 파일 ---------------------------------------------------------------------

def test_excel_bom_crlf_and_quoted_comma():
    r = parse_items_csv("scan", '﻿target\r\na/b\r\n"x,y"\r\n')
    assert r["errors"] == [] and r["items"] == [{"target": "a/b"}, {"target": "x,y"}]


def test_quoted_newline_keeps_physical_line_numbers_for_later_rows():
    r = parse_items_csv("sync", 'a,"b\nc"\nx\n')
    assert r["items"] == [{"source": "a", "destination": "b\nc"}]
    # 칸 안의 줄바꿈은 해석은 되지만 경로로는 unsafe_path 다(2026-10-28 -- 제어 문자 거부, domain.validate_relative_path). 그
    # 행은 시작 줄(1)로, 다음 행은 물리 줄 번호(3)로 말한다.
    assert _codes(r) == [(1, 1, "unsafe_path"), (3, 2, "batch_csv_bad_row")]


def test_broken_quote_is_a_file_error_and_nothing_is_returned():
    r = parse_items_csv("scan", 'a\n"b\n')
    assert r["items"] == [] and [e["reason_code"] for e in r["errors"]] == ["batch_csv_malformed"]


def test_header_finds_columns_by_name_and_ignores_info_columns():
    text = "seq,storage,target,status,request_id\n0,s1,x,Succeeded,abc\n1,s1,y,Failed,\n"
    r = parse_items_csv("rm", text)
    assert r["errors"] == [] and r["items"] == [{"storage": "s1", "target": "x"}, {"storage": "s1", "target": "y"}]
    assert r["storages"] == {"storage": "s1"} and r["header"] == ["storage", "target"]


def test_header_row_longer_than_header_with_values_is_a_column_shift():
    assert _codes(parse_items_csv("scan", "target,status\na,ok,extra\n")) == [(2, 1, "batch_csv_bad_row")]
    # 끝의 빈 칸(「a,ok,」)은 괜찮다 -- 엑셀이 흔히 남긴다
    assert parse_items_csv("scan", "target,status\na,ok,\n")["errors"] == []


def test_formula_guard_is_stripped_only_before_formula_characters():
    r = parse_items_csv("scan", "target\n'=cmd\n'-x\n'plain\n''=lit\n")
    assert [i["target"] for i in r["items"]] == ["=cmd", "-x", "'plain", "'=lit"]


def test_paths_that_really_start_with_an_apostrophe_round_trip():
    # 리뷰(2026-10-27): 내보내기가 ' 로 시작하는 칸에도 ' 를 하나 더 붙여, 다시 읽어도 경로가 바뀌지 않는다
    items = [_item(i, {"storage": "s1", "target": t}) for i, t in enumerate(["'=x", "'plain", "''", "=y", "ok"])]
    r = parse_items_csv("scan", export_items_csv("scan", items))
    assert r["errors"] == []
    assert [i["target"] for i in r["items"]] == ["'=x", "'plain", "''", "=y", "ok"]


# --- 생성과 같은 줄 판정 -----------------------------------------------------------------------------------------------

def test_path_rules_are_build_data_payload_rules_per_line():
    r = parse_items_csv("scan", "ok\n/abs\na/../b\n", storages={"storage": "s1"})
    assert _codes(r) == [(2, 2, "unsafe_path"), (3, 3, "unsafe_path")]


def test_control_characters_inside_a_path_cell_are_unsafe_per_line():
    # 칸 앞뒤의 공백·탭·CR 은 _cell 이 걷는다(엑셀 CSV) -- 경로 **안쪽**의 제어 문자만 unsafe_path(2026-10-28, 생성과 같은 규칙)
    r = parse_items_csv("sync", ' a\t,b\r\n"x\ty",z\nq,"r\x1bs"\n', storages={"source_storage": "s1",
                                                                            "destination_storage": "s2"})
    assert r["items"][0] == {"source_storage": "s1", "destination_storage": "s2", "source": "a", "destination": "b"}
    assert _codes(r) == [(2, 2, "unsafe_path"), (3, 3, "unsafe_path")]


def test_rm_root_and_overlap_per_line_with_related_line():
    r = parse_items_csv("rm", "target\nold/a\nold/a/sub\nold/b\n./old/b\n.\n", storages={"storage": "s1"})
    assert _codes(r) == [(3, 2, "batch_rm_targets_overlap"), (5, 4, "batch_rm_targets_overlap"),
                         (6, 5, "rm_root_forbidden")]
    assert [e["related_line"] for e in r["errors"]] == [2, 4, None]


def test_rm_does_not_need_the_recursive_option_to_judge_rows():
    # recursive 는 배치 옵션(동의)이다 -- CSV 의 일이 아니다(생성이 validate_batch_options 로 본다)
    assert parse_items_csv("rm", "a\nb", storages={"storage": "s1"})["errors"] == []


def test_sync_inside_source_is_judged_only_when_both_storages_are_known():
    same = parse_items_csv("sync", "a,a/b", storages={"source_storage": "s1", "destination_storage": "s1"})
    assert _codes(same) == [(1, 1, "sync_destination_inside_source")]
    assert parse_items_csv("sync", "a,a/b", storages={"source_storage": "s1", "destination_storage": "s2"})["errors"] == []
    # 모르면(화면이 아직 고르지 않음) 경로 형식만 -- 생성이 마저 본다
    assert parse_items_csv("sync", "a,a/b")["errors"] == []


def test_storage_column_must_match_the_selected_storage_and_each_other():
    r = parse_items_csv("scan", "storage,target\ns1,a\ns2,b\n,c\n")
    assert r["storages"] == {"storage": "s1"}
    assert _codes(r) == [(3, 2, "batch_storage_mixed")]
    assert r["items"][2] == {"storage": "s1", "target": "c"}           # 빈 칸 = 배치 스토리지
    r = parse_items_csv("scan", "storage,target\ns1,a\n", storages={"storage": "s9"})
    assert _codes(r) == [(2, 1, "batch_storage_mixed")] and r["storages"] == {"storage": "s9"}


def test_sync_storage_columns():
    text = "source_storage,source,destination_storage,destination\ns1,a,s2,b\ns1,c,s3,d\n"
    r = parse_items_csv("sync", text)
    assert r["storages"] == {"source_storage": "s1", "destination_storage": "s2"}
    assert _codes(r) == [(3, 2, "batch_storage_mixed")]


def test_items_carry_selected_storages_ready_for_create():
    r = parse_items_csv("sync", "a,b", storages={"source_storage": "s1", "destination_storage": "s2"})
    assert r["items"] == [{"source_storage": "s1", "destination_storage": "s2", "source": "a", "destination": "b"}]
    validate_batch("sync", 1, r["items"])


def test_unknown_operation_and_row_cap():
    with pytest.raises(DomainValidationError) as e:
        parse_items_csv("chmod", "a")
    assert e.value.reason_code == "invalid_batch_operation"
    assert MAX_CSV_ROWS == MAX_BATCH_DELETE_ITEMS
    ok = "\n".join(f"p{i}" for i in range(MAX_CSV_ROWS))
    assert parse_items_csv("scan", ok)["rows"] == MAX_CSV_ROWS
    with pytest.raises(DomainValidationError) as e:
        parse_items_csv("scan", ok + "\nmore")
    assert e.value.reason_code == "batch_csv_too_many_rows"


def test_error_list_is_capped_but_counted():
    r = parse_items_csv("scan", "\n".join("/abs" for _ in range(300)))
    assert len(r["errors"]) == 200 and r["error_count"] == 300


# --- rm 겹침 판정은 한 함수(domain.rm_target_overlaps) -- 생성 422 와 CSV 줄 사유가 같은 규칙 ----------------------

def test_rm_target_overlaps_subjects_follow_the_portal_rule():
    assert rm_target_overlaps(["a", "a/b", "c", "c", "a/b/c", "ab", 5, "/x"]) == [
        (1, 0, "a", "inside"), (3, 2, "c", "duplicate"), (4, 0, "a", "inside")]
    assert rm_target_overlaps(["a/b", "a"]) == [(0, 1, "a", "inside")]      # 자손이 주어(앞에 있어도)


def test_validate_batch_still_rejects_overlaps():
    with pytest.raises(DomainValidationError) as e:
        validate_batch("rm", 1, [{"storage": "s", "target": "a/b"}, {"storage": "s", "target": "a"}])
    assert e.value.reason_code == "batch_rm_targets_overlap"
    validate_batch("rm", 1, [{"storage": "s", "target": "a"}, {"storage": "s", "target": "ab"}])


# --- 내보내기·템플릿 ---------------------------------------------------------------------------------------------------

def _item(seq, payload, **kw):
    return {"seq": seq, "payload": payload, "status": "Succeeded", "request_id": None, "request_state": None,
            "reason_code": None, "files_count": None, "completed_at": None, **kw}


def test_export_columns_formula_guard_null_vs_zero():
    text = export_items_csv("rm", [
        _item(0, {"storage": "s1", "target": "=HYPERLINK(1)"}, files_count=0, request_id="r0"),
        _item(1, {"storage": "s1", "target": "a,b"}, status="Failed", reason_code="unsafe_path"),
    ])
    lines = text.split("\r\n")
    assert lines[0] == "seq,storage,target,status,request_id,request_state,reason_code,files_count,completed_at"
    assert lines[1] == "0,s1,'=HYPERLINK(1),Succeeded,r0,,,0,"            # 0 은 "0", 모름은 빈 칸
    assert lines[2] == '1,s1,"a,b",Failed,,,unsafe_path,,'


def test_export_then_parse_round_trips_paths_and_storages():
    items = [_item(0, {"source_storage": "s1", "source": "-a", "destination_storage": "s2", "destination": "b,c"}),
             _item(1, {"source_storage": "s1", "source": "x", "destination_storage": "s2", "destination": "@y"})]
    r = parse_items_csv("sync", export_items_csv("sync", items))
    assert r["errors"] == []
    assert r["items"] == [{"source_storage": "s1", "destination_storage": "s2", "source": "-a", "destination": "b,c"},
                          {"source_storage": "s1", "destination_storage": "s2", "source": "x", "destination": "@y"}]


def test_export_does_not_invent_values_for_a_tampered_payload():
    text = export_items_csv("scan", [_item(0, {"storage": 7, "target": None}), {"seq": "x", "payload": "bad"}])
    assert text.split("\r\n")[1:3] == ["0,,,Succeeded,,,,,", ",,,,,,,,"]


@pytest.mark.parametrize("op,expected", [("scan", "target\r\nteam/projects\r\n"),
                                         ("rm", "target\r\nteam/old-project\r\n"),
                                         ("sync", "source,destination\r\nteam/dataset,backup/dataset\r\n")])
def test_template_parses_cleanly(op, expected):
    assert template_csv(op) == expected
    storages = {"storage": "s1", "source_storage": "s1", "destination_storage": "s2"}
    assert parse_items_csv(op, template_csv(op), storages=storages)["errors"] == []


def test_template_unknown_operation():
    with pytest.raises(DomainValidationError):
        template_csv("nope")
