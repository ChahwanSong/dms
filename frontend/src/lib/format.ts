// 공용 숫자 표기(2026-11-19 -- batchSchedule.ts 에서 옮겼다: 삭제 화면의 상한 문구도 같은 천 단위 구분을 쓴다).
/** 천 단위 구분(ko-KR) -- 50000 → 「50,000」. */
export const n0 = (n: number): string => n.toLocaleString("ko-KR");
