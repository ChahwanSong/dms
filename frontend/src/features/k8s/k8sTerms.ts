import { reasonText } from "../../lib/api";
import { ageDays } from "../../lib/datetime";
import type { PillVariant } from "../../lib/jobState";
import type {
  BindingRef, BindingSubject, CallerNode, ChangeEntry, RunWarning, StorageKind, UnrecognizedBinding,
} from "./types";

// k8s 연동 화면의 용어·표기·라벨 **단일 출처**(설계 §1). 코드는 k8s 용어(pvc·volume)를 쓰고, 화면은 사용자 용어를 쓴다:
//   pvc(PVC metadata.name, `kubectl get pvc` 의 NAME 열 — 사람이 읽는 이름)   → 「PV 이름」
//   volume(PV metadata.name, VOLUME 열 — pvc-5c25…·k8s-44c2…, 경로를 만드는 값) → 「볼륨」
// 사용자가 「PV name」·「pvc name」을 k8s 와 반대로 부르기 때문에(사용자 지시 원문) 화면 라벨을 이 파일 밖에서 지어내지 않는다.
// 상태 배지의 variant 매핑도 여기 둔다 — StatusPill 의 공용 pillVariant 를 고치면 잡·요청 배지까지 바뀐다(M5 관례).

export const TERMS = {
  pvc: "PV 이름",
  volume: "볼륨",
  storageClass: "StorageClass",
  namespace: "namespace",
  cluster: "클러스터",
} as const;

/** 「namespace (storageclass)」 — namespace 는 클러스터마다만 유일하고 StorageClass 는 전 클러스터에서 유일해서(사용자 전제),
 *  둘을 함께 적어야 사람이 한 칸으로 구별한다. SC 가 없는 PVC(정적 바인딩 등)는 그 사실을 적는다. */
export function nsLabel(ns: string, sc: string | null | undefined): string {
  return `${ns} (${sc && sc !== "" ? sc : "StorageClass 없음"})`;
}

interface Meta { label: string; variant: PillVariant; help: string }

const CLUSTER_STATUS: Record<string, Meta> = {
  ok: { label: "정상", variant: "ok", help: "마지막 동기화가 주기 안에 성공했습니다." },
  syncing: { label: "동기화 중", variant: "busy", help: "호출 노드가 지금 이 클러스터의 목록을 받는 중입니다." },
  delayed: { label: "지연", variant: "action",
             help: "마지막 성공이 주기의 2배보다 오래됐습니다 — 권한 판정은 마지막 성공 스냅숏 기준입니다." },
  failing: { label: "실패", variant: "bad", help: "최근 동기화가 실패했습니다 — 권한 판정은 마지막 성공 스냅숏 기준입니다." },
  never: { label: "동기화 전", variant: "neutral",
           help: "아직 한 번도 성공하지 않았습니다 — 이 클러스터의 PV 권한은 「모름」이라 PV 작업을 받지 않습니다." },
  no_node: { label: "호출 노드 없음", variant: "bad",
             help: "주·보조 호출 노드가 둘 다 지정되지 않았거나 쓸 수 없습니다 — 위 「호출 노드」에서 지정하세요." },
  disabled: { label: "꺼짐", variant: "neutral", help: "동기화를 끈 클러스터입니다 — 마지막 스냅숏은 그대로 남습니다." },
  token_missing: { label: "토큰 없음", variant: "action", help: "API 토큰이 없어 동기화하지 않습니다 — 편집에서 토큰을 넣으세요." },
  token_unreadable: { label: "토큰 읽기 불가", variant: "bad",
                      help: "저장된 토큰을 풀 수 없습니다(세션 시크릿 교체 등) — 편집에서 토큰을 다시 넣으세요." },
};

/** 클러스터 상태 → 배지 라벨·variant·설명. 모르는 상태는 원문을 중립 배지로(지어내지 않는다). */
export function clusterStatusMeta(status: string): Meta {
  return CLUSTER_STATUS[status] ?? { label: status, variant: "neutral", help: `알 수 없는 상태(${status})` };
}

