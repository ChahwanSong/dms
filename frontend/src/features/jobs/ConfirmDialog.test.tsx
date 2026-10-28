import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ConfirmDialog, confirmWarnings, previewSummaryText } from "./ConfirmDialog";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// 2026-09-17: 컨펌 창은 실행 결과(result_summary, 컨펌 시점엔 항상 null)가 아니라
// 미리보기 dry-run 의 summary.json 사본(preview_summary)을 보여준다.
const job = { job_id: "j1", request_id: "r1", operation: "sync", state: "ConfirmPending",
  reason_code: null, preview_fingerprint: "abc123", preview_expires_at: "2099-01-01T00:00:00Z",
  result_summary: null, preview_summary: { returncode: 0, files: 3, bytes: 12582912 },
  transitions: [] };

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("버튼·제목은 '작업 컨펌'이고 미리보기 요약(개수·크기)과 지문을 보여준 뒤 지문을 POST 한다", async () => {
  let body: any = null;
  server.use(http.post("/api/user/jobs/j1:confirm", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ state: "Executing" });
  }));
  wrap(<ConfirmDialog job={job as any} />);
  expect(screen.queryByRole("button", { name: "미리보기 확인" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText("sync 작업 컨펌")).toBeInTheDocument();
  expect(screen.getByText(/abc123/)).toBeInTheDocument();
  // 도구를 모르는 옛 잡(3키 요약) -- 뜻을 단정하지 않는 중립 라벨(「복사 대상」은 nsync 의 삭제까지 섞어 불렀다, 괴리 [1])
  expect(screen.getByText("미리보기 항목 3개 · 12.0 MiB")).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "컨펌 전 주의 사항" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "컨펌" }));
  await screen.findByText(/./); // flush
  expect(body).toEqual({ fingerprint: "abc123" });
});

test("요약 문구: null(모름)과 0 을 구분하고, 요약이 없는 옛 잡은 그 사실을 말한다", () => {
  expect(previewSummaryText({ operation: "sync", preview_summary: { returncode: 0, files: 0, bytes: 0 } }))
    .toBe("미리보기 항목 0개 · 0 B");
  expect(previewSummaryText({ operation: "sync", preview_summary: { returncode: 0, files: null, bytes: null } }))
    .toBe("미리보기 항목 모름 · 크기 모름");
  expect(previewSummaryText({ operation: "rm", preview_summary: { returncode: 0, files: 12, bytes: null } }))
    .toBe("삭제 대상 12개(대상 경로 포함, 파일·디렉터리 합계)");
  // 개수를 못 읽은 옛 rm 미리보기(2026-10-12 이전)는 모름 + 목록이 있는 곳
  expect(previewSummaryText({ operation: "rm", preview_summary: { returncode: 0, files: null, bytes: null } }))
    .toBe("삭제 대상 모름 — 미리보기 출력(stdout.log)의 'Walked N items' 줄이 지울 항목 수입니다(목록은 앞·뒤 10개만 찍힙니다)");
  // 0 = 대상 경로를 찾지 못함(drm walk 는 빈 디렉터리도 대상 자신을 세 1) -- 「빈 대상」으로 읽히면 안 된다
  expect(previewSummaryText({ operation: "rm", preview_summary: { returncode: 0, files: 0, bytes: null } }))
    .toBe("삭제 대상 0개 — 대상 경로를 찾지 못했습니다(오타·이미 삭제·권한 — 미리보기 출력의 'Failed to stat' 확인)");
  expect(previewSummaryText({ operation: "sync", preview_summary: { returncode: 2, files: 1, bytes: 10 } }))
    .toBe("미리보기 항목 1개 · 10 B · 미리보기 종료 코드 2");
  expect(previewSummaryText({ operation: "sync", preview_summary: null }))
    .toBe("(요약 없음 — 이 잡은 미리보기 요약이 저장되지 않았습니다)");
  expect(previewSummaryText({ operation: "sync" }))
    .toBe("(요약 없음 — 이 잡은 미리보기 요약이 저장되지 않았습니다)");
});

