// 경과·소요 시간 표기. 백엔드 타임스탬프는 전부 "%Y-%m-%dT%H:%M:%SZ"(UTC 초 단위,
// db.utc_now_iso)라 Date.parse 가 그대로 읽는다.
//
// 이 모듈의 규약: **모름(null)과 0 을 섞지 않는다.** 시각을 모르면(필드 없음·파싱
// 실패) null 을 돌려 화면이 "—"를 찍게 하고, 0 은 "방금 시작"이라는 정상값이라
// "0초"로 찍는다. truthy 검사로 이 둘을 뭉개면 "10분 걸린 빌드"와 "언제 끝났는지
// 모르는 빌드"가 화면에서 같은 모양이 된다.

export function parseTs(ts: string | number | null | undefined): number | null {
  if (ts === null || ts === undefined) return null;
  // epoch ms 를 그대로 넘길 수 있게 한다 -- 호출자가 "지금"(Date.now·
  // dataUpdatedAt)을 끝 시각으로 쓰는 경로가 있다.
  if (typeof ts === "number") return Number.isFinite(ts) ? ts : null;
  const ms = Date.parse(ts);
  return Number.isNaN(ms) ? null : ms;
}

export function spanMs(
  from: string | number | null | undefined,
  to: string | number | null | undefined,
): number | null {
  const a = parseTs(from);
  const b = parseTs(to);
  if (a === null || b === null) return null;
  const ms = b - a;
  // 음수는 브라우저 시계가 서버보다 뒤에 있다는 뜻이다 -- "모름"이지 "-3초"가
  // 아니다. 화면에 음수 경과를 찍으면 그 자체가 거짓말이 된다.
  return ms < 0 ? null : ms;
}

export function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000);
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600) % 24;
  const d = Math.floor(total / 86400);
  if (d > 0) return `${d}일 ${h}시간`;
  if (h > 0) return `${h}시간 ${m}분`;
  if (m > 0) return `${m}분 ${s}초`;
  return `${s}초`;
}
