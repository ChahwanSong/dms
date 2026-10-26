"""배치 항목 CSV — 해석·내보내기·템플릿(2026-10-26 BACKLOG C 「배치 CSV 백엔드화」). 순수 함수(DB 모름) -- 라우트는
routes_batches 의 `:parse-csv`·`/{id}/items.csv`·`/csv-template`.

왜 서버로 옮겼나: 예전 해석은 포탈(lib/csv.ts)에만 있어 (1) 따옴표·BOM 을 몰라 엑셀이 저장한 CSV(첫 칸에 BOM, 콤마가 든
경로는 "…")가 깨졌고, (2) 경로 규칙(unsafe_path·rm_root_forbidden·rm 겹침)은 「배치 생성」을 누른 뒤에야 서버 422 한 문장으로
나왔다 -- 수백 행 CSV 에서 어느 줄인지 알 수 없었다. 이제 해석과 줄별 판정을 생성과 **같은** 검증(domain.build_data_payload ·
validate_batch · rm_target_overlaps)으로 서버가 한다. 생성 라우트는 그대로 최종 심판이다(이 해석은 미리 보기 -- 통과해도
생성이 같은 규칙으로 다시 본다).

열(포탈 lib/csv.ts 시절과 호환 + 내보내기 왕복):
  scan·rm  target(별칭 path) [+ storage]
  sync     source, destination [+ source_storage, destination_storage]
  - 첫 비지 않은 줄에 경로 열 이름(대소문자·앞뒤 공백 무시)이 **모두** 있으면 헤더다 -- 열은 이름으로 찾고 모르는 열(seq·
    status·request_id … 내보내기의 정보 열)은 버린다. 그래서 「CSV 내보내기」 파일을 고쳐 그대로 다시 올릴 수 있다.
  - 헤더가 없으면 위치로 읽는다 -- scan·rm 은 정확히 1열, sync 는 정확히 2열(예전 계약: 콤마가 든 scan 경로를 조용히
    쪼개지 않는다. 콤마가 든 경로는 "…" 로 감싸면 된다).
  - 빈 줄(모든 칸이 공백)은 건너뛴다. 칸은 앞뒤 공백을 걷는다(예전과 같다). 주석 줄은 없다(# 로 시작하는 경로도 경로다).
  - 내보내기의 수식 주입 방어(앞에 붙인 ')는 읽을 때 걷는다: ' 다음이 = + - @ 탭 CR 일 때만(포탈 csvExport 와 같은 집합).
  - 스토리지 열은 선택이다. 화면이 고른 배치 스토리지와 다르거나 줄끼리 다르면 그 줄에 batch_storage_mixed(한 배치 = 한
    스토리지 -- validate_batch 와 같은 규칙). 화면이 아직 스토리지를 고르지 않았으면 CSV 의 첫 값이 배치 스토리지다(응답
    storages 로 돌려준다 -- 포탈이 비어 있는 선택을 채운다).

줄 번호: line = 파일의 물리 줄(1부터, 헤더·빈 줄 포함 -- 편집기에서 찾는 번호), row = 데이터 행 번호(1부터 -- 해석이
전부 통과하면 포탈 표의 행 번호와 같다). 파일 단위 사유(빈 CSV·깨진 따옴표 뒤)는 row 가 None 일 수 있다.
"""
import csv
import io

from .domain import (BATCH_OPERATIONS, DomainValidationError, Operation, build_data_payload, rm_target_overlaps,
                     validate_batch)
from .repositories.request_purges import MAX_BATCH_DELETE_ITEMS

# 본문 상한 -- 1만 행 × 경로 200바이트 ≈ 2 MiB. 넘으면 413(batch_csv_too_large). 운영 CSV 는 수백~수천 행이다.
MAX_CSV_BYTES = 2 * 1024 * 1024
# 데이터 행 상한 = 배치 단위 삭제의 항목 상한(request_purges.MAX_BATCH_DELETE_ITEMS) -- CSV 로 만든 배치가 「삭제 상한 초과」로
# 태어나지 않게. 넘으면 422(batch_csv_too_many_rows). API 로 직접 만드는 배치(validate_batch)엔 상한이 없다(종전대로).
MAX_CSV_ROWS = MAX_BATCH_DELETE_ITEMS
# 응답에 싣는 오류 상한 -- 1만 행이 전부 틀려도 응답이 커지지 않게. 전체 수는 error_count.
MAX_ERRORS = 200

