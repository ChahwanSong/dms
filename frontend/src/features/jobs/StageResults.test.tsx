// rm 미리보기 결과(2026-10-12): drm --dryrun 이 삭제 요약을 찍지 않아 「삭제 대상」이 늘 「모름」이었다 -- 러너가 walk 항목
// 수를 싣게 됐으니 그 수가 무엇을 센 것인지(대상 경로 포함 합계)를 말하고, 모르면(옛 잡) 목록이 있는 곳을 말한다.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { ExecutionResult, PreviewResult, visiblePath } from "./StageResults";

test("rm 미리보기: 삭제 대상 수 + 무엇을 센 수인지, 크기는 「—」(도구가 바이트를 보고하지 않음)", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: 0, files: 6, bytes: null }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("삭제 대상").nextElementSibling).toHaveTextContent("6");
  expect(screen.getByText("크기").nextElementSibling).toHaveTextContent("—");
  expect(screen.getByText(/대상 경로 자신과 그 아래 파일·디렉터리·링크의 합계입니다/)).toBeInTheDocument();
});

test("rm 미리보기 0 은 「모름」이 아니라 「대상 경로를 찾지 못함」(walk 는 빈 디렉터리도 대상 자신을 세 1)", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: 0, files: 0, bytes: null }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("삭제 대상").nextElementSibling).toHaveTextContent("0");
  expect(screen.getByText(/대상 경로를 찾지 못했습니다\(오타·이미 삭제·권한\) — 지울 것이 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/개수를 읽지 못한/)).toBeNull();
  expect(screen.queryByText(/합계입니다/)).toBeNull();
});

test("실패 시점 결과(failedAt)에는 rm 설명 줄이 없다 — 도구가 돌지 않았을 수 있다", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: null, files: null, bytes: null }}
                        failedAt headingId="h" />);
  expect(screen.getByText("미리보기 결과 (실패 시점)")).toBeInTheDocument();
  expect(screen.queryByText(/개수를 읽지 못한|합계입니다|찾지 못했습니다/)).toBeNull();
});

test("개수를 못 읽은 옛 rm 미리보기는 「모름」 + 목록이 있는 곳", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: 0, files: null, bytes: null }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("삭제 대상").nextElementSibling).toHaveTextContent("모름");
  expect(screen.getByText(/개수를 읽지 못한 미리보기입니다 — 아래 「미리보기」 단계 출력\(stdout\.log\)의 'Walked N items' 줄이 지울 항목 수/))
    .toBeInTheDocument();
});

