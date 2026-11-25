import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError, reasonText } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import type { ForgetNodeResult, NodeExclusion } from "../../lib/types";
import { useForgetNode } from "./useNodes";

// 노드 기록 삭제 확인 창(2026-11-25 -- OA 사이트에서 호스트 이름을 바꾼 뒤 옛 이름이 「리포트 지연」으로 영원히 남은 사고).
// 서버 계약은 DELETE /api/admin/nodes/{name}(routes_nodes·node_forget): 최신 보고(agent_nodes)·보고 이력(agent_reports)·배치 제외를
// 한 트랜잭션에 지우고, 정상 보고 중이면 409 node_fresh, 가리키는 설정·진행 중 작업이 있으면 409 「node_in_use:<근거,…>」(진행 중
// 작업이 근거면 「;requests=<요청 id,…>」 — 최대 5개, 작업 목록엔 노드 필터가 없어 창이 요청 상세 링크로 짚어 준다), 없으면
// 404 node_not_found, 공유 토큰은 403 admin_session_required. 버튼을 리포트 지연 행에만 그리는 것은 표시 게이트일 뿐이다.
//
// **페이지 수준 창 하나**다(행 안에 두지 않는다): 성공하면 노드 목록 재조회로 그 행이 사라지는데, 창이 행 안에 있으면 함께
// 언마운트돼 mutate 콜백(결과 줄·닫기)이 불리지 않고 포커스가 <body> 로 떨어진다. 409 node_fresh 뒤 그 노드가 다시 정상 보고로
// 돌아와도 버튼과 함께 오류 문구가 사라졌다(보안 이벤트 GlobalUnlockButton 과 같은 이유). 그래서 창 내용은 연 순간의 스냅숏
// (target)으로 그리고, 열린 사이 10초 재조회가 와도 바뀌지 않는다.
// 보내는 동안은 닫히지 않는다(Esc·X·바깥 클릭·「취소」) -- isPending 은 다음 렌더에야 참이라 누른 순간 동기로 세우는 sending
// 으로도 막는다(그 틈의 Esc·「취소」·두 번째 클릭). 닫히면 mutation 을 reset 해 다음에 연 창에 옛 오류가 남지 않는다.

export interface ForgetTarget {
  node_name: string; reported_at: string; exclusion: NodeExclusion | null; renamed_to: string | null;
}

// node_in_use 근거 토큰(서버 node_forget.REF_KINDS 순서) → 화면 이름. 모르는 토큰(새 서버)은 원문 그대로 보인다.
export const NODE_REF_LABELS: Record<string, string> = {
  build_node: "빌드 노드",
  k8s_primary: "k8s 주 호출 노드",
  k8s_secondary: "k8s 보조 호출 노드",
  active_build: "진행 중 빌드",
  active_jobs: "진행 중 작업",
};

const splitList = (s: string) => s.split(",").map((r) => r.trim()).filter((r) => r !== "");

/** 「node_in_use:build_node,active_jobs;requests=r1,r2」 → 근거 토큰과 막는 요청 id. 모양이 다르면 빈 목록(모름). */
export function parseNodeInUse(code: string): { refs: string[]; requests: string[] } {
  const sep = code.indexOf(":");
  if (sep < 0) return { refs: [], requests: [] };
  const [refPart, ...rest] = code.slice(sep + 1).split(";");
  const req = rest.find((p) => p.startsWith("requests="));
  return { refs: splitList(refPart), requests: req === undefined ? [] : splitList(req.slice("requests=".length)) };
}

/** 「node_in_use:build_node,active_jobs」 → 사유 문구 + (막는 것: 빌드 노드 · 진행 중 작업). 근거가 없으면 사유 문구만.
 *  요청 id(「;requests=」)는 여기 넣지 않는다 — 창이 링크로 따로 그린다. */
export function nodeInUseText(code: string): string {
  const base = reasonText("node_in_use");
  const { refs } = parseNodeInUse(code);
  if (refs.length === 0) return base;
  const labels = refs.map((r) => (Object.prototype.hasOwnProperty.call(NODE_REF_LABELS, r) ? NODE_REF_LABELS[r] : r));
  return `${base} (막는 것: ${labels.join(" · ")})`;
}

/** node_in_use 거절이면 그 코드, 아니면 null. */
const inUseCode = (e: unknown): string | null =>
  e instanceof ApiError && (e.code === "node_in_use" || e.code.startsWith("node_in_use:")) ? e.code : null;