test("shows error message on fingerprint mismatch", async () => {
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  await userEvent.click(screen.getByRole("button", { name: "컨펌" }));
  expect(await screen.findByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeInTheDocument();
});

test("닫았다 다시 열면 이전 확인 오류가 남지 않는다", async () => {
  // Radix 는 "닫기" 버튼의 setOpen(false) 직접 호출에 onOpenChange 를 태우지 않는다
  // -- reset 없이는 지문 만료/변경 409 오류가 재오픈에 그대로 남아 새 시도의 결과와
  // 혼동된다(StoragesList DeleteButton 의 useEffect reset 선례와 같은 처방).
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  await userEvent.click(screen.getByRole("button", { name: "컨펌" }));
  expect(await screen.findByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "닫기" }));
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText(/abc123/)).toBeInTheDocument(); // 재오픈이 실제로 됐다
  expect(screen.queryByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeNull();
});

test("rm job shows rm-specific dialog title", async () => {
  const rmJob = { ...job, operation: "rm" };
  wrap(<ConfirmDialog job={rmJob as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText("rm 작업 컨펌")).toBeInTheDocument();
});

// ---- 옛 스냅숏의 LDAP 보조 그룹 키(2026-10-26 LDAP 정리): 이 라인은 그룹을 싣지 않아 삭제 경고 줄이 없다 -- 키가 남은
// 옛 행이어도 창은 그 키를 읽지 않는다(죽지 않고, 경고를 지어내지 않는다).
test("옛 행의 supplementary_gids 키는 읽지 않는다 -- rm 확인 창에 보조 그룹 삭제 경고가 없다", async () => {
  const old = { worker_pool: { identity: { username: "alice", uid: 10003, gid: 10000, privileged: false,
    supplementary_gids: [10010], supplementary_gids_status: "applied" } } };
  wrap(<ConfirmDialog job={{ ...job, operation: "rm", ...old } as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText("rm 작업 컨펌")).toBeInTheDocument();
  expect(screen.queryByText(/보조 그룹/)).toBeNull();
});

// ---- 결과 소유 줄(2026-10-26): sync 컨펌은 「결과가 누구 소유가 되나」를 승인하는 자리 -- 요청 상세 「목적지 소유」와 같은 값 ----
test("sync: 결과 소유를 알면 한 줄로 보이고(0:0 도 실제 값), 모르면(null·생략) 줄을 그리지 않는다", async () => {
  const view = wrap(<ConfirmDialog job={job as any} resultOwner="목적지 스토리지 기본 소유자 0:0(root)" />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  const line = await screen.findByText("결과 소유:", { exact: false, selector: "p" });
  expect(line.textContent).toBe("결과 소유: 목적지 스토리지 기본 소유자 0:0(root)");
  view.unmount();
  for (const owner of [null, undefined]) {
    const v = wrap(<ConfirmDialog job={job as any} resultOwner={owner} />);
    await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
    expect(await screen.findByText(/abc123/)).toBeInTheDocument();
    expect(screen.queryByText(/^결과 소유:/)).toBeNull();
    v.unmount();
  }
});

test("rm: 결과 소유 줄이 없다(소유를 만드는 연산이 아니다)", async () => {
  wrap(<ConfirmDialog job={{ ...job, operation: "rm" } as any} resultOwner="목적지 스토리지 기본 소유자 1500:1500" />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText("rm 작업 컨펌")).toBeInTheDocument();
  expect(screen.queryByText(/^결과 소유:/)).toBeNull();
});

// ---- d192 summary v2: 도구별 뜻 + 컨펌 전 주의(포탈 괴리 [1]·[5]·[6]·[17]) ------------------------------------------
// 실 도구 출력(mpifileutils 7a3189c, mfu-tests/samples)을 summary v2 계약(러너 _build_summary)대로 옮긴 모양.
// nsync_preview_ok: "Planned actions: copy=3 mkdir=1 symlink-update=1 meta-update=0 remove=2 skipped-dst-only=0", planned-volume=9 B
const NSYNC_PREVIEW_OK = { returncode: 0, files: 7, bytes: 9, detail_version: 2, tool: "nsync", dryrun: true,
  planned: { copy: 3, mkdir: 1, symlink_update: 1, meta_update: 0, remove: 2, skipped_dst_only: 0 }, planned_bytes: 9 };
// nsync_preview_type_change(delete=0): "Planned actions: copy=2 … remove=1 skipped-dst-only=1", "Type changes: to-directory 0,
// from-directory 1" + "A directory that is replaced by a non-directory must be empty unless --delete is given…"
const NSYNC_PREVIEW_TC = { returncode: 0, files: 3, bytes: 16, detail_version: 2, tool: "nsync", dryrun: true,
  planned: { copy: 2, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 1, skipped_dst_only: 1 }, planned_bytes: 16,
  type_changes: { to_dir: 0, from_dir: 1 }, type_change_needs_delete: true };
// dsync_preview: 첫 walk "Walked 6 items" → source_items(소스 전체 -- 변경 수가 아니다), 크기는 세지 않는다.
const DSYNC_PREVIEW = { returncode: 0, files: 6, bytes: null, detail_version: 2, tool: "dsync", dryrun: true, source_items: 6 };

test("요약 문구 v2: nsync 는 「변경 예정」 + 종류별(삭제 포함) + 복사할 크기, dsync 는 「소스 전체」(변경 수 아님)", () => {
  expect(previewSummaryText({ operation: "sync", tool: "nsync", preview_summary: NSYNC_PREVIEW_OK }))
    .toBe("변경 예정 7개(복사 3 · 디렉토리 생성 1 · 링크 1 · 삭제 2) · 복사할 크기 9 B");
  expect(previewSummaryText({ operation: "sync", tool: "dsync", preview_summary: DSYNC_PREVIEW }))
    .toBe("소스 전체 6개(변경 수는 실행 후 확정)");
  // 바꿀 것이 없는 nsync 미리보기 -- 0 은 정상값(「모름」 아님)
  expect(previewSummaryText({ operation: "sync", tool: "nsync", preview_summary: { ...NSYNC_PREVIEW_OK, files: 0, bytes: 0,
    planned: { copy: 0, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 0, skipped_dst_only: 4 }, planned_bytes: 0 } }))
    .toBe("변경 예정 0개(변경 없음) · 복사할 크기 0 B");
  // 상세 없는 옛 잡이라도 잡의 tool 로 뜻을 정한다(nsync 의 옛 files 도 계획 합이다)
  expect(previewSummaryText({ operation: "sync", tool: "nsync", preview_summary: { returncode: 0, files: 4, bytes: 16 } }))
    .toBe("변경 예정 4개 · 복사할 크기 16 B");
  expect(previewSummaryText({ operation: "sync", tool: "dsync", preview_summary: { returncode: 0, files: 6, bytes: null } }))
    .toBe("소스 전체 6개(변경 수는 실행 후 확정)");
  // 잡 행의 tool 이 summary 의 tool 보다 먼저다(d192 리뷰 control-10 -- 행은 플래너의 결정, 서버 _preview_tool·배치 확인 감사
  // 행과 같은 순서). 행을 모를 때만 summary 의 tool.
  expect(previewSummaryText({ operation: "sync", tool: "dsync", preview_summary: NSYNC_PREVIEW_OK }))
    .toMatch(/^소스 전체 7개/);
  expect(previewSummaryText({ operation: "sync", preview_summary: NSYNC_PREVIEW_OK })).toMatch(/^변경 예정 7개/);
});

test("컨펌 전 주의: 삭제 예정·타입 교체 실패 예고가 창에 목록으로 보인다(nsync 타입 교체, 삭제 꺼짐)", async () => {
  wrap(<ConfirmDialog job={{ ...job, tool: "nsync", preview_summary: NSYNC_PREVIEW_TC, options: { delete: false } } as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText("변경 예정 3개(복사 2 · 삭제 1) · 복사할 크기 16 B")).toBeInTheDocument();
  const list = screen.getByRole("list", { name: "컨펌 전 주의 사항" });
  expect(Array.from(list.querySelectorAll("li")).map((li) => li.textContent)).toEqual([
    // 삭제가 꺼져 있으면 계획된 삭제는 전부 형식이 바뀐 항목의 교체다 -- 그 이유를 말한다(리뷰 portal-jobs-23)
    "형식이 바뀐 목적지 항목 1개를 지우고 소스 형식으로 교체를 시도합니다(삭제 옵션과 무관) — 지워진 항목은 되돌릴 수 없습니다",
    "디렉토리 1개가 파일·링크로 바뀝니다 — 삭제를 켜지 않으면 비어 있지 않은 디렉토리는 바뀌지 않고 실패로 남습니다",
  ]);
  expect(screen.getByText("컨펌 전에 확인하세요")).toBeInTheDocument();
});

test("confirmWarnings: 특수 파일·소스 읽기 오류·dsync 삭제(수 모름)·rm 은 없음·요약 없음은 없음", () => {
  const special = { ...NSYNC_PREVIEW_OK, planned: { ...NSYNC_PREVIEW_OK.planned, remove: 0 }, skipped_special: 2 };
  expect(confirmWarnings({ operation: "sync", tool: "nsync", preview_summary: special }))
    .toEqual(["FIFO·소켓·장치 파일 2개는 복사하지 않습니다 — 목적지의 같은 이름 항목은 삭제를 켜도 그대로 둡니다"]);
  // dsync 는 walk 오류 수를 찍지 않는다(scan_incomplete) -- 「소스 1곳」처럼 수를 지어내지 않는다(리뷰 portal-jobs-18)
  expect(confirmWarnings({ operation: "sync", tool: "dsync", preview_summary: { ...DSYNC_PREVIEW, scan_incomplete: true,
    delete_disabled: true } }))
    .toEqual(["소스 일부를 읽지 못했습니다(권한 등) — 읽지 못한 부분은 반영되지 않습니다. 이 때문에 이번 실행에서는 삭제를 하지 "
      + "않습니다. 읽지 못한 경로는 미리보기 출력(stdout.log)의 'Failed to open directory'·'Failed to stat' 줄에 있습니다"]);
  expect(confirmWarnings({ operation: "sync", tool: "dsync", preview_summary: DSYNC_PREVIEW, options: { delete: true } }))
    .toEqual(["dsync 미리보기는 지울 항목 수를 세지 않습니다 — 삭제가 켜져 있어 목적지에만 있는 항목은 실행 때 지워집니다"]);
  // 삭제를 켠 nsync 의 타입 교체 -- 실패 예고가 아니라 교체(안의 항목도 지워짐) 안내
  expect(confirmWarnings({ operation: "sync", tool: "nsync", options: { delete: true },
    preview_summary: { ...NSYNC_PREVIEW_TC, type_change_needs_delete: false } })).toEqual([
    "목적지에서 1개를 삭제합니다(형식이 바뀐 항목의 교체 포함) — 되돌릴 수 없습니다",
    "디렉토리 1개가 파일·링크로 바뀌어 교체됩니다 — 그 안의 목적지 항목도 함께 지워집니다",
  ]);
  expect(confirmWarnings({ operation: "rm", tool: "drm", preview_summary: { returncode: 0, files: 9, bytes: null,
    detail_version: 2, tool: "drm", target_items: 9 } })).toEqual([]);
  expect(confirmWarnings({ operation: "sync", tool: "nsync", preview_summary: null })).toEqual([]);
  expect(confirmWarnings({ operation: "sync", tool: "nsync", preview_summary: { returncode: 0, files: 3, bytes: 1 } })).toEqual([]);
});

// dms-k8s: 컨펌 전 주의(d192)와 결과 소유 줄(2026-10-26)이 한 창에 함께 -- 둘 다 승인 근거라 하나가 다른 하나를 가리지 않는다.
// 읽는 순서는 요약 → 주의 → 결과 소유(무엇이 바뀌나 → 무엇을 조심하나 → 누구 소유가 되나).
test("k8s: nsync 타입 교체 경고와 결과 소유 줄이 함께 보인다(삭제 꺼짐 → 「교체를 시도합니다」)", async () => {
  wrap(<ConfirmDialog job={{ ...job, tool: "nsync", preview_summary: NSYNC_PREVIEW_TC, options: { delete: false } } as any}
                      resultOwner="목적지 스토리지 기본 소유자 1500:1500" />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  const list = await screen.findByRole("list", { name: "컨펌 전 주의 사항" });
  expect(list.textContent).toContain("교체를 시도합니다");
  const owner = screen.getByText("결과 소유:", { exact: false, selector: "p" });
  expect(owner.textContent).toBe("결과 소유: 목적지 스토리지 기본 소유자 1500:1500");
  // 주의 목록이 결과 소유 줄보다 앞에 있다(DOM 순서)
  expect(list.compareDocumentPosition(owner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
