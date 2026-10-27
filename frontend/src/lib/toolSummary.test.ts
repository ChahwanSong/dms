import { describe, expect, test } from "vitest";
import {
  ACTION_LABELS, errnoText, failureCounts, hasFailureItems, isPreToolReason, kindsCountParts, kindsText, opLabel,
  previewWarnings, readSummary, sumKinds, summaryTool,
} from "./toolSummary";

// summary.json v2 판독기(2026-10-27 d192) 표 테스트. 입력은 신뢰 경계 밖(result_summary 는 unknown) -- 모양이 틀린 값은
// 「모름」(null)으로 접고 지어내지 않는다. 0 은 정상값(null≠0).
// 픽스처는 실 도구 출력(mpifileutils 7a3189c, mfu-tests/samples/*.out)을 summary v2 계약(러너 _build_summary)대로 옮긴 모양.

// nsync_exec_type_change(rc 1, delete=0)
const NSYNC_EXEC_TC = { detail_version: 2, returncode: 1, files: 1, bytes: 5, tool: "nsync", dryrun: false,
  planned: { copy: 2, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 1, skipped_dst_only: 1 },
  done: { copy: 1, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 0 }, copied_bytes: 5,
  type_changes: { to_dir: 0, from_dir: 1 }, type_change_needs_delete: true, type_change_refused: 1,
  failures: { total: 1, by_op: { "replace-dir": 1 } }, failure_samples: [{ op: "replace-dir", path: "d", errno: 39 }],
  reason: "sync_type_change_refused" };
// nsync_preview_ok(delete=1)
const NSYNC_PREVIEW_OK = { detail_version: 2, returncode: 0, files: 7, bytes: 9, tool: "nsync", dryrun: true,
  planned: { copy: 3, mkdir: 1, symlink_update: 1, meta_update: 0, remove: 2, skipped_dst_only: 0 }, planned_bytes: 9 };