test("sync 미리보기에는 rm 설명 줄이 없다(도구를 모르는 옛 잡은 중립 라벨 「미리보기 항목」)", () => {
  render(<PreviewResult job={{ operation: "sync" }} summary={{ returncode: 0, files: 3, bytes: 10 }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("미리보기 항목").nextElementSibling).toHaveTextContent("3개");
  expect(screen.queryByText("복사 대상")).toBeNull();
  expect(screen.queryByText(/대상 경로 자신과/)).toBeNull();
  // 정상 미리보기(종료 코드 0)엔 종료 코드 칸이 없다 -- 0 이 아닌 값만 신호다(실패 시점·옛 데이터·스텁)
  expect(screen.queryByText(/종료 코드/)).toBeNull();
});

// ---- d192 summary v2(포탈 괴리 [1]·[2]·[4]·[5]·[6]·[17]) -------------------------------------------------------------
// 실 도구 출력(mpifileutils 7a3189c, mfu-tests/samples/*.out)을 summary v2 계약(러너 _build_summary · spec §1)대로 옮긴 모양.
const V2 = { detail_version: 2 };
// nsync_exec_ok(delete=1, rc 0): Planned = Completed "copy=3 mkdir=1 symlink-update=1 meta-update=0 remove=2 copied-bytes=9"
const NSYNC_EXEC_OK = { ...V2, returncode: 0, files: 7, bytes: 9, tool: "nsync", dryrun: false,
  planned: { copy: 3, mkdir: 1, symlink_update: 1, meta_update: 0, remove: 2, skipped_dst_only: 0 },
  done: { copy: 3, mkdir: 1, symlink_update: 1, meta_update: 0, remove: 2 }, copied_bytes: 9 };
// nsync_exec_items_failed(rc 1): Planned copy=25 mkdir=2 meta-update=1 / Completed copy=20 mkdir=2 meta-update=1 copied-bytes=100,
// "ERROR: Failed items: 5 (copy 5); listed below: 5", "  failed copy `ro/r1`: Permission denied (errno 13)" ×5
const NSYNC_EXEC_FAILED = { ...V2, returncode: 1, files: 23, bytes: 100, tool: "nsync", dryrun: false,
  planned: { copy: 25, mkdir: 2, symlink_update: 0, meta_update: 1, remove: 0, skipped_dst_only: 0 },
  done: { copy: 20, mkdir: 2, symlink_update: 0, meta_update: 1, remove: 0 }, copied_bytes: 100,
  failures: { total: 5, by_op: { copy: 5 } },
  failure_samples: ["r1", "r2", "r3", "r4", "r5"].map((n) => ({ op: "copy", path: `ro/${n}`, errno: 13 })),
  reason: "sync_items_failed" };
// nsync_exec_type_change(rc 1, delete=0): Completed copy=1 copied-bytes=5, "Failed items: 1 (replace-dir 1)",
// "  failed replace-dir `d`: Directory not empty (errno 39)", "Type changes: to-directory 0, from-directory 1" + WARN
const NSYNC_EXEC_TC = { ...V2, returncode: 1, files: 1, bytes: 5, tool: "nsync", dryrun: false,
  planned: { copy: 2, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 1, skipped_dst_only: 1 },
  done: { copy: 1, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 0 }, copied_bytes: 5,
  type_changes: { to_dir: 0, from_dir: 1 }, type_change_needs_delete: true, type_change_refused: 1,
  failures: { total: 1, by_op: { "replace-dir": 1 } }, failure_samples: [{ op: "replace-dir", path: "d", errno: 39 }],
  reason: "sync_type_change_refused" };
// nsync_exec_special_scanerr(rc 1): "Failed scan `locked`: Permission denied (errno 13)", "Source scan had 1 error(s): --delete is
// off for this run", "Skipped 1 special file(s)…", Completed copy=1 mkdir=1 meta-update=1 copied-bytes=2
const NSYNC_EXEC_SCAN = { ...V2, returncode: 1, files: 3, bytes: 2, tool: "nsync", dryrun: false,
  planned: { copy: 1, mkdir: 1, symlink_update: 0, meta_update: 1, remove: 0, skipped_dst_only: 0 },
  done: { copy: 1, mkdir: 1, symlink_update: 0, meta_update: 1, remove: 0 }, copied_bytes: 2,
  skipped_special: 1, scan_errors: 1, source_scan_errors: 1, delete_disabled: true,
  failures: { total: 1, by_op: { scan: 1 } }, failure_samples: [{ op: "scan", path: "locked", errno: 13 }],
  reason: "source_scan_incomplete" };
// nsync_exec_max_errors(rc 1, --max-errors 3): "Stopping after batch 3/32 … reached --max-errors 3", Completed 전부 0,
// "Planned and diff counts above cover batches 1-3 of 32 only (stopped early)"
const NSYNC_EXEC_STOP = { ...V2, returncode: 1, files: 0, bytes: 0, tool: "nsync", dryrun: false,
  planned: { copy: 3, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 0, skipped_dst_only: 0 },
  done: { copy: 0, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 0 }, copied_bytes: 0,
  failures: { total: 3, by_op: { copy: 3 } },
  failure_samples: ["r08", "r26", "r27"].map((n) => ({ op: "copy", path: `ro/${n}`, errno: 13 })),
  stopped_early: true, stop_cause: "max_errors", reason: "sync_stopped_max_errors" };
// nsync_exec_quiet_failed(rc 1): 도구 출력이 비었다(-q 는 ERR 까지 끈다) -- 상세는 quiet 뿐
const NSYNC_EXEC_QUIET = { ...V2, returncode: 1, files: null, bytes: null, tool: "nsync", dryrun: false, quiet: true };
// dsync_exec_type_change(rc 1): "Removed 0 items", "Remove failed for 1 of 1 items", "1 destination item(s) changed type but could
// not be removed", 최종 "Items: 3", "Data: 4.000 B (4 bytes)", "Skipped 1 special file(s)…"
const DSYNC_EXEC_TC = { ...V2, returncode: 1, files: 3, bytes: 4, tool: "dsync", dryrun: false,
  copied_items: 3, copy_failed: 0, copied_bytes: 4, removed: 0, remove_failed: 1, type_change_refused: 1,
  skipped_special: 1, tool_errors: 4, failure_samples: [{ op: "rmdir", path: "/tmp/smp/ds/dst/d", errno: 39 }],
  reason: "sync_type_change_refused" };
// dsync_exec_delete_ok(rc 0): "Removed 3 items", 최종 "Items: 1", "Data: 2.000 B (2 bytes)", Skipped 1 special
const DSYNC_EXEC_DEL = { ...V2, returncode: 0, files: 1, bytes: 2, tool: "dsync", dryrun: false,
  copied_items: 1, copy_failed: 0, copied_bytes: 2, removed: 3, skipped_special: 1 };
// drm_exec_partial(rc 1): "Removed 4 items", "Remove failed for 5 of 9 items", unlink×3(errno 13)·rmdir(39)·rmdir(13)
const DRM_EXEC_PARTIAL = { ...V2, returncode: 1, files: 4, bytes: null, tool: "drm", dryrun: false, removed: 4, remove_failed: 5,
  tool_errors: 6, failure_samples: [
    { op: "unlink", path: "/tmp/smp/rm/t/root/r2", errno: 13 }, { op: "unlink", path: "/tmp/smp/rm/t/root/r1", errno: 13 },
    { op: "unlink", path: "/tmp/smp/rm/t/root/r3", errno: 13 }, { op: "rmdir", path: "/tmp/smp/rm/t/root", errno: 39 },
    { op: "rmdir", path: "/tmp/smp/rm/t", errno: 13 }], reason: "rm_items_failed" };
const DRM_EXEC_OK = { ...V2, returncode: 0, files: 3, bytes: null, tool: "drm", dryrun: false, removed: 3 };

const SYNC = { operation: "sync" };
const tile = (label: string) => screen.getByText(label, { selector: "dt" });
const tileValue = (label: string) => tile(label).nextElementSibling?.textContent;
const tileSub = (label: string) => tile(label).nextElementSibling?.nextElementSibling?.textContent ?? null;

describe("ExecutionResult — 도구별 라벨 타일(원 키가 아니다)", () => {
  test("nsync 성공: 반영한 종류별 수 + 복사한 크기, 계획과 같으면 「계획」 줄 없음, 원 키·0 인 종류는 안 보인다", () => {
    render(<ExecutionResult summary={NSYNC_EXEC_OK} job={{ ...SYNC, tool: "nsync" }} />);
    expect(screen.getByText("실행 결과")).toBeInTheDocument();
    expect(["복사", "디렉토리 생성", "링크", "삭제"].map(tileValue)).toEqual(["3개", "1개", "1개", "2개"]);
    expect(screen.queryByText("속성 갱신", { selector: "dt" })).toBeNull();   // 계획·반영 모두 0
    expect(tileValue("복사한 크기")).toBe("9 B");
    expect(tileSub("복사")).toBeNull();
    expect(screen.queryByText("files")).toBeNull();
    expect(screen.queryByText("returncode")).toBeNull();
    expect(screen.queryByRole("region", { name: "실패 항목" })).toBeNull();
  });

  test("nsync 일부 실패: 반영 수 아래 「계획 N」, 실패 항목(총계·연산 칩·예시 경로·errno 이유) + stdout.log 열기", async () => {
    const open = vi.fn();
    render(<ExecutionResult summary={NSYNC_EXEC_FAILED} job={{ ...SYNC, tool: "nsync" }} onOpenOutput={open} />);
    expect(tileValue("복사")).toBe("20개");
    expect(tileSub("복사")).toBe("계획 25개");
    expect(tileSub("디렉토리 생성")).toBeNull();                       // 계획 = 반영
    expect(tileValue("복사한 크기")).toBe("100 B");
    expect(screen.getByText(/실제로 반영한 수입니다/)).toBeInTheDocument();
    const box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByRole("heading", { name: "실패 항목 5개" })).toBeInTheDocument();
    expect(within(within(box).getByRole("list", { name: "연산별 실패 수" })).getAllByRole("listitem")
      .map((li) => li.textContent)).toEqual(["복사 5"]);
    const samples = within(within(box).getByRole("list", { name: "실패 항목 예시" })).getAllByRole("listitem");
    expect(samples).toHaveLength(5);
    expect(samples[0]).toHaveTextContent("복사 · 권한 없음 (errno 13)");
    expect(within(samples[0]).getByText("ro/r1")).toHaveClass("break-all", "font-mono");
    expect(within(box).getByText(/전체 목록과 원문은 실행 출력\(stdout\.log\)에 있습니다/)).toBeInTheDocument();
    await userEvent.click(within(box).getByRole("button", { name: "stdout.log 열기" }));
    expect(open).toHaveBeenCalledTimes(1);
  });

  test("열 수 있는 stdout.log 가 없으면 버튼 없이 안내만", () => {
    render(<ExecutionResult summary={NSYNC_EXEC_FAILED} job={{ ...SYNC, tool: "nsync" }} onOpenOutput={null} />);
    expect(screen.queryByRole("button", { name: "stdout.log 열기" })).toBeNull();
    expect(screen.getByText(/전체 목록과 원문은 실행 출력\(stdout\.log\)에 있습니다/)).toBeInTheDocument();
  });

  test("nsync 타입 교체 거부: 디렉토리 교체 칩 + 거부·형식 변경 주의 줄", () => {
    render(<ExecutionResult summary={NSYNC_EXEC_TC} job={{ ...SYNC, tool: "nsync" }} />);
    expect(tileValue("복사")).toBe("1개");
    expect(tileSub("복사")).toBe("계획 2개");
    expect(tileValue("삭제")).toBe("0개");
    expect(tileSub("삭제")).toBe("계획 1개");
    const box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByText("디렉토리 교체 1")).toBeInTheDocument();
    expect(within(box).getByRole("list", { name: "실패 항목 예시" })).toHaveTextContent("디렉토리 교체 · 디렉토리가 비어 있지 않음 (errno 39)d");
    const notes = within(screen.getByRole("list", { name: "실행 결과 주의 사항" })).getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(notes).toEqual([
      "주의목적지의 비어 있지 않은 디렉토리 1개는 파일·링크로 바꾸지 못해 그대로 뒀습니다 — 「삭제」를 켜고 다시 실행하면 바뀝니다",
      // 「Type changes」 는 계획 수다(거부된 교체도 든다) -- 바로 위 거부 줄과 모순되지 않게 계획임을 밝힌다(리뷰 portal-jobs-22)
      "안내형식이 바뀌는 항목(계획) — 디렉토리로 0개, 디렉토리에서 파일·링크로 1개",
    ]);
  });

  test("타입 교체 거부의 「삭제를 켜면」 조언은 러너 사유가 그 경우이고 삭제가 꺼졌을 때만(리뷰 portal-jobs-14)", () => {
    const neutral = "형식이 바뀐 목적지 항목 1개를 지우지 못해 그대로 뒀습니다 — 이유는 위 「실패 항목」에 있습니다";
    // (a) 이미 삭제를 켠 실행 -- 켜라고 하지 않는다
    const { unmount } = render(<ExecutionResult summary={NSYNC_EXEC_TC} job={{ ...SYNC, tool: "nsync", options: { delete: true } }} />);
    expect(screen.getByText(neutral)).toBeInTheDocument();
    expect(screen.queryByText(/「삭제」를 켜고/)).toBeNull();
    unmount();
    // (b) dsync 권한 오류(EACCES)로 남은 형식 변경 항목 -- 러너는 sync_items_failed(D1), 조언 없음
    const eacces = { ...DSYNC_EXEC_TC, reason: "sync_items_failed",
      failure_samples: [{ op: "unlink", path: "/tmp/smp/ds/dst/ro/x", errno: 13 }] };
    render(<ExecutionResult summary={eacces} job={{ ...SYNC, tool: "dsync" }} />);
    expect(screen.getByText(neutral)).toBeInTheDocument();
    expect(screen.queryByText(/「삭제」를 켜고/)).toBeNull();
  });

  test("nsync 소스 읽기 오류 + 특수 파일: 스캔 칩, 삭제 꺼짐·특수 파일 주의", () => {
    render(<ExecutionResult summary={NSYNC_EXEC_SCAN} job={{ ...SYNC, tool: "nsync" }} />);
    expect(screen.getByText("스캔(읽기) 1")).toBeInTheDocument();
    const notes = within(screen.getByRole("list", { name: "실행 결과 주의 사항" })).getAllByRole("listitem")
      .map((li) => li.textContent?.replace(/^(주의|안내)/, ""));
    expect(notes).toEqual([
      "FIFO·소켓·장치 파일 1개는 복사하지 않았습니다 — 목적지의 같은 이름 항목은 그대로 뒀습니다",
      "소스 1곳을 읽지 못해 그 부분은 반영되지 않았습니다(권한 등) — 안전을 위해 이번 실행에서는 삭제를 하지 않았습니다"
        + ". 읽지 못한 경로는 위 「실패 항목」의 스캔(읽기) 줄에 있습니다",
    ]);
  });

  test("읽기 오류의 주어는 수를 지어내지 않는다(리뷰 portal-jobs-17·18)", () => {
    // nsync: 스캔 오류 수는 소스·목적지 합 -- 소스 쪽 수가 따로 없거나 다르면 「소스 또는 목적지」
    const { unmount } = render(<ExecutionResult job={{ ...SYNC, tool: "nsync" }}
      summary={{ ...NSYNC_EXEC_SCAN, scan_errors: 3, source_scan_errors: 1 }} />);
    expect(screen.getByText(/^소스 또는 목적지 3곳을 읽지 못해 그 부분은 반영되지 않았습니다/)).toBeInTheDocument();
    unmount();
    // dsync: 수를 찍지 않는다(scan_incomplete) -- 「소스 일부」, 경로는 「실패 항목」이 아니라 출력의 walk 오류 줄
    render(<ExecutionResult job={{ ...SYNC, tool: "dsync", options: { delete: true } }}
      summary={{ ...DSYNC_EXEC_DEL, scan_incomplete: true, delete_disabled: true, removed: undefined }} />);
    expect(screen.getByText("소스 일부를 읽지 못해 그 부분은 반영되지 않았습니다(권한 등) — 안전을 위해 이번 실행에서는 삭제를 하지 않았습니다"
      + ". 읽지 못한 경로는 실행 출력(stdout.log)의 'Failed to open directory'·'Failed to stat' 줄에 있습니다")).toBeInTheDocument();
    expect(screen.queryByText(/1곳/)).toBeNull();
    expect(screen.queryByRole("region", { name: "실패 항목" })).toBeNull();
  });

  test("dsync 비정상 종료: 복사 수·크기는 「모름」, 「중단 전까지 보고된 값」이라 하지 않는다(리뷰 portal-jobs-12)", () => {
    // 옛 값(복사 전 계획 줄의 Items)이 실려 와도 반영 수로 그리지 않는다
    render(<ExecutionResult job={{ ...SYNC, tool: "dsync" }} summary={{ ...V2, returncode: 134, files: 2, bytes: null,
      tool: "dsync", dryrun: false, copied_items: 2, copy_failed: 0, removed: 3, aborted: true, reason: "tool_aborted" }} />);
    expect([tileValue("복사한 항목"), tileValue("복사한 크기"), tileValue("삭제한 항목")]).toEqual(["모름", "모름", "3개"]);
    expect(screen.getByText("도구가 비정상 종료했습니다(MPI 중단) — 끝까지 실행되지 않아 무엇이 반영됐는지 도구가 보고하지 않았습니다. "
      + "원인은 실행 로그의 stderr 발췌나 stderr.log 의 ABORT 줄에 있습니다")).toBeInTheDocument();
    expect(screen.queryByText(/중단 전까지/)).toBeNull();
  });

  test("nsync 중간 멈춤(--max-errors): 반영 0 + 계획, 멈춘 이유와 남은 배치", () => {
    render(<ExecutionResult summary={NSYNC_EXEC_STOP} job={{ ...SYNC, tool: "nsync" }} />);
    expect(tileValue("복사")).toBe("0개");
    expect(tileSub("복사")).toBe("계획 3개");
    expect(screen.getByText("실패가 허용 한도에 닿아 중간에 멈췄습니다 — 뒤 배치는 실행하지 않았습니다(위 수는 멈추기 전까지)"))
      .toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "실패 항목 3개" })).toBeInTheDocument();
  });

  test("nsync quiet 실패: 반영 수 모름 + quiet 주의(지어내지 않는다), 실패 항목 구획 없음", () => {
    render(<ExecutionResult summary={NSYNC_EXEC_QUIET} job={{ ...SYNC, tool: "nsync", options: { quiet: true } }} />);
    expect(tileValue("반영한 항목")).toBe("모름");
    expect(tileValue("복사한 크기")).toBe("—");
    expect(screen.getByText(/quiet 로 실행돼 도구가 세부 결과를 남기지 않았습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "실패 항목" })).toBeNull();
  });

  test("dsync 타입 교체 거부: 복사한 항목·크기·삭제·지우지 못한 항목 + 삭제 칩(타입 교체는 칩이 아니라 주의 줄)", () => {
    render(<ExecutionResult summary={DSYNC_EXEC_TC} job={{ ...SYNC, tool: "dsync" }} />);
    expect([tileValue("복사한 항목"), tileValue("복사한 크기"), tileValue("삭제한 항목"), tileValue("지우지 못한 항목")])
      .toEqual(["3개", "4 B", "0개", "1개"]);
    expect(tile("지우지 못한 항목").nextElementSibling).toHaveClass("text-bad");
    const box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByRole("heading", { name: "실패 항목 1개" })).toBeInTheDocument();
    expect(within(within(box).getByRole("list", { name: "연산별 실패 수" })).getAllByRole("listitem")
      .map((li) => li.textContent)).toEqual(["삭제 1"]);
    expect(within(box).getByRole("list", { name: "실패 항목 예시" }))
      .toHaveTextContent("디렉토리 삭제 · 디렉토리가 비어 있지 않음 (errno 39)/tmp/smp/ds/dst/d");
    expect(screen.getByText(/디렉토리 1개는 파일·링크로 바꾸지 못해 그대로 뒀습니다/)).toBeInTheDocument();
    // dsync 의 특수 파일 줄은 nsync 와 달리 「목적지 항목 그대로」를 말하지 않는다(도구가 그 꼬리를 찍지 않는다)
    expect(screen.getByText("FIFO·소켓·장치 파일 1개는 복사하지 않았습니다")).toBeInTheDocument();
  });

  test("dsync 성공 + 삭제: 삭제한 항목 칸, 실패 구획 없음", () => {
    render(<ExecutionResult summary={DSYNC_EXEC_DEL} job={{ ...SYNC, tool: "dsync" }} />);
    expect([tileValue("복사한 항목"), tileValue("복사한 크기"), tileValue("삭제한 항목")]).toEqual(["1개", "2 B", "3개"]);
    expect(screen.queryByText("지우지 못한 항목", { selector: "dt" })).toBeNull();
    expect(screen.queryByRole("region", { name: "실패 항목" })).toBeNull();
  });

  test("drm 일부 실패: 삭제한 항목 + 지우지 못한 항목, 실패 항목 5개(삭제 칩), 경로 예시", () => {
    render(<ExecutionResult summary={DRM_EXEC_PARTIAL} job={{ operation: "rm", tool: "drm" }} />);
    expect([tileValue("삭제한 항목"), tileValue("지우지 못한 항목")]).toEqual(["4개", "5개"]);
    expect(screen.queryByText("크기", { selector: "dt" })).toBeNull();
    const box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByRole("heading", { name: "실패 항목 5개" })).toBeInTheDocument();
    expect(within(box).getByText("삭제 5")).toBeInTheDocument();
    expect(within(within(box).getByRole("list", { name: "실패 항목 예시" })).getAllByRole("listitem")).toHaveLength(5);
    // 도구가 오류 줄을 남겼지만 실패로 끝난 실행 -- 「성공으로 끝났지만」 줄은 rc 0 에서만
    expect(screen.queryByText(/성공으로 끝났지만/)).toBeNull();
  });

  test("drm 성공: 삭제한 항목만(못 지운 수를 0 으로 지어내지 않는다)", () => {
    render(<ExecutionResult summary={DRM_EXEC_OK} job={{ operation: "rm", tool: "drm" }} />);
    expect(tileValue("삭제한 항목")).toBe("3개");
    expect(screen.queryByText("지우지 못한 항목", { selector: "dt" })).toBeNull();
  });

  test("성공했지만 도구가 오류 줄을 남겼으면(rc 0, tool_errors) 그 사실을 말한다", () => {
    render(<ExecutionResult summary={{ ...DSYNC_EXEC_DEL, skipped_special: undefined, tool_errors: 2 }} job={{ ...SYNC, tool: "dsync" }} />);
    expect(screen.getByText(/성공으로 끝났지만 도구가 오류 줄 2개를 남겼습니다/)).toBeInTheDocument();
  });

  test("dsync 의 아는 수가 덮지 못하는 실패 예시(속성 chmod·utime)면 「실패 항목 0개」가 아니라 수 없는 제목(리뷰 portal-jobs-13)", () => {
    // 실 러너 출력: dsync_exec_delete_ok + chmod·utime ERROR 줄(rc 0) -> copy_failed 0, 예시 2개, tool_errors 2
    render(<ExecutionResult job={{ ...SYNC, tool: "dsync" }} summary={{ ...DSYNC_EXEC_DEL, tool_errors: 2, failure_samples: [
      { op: "chmod", path: "/dst/a", errno: 1 }, { op: "utime", path: "/dst/a", errno: 1 }] }} />);
    const box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByRole("heading", { name: "실패 항목" })).toBeInTheDocument();
    expect(within(box).queryByText(/실패 항목 0개/)).toBeNull();
    expect(within(within(box).getByRole("list", { name: "실패 항목 예시" })).getAllByRole("listitem")).toHaveLength(2);
  });

  test("dsync 생성 실패(mkdir()·symlink() failed): 「생성」 칩, 복사 실패와 함께면 겹칠 수 있어 총계 없음", () => {
    const { unmount } = render(<ExecutionResult job={{ ...SYNC, tool: "dsync" }} summary={{ ...V2, returncode: 1, files: 3, bytes: 4,
      tool: "dsync", dryrun: false, copied_items: 3, copy_failed: 0, create_failed: 1, copied_bytes: 4,
      failure_samples: [{ op: "mkdir", path: "/dst/sub", errno: 13 }], reason: "sync_items_failed" }} />);
    let box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByRole("heading", { name: "실패 항목 1개" })).toBeInTheDocument();
    expect(within(within(box).getByRole("list", { name: "연산별 실패 수" })).getAllByRole("listitem")
      .map((li) => li.textContent)).toEqual(["생성 1"]);
    unmount();
    // mknod 를 못 한 파일은 「Failed to copy」로도 찍힌다 -- 두 수를 더해 단정하지 않는다
    render(<ExecutionResult job={{ ...SYNC, tool: "dsync" }} summary={{ ...V2, returncode: 1, files: 0, bytes: 0, tool: "dsync",
      dryrun: false, copied_items: 0, copy_failed: 2, create_failed: 2, copied_bytes: 0, failure_samples: [
        { op: "mknod", path: "/dst/a", errno: 13 }, { op: "mknod", path: "/dst/b", errno: 13 }], reason: "sync_items_failed" }} />);
    box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByRole("heading", { name: "실패 항목" })).toBeInTheDocument();
    expect(within(within(box).getByRole("list", { name: "연산별 실패 수" })).getAllByRole("listitem")
      .map((li) => li.textContent)).toEqual(["복사 2", "생성 2"]);
    // dsync 는 복사에 실패한 파일을 목적지에 빈 파일로 남긴다(d192 실증 D2) -- 이름이 보여도 복사된 것이 아니라고 말한다
    expect(within(box).getByText(/복사에 실패한 파일 2개는 목적지에 빈 파일\(또는 일부만 쓴 파일\)로 남아 있을 수 있습니다/))
      .toBeInTheDocument();
  });

  test("빈 파일 안내는 dsync 복사 실패에만(nsync 는 실패한 전송이 만든 파일을 지운다)", () => {
    render(<ExecutionResult job={{ ...SYNC, tool: "nsync" }} summary={{ ...V2, returncode: 1, files: 5, tool: "nsync",
      dryrun: false, done: { copy: 5 }, failures: { total: 3, by_op: { copy: 3 } },
      failure_samples: [{ op: "copy", path: "no1.txt", errno: 13 }], reason: "sync_items_failed" }} />);
    expect(screen.queryByText(/목적지에 빈 파일/)).toBeNull();
  });

  test("옛 3키 요약: 핵심 수만 중립 라벨(항목 수·크기·종료 코드) + 이전 형식 안내", () => {
    render(<ExecutionResult summary={{ returncode: 1, files: 4, bytes: 1536 }} job={{ ...SYNC, tool: "nsync" }} />);
    expect([tileValue("항목 수"), tileValue("크기"), tileValue("종료 코드")]).toEqual(["4개", "1.5 KiB (1,536 B)", "1"]);
    expect(tile("종료 코드").nextElementSibling).toHaveClass("text-bad");
    expect(screen.getByText(/이전 형식의 결과 요약이라 종류별 내역과 실패 항목이 없습니다/)).toBeInTheDocument();
    expect(screen.queryByText("복사", { selector: "dt" })).toBeNull();
  });

  test("옛 rm 요약: 「삭제한 항목」, 크기 null 이면 칸 없음", () => {
    render(<ExecutionResult summary={{ returncode: 0, files: 9, bytes: null }} job={{ operation: "rm", tool: "drm" }} />);
    expect(tileValue("삭제한 항목")).toBe("9개");
    expect(screen.queryByText("크기", { selector: "dt" })).toBeNull();
  });

  test("도구가 돌지 않은 실행(3키 전부 null)은 칸을 「—」로 늘어놓지 않고 그 사실만", () => {
    render(<ExecutionResult summary={{ returncode: null, files: null, bytes: null }} job={{ operation: "rm", tool: "drm" }} />);
    expect(screen.getByText(/처리 결과가 없습니다 — 도구가 시작되기 전에 멈춘 실행입니다/)).toBeInTheDocument();
    expect(screen.queryByText("—")).toBeNull();
  });

  test("summary_unavailable 표식: 읽지 못했다는 안내만", () => {
    render(<ExecutionResult summary={{ summary_unavailable: true }} job={{ ...SYNC, tool: "dsync" }} />);
    expect(screen.getByText(/결과 요약을 읽지 못했습니다 — 진단 이벤트의 summary_unreadable 을 보세요/)).toBeInTheDocument();
    expect(screen.queryByText("summary_unavailable")).toBeNull();
  });

  test("scan 결과는 예전 원 키 타일 그대로(dscan 키가 운영자의 검색어)", () => {
    render(<ExecutionResult summary={{ returncode: 0, files: 12, bytes: 2048, dirs: 3 }} job={{ operation: "scan", tool: "dscan" }} />);
    expect(screen.getByText("dirs").nextElementSibling?.textContent).toBe("3");
    expect(screen.getByText("bytes").nextElementSibling?.textContent).toBe("2.0 KiB (2,048 B)");
  });

  test("scan v2 요약: 머리 키(detail_version·tool·dryrun·quiet)는 타일이 아니고, 비정상 종료는 주의 줄(리뷰 portal-jobs-15)", () => {
    // 러너 _build_summary("dscan", …) 의 실제 모양: 핵심 3키 + v2 머리 키(-q 면 quiet: true)
    const { unmount } = render(<ExecutionResult job={{ operation: "scan", tool: "dscan" }}
      summary={{ returncode: 0, files: 12, bytes: 2048, detail_version: 2, tool: "dscan", dryrun: false, quiet: true }} />);
    expect(screen.getAllByRole("term").map((dt) => dt.textContent)).toEqual(["returncode", "files", "bytes"]);
    expect(screen.queryByText("detail_version")).toBeNull();
    expect(screen.queryByRole("list", { name: "실행 결과 주의 사항" })).toBeNull();
    unmount();
    render(<ExecutionResult job={{ operation: "scan", tool: "dscan" }} summary={{ returncode: 134, files: null, bytes: null,
      detail_version: 2, tool: "dscan", dryrun: false, aborted: true, reason: "tool_aborted" }} />);
    expect(screen.getAllByRole("term").map((dt) => dt.textContent)).toEqual(["returncode", "files", "bytes"]);
    const notes = within(screen.getByRole("list", { name: "실행 결과 주의 사항" })).getAllByRole("listitem");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toHaveTextContent(/^주의도구가 비정상 종료했습니다\(MPI 중단\)/);
  });

  test("경로의 제어 문자는 \\xHH 로 보인다(dsync·drm 은 이름을 날것으로 찍는다 -- 한 경로가 두 줄로 읽히지 않게)", () => {
    expect(visiblePath("a\nb\tc\u007f")).toBe("a\\x0ab\\x09c\\x7f");
    render(<ExecutionResult summary={{ ...DRM_EXEC_PARTIAL, failure_samples: [{ op: "unlink", path: "x\ny", errno: 2 }] }}
                            job={{ operation: "rm", tool: "drm" }} />);
    expect(screen.getByText("x\\x0ay")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "실패 항목 예시" })).toHaveTextContent("삭제 · 경로 없음 (errno 2)x\\x0ay");
  });

  test("실패 예시가 총계보다 적으면 「앞의 N개만」", () => {
    render(<ExecutionResult summary={{ ...NSYNC_EXEC_FAILED, failures: { total: 40, by_op: { copy: 40 } } }}
                            job={{ ...SYNC, tool: "nsync" }} />);
    expect(screen.getByText(/앞의 5개만 보입니다 — 전체 목록과 원문은 실행 출력\(stdout\.log\)에 있습니다/)).toBeInTheDocument();
  });
});

