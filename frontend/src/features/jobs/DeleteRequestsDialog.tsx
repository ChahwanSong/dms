import { useEffect, useRef, useState } from "react";
import { TriangleAlert } from "lucide-react";
import type { DeletePreview, DeletePreviewBatch, RequestRow } from "../../lib/types";
import type { useDeleteRequests } from "./useJobs";
import { MAX_DELETE_BATCHES } from "./batchGroups";
import type { BatchSummary } from "./batchGroups";
import { ApiError, apiSend } from "../../lib/api";
import { blockText, limitsLine, parseLimits } from "../../lib/deleteLimits";
import type { BlockDetail, DeleteLimits } from "../../lib/deleteLimits";
import { n0 } from "../../lib/format";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { StatusPill } from "../../components/ui/StatusPill";
import { pathSummary } from "../../lib/storagePaths";
import { batchPillVariant, batchStatusLabel } from "../../lib/jobState";

// 작업(요청) 선택 삭제 확인 창(2026-10-08, 관리자 전용). 트리거(「선택 삭제」)를 이 컴포넌트가 함께 그린다 -- 툴바에
// 늘 렌더되는 버튼이 곧 창의 트리거다(자리 예약, BatchesList 관례).
//
// **연 순간 선택을 스냅숏한다**: 3초 목록 폴링이 열린 창 뒤에서 선택을 바꿔도(유령 선택 정리 등) 보내는 집합은
// 사용자가 이 창에서 확인한 목록 그대로다(배치 confirm 의 preview_round 와 같은 정신 -- 확인한 것만 실행한다).
//
// 배치 단위(2026-10-10): 배치 묶음도 연 순간 스냅숏한다 -- 이름·상태와 함께 **서버가 센 자식 수**까지. 그 수가 곧
// 삭제 CAS(expected_request_count)다: 창을 연 뒤 재실행이 자식을 늘렸으면 서버가 batch_changed 로 그 배치를 통째로
// 거부한다(사용자가 이 창에서 보지 않은 작업은 지우지 않는다). 폴링이 열린 창 뒤에서 수를 바꿔도 보내는 수는 그대로다.
// 배치의 작업은 목록에 보이는 행·필터와 관계없이 전부 지워지므로 창이 그 사실과 배치 기록까지 지워진다는 것을 말한다.
//
// 본문 글자색은 ink/70(흰 4.94:1, AA) -- text-muted 는 장식 전용이다(ConfirmDialog 주석). 이 창은 되돌릴 수 없는
// 삭제를 확인하는 유일한 자리라 모든 안내가 읽혀야 하는 글자다. 한국어 문단은 break-keep(어절 단위 줄바꿈 -- 「없습/
// 니다」처럼 낱말 중간에서 끊지 않는다, 저장소 관례). 경로(pathSummary)는 overflow-wrap:anywhere 로 따로 접는다.
//
// 키보드 포커스(2026-10-09 검증 지적): 진행 중 주 버튼은 disabled 가 아니라 aria-disabled 다 -- 포커스를 쥔 버튼이
// disabled 가 되면 포커스가 창 밖 <body> 로 떨어진다. 닫힐 때 트리거(「선택 삭제」)가 포커스를 받을 수 없으면 -- 성공해
// 선택이 비었거나, 열린 창 뒤에서 폴링이 선택한 행을 모두 목록에서 지워(다른 관리자가 삭제·새 제출에 밀림) 트리거가
// 잠겼으면 -- Radix 의 기본 복귀(잠긴 트리거 → <body>) 대신 목록이 준 자리(focusAfterDelete -- 툴바 결과 줄)로 옮긴다.
// 그 줄이 지금 읽어야 할 것(「N개 삭제됨」·「선택한 작업 중 N개는 목록에서 사라져…」)을 말한다.
//
// 진행 표시(2026-10-09 검증 지적): 최대 200건 일괄 삭제는 요청마다 트랜잭션·감사 스냅숏이라 수 초 걸릴 수 있다.
// 진행 중엔 주 버튼 문구가 「삭제 중…」, 버튼 줄의 늘 있는 role=status 가 「삭제 중…」을 읽고 창은 aria-busy 다
// (다른 폼의 「제출 중…」「생성 중…」 관례) -- Esc·X 를 일부러 무시하는 동안 반응 없는 창으로 보이지 않게.