describe("readSummary", () => {
  test("v2 nsync 실행 요약을 그대로 읽는다(종류별·타입 교체·실패·예시)", () => {
    const s = readSummary(NSYNC_EXEC_TC);
    expect(s).toMatchObject({
      present: true, detailed: true, tool: "nsync", dryrun: false, quiet: false, returncode: 1, files: 1, bytes: 5,
      planned: { copy: 2, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 1 },
      done: { copy: 1, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 0 }, copiedBytes: 5,
      typeToDir: 0, typeFromDir: 1, typeChangeNeedsDelete: true, typeChangeRefused: 1,
      failuresTotal: 1, failuresByOp: { "replace-dir": 1 }, reason: "sync_type_change_refused",
    });
    expect(s.failureSamples).toEqual([{ op: "replace-dir", path: "d", errno: 39 }]);
    // planned 의 skipped_dst_only 는 종류가 아니다(합에 넣지 않는다)
    expect(s.planned).not.toHaveProperty("skipped_dst_only");
  });

  test("옛 3키 요약: 핵심 수만, 상세는 전부 모름(detailed false)", () => {
    const s = readSummary({ returncode: 0, files: 120, bytes: 456 });
    expect(s).toMatchObject({ present: true, detailed: false, tool: null, files: 120, bytes: 456, planned: null, done: null,
      failuresTotal: null, failureSamples: [], removeFailed: null });
  });

  test("객체가 아니면 전부 모름(present false) -- null·배열·문자열", () => {
    for (const raw of [null, undefined, [], "x", 3]) {
      const s = readSummary(raw);
      expect(s.present).toBe(false);
      expect(s.files).toBeNull();
    }
  });

  test("모양이 틀린 수는 null(음수·소수·문자열·NaN) -- 0 은 정상값", () => {
    const s = readSummary({ returncode: 1.5, files: -1, bytes: "9", removed: 0, remove_failed: Number.NaN, copied_items: 2.5 });
    expect([s.returncode, s.files, s.bytes, s.removed, s.removeFailed, s.copiedItems]).toEqual([null, null, null, 0, null, null]);
  });

  test("종류별 객체: 아는 종류만 남기고, 하나도 모르면 null", () => {
    expect(readSummary({ planned: { copy: 2, remove: "x", bogus: 9 } }).planned).toEqual({ copy: 2 });
    expect(readSummary({ planned: { copy: "2" } }).planned).toBeNull();
    expect(readSummary({ planned: [1, 2] }).planned).toBeNull();
    expect(readSummary({ done: null }).done).toBeNull();
  });

  test("실패 예시: op·path 가 문자열인 것만, errno 는 정수 아니면 null, 최대 10개", () => {
    const samples = [
      { op: "copy", path: "a", errno: 13 }, { op: "copy", path: 7, errno: 13 }, null, "x",
      { op: "unlink", path: "b", errno: "13" }, ...Array.from({ length: 12 }, (_, i) => ({ op: "copy", path: `p${i}`, errno: 2 })),
    ];
    const s = readSummary({ failure_samples: samples });
    expect(s.failureSamples).toHaveLength(10);
    expect(s.failureSamples.slice(0, 2)).toEqual([{ op: "copy", path: "a", errno: 13 }, { op: "unlink", path: "b", errno: null }]);
    expect(readSummary({ failure_samples: "nope" }).failureSamples).toEqual([]);
  });

  test("bool 표식은 정확히 true 일 때만(\"true\"·1 은 아니다)", () => {
    const s = readSummary({ quiet: "true", aborted: 1, stopped_early: true, delete_disabled: true, summary_unavailable: true,
      scan_incomplete: "yes" });
    expect([s.quiet, s.aborted, s.stoppedEarly, s.deleteDisabled, s.summaryUnavailable, s.scanIncomplete])
      .toEqual([false, false, true, true, true, false]);
  });

  test("d192 리뷰 뒤 키: source_scan_errors(nsync 소스 쪽)·scan_incomplete(dsync, 수 없음)·create_failed(dsync)", () => {
    const s = readSummary({ detail_version: 2, scan_errors: 3, source_scan_errors: 1, scan_incomplete: true, create_failed: 0 });
    expect([s.scanErrors, s.sourceScanErrors, s.scanIncomplete, s.createFailed]).toEqual([3, 1, true, 0]);
    const empty = readSummary({ detail_version: 2 });
    expect([empty.sourceScanErrors, empty.scanIncomplete, empty.createFailed]).toEqual([null, false, null]);
    expect(readSummary({ create_failed: -1, source_scan_errors: "1" })).toMatchObject({ createFailed: null, sourceScanErrors: null });
  });

  test("by_op 의 모양이 틀린 값은 버리고, 남는 것이 없으면 null", () => {
    expect(readSummary({ failures: { total: 3, by_op: { copy: 2, meta: "1" } } }).failuresByOp).toEqual({ copy: 2 });
    expect(readSummary({ failures: { total: 3, by_op: { meta: "1" } } }).failuresByOp).toBeNull();
    expect(readSummary({ failures: { total: 3, by_op: [1] } }).failuresByOp).toBeNull();
  });
});

