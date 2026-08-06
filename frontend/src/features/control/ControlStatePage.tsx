import { useEffect, useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useNodes } from "../nodes/useNodes";
import { useControlState, useSetControlState } from "./useControlState";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

export function ControlStatePage() {
  const q = useControlState();
  const nodesQ = useNodes();
  const setControlState = useSetControlState();
  const [maintenance, setMaintenance] = useState(false);
  const [drain, setDrain] = useState(false);
  const [reason, setReason] = useState("");
  const [buildNodeName, setBuildNodeName] = useState("");

  useEffect(() => {
    if (!q.data) return;
    setMaintenance(q.data.maintenance === 1);
    setDrain(q.data.drain === 1);
    setReason(q.data.reason ?? "");
    setBuildNodeName(q.data.build_node_name ?? "");
  }, [q.data]);

  const submit = () => {
    setControlState.mutate({
      maintenance, drain,
      reason: reason.trim() === "" ? null : reason,
      build_node_name: buildNodeName.trim() === "" ? null : buildNodeName,
    });
  };

  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">컨트롤 상태</h1>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <>
          {q.data?.maintenance === 1 && (
            <p className="text-bad font-medium">유지보수 중 — 새 작업 제출이 차단됩니다. 이미 접수된 배치는 계속 물질화되므로, 클러스터 작업을 완전히 멈추려면 드레인도 함께 켜세요</p>
          )}
          {q.data?.drain === 1 && (
            <p className="text-bad font-medium">드레인 중 — 진행 중인 작업이 더 전진하지 않습니다</p>
          )}
          <Card>
            <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
              <label className="flex items-center gap-2">
                <input type="checkbox" aria-label="유지보수" checked={maintenance}
                       onChange={(e) => setMaintenance(e.target.checked)} /> 유지보수
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" aria-label="드레인" checked={drain}
                       onChange={(e) => setDrain(e.target.checked)} /> 드레인
              </label>
              <label className="block">사유
                <input aria-label="사유" className={field} value={reason}
                       onChange={(e) => setReason(e.target.value)} /></label>
              <label className="block">빌드 노드
                {/* I1: 자유 입력 금지 -- 오타가 nodeSelector로 새면 빌드 파드가
                    영원히 Pending이다. agent_nodes에 실제로 보고된 노드 중에서만
                    고르게 한다(select). */}
                <select aria-label="빌드 노드" className={field} value={buildNodeName}
                        onChange={(e) => setBuildNodeName(e.target.value)}>
                  <option value="">지정 안 함</option>
                  {(nodesQ.data ?? []).map((n) => (
                    <option key={n.node_name} value={n.node_name}>{n.node_name}</option>
                  ))}
                </select>
              </label>
              {setControlState.isError && (
                <p className="text-bad">{(setControlState.error as ApiError).message}</p>
              )}
              <div className="flex justify-end pt-2">
                <Button type="submit" disabled={setControlState.isPending}>저장</Button>
              </div>
            </form>
          </Card>
          <Card>
            <h2 className="font-medium mb-2">현재 상태</h2>
            <p className="text-sm text-muted">변경자: <span className="text-ink font-medium">{q.data?.changed_by ?? "—"}</span></p>
            <p className="text-sm text-muted">변경 시각: <span className="text-ink font-medium">{q.data?.changed_at ?? "—"}</span></p>
          </Card>
        </>
      )}
    </section>
  );
}
