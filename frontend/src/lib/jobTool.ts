import type { WorkerPool } from "./types";

/** 잡이 **실제로 무엇으로 돌았는지**를 한 줄로 옮기는 공용 규칙.
 *
 *  왜 화면에 필요한가: sync 는 제출 시점에 도구가 정해지지 않는다 — 공존 노드가
 *  있으면 dsync, 없으면 nsync 폴백(placement.py)이다. 이 선택이 노드 수의 의미
 *  (dsync=전체 상한 / nsync=면당 상한)·적용 정책·성능 특성을 바꾸는데, 결과 화면엔
 *  무엇이 돌았는지가 없어 "노드가 왜 4개인지"를 설명할 길이 없었다(배치 생성 화면의
 *  「면당 상한」 캡션과 짝을 이루는 사후 확인이다).
 *
 *  요청 상세 잡 카드와 배치 항목 펼침이 이 한 함수를 공유한다 — 두 벌로 갈라지면
 *  같은 사실이 화면마다 다르게 읽힌다. 노드당 프로세스(× N 프로세스)와 노드 CPU 상한 클램프도
 *  여기서 함께 말한다(procsText).
 */
export function toolSummary(
  job?: { tool?: string | null; worker_pool?: WorkerPool | null } | null,
): string | null {
  const tool = job?.tool;
  // null/부재 = 아직 계획 전(도구 미정)이다. "—"조차 거짓 표시라 호출측이 표시
  // 자체를 생략하도록 null 을 돌려준다. ""(빈 문자열)도 도구 이름이 아니다.
  if (tool === null || tool === undefined || tool === "") return null;
  const wp = job?.worker_pool;
  // 숫자만 받는다: 0 은 정상값이라 그대로 쓰고(null≠0), 그 외 모양(문자열·null·
  // 부재)은 "모름"으로 접어 수치를 지어내지 않는다.
  const num = (v: unknown) =>
    (typeof v === "number" && Number.isFinite(v) ? v : null);
  const src = num(wp?.source_count);
  const dst = num(wp?.destination_count);
  // 도구 이름이 아니라 **worker_pool 의 모양**으로 양면 여부를 가른다 —
  // resolve_fanout(placement.py)이 양면 분기에서만 source/destination_count 를
  // 싣기 때문이다(실질적으로 nsync 한 벌). 이름으로 분기하면 서버가 실은 값과
  // 화면의 가정이 어긋나는 순간 없는 사실을 그린다.
  const procs = procsText(num(wp?.procs_per_node), num(wp?.procs_per_node_requested));
  if (src !== null && dst !== null) return `${tool} · 소스 ${src} + 목적지 ${dst} 노드${procs}`;
  const n = num(wp?.node_count);
  return n === null ? tool : `${tool} · ${n} 노드${procs}`;
}

/** 노드당 프로세스 꼬리(2026-10-16 노드 CPU 상한). 워커는 프로세스 수만큼 CPU 코어를 요청하고, planner·stepper 가 노드가
 *  담을 수 있는 만큼으로 **줄인다**(placement.resolve_fanout·stepper._clamp_to_node_cpu) — 줄었으면 요청값을 함께 말한다
 *  (조용히 줄이지 않는다). procs_per_node 키가 없으면(이 기능 전에 계획된 잡) 꼬리를 생략한다: process_count ÷ 노드 수로
 *  지어내지 않는다(나누어떨어지지 않는 옛 행이 있다 — 요청 상세의 「프로세스 N개」 꼬리가 그 수를 따로 말한다). */
function procsText(ppn: number | null, requested: number | null): string {
  if (ppn === null) return "";
  const clamped = requested !== null && requested > ppn ? ` (요청 ${requested} → 노드 CPU 상한 ${ppn})` : "";
  return ` × ${ppn} 프로세스${clamped}`;
}

/** 정책 캡션 옆 한 줄(작업 제출·배치 생성 — 같은 사실을 같은 문장으로). 노드당 프로세스는 정책·요청값이 아니라 노드가
 *  담을 수 있는 만큼까지만 적용된다(src/dms/node_cpu.py). */
export const NODE_CPU_CAP_NOTE =
  "노드당 프로세스는 노드 CPU 상한 안에서 적용됩니다 — 워커가 프로세스 수만큼 CPU 코어를 요청하므로, 노드가 담지 못하면 계획 때 줄어듭니다.";