PATH_COLUMNS = {"scan": ("target",), "rm": ("target",), "sync": ("source", "destination")}
STORAGE_COLUMNS = {"scan": ("storage",), "rm": ("storage",), "sync": ("source_storage", "destination_storage")}
_ALIASES = {"path": "target"}
# 줄 판정에 쓰는 행 옵션. 옵션은 배치 단위라 CSV 의 일이 아니다(생성이 validate_options·validate_batch_options 로 본다) --
# rm 의 recursive 는 「동의」 옵션이라 그것 없이는 validate_rm_target 이 모든 줄을 rm_recursive_required 로 떨어뜨린다.
_ROW_OPTIONS = {"scan": {}, "sync": {}, "rm": {"recursive": True}}
# 아직 모르는 스토리지(화면이 고르기 전)의 자리표시 -- 경로·연산 규칙만 같은 함수(build_data_payload)로 보기 위해서다. 실제
# 이름과 겹칠 수 없게 NUL 로 시작한다(응답에는 나가지 않는다). sync 의 두 자리는 서로 달라서 「같은 스토리지일 때만」 보는
# 포함 규칙(sync_destination_inside_source)은 둘 다 알 때만 돈다 -- 모르면 생성이 마저 본다.
_UNKNOWN = {"storage": "\x00?", "source_storage": "\x00?src", "destination_storage": "\x00?dst"}
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
EXPORT_COLUMNS = {
    "scan": ("seq", "storage", "target"),
    "rm": ("seq", "storage", "target"),
    "sync": ("seq", "source_storage", "source", "destination_storage", "destination"),
}
_EXPORT_TAIL = ("status", "request_id", "request_state", "reason_code", "files_count", "completed_at")
TEMPLATE_EXAMPLE = {
    "scan": {"target": "team/projects"},
    "rm": {"target": "team/old-project"},
    "sync": {"source": "team/dataset", "destination": "backup/dataset"},
}


def _error(line, row, *, reason_code, message="", related_line=None) -> dict:
    # reason_code= 키워드로 받는다 -- AST 추출기(tests/test_reason_codes_coverage.py)가 리터럴 자리를 본다.
    return {"line": line, "row": row, "reason_code": reason_code, "message": message, "related_line": related_line}


def _cell(value: str) -> str:
    """읽은 칸 하나: 앞뒤 공백을 걷고, 내보내기의 수식 주입 방어(앞의 ')를 하나 걷는다(모듈 docstring). 걷는 것은 ' 다음이
    수식 문자이거나 ' 일 때뿐 -- 내보내기가 ' 로 시작하는 칸에도 ' 를 하나 더 붙이므로(_export_cell) 경로가 정말 "'=x" 여도
    왕복이 그대로다(리뷰: 예전엔 그 경로의 ' 가 사라졌다)."""
    v = value.strip()
    if len(v) >= 2 and v[0] == "'" and (v[1] in _FORMULA_START or v[1] == "'"):
        return v[1:]
    return v


def _header_map(operation, cells) -> "dict[str, int] | None":
    """첫 줄이 헤더면 {열 이름: 칸 번호}(경로·스토리지 열만, 같은 이름이 둘이면 앞의 것), 아니면 None."""
    names = [_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in cells]
    if not all(col in names for col in PATH_COLUMNS[operation]):
        return None
    wanted = PATH_COLUMNS[operation] + STORAGE_COLUMNS[operation]
    return {col: names.index(col) for col in wanted if col in names}


