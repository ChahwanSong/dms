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
// **모르면 생략한다**: managed_root 는 역할 무관(2026-09-29~, routes_storages
// list_user_storages)이지만 비관리자 응답엔 관리자 전용(user_enabled=0) 스토리지가, 모두의
// 응답엔 비활성(enabled≠1) 스토리지가 빠진다 -- 그 이름·조회 실패·구형 서버에서는 null 을
// 돌려주고 화면은 그 줄 자체를 안 그린다 — "undefined/team" 같은 거짓 경로나 빈 문자열로
// 뭉개면 없는 사실을 지어내는 것이다(null≠빈값 규약).

// **글자 그대로의 조합이다(링크를 풀지 않는다)**: 결과는 「입력한 경로를 관리 디렉토리에 붙인 것」이지 파일시스템이 실제로
// 가리키는 곳이 아니다. 그래서 화면 라벨은 「지정 경로」다(2026-10-27 d192, 포탈 괴리 [C2] -- 예전 「실제 경로」는 링크를
// 거치는 경로로 거부된 뒤 「실제 경로로 다시 요청하세요」와 만나 링크가 가리키는 곳으로 다시 내라는 뜻으로 읽혔다).

/** managed_root + 상대경로 → 절대경로(글자 그대로, 링크 미해석). 뿌리를 모르거나 경로가 문자열이 아니면 null.
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

/** sync 목적지의 **상위 디렉토리** 절대경로(2026-09-30 "상위 디렉토리 쓰기 권한 조건을 분명히").
 *  sync 도구(dsync)는 목적지가 이미 있어도 그 상위에 실행 신원의 쓰기 권한을 요구한다 --
 *  제출 화면이 "어느 디렉토리에" 권한이 필요한지 절대경로로 보여 주는 데 쓴다.
 *  목적지 상대경로가 비면(목적지 = 관리 디렉토리 자신) 상위는 관리 디렉토리의 부모다.
 *  뿌리를 모르면 null(absolutePath 와 같은 규칙 -- 지어내지 않는다). */
export function destinationParent(root: string | null | undefined, rel: unknown): string | null {
  if (typeof root !== "string" || root === "" || typeof rel !== "string") return null;
  const parts = rel.split("/").filter((p) => p !== "" && p !== ".");
  if (parts.length === 0) {
    const base = root.replace(/\/+$/, "");
    const up = base.replace(/\/[^/]*$/, "");
    return up === "" ? "/" : up;
  }
  return absolutePath(root, parts.slice(0, -1).join("/"));
}

/** 제출 경로(관리 디렉토리 아래 상대경로)가 서버에서 거부될 이유 -- domain.validate_relative_path
 *  (422 unsafe_path) 의 즉답 미러. 서버가 최종 심판이고 이건 표시·제출 잠금용이다.
 *  거부: "/" 로 시작, ".." 구성요소, 정규화하면 "." (관리 디렉토리 자신 -- rm 은 rm_root_forbidden).
 *  빈 입력은 null(미입력 안내는 호출측 sanity 몫). 공백은 **거부가 아니다** -- 서버는 다듬지 않고
 *  그대로 이름으로 쓴다(그래서 화면도 다듬은 값으로 경로를 지어 보이면 안 된다). */
export function relativePathProblem(rel: string): string | null {
  if (rel.trim() === "") return null;
  if (rel.startsWith("/"))
    return "\"/\" 로 시작할 수 없습니다 — 관리 디렉토리 아래 상대경로로 적으세요";
  const parts = rel.split("/");
  if (parts.some((p) => p === ".."))
    return "\"..\" 은 쓸 수 없습니다 — 관리 디렉토리 밖을 가리킬 수 없습니다";
  if (parts.every((p) => p === "" || p === "."))
    return "관리 디렉토리 자신(.)은 지정할 수 없습니다 — 그 아래 경로를 적으세요";
  return null;
}

