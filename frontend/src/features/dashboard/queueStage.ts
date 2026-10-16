import type { QueueStage } from "../../lib/types";

// 큐 현황의 단계 어휘·색 문턱 -- 대기 표(QueueSection)와 흐름 히어로(QueueFlowHero)가 함께 쓴다(2026-10-16 히어로 분리 때
// QueueSection 에서 옮겼다. QueueSection 이 waitText·waitTone·STAGE_LABEL 을 그대로 다시 내보낸다).

// 초 -> "n초"/"n분 n초"/"n시간 n분". RequestDetail 의 durationText 는 시각 2개를
// 받는 다른 시그니처라 재사용 불가(서버가 이미 초를 계산해 준다).
export function waitText(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}초`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return seconds % 60 === 0 ? `${m}분` : `${m}분 ${seconds % 60}초`;
  const h = Math.floor(m / 60);
  return m % 60 === 0 ? `${h}시간` : `${h}시간 ${m % 60}분`;
}

// running 을 그냥 「실행 중」이라 부르지 않는다: 같은 화면 KPI 타일 「실행 중」은 24h 창 잡 집계이고 확인 대기
// (ConfirmPending)까지 센다 -- 같은 이름에 다른 숫자가 나란히 서면 어느 쪽도 못 믿게 된다. 이 칸은 Volcano 에서
// 실제로 도는 잡이라 「Volcano 대기」와 짝을 맞춘다.
export const STAGE_LABEL: Record<QueueStage, string> = {
  planning: "계획 대기", pickup: "잡 픽업", preflight: "사전 점검", volcano_wait: "Volcano 대기",
  running: "Volcano 실행 중", confirm: "확인 대기", batch: "배치 대기",
};

// 단계 설명(역 이름 옆 HelpTip). 주기·유예 값은 기본 설정(config.py)이다. HelpTip 의 설명 노드는 숨김이지만 문서에 늘
// 있다 -- 상태에 따라 뜨는 문구(드레인 배지·「알 수 없음」 등)와 같은 낱말을 여기 쓰면 그 문구의 있음/없음 판정이 흐려진다.
// 그래서 pickup 은 「운영자가 잡 진행을 멈춰 두면」으로 말한다(옛 title 은 「드레인 중이면」).
export const STAGE_HELP: Record<QueueStage, string> = {
  planning: "요청이 접수돼 planner 가 도구·노드를 정하기를 기다립니다(10초 주기, 노드 신원 전파 유예 최대 5분).",
  pickup: "계획된 잡을 stepper 가 집기를 기다립니다(5초 주기). 운영자가 잡 진행을 멈춰 두면 여기서 멈춥니다.",
  preflight: "노드 사전 점검 파드가 도는 중입니다. 확인 뒤 실행 직전의 재점검도 여기로 다시 들어옵니다.",
  volcano_wait: "Volcano 가 gang(필요한 파드 전부)을 아직 올리지 못했습니다(PodGroup Pending·Inqueue). 워커가 프로세스 "
    + "수만큼 CPU 를 예약하므로 앞선 잡이 노드 CPU 를 쓰는 동안 여기서 차례를 기다리는 것은 정상입니다.",
  running: "Volcano 에서 실행 중인 미리보기·실행 잡입니다(대기가 아닙니다).",
  confirm: "미리보기가 끝나 사람의 컨펌(단건) 또는 확인 대기 배치의 「배치 확인」을 기다립니다. 승인되면 실행 직전 재점검을 "
    + "위해 사전 점검으로 돌아갑니다. 유효기간이 지나면 거부됩니다.",
  batch: "배치 항목이 차례를 기다립니다 — 동시 실행 상한이 풀리기를 기다리는 항목과, 미리보기를 마쳤지만 이미 확인된 배치의 "
    + "실행 슬롯이나 다른 항목의 미리보기를 기다리는 항목입니다(사람이 할 일은 없습니다).",
};

// 색 문턱(초) -- 가장 오래 기다린 시간이 attn 이상이면 주의(앰버), bad 이상이면 경고(적색). DMS 기계 단계는 주기가 초 단위라
// 5분이면 이미 이상하다(planner 신원 전파 유예 상한도 5분). 확인 대기는 사람이 누르는 단계라 길다: 유효기간(기본 24시간)의
// 1/6·1/2. 배치 대기는 큰 배치가 상한만큼씩 도는 정상 상태라 적색을 쓰지 않는다(알람 피로). Volcano 대기도 같다(2026-10-16
// 리뷰): 워커가 프로세스 수만큼 CPU 를 요청한 뒤로는 앞선 잡이 노드를 다 쓰는 동안 다음 잡이 줄을 서는 것이 정상이라, 앞
// 잡이 30분 넘게 돌면 정상 대기가 맨 위 히어로에 적색 「경고」로 떴다 -- 실행 시간 규모인 1시간에 주의만 낸다(멈춘 PodGroup
// 은 대기 표의 사유·메시지가 말한다). 실행 중은 대기가 아니라 색이 없다.
const TONE_LIMITS: Record<QueueStage, { attn: number; bad: number | null } | null> = {
  planning: { attn: 300, bad: 1800 },
  pickup: { attn: 300, bad: 1800 },
  preflight: { attn: 300, bad: 1800 },
  volcano_wait: { attn: 3600, bad: null },
  running: null,
  confirm: { attn: 4 * 3600, bad: 12 * 3600 },
  batch: { attn: 3600, bad: null },
};
export type Tone = "none" | "attn" | "bad";
export function waitTone(stage: QueueStage, seconds: number | null): Tone {
  const lim = TONE_LIMITS[stage];
  if (lim === null || seconds === null) return "none";
  if (lim.bad !== null && seconds >= lim.bad) return "bad";
  return seconds >= lim.attn ? "attn" : "none";
}
export const TEXT_TONE: Record<Tone, string> = { none: "text-muted", attn: "text-attn", bad: "text-bad" };
