// 시각 표시는 한국시간(KST, UTC+9)으로 통일한다(2026-08-24 사용자 결정). 바뀌는
// 것은 "사람에게 보이는 벽시계"뿐이다 -- 저장·API·정렬·나이 계산은 여전히 UTC
// (db.py utc_now_iso, `%Y-%m-%dT%H:%M:%SZ`)다. UTC 저장에 의존하는 불변식(ISO
// 문자열 정렬, atime 나이, planner 유예 창, 보존 기간)을 건드리지 않기 위해서다.
//
// KST 는 DST 가 없어 항상 +9 라, Intl(ICU 데이터 의존)을 안 쓰고 고정 오프셋으로
// 접어도 정확하고 테스트가 결정적이다. 이전엔 utcStamp 국소 사본이 두 파일에
// 복제돼 있었다(BatchDetail·UsageAnalysis) -- 이 모듈로 통합한다.
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

// UTC epoch(ms) -> KST 벽시계의 ISO 문자열. +9h 를 더한 뒤 UTC 로 찍으면(toISOString
// 은 항상 UTC 표기) 그 문자열의 날짜·시각 부분이 곧 KST 벽시계다.
function kstIso(utcMs: number): string {
  return new Date(utcMs + KST_OFFSET_MS).toISOString();
}

// ISO-8601 UTC 문자열(created_at·updated_at·reported_at 등) -> "YYYY-MM-DD HH:MM:SS KST".
// 파싱 불가면 원문 그대로 돌려준다 -- 모르는 값을 지어내지 않는다(시각판 null≠0).
export function kstStamp(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return kstIso(ms).replace("T", " ").slice(0, 19) + " KST";
}

// null/빈 값이면 "—", 아니면 kstStamp. 원시 ISO 를 `?? "—"` 로 뿌리던 표들의 교체용.
export function kstStampOrDash(iso: string | null | undefined): string {
  return iso ? kstStamp(iso) : "—";
}

// epoch(초) -> "YYYY-MM-DD HH:MM:SS KST". scan 리포트 generated_at_epoch 용.
export function kstStampEpoch(epochSec: number): string {
  return kstIso(epochSec * 1000).replace("T", " ").slice(0, 19) + " KST";
}

// epoch(초) -> "MM-DD" (KST 날짜). 온도열 축 라벨용 -- KST 날짜 경계 기준이라
// UTC 15:00 이후는 다음날로 넘어간다(의도된 KST 표기).
export function kstDay(epochSec: number): string {
  return kstIso(epochSec * 1000).slice(5, 10);
}
