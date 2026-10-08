import { useEffect, useRef, useState } from "react";
import { TriangleAlert } from "lucide-react";
import type { RequestRow } from "../../lib/types";
import type { useDeleteRequests } from "./useJobs";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { StatusPill } from "../../components/ui/StatusPill";
import { pathSummary } from "../../lib/storagePaths";

// 작업(요청) 선택 삭제 확인 창(2026-10-08, 관리자 전용). 트리거(「선택 삭제」)를 이 컴포넌트가 함께 그린다 -- 툴바에
// 늘 렌더되는 버튼이 곧 창의 트리거다(자리 예약, BatchesList 관례).
//
// **연 순간 선택을 스냅숏한다**: 3초 목록 폴링이 열린 창 뒤에서 선택을 바꿔도(유령 선택 정리 등) 보내는 집합은
// 사용자가 이 창에서 확인한 목록 그대로다(배치 confirm 의 preview_round 와 같은 정신 -- 확인한 것만 실행한다).
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

const PREVIEW_N = 10;
// 연산별 개수의 표시 순서(목록 필터 OPERATIONS 와 같은 축). 모르는 연산(DB 변조·새 연산)은 뒤에 원문 그대로.
const OP_ORDER = ["sync", "scan", "rm"];

/** 사용량 분석 지점이 되는 scan = **성공한 scan 잡**을 가진 요청(실패 scan 은 사용량에 영향이 없다 -- 스펙 §9).
 *  판정은 서버의 has_succeeded_scan(잡 상태 기준)이 먼저다 -- 취소 경합으로 「요청 Cancelled · 잡 Succeeded」인 요청도
 *  사용량 분석의 지점이다(2026-10-09 검증 지적). 필드가 없을 때만(구형 응답) 요청 상태로 근사한다. */
export const isSucceededScan = (r: Pick<RequestRow, "operation" | "state" | "has_succeeded_scan">): boolean =>
  typeof r.has_succeeded_scan === "boolean" ? r.has_succeeded_scan
    : r.operation === "scan" && r.state === "Succeeded";

/** 「sync 2 · scan 1」 -- 0개 연산은 빼고, 알려진 연산 순서 뒤에 모르는 연산을 붙인다. */
export function opCountLine(rows: Pick<RequestRow, "operation">[]): string {
  const c = new Map<string, number>();
  for (const r of rows) c.set(r.operation, (c.get(r.operation) ?? 0) + 1);
  const keys = [...OP_ORDER.filter((o) => c.has(o)), ...[...c.keys()].filter((o) => !OP_ORDER.includes(o))];
  return keys.map((o) => `${o} ${c.get(o)}`).join(" · ");
}

export function DeleteRequestsDialog({ rows, disabled, del, onDeleted, focusAfterDelete }: {
  /** 지금 선택된 행(연 순간 스냅숏한다). */
  rows: RequestRow[];
  /** 트리거 잠금(선택 없음·200개 초과·진행 중). 닫힐 때 잠겨 있으면 포커스를 focusAfterDelete 로 옮긴다. */
  disabled: boolean;
  /** 목록이 소유한 삭제 mutation -- 결과(부분 성공)는 목록 툴바가 그린다. */
  del: ReturnType<typeof useDeleteRequests>;
  /** 성공(부분 성공 포함) 후 -- 목록이 선택을 비운다. */
  onDeleted: () => void;
  /** 닫힌 뒤 트리거가 포커스를 받을 수 없을 때(성공 · 선택이 비어 잠김) 포커스를 둘 곳(늘 렌더되는 자리). 없으면
   *  Radix 기본(트리거). */
  focusAfterDelete?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [snap, setSnap] = useState<RequestRow[]>([]);
  const [ack, setAck] = useState(false);
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
    if (o) { setSnap(rows); setAck(false); succeeded.current = false; }
    setOpen(o);
  };
  const n = snap.length;
  const scans = snap.filter(isSucceededScan).length;
  const rest = n - PREVIEW_N;
  const confirm = () => {
    if (sending.current || del.isPending) return;
    sending.current = true;
    del.mutate(snap.map((r) => r.request_id), {
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
        <p className="font-medium text-ink">{`선택한 작업 ${n}개를 영구 삭제합니다. 되돌릴 수 없습니다.`}</p>
        <p>{`연산별: ${opCountLine(snap)}`}</p>
        {/* 높이 상한 -- 375px 에서 경로 10줄이 1200px 가까이 차지해 scan 경고·「스토리지 데이터는 그대로」 문구를 화면
            밖으로 밀었다(2026-10-09 검증 지적). 넘치면 목록 안에서 스크롤한다. */}
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
        {/* 성공 scan 잡 = 사용량 분석의 지점(사용자 결정 2026-10-08: scan 도 지울 수 있되 확인 창에서 경고). 성공 scan
            잡이 없는 요청(실패 scan 등)은 사용량에 쓰이지 않아 개수에서 뺀다 -- 판정은 isSucceededScan(서버의 잡 상태
            기준 has_succeeded_scan -- 취소 경합의 「Cancelled 인데 잡은 성공」도 센다). 해당 없으면 상자를 그리지 않는다. */}
        {scans > 0 && (
          <div role="note" className="flex items-start gap-2 rounded-lg border border-attn/30 bg-attnbg px-3 py-2 text-ink">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-attn" aria-hidden />
            <p className="min-w-0 flex-1 break-keep">
              {`성공한 scan ${scans}개 포함 — 사용량 분석(대상별 최신 사용량·이력 차트·CSV)에서 이 결과가 사라집니다. `
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
          <span role="status" className="sr-only">{del.isPending ? "삭제 중…" : ""}</span>
          <span aria-hidden className="mr-auto" />
          <Button variant="ghost" disabled={del.isPending} onClick={() => setOpen(false)}>닫기</Button>
          {/* 진행 중엔 aria-disabled(포커스 유지 -- 위 주석) + confirm 가드. 확인 전·빈 선택은 진짜 disabled. */}
          <Button disabled={!ack || n === 0} aria-disabled={del.isPending || undefined}
                  className={del.isPending ? "cursor-not-allowed opacity-50" : ""} onClick={confirm}>
            {del.isPending ? "삭제 중…" : `${n}개 영구 삭제`}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