def parse_items_csv(operation: str, text: str, *, storages: "dict | None" = None) -> dict:
    """CSV 본문 → {"items", "errors", "error_count", "rows", "header", "storages"}.

    items  = 모양이 맞는 데이터 행마다 항목 payload 하나(입력 순서, 생성 바디의 items 와 같은 모양 -- 스토리지는 알 때만
             싣는다). **errors 가 비어 있지 않으면 부분 결과다** -- 포탈은 반영하지 않고, 생성도 같은 규칙으로 422 다.
    errors = [{line, row, reason_code, message, related_line}] 줄 순, 최대 MAX_ERRORS 개(전체 수 error_count).
    rows   = 데이터 행 수(빈 줄·헤더 제외). header = 헤더로 읽은 경로·스토리지 열 이름 목록(없으면 None).
    storages = 배치 스토리지(요청이 준 값 → 없으면 CSV 의 첫 값 → 없으면 None) -- scan·rm {"storage"}, sync 두 키.

    던지는 것: DomainValidationError("invalid_batch_operation") · ("batch_csv_too_many_rows") -- 라우트가 422 로."""
    if operation not in BATCH_OPERATIONS:
        raise DomainValidationError("invalid_batch_operation", repr(operation))
    given = storages or {}
    skeys = STORAGE_COLUMNS[operation]
    pkeys = PATH_COLUMNS[operation]
    if text.startswith("﻿"):
        text = text[1:]                      # 엑셀 UTF-8 BOM
    errors: list[dict] = []
    raw: list[dict] = []                     # 모양이 맞는 행: {line, row, paths, csv_storages}
    header: "dict[str, int] | None" = None
    header_width = 0
    header_seen = False
    data_rows = 0
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    line = 0
    try:
        for cells in reader:
            start = line + 1
            line = reader.line_num
            if all(c.strip() == "" for c in cells):
                continue
            if not header_seen:
                header_seen = True
                header = _header_map(operation, cells)
                if header is not None:
                    header_width = len(cells)
                    continue
            data_rows += 1
            if data_rows > MAX_CSV_ROWS:
                raise DomainValidationError("batch_csv_too_many_rows", f"> {MAX_CSV_ROWS} rows")
            row = data_rows
            if header is None:
                # 위치 읽기: 열 수가 정확해야 한다(scan·rm 1, sync 2). 빈 칸도 모양 오류다(예전 계약 그대로).
                vals = [_cell(c) for c in cells]
                if len(vals) != len(pkeys) or any(v == "" for v in vals):
                    errors.append(_error(start, row, reason_code="batch_csv_bad_row",
                                         message=f"expected {len(pkeys)} non-empty column(s), got {len(vals)}"))
                    continue
                raw.append({"line": start, "row": row, "paths": dict(zip(pkeys, vals)), "csv_storages": {}})
                continue
            vals = [_cell(c) for c in cells]
            paths = {k: (vals[header[k]] if header[k] < len(vals) else "") for k in pkeys}
            missing = any(v == "" for v in paths.values())
            # 헤더보다 긴 행의 넘친 칸이 비어 있지 않으면 열 밀림 신호다(조용히 버리지 않는다). 끝의 빈 칸(「a,」)은 괜찮다.
            spill = any(v != "" for v in vals[header_width:])
            if missing or spill:
                errors.append(_error(start, row, reason_code="batch_csv_bad_row",
                                     message="missing path cell" if missing else "more cells than the header"))
                continue
            csv_storages = {k: vals[header[k]] for k in skeys if k in header and header[k] < len(vals)
                            and vals[header[k]] != ""}
            raw.append({"line": start, "row": row, "paths": paths, "csv_storages": csv_storages})
    except csv.Error as e:
        # 깨진 따옴표 등 -- 그 뒤는 읽을 수 없다. 부분 결과로 반영하지 않게 items 를 비운다.
        return _result([], [_error(reader.line_num or None, None, reason_code="batch_csv_malformed",
                                   message=str(e)[:200])], data_rows, header, {k: None for k in skeys})
    batch_storage = {}
    for k in skeys:
        v = given.get(k)
        if isinstance(v, str) and v.strip() != "":
            batch_storage[k] = v.strip()
        else:
            batch_storage[k] = next((r["csv_storages"][k] for r in raw if k in r["csv_storages"]), None)
    if data_rows == 0:
        errors.append(_error(None, None, reason_code="empty_batch", message="no data rows"))
        return _result([], errors, 0, header, batch_storage)
    items: list[dict] = []
    checked: list[tuple[dict, dict]] = []    # (raw 행, 항목) -- 경로 판정을 통과한 행만(rm 겹침 판정 대상)
    for r in raw:
        mixed = [k for k, v in r["csv_storages"].items() if batch_storage[k] is not None and v != batch_storage[k]]
        item = {**{k: batch_storage[k] for k in skeys if batch_storage[k] is not None}, **r["paths"]}
        items.append(item)
        if mixed:
            k = mixed[0]
            errors.append(_error(r["line"], r["row"], reason_code="batch_storage_mixed",
                                 message=f"{k} {r['csv_storages'][k]!r} != {batch_storage[k]!r}"))
            continue
        try:
            build_data_payload(operation, options=_ROW_OPTIONS[operation],
                               **{**{k: (item.get(k) or _UNKNOWN[k]) for k in skeys}, **r["paths"]})
        except DomainValidationError as e:
            errors.append(_error(r["line"], r["row"], reason_code=e.reason_code, message=str(e)[:200]))
            continue
        checked.append((r, item))
    if operation == Operation.RM.value:
        for subject, other, other_norm, kind in rm_target_overlaps([it["target"] for _, it in checked]):
            r, o = checked[subject][0], checked[other][0]
            errors.append(_error(r["line"], r["row"], reason_code="batch_rm_targets_overlap",
                                 message=f"{kind} {other_norm!r}", related_line=o["line"]))
    if not errors:
        # 그물: 위 줄별 판정이 생성의 배치 규칙과 어긋나지 않았는지 같은 함수로 한 번 더(어긋나면 줄 없이 말한다).
        try:
            validate_batch(operation, 1, items)
            if all(batch_storage[k] is not None for k in skeys):
                for it in items:
                    build_data_payload(operation, options=_ROW_OPTIONS[operation], **it)
        except DomainValidationError as e:
            errors.append(_error(None, None, reason_code=e.reason_code, message=str(e)[:200]))
    errors.sort(key=lambda e: (e["line"] is None, e["line"] or 0))
    return _result(items, errors, data_rows, header, batch_storage)


