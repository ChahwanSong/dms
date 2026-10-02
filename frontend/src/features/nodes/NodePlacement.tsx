import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import type { NodeInfo } from "../../lib/types";
import { useExcludeNode, useIncludeNode } from "./useNodes";

// 노드 배치 제외·다시 포함·cordon 표시(2026-10-02 사용자 요청 "문제가 생긴 노드에 더 이상 잡이 안 들어가게 + 나중에
// 다시 포함"). 서버 계약은 repositories/node_exclusions.py 모듈 docstring:
//   - 배치 제외 = 관리자가 포탈에서 뺀 노드(사유·누가·언제). 다시 포함하면 다음 계획부터 후보로 돌아온다.
//   - cordon = k8s 에서 스케줄 불가(cordon·NoSchedule/NoExecute taint)로 에이전트가 보고한 노드 -- DMS 도 자동으로
//     피한다. 포탈에서 풀 수 없다(kubectl uncordon / taint 제거가 원천).
// 버튼 노출은 표시 게이트일 뿐이고 진짜 판정은 서버다.

const MAX_REASON = 500;

export function k8sNodeState(node: Pick<NodeInfo, "report">):
    { schedulable: boolean | null; reason: string | null; transient: boolean } {
  const k = (node.report as Record<string, unknown> | null)?.k8s_node;
  if (k === null || typeof k !== "object") return { schedulable: null, reason: null, transient: false };
  const o = k as Record<string, unknown>;
  return {
    schedulable: typeof o.schedulable === "boolean" ? o.schedulable : null,
    reason: typeof o.reason === "string" ? o.reason : null,
    // kubelet 조건 taint(압박·준비 안 됨)만이면 일시 -- 새 계획만 피하고 이미 계획된 작업은 기다린다(서버 k8s_hard_block)
    transient: o.transient === true,
  };
}

export const exclusionTitle = (n: NodeInfo) => n.exclusion
  ? `사유: ${n.exclusion.reason ?? "(없음)"} · ${n.exclusion.created_by} · ${kstStamp(n.exclusion.created_at)}`
  : "";

const pill = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap";

// 상태 배지만(대시보드·상세 공용): 배치 제외(빨강) / cordon(주황). 둘 다 아니면 아무것도 그리지 않는다.
export function PlacementBadges({ node }: { node: NodeInfo }) {
  const k8s = k8sNodeState(node);
  return (
    <>
      {node.exclusion && (
        <span className={`${pill} text-bad bg-badbg`} title={exclusionTitle(node)}>배치 제외</span>
      )}
      {k8s.schedulable === false && !k8s.transient && (
        <span className={`${pill} text-orange-700 bg-orange-50`}
              title={`k8s 스케줄 불가(${k8s.reason ?? "사유 모름"}) — DMS 도 이 노드에 작업을 배치하지 않습니다. `
                + "해제는 kubectl uncordon / taint 제거"}>cordon</span>
      )}
      {k8s.schedulable === false && k8s.transient && (
        <span className={`${pill} text-muted bg-canvas`}
              title={`k8s 일시 스케줄 불가(${k8s.reason ?? "사유 모름"}) — 새 작업은 다른 노드로 계획하고, 이미 계획된 `
                + "작업은 상태가 풀릴 때까지 기다립니다"}>일시 불가</span>
      )}
    </>
  );
}

export function PlacementCell({ node }: { node: NodeInfo }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const exclude = useExcludeNode();
  const include = useIncludeNode();
  const excluded = !!node.exclusion;
  const tooLong = reason.trim().length > MAX_REASON;
  const onOpen = (o: boolean) => {
    if (o) { setReason(""); exclude.reset(); include.reset(); }
    setOpen(o);
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <PlacementBadges node={node} />
      {excluded ? (
        <Dialog open={open} onOpenChange={onOpen} title={`${node.node_name} 다시 포함`}
                trigger={<Button variant="ghost" aria-label={`${node.node_name} 다시 포함`}>다시 포함</Button>}>
          <div className="space-y-3 text-sm">
            {/* cordon·리포트 지연이면 다시 포함해도 바로 배치되지 않는다 -- 그 사실을 말한다(리뷰) */}
            {k8sNodeState(node).schedulable === false && !k8sNodeState(node).transient ? (
              <p>배치 제외는 풀리지만 이 노드는 k8s 에서 스케줄 불가(cordon) 상태라 <b>uncordon 전까지는</b> 작업이
                배치되지 않습니다.</p>
            ) : !node.fresh ? (
              <p>배치 제외는 풀리지만 이 노드는 리포트가 지연돼 있어 <b>보고가 정상화된 뒤</b> 작업이 배치됩니다.</p>
            ) : (
              <p>다시 포함하면 다음 계획(약 10초)부터 이 노드에 작업이 배치됩니다.</p>
            )}
            <p className="text-muted text-xs">
              {`제외 사유: ${node.exclusion?.reason ?? "(없음)"} · ${node.exclusion?.created_by} · `
                + `${node.exclusion ? kstStamp(node.exclusion.created_at) : ""}`}
            </p>
            <p className="text-muted text-xs">제외 때문에 거부·종료된 작업은 자동으로 다시 실행되지 않습니다 — 다시 제출하세요.</p>
            {include.isError && <p role="alert" className="text-bad">{(include.error as ApiError).message}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => onOpen(false)}>닫기</Button>
              <Button disabled={include.isPending}
                      onClick={() => include.mutate(node.node_name, { onSuccess: () => setOpen(false) })}>
                다시 포함
              </Button>
            </div>
          </div>
        </Dialog>
      ) : (
        <Dialog open={open} onOpenChange={onOpen} title={`${node.node_name} 배치 제외`}
                trigger={<Button variant="ghost" aria-label={`${node.node_name} 배치 제외`}>배치 제외</Button>}>
          <div className="space-y-3 text-sm">
            <ul className="list-disc space-y-1 pl-5">
              <li>새 작업은 이 노드에 배치되지 않습니다(남은 노드로 실행, 남은 노드가 없으면 거부).</li>
              <li>이미 이 노드로 계획됐거나 제출됐지만 아직 시작 전(대기 중)인 작업은 종료됩니다 — 다시 제출하면 남은
                노드로 실행됩니다. 배치 작업 항목도 같은 이유로 종료됩니다.</li>
              <li>이 노드에서 이미 실행 중인 작업은 건드리지 않습니다. 에이전트 보고·지표는 계속 보입니다.</li>
            </ul>
            <label className="block">사유(선택 · 500자 이하)
              <input aria-label="제외 사유" value={reason} onChange={(e) => setReason(e.target.value)}
                     placeholder="예: 디스크 오류 점검 중"
                     className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm" />
            </label>
            {tooLong && <p className="text-bad">사유는 500자 이하여야 합니다</p>}
            {exclude.isError && <p role="alert" className="text-bad">{(exclude.error as ApiError).message}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => onOpen(false)}>닫기</Button>
              <Button disabled={exclude.isPending || tooLong}
                      onClick={() => exclude.mutate({ name: node.node_name, reason },
                                                     { onSuccess: () => setOpen(false) })}>
                배치에서 제외
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}