describe("합·문구 헬퍼", () => {
  test("sumKinds: 아는 종류만 더하고, 모르면 null(0 과 다르다)", () => {
    expect(sumKinds({ copy: 3, mkdir: 1, symlink_update: 1, meta_update: 0, remove: 2 })).toBe(7);
    expect(sumKinds({ copy: 0 })).toBe(0);
    expect(sumKinds({})).toBeNull();
    expect(sumKinds(null)).toBeNull();
  });

  test("kindsText: 0 이 아닌 종류만 고정 순서, 전부 0 이면 「변경 없음」, 모르면 null, 천 단위 구분", () => {
    expect(kindsText(readSummary(NSYNC_PREVIEW_OK).planned)).toBe("복사 3 · 디렉토리 생성 1 · 링크 1 · 삭제 2");
    expect(kindsText({ remove: 1200, copy: 0 })).toBe("삭제 1,200");
    expect(kindsText({ copy: 0, remove: 0 })).toBe("변경 없음");
    expect(kindsText(null)).toBeNull();
  });

  test("kindsCountParts: 배너 문장용 「복사 20개」 조각(0 은 빼고, 모르면 빈 배열)", () => {
    expect(kindsCountParts({ copy: 20, mkdir: 2, symlink_update: 0, meta_update: 1, remove: 0 }))
      .toEqual(["복사 20개", "디렉토리 생성 2개", "속성 갱신 1개"]);
    expect(kindsCountParts({ copy: 0 })).toEqual([]);
    expect(kindsCountParts(null)).toEqual([]);
    expect(Object.keys(ACTION_LABELS)).toEqual(["copy", "mkdir", "symlink_update", "meta_update", "remove"]);
  });

  test("opLabel: 알려진 연산은 한국어, 모르는 이름은 원문 그대로", () => {
    // nsync 실패 목록의 실제 op 이름(nsync_fail_op_names)과 dsync·drm 동사 -- 옛 「meta」「type」 은 도구가 내지 않는다.
    expect(["copy", "replace-dir", "scan", "unlink", "rmdir", "metadata", "type-change", "chown", "opendir"].map(opLabel))
      .toEqual(["복사", "디렉토리 교체", "스캔(읽기)", "삭제", "디렉토리 삭제", "속성", "타입 교체", "소유자 변경", "디렉토리 열기"]);
    expect(opLabel("meta")).toBe("meta");
    expect(opLabel("frobnicate")).toBe("frobnicate");
    expect(opLabel("toString")).toBe("toString");          // 프로토타입 키를 읽지 않는다
  });

  test("errnoText: 흔한 errno 는 이유, 모르면 「오류 N」, null 은 「이유 모름」", () => {
    expect([13, 2, 39, 28, 122, 30, 40, 1].map(errnoText)).toEqual([
      "권한 없음", "경로 없음", "디렉토리가 비어 있지 않음", "공간 부족", "쿼터 초과", "읽기 전용 파일시스템", "심볼릭 링크를 거침",
      "허용되지 않음(소유자만 가능한 변경)"]);
    expect(errnoText(999)).toBe("오류 999");
    expect(errnoText(null)).toBe("이유 모름");
  });

  test("isPreToolReason: 도구 시작 전 사유·제출 실패 접두만 참, 도구 실패 사유·일반 실패는 거짓", () => {
    for (const c of ["workers_unreachable", "identity_groups_not_applied", "destination_path_redirected", "source_path_redirected",
      "target_path_redirected", "unknown_tool", "execution_submit_failed:422", "preview_submit_failed:x", "node_excluded_at_step"]) {
      expect([c, isPreToolReason(c)]).toEqual([c, true]);
    }
    for (const c of ["sync_items_failed", "tool_aborted", "rm_items_failed", "execution_failed", "preview_failed:rc2", "", null, undefined]) {
      expect([c, isPreToolReason(c)]).toEqual([c, false]);
    }
  });

  test("summaryTool: 잡 행의 tool 이 먼저(서버 _preview_tool 과 같은 순서), 아니면 summary 의 tool, 네 도구 밖은 null", () => {
    // 둘이 어긋나면(변조) 행이 이긴다 -- 배치 확인 감사 행(files_by_tool)이 행 기준이라 창 합계도 같은 도구에 붙어야 한다
    // (d192 리뷰 control-10)
    expect(summaryTool(readSummary({ tool: "nsync" }), "dsync")).toBe("dsync");
    expect(summaryTool(readSummary({ tool: "dsync" }), "nsync")).toBe("nsync");
    // 행을 모르면(옛 응답·빈 값·변조 문자열) summary 의 tool
    expect(summaryTool(readSummary({ tool: "nsync" }), undefined)).toBe("nsync");
    expect(summaryTool(readSummary({ tool: "nsync" }), "rm -rf")).toBe("nsync");
    expect(summaryTool(readSummary({ files: 1 }), "dsync")).toBe("dsync");
    // 허용 목록 밖의 문자열은 어느 쪽이든 그대로 넘기지 않는다
    expect(summaryTool(readSummary({ tool: "bogus" }), "rm -rf")).toBeNull();
    expect(summaryTool(readSummary({ tool: "" }), "")).toBeNull();
    expect(summaryTool(readSummary(null), 7)).toBeNull();
    expect(summaryTool(readSummary(null), "toString")).toBeNull();
  });
});