function errorText(e: unknown): string {
  if (!(e instanceof ApiError)) return "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요";
  const inUse = inUseCode(e);
  return inUse !== null ? nodeInUseText(inUse) : e.message;
}

// 막는 진행 중 작업의 요청 — 요청 상세에서 취소하거나 끝나기를 기다린다(작업 목록엔 노드 필터가 없다).
function BlockingRequests({ ids }: { ids: string[] }) {
  if (ids.length === 0) return null;
  return (
    <span className="block mt-1">
      {"이 노드를 후보로 둔 진행 중 작업의 요청: "}
      {ids.map((id, i) => (
        <span key={id}>
          {i > 0 && ", "}
          <Link to={`/jobs/${encodeURIComponent(id)}`} title={id}
                className="font-mono text-xs text-accent hover:underline">{id.slice(0, 12)}</Link>
        </span>
      ))}
      {" — 요청 상세에서 취소하거나 끝나기를 기다린 뒤 다시 시도하세요."}
    </span>
  );
}

export function ForgetNodeDialog({ target, onClose, onDone, focusAfterDone, returnFocus }: {
  target: ForgetTarget | null;
  onClose: () => void;
  onDone: (r: ForgetNodeResult) => void;
  // 성공해 닫히면 호출측 결과 줄로 -- 지운 행의 버튼은 이미 없다.
  focusAfterDone: () => void;
  // 취소·실패 뒤 닫히면 연 버튼으로 -- 트리거가 없는 창이라 Radix 가 돌려줄 자리를 모른다.
  returnFocus: () => void;
}) {
  const forget = useForgetNode();
  const sending = useRef(false);
  const succeeded = useRef(false);
  const open = target !== null;
  const inUse = forget.isError ? inUseCode(forget.error) : null;
  const close = () => { if (!sending.current && !forget.isPending) onClose(); };
  useEffect(() => { if (!open) forget.reset(); }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Dialog open={open} title={target ? `${target.node_name} 기록 삭제` : "기록 삭제"} busy={forget.isPending}
            onOpenChange={(o) => { if (!o) close(); }}
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              const done = succeeded.current;
              succeeded.current = false;
              if (done) focusAfterDone(); else returnFocus();
            }}>
      {target && (
        <div className="space-y-3 text-sm">
          <p>
            {`이 노드의 기록(최신 보고·보고 이력${target.exclusion ? "·배치 제외" : ""})을 지웁니다. `}
            <b>되돌릴 수 없습니다.</b>
          </p>
          <p>
            같은 이름의 노드가 다시 보고하면 새 행으로 다시 생깁니다 — 노드 이름이 바뀌었거나 클러스터에서 뺀 노드를 정리할 때
            씁니다. 스토리지 데이터와 작업 기록은 건드리지 않습니다.
          </p>
          {target.exclusion && (
            <p>배치 제외도 함께 지워집니다 — 같은 이름으로 다시 보고하면 배치 대상이 됩니다.</p>
          )}
          <p className="text-muted text-xs">
            빌드 노드·k8s 호출 노드(주·보조)로 지정돼 있거나 이 노드에 배치된 진행 중 작업·빌드가 있으면 지울 수 없습니다.
          </p>
          <p className="text-muted text-xs">{`마지막 보고: ${kstStamp(target.reported_at)}`}</p>
          {typeof target.renamed_to === "string" && target.renamed_to !== "" && (
            <p className="text-muted text-xs">{`같은 장비로 보이는 새 이름: ${target.renamed_to}`}</p>
          )}
          {forget.isPending && <p role="status" className="text-muted">지우는 중…</p>}
          {forget.isError && (
            <p role="alert" className="text-bad">
              {errorText(forget.error)}
              {inUse !== null && <BlockingRequests ids={parseNodeInUse(inUse).requests} />}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" disabled={forget.isPending} onClick={close}>취소</Button>
            <Button variant="danger" type="button" disabled={forget.isPending}
                    onClick={() => {
                      if (sending.current) return;
                      sending.current = true;
                      forget.mutate(target.node_name, {
                        onSuccess: (r) => {
                          succeeded.current = true;
                          onDone(r);
                          onClose();
                        },
                        onSettled: () => { sending.current = false; },
                      });
                    }}>
              기록 삭제
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