describe("PreviewResult — 도구별 뜻 + 컨펌 전 주의", () => {
  test("nsync: 「변경 예정」 + 종류별(삭제 포함) + 복사할 크기 + 삭제 주의", () => {
    render(<PreviewResult job={{ ...SYNC, tool: "nsync", options: { delete: true } }} failedAt={false} headingId="h" summary={{ ...V2, returncode: 0,
      files: 7, bytes: 9, tool: "nsync", dryrun: true, planned_bytes: 9,
      planned: { copy: 3, mkdir: 1, symlink_update: 1, meta_update: 0, remove: 2, skipped_dst_only: 0 } }} />);
    expect(tileValue("변경 예정")).toBe("7개");
    expect(tileSub("변경 예정")).toBe("복사 3 · 디렉토리 생성 1 · 링크 1 · 삭제 2");
    expect(tileValue("복사할 크기")).toBe("9 B");
    expect(screen.getByText("목적지에서 2개를 삭제합니다 — 되돌릴 수 없습니다")).toBeInTheDocument();
    expect(screen.queryByText("복사 대상")).toBeNull();
  });

  test("nsync 타입 교체(삭제 꺼짐): 실패 예고 주의", () => {
    render(<PreviewResult job={{ ...SYNC, tool: "nsync", options: { delete: false } }} failedAt={false} headingId="h"
      summary={{ ...V2, returncode: 0, files: 3, bytes: 16, tool: "nsync", dryrun: true, planned_bytes: 16,
        planned: { copy: 2, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 1, skipped_dst_only: 1 },
        type_changes: { to_dir: 0, from_dir: 1 }, type_change_needs_delete: true }} />);
    expect(screen.getByText("디렉토리 1개가 파일·링크로 바뀝니다 — 삭제를 켜지 않으면 비어 있지 않은 디렉토리는 바뀌지 않고 실패로 남습니다"))
      .toBeInTheDocument();
  });

  test("dsync: 「소스 전체」(변경 수 아님), 크기를 모르면 칸 없음", () => {
    render(<PreviewResult job={{ ...SYNC, tool: "dsync" }} failedAt={false} headingId="h"
      summary={{ ...V2, returncode: 0, files: 6, bytes: null, tool: "dsync", dryrun: true, source_items: 6 }} />);
    expect(tileValue("소스 전체")).toBe("6개");
    expect(tileSub("소스 전체")).toBe("변경 수는 실행 후 확정");
    expect(screen.queryByText("크기", { selector: "dt" })).toBeNull();
    expect(screen.queryByRole("list", { name: "미리보기 주의 사항" })).toBeNull();
  });

  test("실패 시점(failedAt): 0 이 아닌 종료 코드 + 실패 항목 구획(미리보기 출력 안내), 앞으로의 실행 주의는 없다", () => {
    // 계획에 삭제(타입 교체)·읽기 오류·삭제 꺼짐이 있어도 실패한 미리보기는 실행되지 않는다 -- 「…삭제합니다」·「이번 실행에서는」
    // 같은 미래형 주의를 붙이지 않는다(리뷰 portal-jobs-21)
    render(<PreviewResult job={{ ...SYNC, tool: "nsync", options: { delete: true } }} failedAt headingId="h" summary={{ ...V2,
      returncode: 1, files: 5, bytes: 0, tool: "nsync", dryrun: true, scan_errors: 1, source_scan_errors: 1, delete_disabled: true,
      planned: { copy: 2, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 3, skipped_dst_only: 0 },
      failures: { total: 1, by_op: { scan: 1 } },
      failure_samples: [{ op: "scan", path: "locked", errno: 13 }], reason: "preview_scan_incomplete" }} />);
    expect(screen.getByText("미리보기 결과 (실패 시점)")).toBeInTheDocument();
    expect(tileValue("미리보기 종료 코드")).toBe("1");
    const box = screen.getByRole("region", { name: "실패 항목" });
    expect(within(box).getByText(/전체 목록과 원문은 미리보기 출력\(stdout\.log\)에 있습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "미리보기 주의 사항" })).toBeNull();
    expect(screen.queryByText(/삭제합니다|이번 실행에서는/)).toBeNull();
  });
});
