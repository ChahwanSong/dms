// 보안 이벤트 종류의 화면 라벨(2026-11-03). 키 집합은 서버 routes_security.SECURITY_EVENT_TYPES 와 **정확히** 같아야 한다 --
// 백엔드 테스트(test_api_security)가 이 파일을 정규식 `^  ([a-z_]+): "` 로 읽어 대조한다. 그래서 아래 객체는 한 줄에 한 키,
// 들여쓰기 두 칸, 따옴표 없는 키 모양을 지킨다(다른 곳에 같은 모양의 줄을 두지 마라 -- 대조가 흐려진다).
// verification_client_locked 는 더 내지 않는 옛 종류지만(원 IP 축 제거) 보존 기간 동안 옛 행이 보이도록 남긴다.
export const SECURITY_EVENT_LABELS: Record<string, string> = {
  login_rate_limited: "로그인 감속(아이디별 실패 상한)",
  login_attempt_limited: "로그인 시도 상한(아이디별, 성공 포함)",
  login_failure_alarm: "경보: 로그인 실패 급증",
  login_attempt_alarm: "경보: 로그인 시도 급증",
  login_source_alarm: "경보: 한 출발지의 로그인 실패",
  login_pacing_engaged: "전체 스위치: 대기 시작",
  login_busy: "로그인 과부하(503)",
  login_pacing_enabled: "전체 스위치 켜진 채 기동",
  verification_locked: "인증번호 잠금(아이디별)",
  verification_global_locked: "인증번호 전체 오답 상한 도달",
  verification_client_locked: "(이전 버전) 접속 IP 별 인증번호 잠금",
  verification_unlocked: "인증 잠금 해제(아이디)",
  verification_global_unlocked: "인증 전체 잠금 해제",
  password_admin_reset: "관리자 비밀번호 재설정",
  verification_email_throttled: "인증 메일 상한",
  verification_email_failed: "인증 메일 발송 실패",
  verification_email_uncertain: "인증 메일 발송 불확실(릴레이 무응답)",
  account_notice_sent: "완료 안내 메일 발송",
  account_notice_stub: "완료 안내 메일(stub — 미발송)",
  account_notice_failed: "완료 안내 메일 실패",
  account_notice_skipped: "완료 안내 메일 건너뜀(상한·혼잡)",
};

// 종류 필터(select 의 optgroup) 묶음 -- 모든 라벨 키가 정확히 한 묶음에 든다(eventLabels.test).
export const SECURITY_EVENT_GROUPS: { label: string; types: string[] }[] = [
  { label: "로그인", types: [
    "login_rate_limited", "login_attempt_limited", "login_failure_alarm", "login_attempt_alarm",
    "login_source_alarm", "login_pacing_engaged", "login_busy", "login_pacing_enabled",
  ] },
  { label: "인증번호", types: ["verification_locked", "verification_global_locked", "verification_client_locked"] },
  { label: "메일", types: [
    "verification_email_throttled", "verification_email_failed", "verification_email_uncertain",
    "account_notice_sent", "account_notice_stub", "account_notice_failed", "account_notice_skipped",
  ] },
  { label: "관리자 조치", types: ["verification_unlocked", "verification_global_unlocked", "password_admin_reset"] },
];

/** 문자열 키 사전의 **자기** 키만 읽는다 -- 「constructor」·「toString」 같은 값이 Object.prototype 의 함수를 집어 화면에 함수
 *  원문이 찍히지 않게(이벤트 행은 DB 에서 오고 DB 는 신뢰 경계다). */
export function ownValue<T>(map: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : undefined;
}

/** 종류 코드 → 화면 라벨. 모르는 종류(서버가 먼저 배포된 새 종류)는 원문 코드 그대로 -- 지어내지 않는다. */
export function eventLabel(type: string): string {
  return ownValue(SECURITY_EVENT_LABELS, type) ?? type;
}