// 상한·미리보기(2026-11-19 사용자 요청 「상한 값도 삭제 시 보여지고 넘으면 삭제 불가 메시지」): 배치가 있으면 창을 열 때 한 번
// POST /api/admin/requests:delete-preview 로 limits 와 배치마다 정확한 수·최대 commit_order·서버 판정(verdict)을 받는다(작업 목록
// 폴링은 5000 에서 세기를 멈춘다). 보내는 expected = 미리보기의 수, expected_max_commit_order = 미리보기의 최대(최대 commit_order CAS).
// 문제가 하나라도 있으면(배치 판정 blocked · 호출 상한) role=alert 문제 상자가 이유와 숫자를 보이고 제출을 막는다. 미리보기 중에도
// 막는다. 「이미 삭제 중」·「이미 지워짐」 배치는 수·전송에서 뺀다. 미리보기가 404/405(옛 서버)·실패면 목록의 수로 대신하고(서버가 제출
// 때 다시 판정한다), 목록이 정확한 수를 모르는 배치(5,000개 이상)는 막는다(fail-closed).
const PREVIEW_N = 10;
const MAX_DELETE = 200;
/** 미리보기 상태: idle(배치 없음) · loading · ok(서버 판정) · fallback(옛 서버·실패 -- 목록의 수). */
type PreviewState =
  | { kind: "idle" } | { kind: "loading" }
  | { kind: "ok"; byId: Map<string, DeletePreviewBatch>; limits: DeleteLimits | null }
  | { kind: "fallback"; old: boolean };
/** 배치 하나의 확인 창 판정(미리보기 또는 목록의 수). count = 보내는 수(삭제 CAS -- 모르면 null), shown·atLeast = 보이는 수
 *  (상한을 넘어 count 가 null 이어도 미리보기가 센 정확한 수 또는 하한 -- 「작업 73,210개」·「작업 500,001개 이상」). shown 이
 *  count 와 다른 배치는 늘 제출이 막힌다(상한 초과·모름 = blocked, 로딩 중 = 잠금). */
interface Effective {
  b: BatchSummary; count: number | null; shown: number; atLeast: boolean; max: number | null; items: number | null;
  scans: number | null; verdict: "sync" | "async" | "blocked" | "deleting" | "gone" | null; problem: string | null;
  pct: number | null;
}
const VERDICT_CHIP: Record<string, string> = {
  sync: "바로 삭제", async: "백그라운드 삭제", gone: "이미 지워짐", blocked: "삭제 불가",
};