const RUN_STATUS: Record<string, Meta> = {
  claimed: { label: "받는 중", variant: "busy", help: "호출 노드가 작업을 가져가 목록을 받는 중입니다." },
  received: { label: "적용 대기", variant: "busy", help: "결과를 받았고 컨트롤러가 스냅숏에 적용하기를 기다립니다." },
  applied: { label: "적용됨", variant: "ok", help: "스냅숏에 반영됐습니다." },
  failed: { label: "실패", variant: "bad", help: "목록을 받지 못했거나 적용에 실패했습니다 — 스냅숏은 바뀌지 않았습니다." },
  expired: { label: "기한 초과", variant: "bad", help: "호출 노드가 기한(유예 포함) 안에 결과를 올리지 않았습니다." },
  superseded: { label: "건너뜀", variant: "neutral", help: "더 새 결과가 먼저 적용돼 이 결과는 버렸습니다." },
  discarded: { label: "폐기", variant: "neutral", help: "받는 사이 클러스터 주소·토큰·TLS 가 바뀌어(또는 삭제돼) 적용하지 않았습니다." },
};

export function runStatusMeta(status: string): Meta {
  return RUN_STATUS[status] ?? { label: status, variant: "neutral", help: `알 수 없는 상태(${status})` };
}

export const TRIGGER_LABEL: Record<string, string> = { schedule: "정규", manual: "수동", failover: "대체 재시도" };
export const ROLE_LABEL: Record<string, string> = { primary: "주", secondary: "보조" };
export const TLS_MODE_LABEL: Record<string, string> = {
  ca: "지정한 CA로 검증", insecure: "검증 안 함", default: "시스템 기본 신뢰",
};
export const TOKEN_LABEL: Record<string, string> = { set: "설정됨", missing: "없음", unreadable: "읽을 수 없음" };
export const KIND_LABEL: Record<StorageKind, string> = { filesystem: "파일시스템", pv: "PV", both: "PV·파일시스템" };
export const ACCESS_LABEL: Record<string, string> = { admin: "admin", edit: "edit" };

/** 스토리지 종류 라디오의 짧은 라벨(스토리지 등록 창) — 목록 배지는 KIND_LABEL. */
export const KIND_CHOICES: { value: StorageKind; label: string; help: string }[] = [
  { value: "filesystem", label: "파일시스템",
    help: "직접 경로(스토리지 + 관리 디렉토리 아래 상대경로)로만 씁니다 — StorageClass 매핑 대상이 아닙니다." },
  { value: "pv", label: "PV",
    help: "k8s PV 의 실제 위치로만 씁니다 — StorageClass 를 이 스토리지에 매핑하면 PV 주소가 이 관리 디렉토리 아래로 해석됩니다. 직접 경로 선택지에는 보이지 않습니다." },
  { value: "both", label: "둘 다",
    help: "PV 와 직접 경로 둘 다 — 같은 파일시스템을 k8s PV 와 일반 디렉토리로 함께 씁니다(예: GPFS)." },
];

/** PV 경로 프리셋(설계 D9 — 서버 pv_paths.PRESETS 의 미러, 표시 전용). 관리 디렉토리 기준 상대 템플릿. 여기 없는 백엔드는
 *  StorageClass 화면에서 템플릿을 지정해야 PV 를 해석할 수 있다(서버가 최종 판정 — invalid_pv_path_template). */
export const PV_PATH_PRESETS: Record<string, string> = {
  gpfs: "{volume}/{volume}-data",
  onefs: "data/csi/{volume}",
  nfs: "{subdir}",
};

/** 스토리지 응답의 kind(설계 D11) — 없거나 null 이면 filesystem(옛 행·옛 서버). */
// 인자는 아무 스토리지 행(object) -- lib/types 의 Storage·UserStorage 는 kind 칸을 선언하지 않는다(공용 타입 파일을 브랜치마다
// 고치지 않게). 서버가 kind 를 실으면 읽고, 없으면 filesystem.
export function storageKindOf(s: object): StorageKind {
  const kind = (s as { kind?: unknown }).kind;
  return kind === "pv" || kind === "both" ? kind : "filesystem";
}

