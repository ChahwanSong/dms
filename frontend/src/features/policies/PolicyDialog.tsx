import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useUpsertPolicy } from "./usePolicies";
import type { Policy } from "../../lib/types";
const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

/** 필수 정수(≥1) 검증. 서버 계약(pydantic ge=1)의 미러 — 상한은 지어내지
    않는다(정책이 곧 캡이라 서버에 상한이 없다). 빈 값은 intFieldError(선택
    필드용)와 달리 여기선 오류다 — 이 필드들은 생략 불가. */
export function requiredIntError(label: string, raw: string): string | null {
  const v = raw.trim();
  if (v === "") return `${label}: 값을 입력하세요`;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) return `${label}: 1 이상의 정수여야 합니다`;
  return null;
}

/** 선택 정수(비우면 없음). 미리보기 타임아웃 전용 — null 은 "타임아웃 없음". */
export function optionalIntError(label: string, raw: string): string | null {
  return raw.trim() === "" ? null : requiredIntError(label, raw);
}

export function PolicyDialog({ policy, trigger }: { policy: Policy; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  // 숫자 필드는 **문자열 상태**다: number 상태 + Number(e.target.value) 는
  // 지우는 순간 Number("")=0 이 값이 되어 "0"이 그려지고, 그 뒤 "8"을 치면
  // DOM "08" vs 상태 8 을 type=number 가 숫자 동등으로 보아 표시를 안 고쳐
  // "08"이 남는다. 변환은 저장 시점 한 곳에서만 한다(미리보기 타임아웃 선례).
  const [maxNodes, setMaxNodes] = useState(String(policy.max_nodes));
  const [procsPerNode, setProcsPerNode] = useState(String(policy.procs_per_node));
  const [queue, setQueue] = useState(policy.queue);
  const [defaultPriority, setDefaultPriority] = useState(policy.default_priority);
  const [maxPriority, setMaxPriority] = useState(policy.max_priority);
  const [previewTimeout, setPreviewTimeout] = useState(
    policy.preview_timeout_seconds === null ? "" : String(policy.preview_timeout_seconds));
  const [executionTimeout, setExecutionTimeout] = useState(String(policy.execution_timeout_seconds));
  const [enabled, setEnabled] = useState(policy.enabled === 1);
  const m = useUpsertPolicy();
  useEffect(() => {
    if (!open) { m.reset(); return; }
    setMaxNodes(String(policy.max_nodes)); setProcsPerNode(String(policy.procs_per_node));
    setQueue(policy.queue); setDefaultPriority(policy.default_priority); setMaxPriority(policy.max_priority);
    setPreviewTimeout(policy.preview_timeout_seconds === null ? "" : String(policy.preview_timeout_seconds));
    setExecutionTimeout(String(policy.execution_timeout_seconds));
    setEnabled(policy.enabled === 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, policy]);
  // 저장 전 검증: 서버 422(pydantic detail 은 원시 문구)에 앞서 어느 필드가
  // 왜 틀렸는지 한국어로 필드 밑에 보여준다.
  const errors = {
    maxNodes: requiredIntError("최대 노드", maxNodes),
    procsPerNode: requiredIntError("노드당 프로세스", procsPerNode),
    previewTimeout: optionalIntError("미리보기 타임아웃", previewTimeout),
    executionTimeout: requiredIntError("실행 타임아웃", executionTimeout),
  };
  const invalid = Object.values(errors).some((e) => e !== null);
  const submit = () => {
    if (invalid) return;
    m.mutate({
      tool: policy.tool,
      body: {
        max_nodes: Number(maxNodes), procs_per_node: Number(procsPerNode), queue,
        default_priority: defaultPriority, max_priority: maxPriority,
        preview_timeout_seconds: previewTimeout.trim() === "" ? null : Number(previewTimeout),
        execution_timeout_seconds: Number(executionTimeout), enabled,
      },
    }, { onSuccess: () => setOpen(false) });
  };
  return (
    <Dialog open={open} onOpenChange={setOpen} title={`${policy.tool} 정책 수정`} trigger={trigger}>
      <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="block">도구
          <input className={field} value={policy.tool} disabled /></label>
        <label className="block">최대 노드
          <input aria-label="최대 노드" type="number" min={1} className={field} value={maxNodes}
                 onChange={(e) => setMaxNodes(e.target.value)} /></label>
        {errors.maxNodes && <p className="text-bad">{errors.maxNodes}</p>}
        <label className="block">노드당 프로세스
          <input aria-label="노드당 프로세스" type="number" min={1} className={field} value={procsPerNode}
                 onChange={(e) => setProcsPerNode(e.target.value)} /></label>
        {errors.procsPerNode && <p className="text-bad">{errors.procsPerNode}</p>}
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
        <label className="block">미리보기 타임아웃(초) — 비우면 타임아웃 없음
          <input aria-label="미리보기 타임아웃(초)" type="number" min={1} className={field} value={previewTimeout}
                 onChange={(e) => setPreviewTimeout(e.target.value)} /></label>
        {errors.previewTimeout && <p className="text-bad">{errors.previewTimeout}</p>}
        <label className="block">실행 타임아웃(초)
          <input aria-label="실행 타임아웃(초)" type="number" min={1} className={field} value={executionTimeout}
                 onChange={(e) => setExecutionTimeout(e.target.value)} /></label>
        {errors.executionTimeout && <p className="text-bad">{errors.executionTimeout}</p>}
        <label className="flex items-center gap-2"><input type="checkbox" checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)} /> 활성</label>
        {m.isError && <p className="text-bad">{(m.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={m.isPending || invalid}>저장</Button>
        </div>
      </form>
    </Dialog>
  );
}