function effectiveOf(b: BatchSummary, pv: PreviewState): Effective {
  if (pv.kind === "ok") {
    const row = pv.byId.get(b.batch_id);
    if (row !== undefined) {
      const detail: BlockDetail = { limit: row.limit, count: row.count, count_at_least: row.count_at_least };
      const d = row.deleting;
      // 상한을 넘은 배치(capped)의 보이는 수는 미리보기가 센 정확한 수(count), 그것도 없으면 하한 -- 목록의 낡은 하한(5,001)을
      // 쓰지 않는다(같은 줄의 「삭제 불가 — 작업 73,210개가 …」와 숫자가 갈렸다, 2026-11-19 검증 지적).
      const capped = row.request_count_capped;
      return {
        b, count: capped ? null : row.request_count,
        shown: !capped ? row.request_count : row.count ?? row.count_at_least ?? row.request_count,
        atLeast: capped && row.count === null, max: row.max_commit_order,
        items: row.item_count, scans: row.succeeded_scan_count ?? b.scans, verdict: row.verdict,
        problem: row.verdict === "blocked" && row.reason !== null ? blockText(row.reason, detail) : null,
        pct: d != null && d.expected_request_count > 0
          ? Math.min(100, Math.floor((d.deleted_request_count / d.expected_request_count) * 100)) : null,
      };
    }
  }
  // 목록의 수(옛 서버·미리보기 실패 -- 또는 로딩 중의 자리 값).
  const problem = b.over ? blockText(b.over.reason, b.over)
    : b.request_count === null && pv.kind === "fallback"
      // 옛 서버(미리보기 없음 404·405)만 「이 서버에서는」 -- 일시 실패(503·네트워크)는 다시 열면 될 일이다(이식 리뷰).
      ? `삭제 불가 — 작업 수를 확인할 수 없습니다(${n0(b.request_count_at_least ?? 0)}개 이상) — ${pv.old
        ? "이 서버에서는 지울 수 없습니다" : "창을 닫았다가 다시 열어 주세요"}`
      : null;
  return { b, count: b.request_count, shown: b.request_count ?? b.request_count_at_least ?? 0,
           atLeast: b.request_count === null, max: null, items: null, scans: b.scans,
           verdict: problem !== null ? "blocked" : null, problem, pct: null };
}
// 연산별 개수의 표시 순서(목록 필터 OPERATIONS 와 같은 축). 모르는 연산(DB 변조·새 연산)은 뒤에 원문 그대로.
const OP_ORDER = ["sync", "scan", "rm"];

/** 사용량 분석 지점이 되는 scan = **성공한 scan 잡**을 가진 요청(실패 scan 은 사용량에 영향이 없다 -- 스펙 §9).
 *  판정은 서버의 has_succeeded_scan(잡 상태 기준)이 먼저다 -- 취소 경합으로 「요청 Cancelled · 잡 Succeeded」인 요청도
 *  사용량 분석의 지점이다(2026-10-09 검증 지적). 필드가 없을 때만(구형 응답) 요청 상태로 근사한다. */
export const isSucceededScan = (r: Pick<RequestRow, "operation" | "state" | "has_succeeded_scan">): boolean =>
  typeof r.has_succeeded_scan === "boolean" ? r.has_succeeded_scan
    : r.operation === "scan" && r.state === "Succeeded";

/** 「sync 2 · scan 1」 -- 0개 연산은 빼고, 알려진 연산 순서 뒤에 모르는 연산을 붙인다. count 는 가중치(배치 묶음 =
 *  그 배치의 자식 수), 없으면 1(단건 행). */
// atLeast = 그 행의 수가 하한(상한을 넘은 배치 — 미리보기가 다시 세지 않는다). 한 연산에 하한 행이 하나라도 있으면 「… 이상」
// (2026-11-19 dms-k8s 06427f15 이식 — 그 라인 테스트베드 실증: 첫 줄·버튼은 「6개 이상」인데 연산별 줄만 「scan 6」이었다).
export function opCountLine(rows: { operation: string; count?: number; atLeast?: boolean }[]): string {
  const c = new Map<string, number>();
  const lower = new Set<string>();
  for (const r of rows) {
    c.set(r.operation, (c.get(r.operation) ?? 0) + (r.count ?? 1));
    if (r.atLeast) lower.add(r.operation);
  }
  const keys = [...OP_ORDER.filter((o) => c.has(o)), ...[...c.keys()].filter((o) => !OP_ORDER.includes(o))];
  return keys.filter((o) => c.get(o) !== 0)
    .map((o) => `${o} ${n0(c.get(o) ?? 0)}${lower.has(o) ? " 이상" : ""}`).join(" · ");
}

/** 첫 줄(무엇을 몇 개). 단건만이면 기존 문구 그대로(e2e 06). atLeast = 배치 자식 수가 하한인 배치가 있다(「…개 이상」). */
export function deleteHeadline(n: number, batches: number, children: number, atLeast = false): string {
  // 조사는 끝 글자에 맞춘다 -- 「…개를」 · 「…개 이상을」.
  const kids = `작업 ${n0(children)}개${atLeast ? " 이상을" : "를"}`;
  if (batches === 0) return `선택한 작업 ${n0(n)}개를 영구 삭제합니다. 되돌릴 수 없습니다.`;
  if (n === 0) return `선택한 배치 ${batches}개의 ${kids} 영구 삭제합니다. 되돌릴 수 없습니다.`;
  return `선택한 작업 ${n0(n)}개와 배치 ${batches}개의 ${kids} 영구 삭제합니다. 되돌릴 수 없습니다.`;
}

