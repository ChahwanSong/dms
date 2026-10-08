// 요청 상세 화면의 표기 헬퍼(2026-10-08 재설계). 화면 곳곳(배너 KPI·단계 행·출력 칩·뷰어)이 같은 숫자를 다른
// 모양으로 찍으면 같은 사실이 화면마다 다르게 읽힌다 -- 이 화면 안에서는 이 모듈 하나로 통일한다. 다른 화면의
// humanBytes 국소 사본(NodesList·JobStats·BatchDetail 관례)은 그대로 둔다(이 재설계의 범위 밖).
//
// 규약: **모름(null)과 0 을 섞지 않는다.** 모르면 null(호출측이 "—" 를 찍거나 그 조각을 생략), 0 은 "0초"·"0 B"·
// "0개" 라는 정상값이다(CLAUDE.md null≠0).
import { spanMs } from "../../lib/duration";

// JobViewer(삭제)판 그대로: 아티팩트에는 작은 파일이 흔해 KiB 단위를 쓰고, 0 바이트는 정상값("0 B") -- "—" 는
// null·비유한수(크기 모름) 전용이다.
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2], ["KiB", 1024],
];
export function humanBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes)) return "—";
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${bytes} B`;
}

// 옛 RequestDetail.durationText 의 반올림 규칙 그대로(초 반올림, 60초 미만 "N초", 정각 분 "N분", 그 외 "N분 S초")
// -- RequestDetail.test 의 "1분 30초" 정확 일치가 이 규칙에 묶여 있다. 1시간 이상은 "N시간 M분"(정각이면 "N시간").
// 음수·비유한수는 null(모름) -- "-3초" 는 그 자체로 거짓말이다(lib/duration 과 같은 이유).
export function msText(ms: number): string | null {
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return m % 60 === 0 ? `${h}시간` : `${h}시간 ${m % 60}분`;
  }
  return s % 60 === 0 ? `${m}분` : `${m}분 ${s % 60}초`;
}

// 두 시각 사이 소요. 어느 쪽이든 모르거나(null·파싱 불가) 거꾸로면 null -- 화면은 그 조각을 생략하거나 "—".
export function spanText(from: string | null | undefined, to: string | null | undefined): string | null {
  const ms = spanMs(from, to);
  return ms === null ? null : msText(ms);
}

// KST 벽시계(lib/datetime 과 같은 고정 +9h -- DST 없음). 요청 상세의 단계 시각은 하루 안에 끝나는 게 보통이라
// "HH:MM:SS" 만 찍고, 기준 시각(요청 제출)과 KST 날짜가 다를 때만 "MM-DD HH:MM:SS" 로 날짜를 붙인다(자정을 넘긴
// 잡에서 03:15 가 어느 날인지 모호해지지 않게). 파싱 불가면 원문 그대로 -- 지어내지 않는다.
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
function kstParts(iso: string): { day: string; clock: string } | null {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  const s = new Date(ms + KST_OFFSET_MS).toISOString();
  return { day: s.slice(5, 10), clock: s.slice(11, 19) };
}
export function kstClock(iso: string, refIso?: string | null): string {
  const p = kstParts(iso);
  if (p === null) return iso;
  const ref = refIso ? kstParts(refIso) : null;
  return ref !== null && ref.day !== p.day ? `${p.day} ${p.clock}` : p.clock;
}

// 개수 "1,204개". 0 은 "0개"(정상값).
export function countText(n: number): string {
  return `${n.toLocaleString("ko-KR")}개`;
}

// 유한한 숫자만 숫자로 -- 서버가 문자열·null·NaN 을 실어도 수치를 지어내지 않는다.
export function finiteOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