// 심볼릭 링크 경로 규칙(d192 포탈 괴리 [14] -- 2026-10-28 dms-ssc 에서 이식, dms-k8s 통일 봉쇄에 맞춘 문구) -- 서버 봉쇄 검사
// (execution_manifests dms_path_contained·러너 contain.path_redirected: realpath(경로) == realpath(관리 디렉토리) + 상대경로, PV
// 주소 쪽은 그에 더해 PV 루트 아래 글자 경계)의 화면 문구. 관리 디렉토리·PV 아래 성분이 링크면 preflight·실행 직전 재점검·도구
// 직전(dsync·drm 런처, nsync rank) 세 시점에서 거부된다(source/destination/target_path_redirected).
//   - sync: 어느 성분이든(마지막 이름 포함, 아직 없는 목적지는 있는 가장 깊은 상위까지) 링크면 거부.
//   - rm: **중간 성분**만 거부한다 -- 대상 자신(끝 성분)이 링크면 drm 은 링크만 지운다(2026-10-28 사용자 결정 「rm 끝 링크 허용
//     유지」, dms-ssc 는 끝 링크도 거부한다 -- ssc 문구 「대상 자신이 링크여도 삭제하지 않습니다」를 옮기면 틀린다).
//   - 직접 경로 scan 은 봉쇄하지 않는다(특권 운영자 전용·읽기만), PV scan 은 끝 링크까지 거부 -- 둘 다 scan 화면 몫이라 여기 없다.
// 조건 목록(단건 제출의 목적지 조건 카드, 배치 생성의 sync 안내·rm 삭제 범위)이 이 규칙을 말하지 않아, 링크로 된 목적지가
// 「디렉토리여야 합니다」를 만족한다고 보고 낸 요청이 거부됐다. 링크가 가리키는 곳으로 다시 내라고 하지 않는다(rm 은 링크 너머를
// 지우게 되고, sync 는 남이 심은 링크의 대상에 쓰게 된다 -- 괴리 [27]).
export const SYNC_PATH_LINK_RULE =
  "소스·목적지 경로가 관리 디렉토리·PV 아래 어느 단계든(마지막 이름 포함) 심볼릭 링크를 거치면 거부됩니다 — 링크가 가리키는 "
  + "곳이 아니라 링크를 거치지 않는 경로로 요청하세요.";
export const RM_PATH_LINK_RULE =
  "삭제할 경로의 중간 단계(관리 디렉토리·PV 아래)가 심볼릭 링크면 거부됩니다. 대상 자신이 심볼릭 링크면 링크만 지우고, 링크가 "
  + "가리키는 곳은 그대로 둡니다.";

export type StorageRoots = Record<string, string | undefined>;

const _root = (roots: StorageRoots, storage: unknown) =>
  typeof storage === "string" ? roots[storage] : undefined;

/** 상대경로 표기(storage:path — sync 는 출발 → 도착). 대시보드·배치 항목의
 *  요약과 같은 문법이되 operation 접두는 붙이지 않는다(호출측이 이미 말한다). */
export function pathSummary(operation: string | undefined,
                            payload: Record<string, unknown> | undefined): string {
  return pathParts(operation, payload).join(" → ");
}

// ---- PV 주소 표기(dms-k8s 설계 D8·D17, 2026-10-25) ----------------------------------------------------------------
// PV 주소로 낸 요청은 payload 에 pv | source_pv | destination_pv 블록 {cluster, namespace, pvc, volume, storage_class, root, path}
// 이 실리고, storage/target 은 서버가 해석한 물리 위치(관리 디렉토리 기준 root[/하위경로])다. 사람은 pvc-uuid 경로가 아니라
// 「PV 이름 — namespace (StorageClass) / 하위경로」로 읽는다(서버 requests.target_label 과 같은 문법). 블록은 DB 신뢰 경계
// 밖이라 모양을 믿지 않는다: 객체가 아니면 PV 표기를 하지 않고(옛 문법으로), 칸이 문자열이 아니면 「?」.
// path = PV 안 하위경로(제출 바디의 target/source/destination 그대로). 없으면 payload 의 물리 target 에서 root 를 떼어 짓는다.

/** 「PV 전체」 -- 하위경로가 비었거나 '.'·'./' 처럼 정규화하면 PV 루트 자신인 경우. */
export const PV_WHOLE = "(PV 전체)";

const _pvField = (v: unknown) => (typeof v === "string" ? v : v === null || v === undefined ? "—" : "?");

function _isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** payload 의 PV 블록 하나(객체가 아니면 null -- PV 주소 요청이 아니거나 변조 행). */
export function pvBlockOf(v: unknown): Record<string, unknown> | null {
  return _isObj(v) ? v : null;
}

/** PV 안 하위경로 표시값: 앞의 './'·끝의 '/' 를 떼고 비면(또는 '.') PV_WHOLE. 문자열이 아니면 「?」. */
export function pvSubpathText(sub: unknown): string {
  if (typeof sub !== "string") return sub === null || sub === undefined ? PV_WHOLE : "?";
  const parts = sub.split("/").filter((p) => p !== "" && p !== ".");
  return parts.length === 0 ? PV_WHOLE : parts.join("/");
}

/** 블록의 하위경로 -- path 가 있으면 그것, 없으면 물리 target 에서 root 접두를 뗀 값(root 와 같으면 ""). 모르면 undefined. */
function _pvSub(block: Record<string, unknown>, target: unknown): unknown {
  if ("path" in block) return block.path;
  const root = block.root;
  if (typeof root !== "string" || typeof target !== "string") return undefined;
  const r = root.replace(/\/+$/, "");
  if (target === r || target === `${r}/`) return "";
  return target.startsWith(`${r}/`) ? target.slice(r.length + 1) : undefined;
}