describe("failureCounts · hasFailureItems(결과의 「실패 항목」 -- 화면과 배너가 같은 판정)", () => {
  test("nsync: 도구가 센 by_op 그대로(한국어), 총계는 failures.total", () => {
    const s = readSummary(NSYNC_EXEC_TC);
    expect(failureCounts(s, "nsync")).toEqual({ total: 1, byOp: [["디렉토리 교체", 1]] });
    expect(hasFailureItems(s, "nsync")).toBe(true);
  });

  test("dsync: 복사·삭제 실패 키로(타입 교체 거부는 삭제 실패의 일부라 따로 세지 않는다), 0 칩은 없다", () => {
    const s = readSummary({ detail_version: 2, tool: "dsync", copy_failed: 0, remove_failed: 1, type_change_refused: 1 });
    expect(failureCounts(s, "dsync")).toEqual({ total: 1, byOp: [["삭제", 1]] });
    expect(failureCounts(readSummary({ detail_version: 2, tool: "dsync", copy_failed: 2 }), "dsync"))
      .toEqual({ total: 2, byOp: [["복사", 2]] });
  });

  test("drm: 삭제 실패만(copy_failed 는 drm 에 없다), 모르면 총계 null", () => {
    expect(failureCounts(readSummary({ detail_version: 2, tool: "drm", remove_failed: 5 }), "drm"))
      .toEqual({ total: 5, byOp: [["삭제", 5]] });
    expect(failureCounts(readSummary({ detail_version: 2, tool: "drm", removed: 3 }), "drm")).toEqual({ total: null, byOp: [] });
  });

  test("dsync 총계는 아는 수가 실패를 다 덮을 때만(리뷰 portal-jobs-13) -- 아니면 null(「실패 항목 0개」를 만들지 않는다)", () => {
    const ds = (raw: object) => failureCounts(readSummary({ detail_version: 2, tool: "dsync", ...raw }), "dsync");
    // 실 러너 출력(dsync_exec_items_failed): 「Failed to open input file」 예시 1 + Failed to copy 1 -> 복사 실패가 덮는다
    expect(ds({ copy_failed: 1, failure_samples: [{ op: "open", path: "/s/no", errno: 13 }] })).toEqual({ total: 1, byOp: [["복사", 1]] });
    // 속성 적용 실패(chmod·utime)는 어느 수에도 들지 않는다 -- rc 0 dsync 에서도 나온다
    expect(ds({ copy_failed: 0, failure_samples: [{ op: "chmod", path: "/d/a", errno: 1 }, { op: "utime", path: "/d/a", errno: 1 }] }))
      .toEqual({ total: null, byOp: [] });
    // 예시가 아는 수보다 많다(한 항목이 여러 줄을 남기거나 세지 않는 줄) -> null
    expect(ds({ copy_failed: 1, failure_samples: [{ op: "open", path: "a", errno: 13 }, { op: "write", path: "a", errno: 5 }] }).total)
      .toBeNull();
    // 생성 실패(create_failed) -- 「생성」 칩과 총계
    expect(ds({ copy_failed: 0, create_failed: 2, failure_samples: [{ op: "mkdir", path: "a", errno: 13 },
      { op: "symlink", path: "b", errno: 13 }] })).toEqual({ total: 2, byOp: [["생성", 2]] });
    // 복사 + 생성 실패: mknod 를 못 한 파일은 두 수에 다 든다 -- 겹친 수를 몰라 총계 null, 칩은 둘 다
    expect(ds({ copy_failed: 2, create_failed: 2 })).toEqual({ total: null, byOp: [["복사", 2], ["생성", 2]] });
    // walk 오류(opendir)는 실패 수가 없다
    expect(ds({ copy_failed: 0, remove_failed: 1, failure_samples: [{ op: "rmdir", path: "d", errno: 39 },
      { op: "opendir", path: "s", errno: 13 }] }).total).toBeNull();
    // drm: 삭제 실패 수가 unlink·rmdir 예시를 덮는다(drm_exec_partial)
    expect(failureCounts(readSummary({ detail_version: 2, tool: "drm", remove_failed: 5, failure_samples: [
      ...["r1", "r2", "r3"].map((p) => ({ op: "unlink", path: p, errno: 13 })),
      { op: "rmdir", path: "root", errno: 39 }, { op: "rmdir", path: "t", errno: 13 }] }), "drm").total).toBe(5);
  });

  test("실패가 없는 v2·옛 3키 요약은 「실패 항목」 없음", () => {
    expect(hasFailureItems(readSummary({ detail_version: 2, tool: "dsync", copy_failed: 0, aborted: true }), "dsync")).toBe(false);
    expect(hasFailureItems(readSummary({ returncode: 1, files: 3, bytes: 1, failure_samples: [{ op: "copy", path: "a", errno: 13 }] }), "nsync"))
      .toBe(false);
  });
});