/** PVC phase 배지. Bound 만 고를 수 있다(제출 pv_not_bound). */
export function phaseVariant(phase: string | null | undefined): PillVariant {
  if (phase === "Bound") return "ok";
  if (phase === "Lost") return "bad";
  if (phase === "Pending") return "action";
  return "neutral";
}

/** 개수 칸: null(모름 — 목록 권한 없음·아직 동기화 전)은 「—」, 0 은 「0」. */
export function countText(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("ko-KR") : "—";
}

/** 주기 표기 — 정수 분이면 「n분」, 시간 단위면 「n시간」, 그 밖은 초. */
export function intervalText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds % 3600 === 0) return `${seconds / 3600}시간`;
  if (seconds % 60 === 0) return `${seconds / 60}분`;
  return `${seconds}초`;
}

/** 주기 선택지(초) — 설계 §0 「기본 5분 전체」. 직접 입력은 분 단위(60초 이상). */
export const INTERVAL_PRESETS = [60, 300, 900, 3600] as const;
export const MIN_INTERVAL_SECONDS = 60;

/** 미래 시각까지 「n분 뒤」(지났으면 「곧」). 모르면 「—」. lib/datetime.agoText 의 짝. */
export function untilText(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "—";
  const minutes = Math.floor((ms - nowMs) / 60_000);
  if (minutes < 1) return "곧";
  if (minutes < 60) return `${minutes}분 뒤`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours}시간 뒤` : `${Math.floor(hours / 24)}일 뒤`;
}

/** 토큰 만료까지 남은 일수(음수 = 지남). 모르면 null. */
export function daysUntil(iso: string | null | undefined, nowMs: number): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  return (ms - nowMs) / 86_400_000;
}

/** 시각 + 간격(초) → ISO. 다음 정규 동기화 예정(last_attempt_at + interval) 계산용. */
export function plusSeconds(iso: string | null | undefined, seconds: number): string | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : new Date(ms + seconds * 1000).toISOString();
}

/** 소요 ms → 「1.2초」·「2분 3초」. null 은 「—」. */
export function durationText(ms: number | null | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}초`;
  const m = Math.floor(s / 60);
  return `${m}분 ${Math.round(s - m * 60)}초`;
}

// 경로 템플릿 자리표시자(설계 D9 — 서버 pv_paths 가 해석한다; 여기는 안내 문구일 뿐이다).
export const PATH_PLACEHOLDERS: { token: string; help: string }[] = [
  { token: "{volume}", help: "볼륨(PV 이름 — pvc-5c25… · k8s-44c2…)" },
  { token: "{pvc}", help: "PV 이름(PVC 이름 — 예: models)" },
  { token: "{namespace}", help: "namespace" },
  { token: "{storageclass}", help: "StorageClass 이름" },
  { token: "{subdir}", help: "PV volumeAttributes 의 subdir(csi-driver-nfs) — / 포함 가능" },
  { token: "{attr:<키>}", help: "PV volumeAttributes 의 임의 키(대소문자 무시) — / 포함 가능" },
  { token: "{handle:<구분자>:<i>}", help: "volumeHandle 을 구분자로 나눈 i번째 조각 — / 포함 가능" },
];

// 인식 규칙(설계 D5 — 사용자 지시 그대로). 서버 k8s_rbac 이 판정하고, 화면은 「왜 인식하지 않았나」를 설명할 때만 쓴다.
export const RECOGNIZED_ROLES = ["kubeflow-admin", "kubeflow-edit"] as const;
export const BINDING_NAME_RE = /^(.+)-clusterrole-(admin|edit)$/;

/** 인식하지 않은 바인딩의 이유(표시용 — 판정은 서버). */
export function unrecognizedWhy(b: Pick<UnrecognizedBinding, "name" | "role_kind" | "role_name">): string {
  const roleOk = b.role_kind === "ClusterRole" && (RECOGNIZED_ROLES as readonly string[]).includes(b.role_name ?? "");
  const nameOk = BINDING_NAME_RE.test(b.name);
  if (!roleOk && !nameOk) return "역할이 ClusterRole kubeflow-admin·kubeflow-edit 가 아니고 이름도 <ID>-clusterrole-admin|edit 꼴이 아님";
  if (!roleOk) return "이름은 맞지만 역할이 ClusterRole kubeflow-admin·kubeflow-edit 가 아님";
  if (!nameOk) return "역할은 맞지만 이름이 <ID>-clusterrole-admin|edit 꼴이 아님";
  return "서버 규칙 밖(예: 같은 namespace 의 중복 바인딩)";
}