def _result(items, errors, rows, header, storages) -> dict:
    return {"items": items, "errors": errors[:MAX_ERRORS], "error_count": len(errors), "rows": rows,
            "header": None if header is None else sorted(header, key=header.get), "storages": storages}


def _guard(value) -> str:
    """내보낼 칸 하나(포탈 lib/csvExport.csvCell 과 같은 규칙): None = 빈 칸(모름), 정수는 그대로(0 은 "0"), 문자열이 =
    + - @ 탭 CR 로 시작하면 앞에 ' -- 스프레드시트가 경로 "=HYPERLINK(…)" 를 수식으로 실행하지 않게(OWASP CSV injection).
    ' 로 시작하는 칸에도 ' 를 하나 더 붙인다(읽을 때 하나 걷으니 경로가 정말 ' 로 시작해도 왕복이 성립한다 -- 포탈 csvCell
    보다 하나 더 하는 규칙, 이 CSV 는 다시 읽히기 때문). 읽을 때(_cell) 같은 집합만 하나 걷는다."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = value if isinstance(value, str) else str(value)
    return "'" + s if s.startswith(_FORMULA_START) or s.startswith("'") else s


def _write(rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    for r in rows:
        w.writerow([_guard(v) for v in r])
    return buf.getvalue()


def export_items_csv(operation: str, items) -> str:
    """배치 항목 → CSV 본문(BOM 없음 -- 라우트가 붙인다). 열 = EXPORT_COLUMNS[연산] + 상태 열(status·request_id·
    request_state·reason_code·files_count·completed_at). 경로·스토리지 열 이름이 해석기와 같아 이 파일을 고쳐 그대로 다시
    올릴 수 있다(정보 열은 버려진다). 모르는 연산(DB 신뢰 경계)은 scan 모양으로 -- 항목 payload 의 키를 그대로 옮길 뿐
    실행과 무관하다. payload 의 값이 문자열이 아니면 빈 칸이다(지어내지 않는다)."""
    cols = EXPORT_COLUMNS.get(operation, EXPORT_COLUMNS["scan"])
    rows = [cols + _EXPORT_TAIL]
    for it in items:
        payload = it.get("payload") if isinstance(it.get("payload"), dict) else {}
        head = [it.get("seq") if isinstance(it.get("seq"), int) else None]
        head += [payload.get(c) if isinstance(payload.get(c), str) else None for c in cols[1:]]
        fc = it.get("files_count")
        tail = [it.get("status"), it.get("request_id"), it.get("request_state"), it.get("reason_code"),
                fc if isinstance(fc, int) and not isinstance(fc, bool) else None, it.get("completed_at")]
        rows.append(head + [v if isinstance(v, (str, int)) or v is None else str(v) for v in tail])
    return _write(rows)


def template_csv(operation: str) -> str:
    """템플릿 = 경로 열 헤더 + 예시 한 줄(BOM 없음 -- 라우트가 붙인다). 스토리지 열은 넣지 않는다: 스토리지는 배치 생성
    화면에서 고른다(CSV 에 쓰면 그 값과 같아야 한다 -- 모듈 docstring)."""
    if operation not in BATCH_OPERATIONS:
        raise DomainValidationError("invalid_batch_operation", repr(operation))
    cols = PATH_COLUMNS[operation]
    return _write([cols, [TEMPLATE_EXAMPLE[operation][c] for c in cols]])
