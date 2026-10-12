// 작업 단계·상태 설명(2026-10-12 사용자 요청 「DMS 의 작업 단계에 대한 내용을 툴팁으로 설명 추가해줘」).
// 설명 원본은 stateHelp.json 한 곳 -- 백엔드 계약 테스트(tests/test_state_help_contract.py)가 키가 서버 상태 목록(요청·잡·배치)과
// 같은지 고정한다(새 상태를 서버에 더하면 여기도). 모르는 상태는 null -- 설명을 지어내지 않는다(툴팁 없음).
// 문구 규칙(vitest 가 고정): 한국어 한 줄, 영문 **상태명**을 쓰지 않는다(e2e E6 의 정확 일치 「Succeeded」 개수와 행 수렴 단언을
// 흐리지 않게), 행동 약속(「취소할 수 있다」 등)은 넣지 않는다.
// 도메인을 섞지 않는다: 빌드·릴리스·스토리지 배지도 "Pending"·"Running" 같은 같은 문자열을 쓰므로 StatusPill 이 상태 문자열로
// 설명을 자동 조회하지 않고, 호출처가 도메인 함수로 골라 help 로 넘긴다.
import HELP from "./stateHelp.json";

type Table = Record<string, string>;
const pick = (t: Table, key: string): string | null => (Object.prototype.hasOwnProperty.call(t, key) ? t[key] : null);

export const requestStateHelp = (state: string): string | null => pick(HELP.request, state);
export function jobStateHelp(state: string, { batchChild = false }: { batchChild?: boolean } = {}): string | null {
  return (batchChild ? pick(HELP.job_batch_child, state) : null) ?? pick(HELP.job, state);
}
export const batchStateHelp = (state: string): string | null => pick(HELP.batch, state);
/** 요청 상세 단계 머리(사전 점검·미리보기 / 실행)와 컨펌 관문의 설명. flow 는 stageModel 의 흐름("scan" | 그 밖 = 미리보기 있음). */
export function stageHelp(stage: "pre" | "exec" | "gate", flow: string,
                          { batchChild = false }: { batchChild?: boolean } = {}): string {
  if (stage === "gate") return batchChild ? HELP.stage.gate_batch_child : HELP.stage.gate;
  const scan = flow === "scan";
  if (stage === "pre") return scan ? HELP.stage.pre_scan : HELP.stage.pre_previewed;
  return scan ? HELP.stage.exec_scan : HELP.stage.exec_previewed;
}
export { HELP as STATE_HELP };