export function DeleteRequestsDialog({ rows, batches = [], disabled, del, onDeleted, focusAfterDelete, limits = null,
                                      limitsFallback }: {
  /** 지금 선택된 단건 행(연 순간 스냅숏한다). */
  rows: RequestRow[];
  /** 지금 선택된 배치 묶음(연 순간 스냅숏한다 -- 수는 서버 요약). */
  batches?: BatchSummary[];
  /** 트리거 잠금(선택 없음·진행 중 -- 2026-11-19 부터 상한 초과는 잠그지 않는다: 창의 문제 상자가 제출을 막는다). 닫힐 때 잠겨
   *  있으면 포커스를 focusAfterDelete 로 옮긴다. */
  disabled: boolean;
  /** 목록이 소유한 삭제 mutation -- 결과(부분 성공)는 목록 툴바가 그린다. */
  del: ReturnType<typeof useDeleteRequests>;
  /** 성공(부분 성공 포함) 후 -- 목록이 선택을 비운다. */
  onDeleted: () => void;
  /** 닫힌 뒤 트리거가 포커스를 받을 수 없을 때(성공 · 선택이 비어 잠김) 포커스를 둘 곳(늘 렌더되는 자리). 없으면
   *  Radix 기본(트리거). */
  focusAfterDelete?: () => void;
  /** 서버 상한(정리 현황의 limits -- 미리보기의 limits 가 오면 그쪽). null = 모름. */
  limits?: DeleteLimits | null;
  /** 상한을 모를 때의 상한 줄(툴바와 같은 문구). */
  limitsFallback?: string;
}) {
  const [open, setOpen] = useState(false);
  const [snap, setSnap] = useState<RequestRow[]>([]);
  const [snapBatches, setSnapBatches] = useState<BatchSummary[]>([]);
  const [ack, setAck] = useState(false);
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  // 창을 열 때마다 새 토큰 -- 닫았다 다시 연 뒤 늦게 온 옛 미리보기 응답을 버린다.
  const previewToken = useRef(0);
  // 같은 틱 두 번 클릭(튀는 스위치·dblclick) 가드: isPending 은 mutation 관찰자의 **예약된** 재렌더 뒤에야 보인다 --
  // 그 사이 두 번째 클릭이 POST 를 한 번 더 보내고, mutate 단위 콜백은 마지막 호출 것만 돌아 툴바가 두 번째 응답
  // (「0개 삭제됨 · N개 제외」 -- 이미 지워짐)을 보였다(2026-10-09 검증 지적). 동기 ref 로 창 하나에 POST 하나.
  const sending = useRef(false);
  const succeeded = useRef(false);
  // 닫힐 때 **전체 실패** 오류만 비운다(ConfirmDialog 관례: 「닫기」는 setOpen(false) 를 직접 불러 Radix 가
  // onOpenChange 를 발화하지 않으므로 effect 로). 성공 결과는 지우지 않는다 -- 툴바의 「N개 삭제됨」이 그 데이터다.
  useEffect(() => { if (!open && del.isError) del.reset(); }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps

  const onOpenChange = (o: boolean) => {
    // 진행 중엔 닫지 않는다(Esc·바깥 클릭) -- 닫힌 뒤 전체 실패가 오면 보일 자리가 없다.
    if (!o && del.isPending) return;
    if (o) {
      setSnap(rows); setSnapBatches(batches); setAck(false); succeeded.current = false;
      const token = ++previewToken.current;
      if (batches.length === 0) {
        setPreview({ kind: "idle" });
      } else {
        setPreview({ kind: "loading" });
        apiSend<DeletePreview>("POST", "/api/admin/requests:delete-preview", { batch_ids: batches.map((b) => b.batch_id) })
          .then((r) => {
            if (token !== previewToken.current) return;
            setPreview({ kind: "ok", byId: new Map(r.batches.map((x) => [x.batch_id, x])), limits: parseLimits(r.limits) });
          })
          .catch((e: unknown) => {
            if (token !== previewToken.current) return;
            setPreview({ kind: "fallback", old: e instanceof ApiError && (e.status === 404 || e.status === 405) });
          });
      }
    }
    setOpen(o);
  };
  const n = snap.length;
  const eff = snapBatches.map((b) => effectiveOf(b, preview));
  // 보내는 배치 = 이미 삭제 중·이미 지워짐을 뺀 것(2026-11-19).
  const sendable = eff.filter((e) => e.verdict !== "deleting" && e.verdict !== "gone");
  const nb = sendable.length;
  // children = 보내는 수의 합(호출 합 판정 -- 서버가 expected 로 보는 값), shownKids = 보이는 수의 합(첫 줄·버튼 -- 상한을 넘은 배치는
  // 미리보기가 센 수, 하한이면 「이상」). 둘이 다르면 늘 제출이 막힌다(Effective 주석).
  const children = sendable.reduce((a, e) => a + (e.count ?? 0), 0);
  const shownKids = sendable.reduce((a, e) => a + e.shown, 0);
  const kidsAtLeast = sendable.some((e) => e.atLeast);
  const total = n + children;
  const shownTotal = n + shownKids;
  const lim = (preview.kind === "ok" ? preview.limits : null) ?? limits;
  const loading = preview.kind === "loading";
  const fallbackMsg = preview.kind !== "fallback" ? null : preview.old
    ? "상한을 서버에서 확인하지 못했습니다(서버가 옛 버전) — 목록의 수로 확인합니다"
    : "상한을 서버에서 확인하지 못했습니다 — 목록의 수로 확인합니다(서버가 삭제할 때 다시 판정합니다)";
  // 문제 목록(role=alert -- 미리보기가 끝난 뒤 삽입): 호출 상한 + 배치별 판정.
  const problems: string[] = [];
  if (!loading) {
    const reqLimit = lim?.requests_per_call ?? MAX_DELETE, batchLimit = lim?.batches_per_call ?? MAX_DELETE_BATCHES;
    if (n > reqLimit) problems.push(blockText("delete_selection_too_large", { limit: reqLimit, count: n }));
    if (nb > batchLimit) problems.push(blockText("delete_batch_selection_too_large", { limit: batchLimit, count: nb }));
    for (const e of sendable) if (e.problem !== null) problems.push(`배치 ${e.b.label}: ${e.problem}`);
    if (lim !== null && nb > 1 && children > lim.call_request_count) {
      problems.push(blockText("batch_delete_call_budget", { limit: lim.call_request_count, count: children }));
    }
  }
  // 사용량 경고의 개수 = 단건 성공 scan + 배치마다 서버가 센 성공 scan 자식 수(모르면 0 -- 경고 상자는 아는 수만).
  const scans = snap.filter(isSucceededScan).length + sendable.reduce((a, e) => a + (e.scans ?? 0), 0);
  const rest = n - PREVIEW_N;
  const confirm = () => {
    if (sending.current || del.isPending || loading || problems.length > 0) return;
    sending.current = true;
    del.mutate({
      request_ids: snap.map((r) => r.request_id),
      batches: sendable.map((e) => ({
        batch_id: e.b.batch_id, expected_request_count: e.count ?? 0,
        ...(e.max !== null ? { expected_max_commit_order: e.max } : {}),
      })),
    }, {
      onSuccess: () => { succeeded.current = true; setOpen(false); onDeleted(); },
      onSettled: () => { sending.current = false; },
    });
  };
  const onCloseAutoFocus = (e: Event) => {
    if ((succeeded.current || disabled) && focusAfterDelete) { e.preventDefault(); focusAfterDelete(); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg" title="작업 영구 삭제" onCloseAutoFocus={onCloseAutoFocus}
            busy={del.isPending}
            trigger={<Button variant="outline" disabled={disabled}>선택 삭제</Button>}>
      <div className="space-y-3 text-sm text-ink/70 break-keep">
        <p className="font-medium text-ink">{deleteHeadline(n, nb, shownKids, kidsAtLeast)}</p>
        {/* 연산별 수는 배치 자식까지 함께 센다(배치 묶음 = 그 배치의 연산 × 서버가 센 자식 수). */}
        <p>{`연산별: ${opCountLine([...snap, ...sendable.map((e) => ({ operation: e.b.operation, count: e.shown, atLeast: e.atLeast }))])}`}</p>
        {/* 상한 상자(2026-11-19 -- 배치가 있거나 단건 2개 이상일 때): 서버 값으로만. */}
        {(snapBatches.length > 0 || n >= 2) && (
          <p className="rounded-lg border border-line bg-surface px-3 py-2 text-xs" data-testid="delete-dialog-limits">
            {`한 번에 지울 수 있는 상한: ${lim !== null ? limitsLine(lim) : (limitsFallback ?? "서버가 판정합니다")}`}
            {lim !== null && ` · 작업 ${n0(lim.sync_request_count)}개를 넘는 배치는 백그라운드에서 나눠 지웁니다(진행은 배치 칸·배치 상세)`}
          </p>
        )}
        {/* 미리보기를 못 받았다(옛 서버·실패) -- 보이는 글자 + 버튼 줄의 늘 있는 status 가 같은 말을 읽는다. */}
        {fallbackMsg !== null && <p className="text-xs">{fallbackMsg}</p>}
        {problems.length > 0 && (
          <div role="alert" className="space-y-1 rounded-lg border border-bad/40 bg-badbg/40 px-3 py-2 text-bad">
            <p className="font-medium">삭제 불가 — 아래를 선택에서 빼고 다시 시도하세요</p>
            <ul className="list-disc space-y-0.5 pl-5 text-sm">
              {problems.map((t) => <li key={t}>{t}</li>)}
            </ul>
          </div>
        )}
        {/* 배치 목록(배치가 있을 때만) -- 단건 목록 위. 최대 10개(서버 상한)라 줄이지 않고 높이만 묶는다. 두 목록은 같은
            모양의 테두리 상자라 섞인 선택에서 어느 상자가 배치인지 보이지 않았다(이름 없는 배치의 id 12자와 작업 id 12자가
            나란히 -- 2026-10-11 검증 지적) -- 상자마다 **보이는** 머리말을 단다(배치가 있을 때만: 단일 작업만이면 상자가
            하나라 예전 그대로). */}
        {snapBatches.length > 0 && <p className="text-xs font-medium text-ink">{`삭제할 배치 ${nb}개`}</p>}
        {snapBatches.length > 0 && (
          <ul aria-label="삭제할 배치" className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-line px-3 py-2">
            {eff.map(({ b, shown, atLeast, items, verdict, problem, pct }) => (
              <li key={b.batch_id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className={`min-w-0 text-ink [overflow-wrap:anywhere] ${b.named ? "" : "font-mono text-xs"}`}>{b.label}</span>
                <span aria-hidden>·</span><span>{b.operation}</span>
                {/* 배치 행이 없는 묶음(배치 기록만 지워진 것)은 상태가 없다 -- 지어내지 않고 그 사실을 말한다. 「삭제된
                    배치」는 「이미 지워져 처리할 것이 없다」로 읽혔다(2026-10-10 검증 지적) -- 지울 작업은 남아 있다. */}
                {b.exists && b.status !== null
                  ? <StatusPill state={b.status} variant={batchPillVariant(b.status)} label={batchStatusLabel(b.status)} />
                  : <span className="text-xs">배치 기록 없음</span>}
                <span aria-hidden>·</span>
                <span>{`작업 ${n0(shown)}개${atLeast ? " 이상" : ""}` + (items !== null ? ` · 항목 ${n0(items)}개` : "")}</span>
                {/* 판정 칩(미리보기 -- 2026-11-19): 바로 삭제·백그라운드 삭제·이미 삭제 중 N%·이미 지워짐·삭제 불가. */}
                {verdict !== null && (
                  <span className={`rounded px-1.5 text-xs ${verdict === "blocked" ? "bg-badbg text-bad"
                    : verdict === "async" || verdict === "deleting" ? "bg-attnbg text-ink" : "bg-surface text-ink"}`}>
                    {verdict === "deleting" ? `이미 삭제 중${pct !== null ? ` ${pct}%` : ""}` : VERDICT_CHIP[verdict]}
                  </span>
                )}
                {problem !== null && <span className="basis-full text-xs text-bad">{problem}</span>}
              </li>
            ))}
          </ul>
        )}
        {/* 배치 단위 삭제는 배치 기록과 함께 스케줄·실행 기록·보존 회차 기록도 지운다(request_purges.delete_batch -- 2026-11-16
            회차 보존). 묶음 요약엔 스케줄 유무가 없어 「있으면」으로 말하고, 배치 기록이 남은 묶음이 있을 때만(기록만 지워진
            묶음은 그때 스케줄도 함께 지워졌다). */}
        {snapBatches.some((b) => b.exists) && (
          <p className="text-xs">배치에 스케줄이 있으면 스케줄·실행 기록·보존 회차 기록도 함께 삭제됩니다.</p>
        )}
        {/* 높이 상한 -- 375px 에서 경로 10줄이 1200px 가까이 차지해 scan 경고·「스토리지 데이터는 그대로」 문구를 화면
            밖으로 밀었다(2026-10-09 검증 지적). 넘치면 목록 안에서 스크롤한다. 단건이 없으면(배치만) 그리지 않는다. */}
        {n > 0 && nb > 0 && <p className="text-xs font-medium text-ink">{`삭제할 작업 ${n}개`}</p>}
        {n > 0 && (
          <ul aria-label="삭제할 작업" className="max-h-60 space-y-1 overflow-y-auto rounded-lg border border-line px-3 py-2">
            {snap.slice(0, PREVIEW_N).map((r) => (
              <li key={r.request_id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="font-mono text-xs text-ink">{r.request_id.slice(0, 12)}</span>
                <span aria-hidden>·</span><span>{r.operation}</span>
                <span aria-hidden>·</span>
                <span className="min-w-0 [overflow-wrap:anywhere]">{pathSummary(r.operation, r.payload)}</span>
                <StatusPill state={r.state} />
              </li>
            ))}
            {rest > 0 && <li>{`외 ${rest}개`}</li>}
          </ul>
        )}
        {/* 배치 단위의 두 사실(배치가 있을 때만): 화면에 보이는 행만이 아니라 전부 지워진다 · 배치 기록도 지워져 다시
            돌릴 수 없다(scan 배치면 성장 모니터링 정의가 사라진다). 배치 화면의 「배치 삭제」(기록만)와 다른 점이다.
            둘째 줄은 배치 기록이 **있는** 묶음이 있을 때만 -- 기록이 이미 없는 묶음만 골랐으면 일어날 일이 아니라 이미
            일어난 일이다(같은 창의 목록 항목이 「배치 기록 없음」이라 말한다, 2026-10-11 검증 지적). */}
        {nb > 0 && (
          <div className="space-y-1">
            <p className="font-medium text-ink">배치의 작업은 목록에 보이는 행·필터와 관계없이 전부(재실행 이력 포함) 지워집니다.</p>
            {snapBatches.some((b) => b.exists) && (
              <p>배치 기록(항목 목록·이름·메모·실행 설정)도 함께 지워져 배치 작업 화면에서 사라지고, 다시 스캔·재실행할 수 없습니다.</p>
            )}
          </div>
        )}
        {/* 성공 scan 잡 = 사용량 분석의 지점(사용자 결정 2026-10-08: scan 도 지울 수 있되 확인 창에서 경고). 성공 scan
            잡이 없는 요청(실패 scan 등)은 사용량에 쓰이지 않아 개수에서 뺀다 -- 판정은 isSucceededScan(서버의 잡 상태
            기준 has_succeeded_scan -- 취소 경합의 「Cancelled 인데 잡은 성공」도 센다). 배치 묶음은 서버가 센 성공 scan
            자식 수(batch_succeeded_scan_count)를 더한다. 해당 없으면 상자를 그리지 않는다. */}
        {scans > 0 && (
          <div role="note" className="flex items-start gap-2 rounded-lg border border-attn/30 bg-attnbg px-3 py-2 text-ink">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-attn" aria-hidden />
            <p className="min-w-0 flex-1 break-keep">
              {`성공한 scan ${n0(scans)}개 포함 — 사용량 분석(대상별 최신 사용량·이력 차트·CSV)에서 이 결과가 사라집니다. `
                + "같은 대상에 이전 scan 이 있으면 그 값이 최신으로 보이고, 없으면 대상이 목록에서 빠집니다."}
            </p>
          </div>
        )}
        <div className="space-y-1">
          <p>
            함께 지워지는 것: 요청·잡 기록(상태 이력·결과·진단 이벤트·실패 시 보관한 로그), 결과 파일(미리보기·실행 출력·scan
            리포트), 남아 있는 파드와 그 로그.
          </p>
          {/* 가장 흔한 오해(「기록을 지우면 복사·삭제도 되돌려지나?」)를 정면으로 -- 강조 글자. */}
          <p className="font-medium text-ink">
            스토리지의 실제 데이터(복사·삭제된 파일)는 건드리지 않습니다 — 작업 기록 삭제는 실행 취소가 아닙니다.
          </p>
          <p>
            대시보드 잡 통계에서도 빠집니다. 감사 로그에는 누가·언제·무엇을 지웠는지 남습니다. 결과 파일·파드 정리는 삭제
            직후 백그라운드에서 진행됩니다.
          </p>
        </div>
        <label className="flex items-center gap-2 text-ink">
          <input type="checkbox" className="h-4 w-4" checked={ack} disabled={del.isPending}
                 onChange={(e) => setAck(e.target.checked)} />
          되돌릴 수 없음을 확인했습니다
        </label>
        {/* 전체 실패(403 admin_session_required·422·503 maintenance_mode)는 창 안에서 -- 부분 제외는 오류가 아니라
            툴바의 결과 줄이 말한다. ApiError.message 는 이미 reasonText 를 거친 문구다. */}
        {del.isError && <p role="alert" className="text-bad">{(del.error as Error).message}</p>}
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          {/* 진행 문구 자리(늘 있다 -- 라이브 리전은 내용이 바뀌기 전부터 DOM 에 있어야 읽힌다). 화면에는 버튼 글자
              「삭제 중…」이 보이므로 이 줄은 보조기기 전용(같은 글자가 두 번 보이지 않게). mr-auto 는 버튼을 오른쪽에 둔다. */}
          <span role="status" className="sr-only">
            {del.isPending ? "삭제 중…" : loading ? "상한과 작업 수를 확인하는 중…" : (fallbackMsg ?? "")}
          </span>
          <span aria-hidden className="mr-auto" />
          <Button variant="ghost" disabled={del.isPending} onClick={() => setOpen(false)}>닫기</Button>
          {/* 진행 중엔 aria-disabled(포커스 유지 -- 위 주석) + confirm 가드. 확인 전·빈 선택은 진짜 disabled. */}
          {/* 수 = 단건 + 배치 자식(서버 수) -- 단건만이면 기존 글자 그대로(e2e 06). */}
          {/* 문제가 하나라도 있거나 미리보기 중이면 잠근다(2026-11-19). */}
          <Button disabled={!ack || total === 0 || loading || problems.length > 0} aria-disabled={del.isPending || undefined}
                  className={del.isPending ? "cursor-not-allowed opacity-50" : ""} onClick={confirm}>
            {del.isPending ? "삭제 중…" : `${n0(shownTotal)}개${kidsAtLeast ? " 이상" : ""} 영구 삭제`}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