export function subjectText(s: BindingSubject): string {
  return s.namespace ? `${s.kind} ${s.namespace}/${s.name}` : `${s.kind} ${s.name}`;
}

/** 근거 바인딩 원문을 한 모양으로 — 서버가 이름만 줘도(string) 원문 객체를 줘도 같은 칸에 그린다. */
export function bindingParts(b: string | BindingRef): { name: string; role: string | null; subjects: string[] } {
  if (typeof b === "string") return { name: b, role: null, subjects: [] };
  const role = b.role_name ? `${b.role_kind ?? "ClusterRole"}/${b.role_name}` : null;
  return { name: b.name, role, subjects: (b.subjects ?? []).map(subjectText) };
}

/** 「이 PV로 단일 작업」 링크 — 제출 화면이 ?pv=<cluster>/<ns>/<pvc> 를 읽어 그 PV 를 미리 고른다(k8s 이름엔 / 가 없다). */
export function pvJobLink(cluster: string, ns: string, pvc: string): string {
  return `/jobs/new?pv=${[cluster, ns, pvc].map(encodeURIComponent).join("/")}`;
}

/** 호출 노드 카드의 상태 배지 — 보고 지연·미지원 에이전트·최근 실패·사용 중·대기. 노드가 목록에 없으면 「보고 없음」. */
export function callerNodeState(n: CallerNode | undefined): { label: string; variant: PillVariant } {
  if (n === undefined) return { label: "보고 없음", variant: "bad" };
  if (!n.fresh) return { label: "보고 지연", variant: "bad" };
  if (!n.capable) return { label: "미지원 에이전트", variant: "action" };
  const fail = n.last_fail_at ? Date.parse(n.last_fail_at) : NaN;
  const ok = n.last_ok_at ? Date.parse(n.last_ok_at) : NaN;
  if (!Number.isNaN(fail) && (Number.isNaN(ok) || fail > ok)) return { label: "최근 실패", variant: "bad" };
  if (n.serving.length > 0) return { label: "사용 중", variant: "ok" };
  return { label: "대기", variant: "neutral" };
}

/** run 경고 한 줄 — 사유 코드면 사람 말로, 객체면 「목록: 사유」. */
export function warningText(w: RunWarning): string {
  if (typeof w === "string") return reasonText(w);
  const list = typeof w.list === "string" ? w.list : null;
  const reason = typeof w.reason === "string" ? reasonText(w.reason) : null;
  if (list && reason) return `${list}: ${reason}`;
  return Object.entries(w).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ");
}

/** 변경 요약의 이름 목록 — 추가·삭제로 나뉘었으면 각각, 하나뿐이면 전부 「바뀐 이름」으로. */
export function changeNames(e: ChangeEntry): { added: string[]; removed: string[]; other: string[] } {
  const n = e.names;
  if (Array.isArray(n)) return { added: [], removed: [], other: n };
  if (n && typeof n === "object") return { added: n.added ?? [], removed: n.removed ?? [], other: [] };
  return { added: [], removed: [], other: [] };
}

export const CHANGE_LABEL: Record<string, string> = {
  pvcs: "PV 이름(PVC)", bindings: "권한 바인딩", role_bindings: "권한 바인딩", pvs: "볼륨(PV)",
  storage_classes: "StorageClass",
};

export const COUNT_LABEL: Record<string, string> = {
  storage_classes: "StorageClass", pvs: "볼륨(PV)", pvcs: "PV 이름(PVC)", pvcs_bound: "Bound", namespaces: "namespace",
  bindings: "바인딩", role_bindings: "바인딩", recognized_bindings: "인식된 바인딩", users: "사용자",
};

/** 마지막 성공이 얼마나 오래됐나(분) — 화면의 「n분 전 기준」 보조. */
export function minutesSince(iso: string | null | undefined, nowMs: number): number | null {
  const d = ageDays(iso, nowMs);
  return d === null ? null : d * 1440;
}