/** 「PV 이름 — namespace (StorageClass) / 하위경로」. namespace (sc) 문법은 features/k8s/k8sTerms.nsLabel 과 같다(테스트가
 *  둘을 대조한다 -- lib 가 features 를 가져오지 않게 여기 한 줄로 둔다). */
export function pvLocationText(block: Record<string, unknown>, target: unknown): string {
  const sc = block.storage_class;
  const scText = typeof sc === "string" && sc !== "" ? sc : sc === null || sc === undefined || sc === "" ? "StorageClass 없음" : "?";
  return `${_pvField(block.pvc)} — ${_pvField(block.namespace)} (${scText}) / ${pvSubpathText(_pvSub(block, target))}`;
}

/** pathSummary 의 조각(sync = [출발, 도착], 그 밖 = [대상]). 화면이 출발·도착 사이에서 줄을 바꿀 때 **구조로**
 *  가른다 -- 합친 문자열에서 " → " 를 찾으면 경로 이름에 든 " → " 에서 갈린다(validate_relative_path 는 그 문자를
 *  막지 않는다). 문자열이 아닌 값(DB 변조)은 "?" -- String({}) 의 "[object Object]" 를 화면에 내지 않는다.
 *  PV 주소로 낸 쪽(pv | source_pv | destination_pv 블록)은 pvLocationText -- 쪽마다 따로라 PV ↔ 직접 경로 혼합도 그대로 읽힌다. */
export function pathParts(operation: string | undefined,
                          payload: Record<string, unknown> | undefined): string[] {
  const p = payload ?? {};
  // null·undefined 만 「—」로 접는다 — truthy 검사는 ""(빈 경로 = 뿌리)를 "모름"으로 뭉갠다.
  const part = (v: unknown) => (typeof v === "string" ? v : v === null || v === undefined ? "—" : "?");
  const side = (block: unknown, storage: unknown, path: unknown) => {
    const b = pvBlockOf(block);
    return b !== null ? pvLocationText(b, path) : `${part(storage)}:${part(path)}`;
  };
  return operation === "sync"
    ? [side(p.source_pv, p.source_storage, p.source), side(p.destination_pv, p.destination_storage, p.destination)]
    : [side(p.pv, p.storage, p.target)];
}

/** 요청 상세 「PV」 행(설계 §7): 클러스터 · 볼륨 · StorageClass. PV 주소가 아닌 쪽은 없다(행 생략). */
export function pvDetailParts(operation: string | undefined,
                              payload: Record<string, unknown> | undefined): { side: "소스" | "목적지" | null; text: string }[] {
  const p = payload ?? {};
  const one = (side: "소스" | "목적지" | null, v: unknown) => {
    const b = pvBlockOf(v);
    return b === null ? null
      : { side, text: `${_pvField(b.cluster)} · ${_pvField(b.volume)} · ${_pvField(b.storage_class)}` };
  };
  const rows = operation === "sync" ? [one("소스", p.source_pv), one("목적지", p.destination_pv)] : [one(null, p.pv)];
  return rows.filter((r): r is { side: "소스" | "목적지" | null; text: string } => r !== null);
}

/** PV 주소 블록이 하나라도 있나(요청 상세 「대상 사용자」 행 등). */
export function hasPvAddress(payload: Record<string, unknown> | undefined): boolean {
  const p = payload ?? {};
  return [p.pv, p.source_pv, p.destination_pv].some((v) => pvBlockOf(v) !== null);
}

/** 절대경로 표기. 하나라도 조합할 수 없으면 null(그 줄을 안 그린다) — sync 는
 *  출발·도착 **둘 다** 알 때만: 한쪽만 절대경로로 보여주면 나머지 한쪽이
 *  상대경로인지 모르는 경로인지 화면이 구분해 주지 못한다. */
export function absSummary(operation: string | undefined,
                           payload: Record<string, unknown> | undefined,
                           roots: StorageRoots): string | null {
  const parts = absParts(operation, payload, roots);
  return parts === null ? null : parts.join(" → ");
}

/** absSummary 의 조각(pathParts 와 같은 모양). null = 조합 불가(행 생략). */
export function absParts(operation: string | undefined,
                         payload: Record<string, unknown> | undefined,
                         roots: StorageRoots): string[] | null {
  const p = payload ?? {};
  if (operation === "sync") {
    const s = absolutePath(_root(roots, p.source_storage), p.source);
    const d = absolutePath(_root(roots, p.destination_storage), p.destination);
    return s !== null && d !== null ? [s, d] : null;
  }
  const t = absolutePath(_root(roots, p.storage), p.target);
  return t === null ? null : [t];
}
