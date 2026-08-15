// 스토리지 관리 디렉토리(managed_root) + 상대경로 → 절대경로 조합. 화면 세 곳
// (요청 상세·배치 항목·대시보드 최근 작업)이 같은 규칙을 쓰도록 한 곳에 둔다.
//
// **왜 프론트에서 조합하나**: 잡 payload 에는 상대경로만 남는다(서버 계약 무변경).
// 서버가 기록 시점의 절대경로를 payload 에 박아 두면 그 뒤 스토리지 managed_root
// 를 바꿨을 때 화면이 지금 존재하지 않는 경로를 사실처럼 보여준다. 반대로 지금의
// managed_root 로 조합하면 화면은 늘 "지금 이 이름의 스토리지에서 그 상대경로가
// 가리키는 곳"을 말한다 — 과거 기록은 payload 그대로 남고(불변), 해석만 현재
// 진실을 따른다.
//
// **모르면 생략한다**: managed_root 는 관리자 응답에만 실린다(routes_storages
// list_user_storages). 비관리자·조회 실패·구형 서버에서는 null 을 돌려주고 화면은
// 그 줄 자체를 안 그린다 — "undefined/team" 같은 거짓 경로나 빈 문자열로 뭉개면
// 없는 사실을 지어내는 것이다(null≠빈값 규약).

/** managed_root + 상대경로 → 절대경로. 뿌리를 모르거나 경로가 문자열이 아니면 null.
 *  빈 상대경로("")는 **뿌리 자신**이다(정상값 — "모름"으로 뭉개지 않는다). */
export function absolutePath(root: string | null | undefined,
                             rel: unknown): string | null {
  if (typeof root !== "string" || root === "") return null;
  if (typeof rel !== "string") return null;
  const base = root.replace(/\/+$/, "");
  const tail = rel.replace(/^\/+/, "");
  if (tail === "") return base === "" ? "/" : base;
  return `${base === "" ? "" : base}/${tail}`;
}

export type StorageRoots = Record<string, string | undefined>;

const _root = (roots: StorageRoots, storage: unknown) =>
  typeof storage === "string" ? roots[storage] : undefined;

/** 상대경로 표기(storage:path — sync 는 출발 → 도착). 대시보드·배치 항목의
 *  요약과 같은 문법이되 operation 접두는 붙이지 않는다(호출측이 이미 말한다). */
export function pathSummary(operation: string | undefined,
                            payload: Record<string, unknown> | undefined): string {
  const p = payload ?? {};
  // ?? 로만 접는다 — truthy 검사는 ""(빈 경로 = 뿌리)를 "모름"으로 뭉갠다.
  const part = (v: unknown) => String(v ?? "—");
  return operation === "sync"
    ? `${part(p.source_storage)}:${part(p.source)} → ${part(p.destination_storage)}:${part(p.destination)}`
    : `${part(p.storage)}:${part(p.target)}`;
}

/** 절대경로 표기. 하나라도 조합할 수 없으면 null(그 줄을 안 그린다) — sync 는
 *  출발·도착 **둘 다** 알 때만: 한쪽만 절대경로로 보여주면 나머지 한쪽이
 *  상대경로인지 모르는 경로인지 화면이 구분해 주지 못한다. */
export function absSummary(operation: string | undefined,
                           payload: Record<string, unknown> | undefined,
                           roots: StorageRoots): string | null {
  const p = payload ?? {};
  if (operation === "sync") {
    const s = absolutePath(_root(roots, p.source_storage), p.source);
    const d = absolutePath(_root(roots, p.destination_storage), p.destination);
    return s !== null && d !== null ? `${s} → ${d}` : null;
  }
  return absolutePath(_root(roots, p.storage), p.target);
}
