import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useUpsertPolicy } from "./usePolicies";
import type { Policy } from "../../lib/types";
const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
export function PolicyDialog({ policy, trigger }: { policy: Policy; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [maxNodes, setMaxNodes] = useState(policy.max_nodes);
  const [procsPerNode, setProcsPerNode] = useState(policy.procs_per_node);
  const [queue, setQueue] = useState(policy.queue);
  const [defaultPriority, setDefaultPriority] = useState(policy.default_priority);
  const [maxPriority, setMaxPriority] = useState(policy.max_priority);
  const [previewTimeout, setPreviewTimeout] = useState(
    policy.preview_timeout_seconds === null ? "" : String(policy.preview_timeout_seconds));
  const [executionTimeout, setExecutionTimeout] = useState(policy.execution_timeout_seconds);
  const [enabled, setEnabled] = useState(policy.enabled === 1);
  const m = useUpsertPolicy();
  useEffect(() => {
    if (!open) { m.reset(); return; }
    setMaxNodes(policy.max_nodes); setProcsPerNode(policy.procs_per_node);
    setQueue(policy.queue); setDefaultPriority(policy.default_priority); setMaxPriority(policy.max_priority);
    setPreviewTimeout(policy.preview_timeout_seconds === null ? "" : String(policy.preview_timeout_seconds));
    setExecutionTimeout(policy.execution_timeout_seconds);
    setEnabled(policy.enabled === 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, policy]);
  const submit = () => {
    m.mutate({
      tool: policy.tool,
      body: {
        max_nodes: maxNodes, procs_per_node: procsPerNode, queue,
        default_priority: defaultPriority, max_priority: maxPriority,
        preview_timeout_seconds: previewTimeout === "" ? null : Number(previewTimeout),
        execution_timeout_seconds: executionTimeout, enabled,
      },
    }, { onSuccess: () => setOpen(false) });
  };
  return (
    <Dialog open={open} onOpenChange={setOpen} title={`${policy.tool} 정책 수정`} trigger={trigger}>
      <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="block">도구
          <input className={field} value={policy.tool} disabled /></label>
        <label className="block">최대 노드
          <input aria-label="최대 노드" type="number" className={field} value={maxNodes}
                 onChange={(e) => setMaxNodes(Number(e.target.value))} /></label>
        <label className="block">노드당 프로세스
          <input aria-label="노드당 프로세스" type="number" className={field} value={procsPerNode}
                 onChange={(e) => setProcsPerNode(Number(e.target.value))} /></label>
        <label className="block">큐
          <input aria-label="큐" className={field} value={queue} onChange={(e) => setQueue(e.target.value)} /></label>
        <label className="block">기본 우선순위
          <select aria-label="기본 우선순위" className={field} value={defaultPriority}
                  onChange={(e) => setDefaultPriority(e.target.value)}>
            <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
          </select></label>
        <label className="block">최대 우선순위
          <select aria-label="최대 우선순위" className={field} value={maxPriority}
                  onChange={(e) => setMaxPriority(e.target.value)}>
            <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
          </select></label>
        <label className="block">미리보기 타임아웃(초)
          <input aria-label="미리보기 타임아웃(초)" type="number" className={field} value={previewTimeout}
                 onChange={(e) => setPreviewTimeout(e.target.value)} /></label>
        <label className="block">실행 타임아웃(초)
          <input aria-label="실행 타임아웃(초)" type="number" className={field} value={executionTimeout}
                 onChange={(e) => setExecutionTimeout(Number(e.target.value))} /></label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)} /> 활성</label>
        {m.isError && <p className="text-bad">{(m.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={m.isPending}>저장</Button>
        </div>
      </form>
    </Dialog>
  );
}