describe("previewWarnings(컨펌 전 주의 -- 포탈 괴리 [1]·[6]·[17])", () => {
  const w = (raw: unknown, tool: string | null, deleteOn = false, op = "sync") =>
    previewWarnings(readSummary(raw), { op, tool, deleteOn });

  test("nsync 삭제 예정: 수 + 되돌릴 수 없음(타입 교체 삭제가 섞이면 그 사실도)", () => {
    expect(w(NSYNC_PREVIEW_OK, "nsync", true)).toEqual(["목적지에서 2개를 삭제합니다 — 되돌릴 수 없습니다"]);
    // 디렉토리로 바뀌는 교체(to_dir)도 「교체 포함」(예전엔 from_dir 만 봤다)
    expect(w({ ...NSYNC_PREVIEW_OK, type_changes: { to_dir: 1, from_dir: 0 } }, "nsync", true))
      .toEqual(["목적지에서 2개를 삭제합니다(형식이 바뀐 항목의 교체 포함) — 되돌릴 수 없습니다"]);
  });

  test("삭제 꺼짐 + 삭제 예정: 그 수는 형식이 바뀐 항목의 교체뿐이라 그 이유를 말한다(리뷰 portal-jobs-23)", () => {
    // 파일↔링크 교체는 「Type changes」 줄에 안 잡힌다(type_changes 없음) -- 그래도 이유가 보여야 한다
    const fileToLink = { detail_version: 2, tool: "nsync", dryrun: true,
      planned: { copy: 0, mkdir: 0, symlink_update: 1, meta_update: 0, remove: 1, skipped_dst_only: 0 } };
    expect(w(fileToLink, "nsync", false))
      .toEqual(["형식이 바뀐 목적지 항목 1개를 지우고 소스 형식으로 교체합니다(삭제 옵션과 무관) — 되돌릴 수 없습니다"]);
  });

  test("타입 교체 + 삭제 꺼짐: 실패 예고(조건문), 수를 모르면 「일부 디렉토리가」", () => {
    expect(w({ ...NSYNC_EXEC_TC, dryrun: true }, "nsync")).toEqual([
      "형식이 바뀐 목적지 항목 1개를 지우고 소스 형식으로 교체를 시도합니다(삭제 옵션과 무관) — 지워진 항목은 되돌릴 수 없습니다",
      "디렉토리 1개가 파일·링크로 바뀝니다 — 삭제를 켜지 않으면 비어 있지 않은 디렉토리는 바뀌지 않고 실패로 남습니다",
    ]);
    expect(w({ detail_version: 2, tool: "nsync", type_change_needs_delete: true }, "nsync"))
      .toEqual(["일부 디렉토리가 파일·링크로 바뀝니다 — 삭제를 켜지 않으면 비어 있지 않은 디렉토리는 바뀌지 않고 실패로 남습니다"]);
  });

  test("특수 파일: nsync 는 목적지 항목을 남긴다는 꼬리까지, dsync 는 복사 안 함만", () => {
    expect(w({ skipped_special: 3 }, "nsync"))
      .toEqual(["FIFO·소켓·장치 파일 3개는 복사하지 않습니다 — 목적지의 같은 이름 항목은 삭제를 켜도 그대로 둡니다"]);
    expect(w({ skipped_special: 1 }, "dsync")).toEqual(["FIFO·소켓·장치 파일 1개는 복사하지 않습니다"]);
  });

  test("읽기 오류: 반영되지 않음 + 삭제 꺼짐이면 그 사실, 주어는 수를 지어내지 않는다(리뷰 portal-jobs-18)", () => {
    // nsync 의 스캔 오류 수는 소스·목적지 합 -- 소스 쪽 수(Source scan had N)가 같을 때만 「소스」
    expect(w({ scan_errors: 2 }, "nsync")).toEqual(["소스 또는 목적지 2곳을 읽지 못했습니다(권한 등) — 읽지 못한 부분은 반영되지 않습니다"]);
    expect(w({ scan_errors: 1, source_scan_errors: 1, delete_disabled: true }, "nsync", true))
      .toEqual(["소스 1곳을 읽지 못했습니다(권한 등) — 읽지 못한 부분은 반영되지 않습니다. 이 때문에 이번 실행에서는 삭제를 하지 않습니다"]);
    expect(w({ scan_errors: 3, source_scan_errors: 1, delete_disabled: true }, "nsync", true)[0])
      .toMatch(/^소스 또는 목적지 3곳을 읽지 못했습니다/);
    // dsync 는 수를 찍지 않는다(scan_incomplete) -- 「소스 일부」 + 경로가 있는 곳(walk 오류 줄은 「실패 항목」이 되지 않는다)
    const dsyncScan = ["소스 일부를 읽지 못했습니다(권한 등) — 읽지 못한 부분은 반영되지 않습니다. 이 때문에 이번 실행에서는 삭제를 하지 "
      + "않습니다. 읽지 못한 경로는 미리보기 출력(stdout.log)의 'Failed to open directory'·'Failed to stat' 줄에 있습니다",
      "dsync 미리보기는 지울 항목 수를 세지 않습니다 — 삭제가 켜져 있어 목적지에만 있는 항목은 실행 때 지워집니다"];
    expect(w({ detail_version: 2, tool: "dsync", scan_incomplete: true, delete_disabled: true }, "dsync", true)).toEqual(dsyncScan);
    // 옛 초안의 자리표시 scan_errors=1 도 dsync 에선 수로 읽지 않는다
    expect(w({ detail_version: 2, tool: "dsync", scan_errors: 1, delete_disabled: true }, "dsync", true)).toEqual(dsyncScan);
  });

  test("dsync + 삭제 켬: 수를 지어내지 않고 「세지 않는다」만", () => {
    expect(w({ detail_version: 2, tool: "dsync", source_items: 6, files: 6 }, "dsync", true))
      .toEqual(["dsync 미리보기는 지울 항목 수를 세지 않습니다 — 삭제가 켜져 있어 목적지에만 있는 항목은 실행 때 지워집니다"]);
    expect(w({ detail_version: 2, tool: "dsync", source_items: 6, files: 6 }, "dsync", false)).toEqual([]);
  });

  test("rm·삭제 0·옛 요약·0 값은 주의 없음", () => {
    expect(w(NSYNC_PREVIEW_OK, "drm", false, "rm")).toEqual([]);
    expect(w({ ...NSYNC_PREVIEW_OK, planned: { ...NSYNC_PREVIEW_OK.planned, remove: 0 } }, "nsync")).toEqual([]);
    expect(w({ returncode: 0, files: 3, bytes: 1 }, "nsync")).toEqual([]);
    expect(w({ skipped_special: 0, scan_errors: 0, type_changes: { to_dir: 0, from_dir: 0 } }, "nsync")).toEqual([]);
  });
});
