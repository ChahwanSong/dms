export type Role = "user" | "admin";
// can_run_as_root(2026-09-30): 이 로그인이 잡을 root 로 낼 자격(관리자 + 특권 목록 + 세션).
// 포탈이 'root 권한으로 실행' 을 보이고 관리자 기본값(root)을 켤지 정한다 -- 표시용, 서버가 재판정.
export interface Me { actor: string; role: Role; can_run_as_root?: boolean }
export interface Transition {
  from_state: string | null; to_state: string;
  reason_code?: string | null; actor?: string; at: string;
}
export interface RequestRow {
  request_id: string; operation: string; requester_id: string; resource_key: string;
  priority: string; state: string; created_at: string; updated_at: string; payload: Record<string, unknown>;
  // 무한 스크롤 커서(슬라이스 39): 단조 증가. 다음 쪽은 ?before=<이 값>.
  commit_order: number;
  // 배치 자식이면 그 배치 id(서버 requests.batch_id). 배치 자식은 단건 컨펌을 못 한다 -- 배치 확인으로만 실행된다
  // (2026-10-07, 409 batch_child_confirm_via_batch). 옵션(?) = 구형 fixture 호환. null/부재 = 단건 요청.
  batch_id?: string | null;
}
// events는 state_transitions가 담지 못하는 것 -- 일어나지 않은 전이 -- 를 담는
// 진단 이벤트다(plan_error/step_error/terminate_failed/terminal_guard_skip/summary_unreadable).
export interface DiagEvent {
  id: number; component: string; severity: string; event_type: string;
  message: string | null; payload: unknown; at: string;
}
export interface RequestDetail extends RequestRow {
  transitions: Transition[];
  // 서버는 표시 상한(100건)보다 하나 더 가져와 잘림 여부를 판별한다 -- 조용한
  // 절단을 피하기 위함(routes_requests.py 참고). 두 필드 다 백엔드 응답이 배열이
  // 아니거나 필드 자체가 없을 수 있어 프론트는 방어적으로 정규화해야 한다.
  events?: DiagEvent[];
  events_truncated?: boolean;
  // 요청 **자신의** 종단 결과(results 행 투영, routes_requests._attach_terminal_result).
  // 잡의 reason_code 와 다른 축이다 -- 잡이 생기기 전에 거부된 요청은 잡이 없다.
  // null = 아직 종단이 아니거나 결과 행이 없음(≠ "사유 없음"). optional 인 이유는
  // events 와 같다: 응답에 필드가 없어도 화면이 죽지 않아야 한다.
  reason_code?: string | null;
  reason_message?: string | null;
  terminal_state?: string | null;
  completed_at?: string | null;
}
// 플래너가 배치 시점에 확정한 워커 배치(planner.py: resolve_fanout 결과 + 후보·
// 신원). 화면이 읽는 건 **수치 몇 개**와 identity 의 보조 그룹 4키(잡 상세 '보조
// 그룹(gid)' 행)뿐이라 그것만 선언한다(identity 의 나머지·candidates·rejections 는
// 화면 계약이 아니다 — 필요해지면 그때 넓힌다).
// node_count 는 전 도구 공통(총 노드), source_count/destination_count 는 양면
// 배치(nsync)에서만 실린다 — resolve_fanout 의 두 분기가 그대로 모양이 된다.
// 전부 옵셔널: 구버전 응답·미기록에서 키가 없을 수 있고, 없음(모름)과 0(정상값)은
// 다른 사실이다(null≠0).
export interface WorkerPool {
  node_count?: number | null;
  process_count?: number | null;
  source_count?: number | null;
  destination_count?: number | null;
  identity?: WorkerPoolIdentity | null;
}
// 계획 시점 보조 그룹 판정(identity.resolve_job_identity 의 SUPP_* -- 서버 어휘 그대로).
// string 도 받는 이유: 서버가 어휘를 넓혀도 화면이 타입 오류로 죽지 않게(모르는 값은 행을 숨긴다).
export type SupplementaryGidsStatus = "applied" | "none" | "over_limit" | "disabled" | "privileged";
export interface WorkerPoolIdentity {
  username?: string; uid?: number; gid?: number; privileged?: boolean;
  // 보조 그룹 기능 배포(2026-10-07) 전에 계획된 잡은 이 키들이 없다(행을 숨긴다) -- 없음(모름)과
  // [](없음 확정)은 다른 사실이다. found 는 null(보지 않았다 -- 기능 꺼짐·root)과 0(없음 확정)이 다르다.
  supplementary_gids?: number[] | null;
  supplementary_gids_status?: SupplementaryGidsStatus | string | null;
  supplementary_gids_excluded?: number[] | null;
  supplementary_gids_found?: number | null;
}
export interface DataJob {
  job_id: string; request_id: string; operation: string; state: string;
  reason_code: string | null; preview_fingerprint: string | null;
  preview_expires_at: string | null; result_summary: unknown;
  // 미리보기(dry-run) summary.json 사본(2026-09-17): 러너 계약대로 정확히
  // {returncode, files, bytes} 이고 개수는 모르면 null. 지문(preview_fingerprint)이
  // 이 객체의 해시라 컨펌 창이 "무엇을 컨펌하는지" 를 보여줄 수 있다. 이전 잡은 null.
  preview_summary?: { returncode?: number | null; files?: number | null; bytes?: number | null } | null;
  transitions: Transition[]; artifact_uri: string | null;
  // phase -> 실행 ref("pod/<name>" 등). 로그를 실제로 조회할 수 있는 phase가 정확히
  // 이 키들이다 — 뷰어는 하드코딩된 "preflight"가 아니라 여기에 맞춰 탭을 만든다.
  phase_refs?: Record<string, string> | null;
  // 실제로 무엇이 돌았는지(dscan/dsync/nsync/drm). null = 아직 계획 전(플래너가
  // 도구를 고르기 전) — 서버는 늘 이 두 키를 보낸다(SELECT 명시 컬럼). 옵션(?)은
  // 기존 fixture 무수정 컴파일용이다.
  tool?: string | null;
  worker_pool?: WorkerPool | null;
  // 서버가 이미 보내는 잡 컬럼(data_jobs._ROW_COLUMNS_SANS_DIAG). scan·rm 은 storage_name, sync 는
  // source_storage·destination_storage 가 차고 나머지는 null -- 잡 상세의 스토리지 종류별 주의문과
  // 컨펌 창의 삭제 경고(options.delete)가 읽는다. 옵션(?)은 기존 fixture 무수정 컴파일용.
  storage_name?: string | null;
  source_storage?: string | null;
  destination_storage?: string | null;
  options?: Record<string, unknown> | null;
  // 서버가 이미 보내는 시각·대기 컬럼(같은 _ROW_COLUMNS_SANS_DIAG). 요청 상세의 단계 시각(stageModel)이 읽는다.
  // exec_submitted_at = 실행 vcjob 제출 시각(실행 단계 시작), sched_wait_seconds = 제출 → 첫 RUNNING 관측(Volcano
  // 대기 근사). null = 모름(구 잡·미도달) -- 0(대기 없음)과 다르다. 옵션(?)은 기존 fixture 무수정 컴파일용.
  created_at?: string;
  updated_at?: string;
  exec_submitted_at?: string | null;
  sched_wait_seconds?: number | null;
  submit_wait_seconds?: number | null;
}
export interface ArtifactEntry { phase: string; name: string; size: number; modified_at: number }
// 목록은 상한(MAX_ENTRIES)이 있어 배열이 아니라 truncated 플래그를 동반한 객체다.
export interface ArtifactList { entries: ArtifactEntry[]; truncated: boolean }
export interface ArtifactFile {
  phase: string; name: string; size: number; truncated: boolean; content: string;
}
export interface JobLogs {
  // ref=null: 제출 자체가 실패해 파드가 없고 박제 사본(submit:<phase> 합성 항목)만 있는 경우.
  phase: string; ref: string | null;
  // 슬라이스 25: live = 지금 파드에서 읽음, archived = 실패 종단 시점의 박제 사본.
  source: "live" | "archived";
  entries: {
    // log 은 string | null 이다 -- ""(빈 로그, launcher 의 정상값)와 null(로그를
    // 얻을 수 없음)은 다른 사실이라 optional 로 뭉개지 않는다.
    pod: string; log: string | null;
    waiting_reason?: string | null;   // live 전용 -- 로그가 없는 "이유"의 별 채널
    truncated?: boolean;              // archived 전용 -- 파드당 16KB 꼬리 잘림
  }[];
}
export interface Storage {
  storage_name: string; mount_path: string; managed_root: string; backend_type: string;
  // 사용 범위(2026-09-30): enabled=0 완전 비활성, user_enabled=0 관리자 전용(사용자에게만
  // 비활성). user_enabled 부재·null(옛 서버·컬럼 이전 행)은 사용자 공개로 읽는다.
  enabled: number; user_enabled?: number | null; status: string; status_detail: string | null;
}
export interface AuditEntry {
  id: number; mutation_class: string; operation: string; target_key: string;
  actor: string; before_state: string | null; after_state: string | null; at: string;
}
export interface Node { node_name: string; reported_at: string; fresh: boolean; report: unknown }
export interface Account {
  username: string; role: string; email: string | null;
  disabled: number; created_at: string;
}
export interface NodeMount {
  storage_name: string; mount_path: string; status: string;
  exists?: boolean; is_mountpoint?: boolean; readable?: boolean; reason?: string | null;
}
export interface NodeTool {
  // 노드 도구는 존재 확인만 한다(2026-08-30): status Ready/Missing(=바이너리
  // 존재/없음, placement 게이트 계약) + path. 버전은 노드에서 확인 불가라
  // (도구는 잡 파드에서 실행) 필드 자체를 뺐다. reason 은 Missing 진단용.
  name: string; status: string; path?: string; reason?: string | null;
}
export interface NodeDisk { storage_name: string; total_bytes: number; used_bytes: number }
export interface NodeReportBody {
  node_name?: string; probed_at?: string;
  mounts?: NodeMount[]; tools?: NodeTool[];
  os?: { disks?: NodeDisk[] } & Record<string, unknown>;
  identities?: unknown[];
}
export interface NodeInfo {
  node_name: string; reported_at: string; fresh: boolean; report: NodeReportBody;
  // 노드 배치 제외(2026-10-02): 관리자가 뺀 노드면 사유·누가·언제, 아니면 null. 옵션(?) = 구형 서버·fixture 호환.
  exclusion?: NodeExclusion | null;
}
export interface NodeExclusion {
  node_name: string; reason: string | null; created_by: string; created_at: string;
}
export interface NodeReport { reported_at: string; report: NodeReportBody }
export interface Batch {
  batch_id: string; operation: string; status: string; max_concurrency: number;
  item_count: number; succeeded_count: number; failed_count: number;
  note: string | null; created_at: string;
  // 마지막 갱신 시각(batches.updated_at — 서버 NOT NULL 이라 실서버 응답엔 늘 있다).
  // 옵션(?)은 기존 fixture 무수정 컴파일용일 뿐. 실행뿐 아니라 메타 수정·항목 편집·
  // 취소 등 **모든 전이**가 이 값을 민다 — 그래서 화면 문구도 "수행"이 아닌 "갱신".
  updated_at?: string | null;
  // 배치 이름(선택). 옵션 필드 — 기존 fixture 무수정 컴파일. null/부재 = 이름 없음.
  name?: string | null;
  // 실행 제어(슬라이스 32). 옵션 필드 — 기존 fixture 무수정 컴파일. null = 정책 기본.
  priority?: string | null; node_count?: number | null;
  // 노드당 프로세스 수 override — node_count 와 같은 계약(null/부재 = 정책 기본).
  procs_per_node?: number | null;
  // 배치 특권 실행. null/부재 = 비특권 현행 — 화면은 있을 때만 표시한다.
  owner_username?: string | null;
  // 생성 시 실은 연산 옵션(서버는 늘 보낸다 — SELECT * + load_json). 옵션(?)은
  // 기존 fixture 무수정 컴파일용일 뿐이다.
  options?: Record<string, unknown>;
  // 확인 회차(2026-10-07): 확인 대기(PreviewReady)가 될 때마다 +1. 「배치 확인」은 대화상자를 연 회차를 실어 보낸다.
  // 서버는 늘 정수로 보낸다(NULL 행은 0 으로 접는다) -- 옵션(?)은 기존 fixture 무수정 컴파일용, 부재는 0회차로 읽는다.
  preview_round?: number;
}
export interface BatchItem {
  seq: number; payload: Record<string, unknown>; status: string;
  request_id: string | null; reason_code: string | null;
  // 자식 요청 조인 필드(결과 항목 상세화). 옵션(?) = 구형 서버 호환.
  // request_state: 자식 요청의 현재 상태(null = 미 materialize).
  // files_count: null = 모름(잡 없음/미기록) — 0(파일 없음)과 다르다(null≠0).
  // completed_at: results.completed_at — 종단만 실값, 비종단 null.
  request_state?: string | null;
  files_count?: number | null;
  completed_at?: string | null;
  // 미리보기 조인(2026-10-07, 배치 확인 대화상자): 자식 잡(최신 1개)의 상태·미리보기 요약·만료. null = 모름(잡 없음).
  // job_state === "ConfirmPending" = 미리보기를 마치고 배치 확인을 기다리는 항목. preview_summary.files 는 단건
  // ConfirmDialog 의 "복사 대상" 과 같은 값이다(같은 노드 dsync 는 dry-run 이 훑은 소스 항목 수, 노드 간 nsync 는 계획된
  // 변경 수라 이미 맞는 항목은 빠진다). null = 모름(dsync dryrun 의 bytes 는 null -- 0 으로 뭉개지 않는다).
  job_state?: string | null;
  preview_summary?: { files?: number | null; bytes?: number | null; returncode?: number | null } | null;
  preview_expires_at?: string | null;
}
export interface BatchDetail extends Batch { items: BatchItem[] }
// 요청 단위 scan 리포트 통계(항목별 데이터 온도 — 배치 합산은 제거됐다). 서버가
// 모양 투영(숫자·구간 라벨만)을 거친 값만 준다 — ScanPathStats 에서 covered_by
// 를 뺀 같은 계약(단일 리포트 서빙, 합산 카운트 없음).
export interface RequestScanStats {
  // 리포트에 생성 시각이 없거나 수치가 아니면 null(경로가 섞여 드는 걸 막는다).
  generated_at_epoch: number | null;
  summary: Record<string, number>;
  file_size_histogram: HistogramBucket[];
  time_histograms: Record<string, HistogramBucket[]>;
  // 실 사용량(스캔 트리 파일 크기 합, 2026-08-23). null = 모름(구형·오염
  // 리포트) — 0(빈 트리)과 다르다(null≠0). 옵션(?) = 구형 서버 호환.
  total_bytes?: number | null;
  // null = 구형 리포트(총계 미기록) — 0(파손 없음)과 다르다(null≠0).
  broken_paths_total: number | null;
  broken_paths_limit: number | null;
}
// 사용량 분석(2026-08-23): 전 요청자 통합 scan 이력. 서버 routes_usage.py 계약.
export interface ScanTargetRow {
  storage_name: string; target: string; scan_count: number;
  last_scan_at: string | null;
  // 2026-10-02: 최초 성공 스캔 시각 + 최신 성공 scan 1건(목록 컬럼 실 사용량·파일 수·hot 비율의 원천).
  // 옵션(?) = 구형 서버·기존 fixture 호환. latest null = 성공 scan 잡을 못 찾음(경합).
  first_scan_at?: string | null;
  latest?: UsagePoint | null;
}
// 사용량 분석 전체 내보내기(GET /api/admin/usage/export). previous = 최신 직전 성공 scan(증감 계산용).
export interface UsageExportRow extends ScanTargetRow {
  latest: UsagePoint | null;
  // 직전 성공 scan -- 증감용 값만(서버가 행 크기를 줄인다). total_bytes null = 모름.
  previous: { job_id: string; request_id: string; finished_at: string | null; total_bytes: number | null } | null;
}
export interface UsageExport {
  generated_at: string; count: number; truncated: boolean; rows: UsageExportRow[];
}
export interface UsagePoint {
  job_id: string; request_id: string; finished_at: string | null;
  generated_at_epoch: number | null;
  // 실 사용량. null = 모름(구형·오염 리포트) ≠ 0(빈 트리) — 차트는 이 포인트를
  // 0 으로 그리지 않고 제외 + 개수 고지한다.
  total_bytes: number | null;
  summary: Record<string, number>;
  time_histograms: Record<string, HistogramBucket[]>;
  requester: string | null;
  // 2026-10-02(리포트 요약 캐시와 함께): 옵션(?) = 구형 서버 호환. report_readable=false 면 리포트를 못 읽어
  // summary·히스토그램이 비고 total_bytes 는 DB 값(러너 bytes_count)으로 대신한다.
  file_size_histogram?: HistogramBucket[];
  broken_paths_total?: number | null;
  broken_paths_limit?: number | null;
  report_readable?: boolean;
}
export interface UsageHistory {
  storage_name: string; target: string; points: UsagePoint[];
  skipped_unreadable: number;
  // 이력은 최신 window_limit 건 창이다. window_full 이면 그 너머가 있을 수 있어
  // 화면이 "전체 이력"인 척하지 않도록 고지한다(타깃 목록의 전수 scan_count 와
  // 한 화면에서 모순되지 않게). 옵션(?) = 구형 서버 호환.
  window_limit?: number; window_full?: boolean;
}
export interface Policy {
  tool: string;
  max_nodes: number;
  procs_per_node: number;
  queue: string;
  default_priority: string;
  max_priority: string;
  preview_timeout_seconds: number | null;
  execution_timeout_seconds: number;
  enabled: number;
  updated_at: string;
  updated_by: string;
}

// 사용자 sync 허용 스토리지 쌍(2026-09-30, 서버 repositories/sync_pairs.py). 방향이 있다
// (소스 → 목적지; A → B 와 B → A 는 별개, A → A 도 하나의 쌍). 기본 전부 불가.
export interface SyncPair {
  source_storage: string; destination_storage: string;
  created_at?: string; created_by?: string;
}
// /api/user/sync-pairs: restricted=false 면 제한 없음(관리자 -- 화면은 거르지 않는다).
// 사용자에겐 자기가 고를 수 있는 스토리지(활성 + 사용자 공개)끼리의 쌍만 온다.
export interface UserSyncPairs {
  restricted: boolean;
  pairs: { source_storage: string; destination_storage: string }[];
}

export interface DenyEntry {
  subject_type: string;
  subject: string;
  reason: string | null;
}

// managed_root(관리 디렉토리)는 **관리자 응답에만** 실린다(routes_storages
// list_user_storages) — 비관리자·구형 서버에선 키 자체가 없다. 그래서 옵셔널이고,
// 없으면 화면은 절대경로 줄을 아예 그리지 않는다(거짓 경로 금지).
export interface UserStorage {
  storage_name: string; backend_type: string; status: string;
  managed_root?: string;
  // 관리자 전용(2026-09-30) -- 비관리자 응답엔 관리자 전용이 아예 없어 항상 false.
  admin_only?: boolean;
}

export interface ControlState {
  maintenance: number;
  drain: number;
  reason: string | null;
  build_node_name: string | null;
  // 빌드 노드에서 DMS 저장소가 있는 절대 경로(로컬 소스 빌드). null = 미설정.
  build_source_path: string | null;
  // 빌드 노드 프록시(2026-09-08): 빌드·프리플라이트 파드 env 로 실린다. null = 없음.
  build_http_proxy?: string | null;
  build_https_proxy?: string | null;
  build_no_proxy?: string | null;
  // 빌드 파드 호스트 네트워크 스위치(2026-09-09). loopback 프록시는 서버가 자동으로
  // 호스트 네트워크를 켜므로, 이 값은 그 밖의 경우용이다. 1 = 켬.
  build_host_network?: number | null;
  // 사내 프록시 CA(2026-09-09): 빌드 노드 위 PEM 파일 절대 경로. null = 없음.
  build_proxy_ca_path?: string | null;
  changed_by: string | null;
  changed_at: string | null;
}

// 컨트롤 상태의 프록시 제외(no_proxy) 힌트(2026-09-09). nodes_known=false 는 노드
// 조회 실패(권한·클러스터) -- 노드 IP 만 빠지고 나머지 힌트는 유효하다.
export interface ProxyHints {
  registry: string; registry_host: string;
  nodes: { name: string | null; ip: string }[];
  nodes_known: boolean;
  suggested_no_proxy: string[];
  auto_added: string[];
}

export interface ArtifactBaseNodeCheck {
  node_name: string; reported_at: string; fresh: boolean;
  // pending = 이 노드가 아직 현재 base 를 프로브하지 않았다("확인 대기 중") --
  // 실패와 다른 상태다(설계 §4). pending 이면 exists/writable 은 null 이다.
  pending: boolean; exists: boolean | null; writable: boolean | null;
}
export interface ArtifactBaseControllerCheck {
  pending: boolean; ok: boolean | null; reason: string | null;
  checked_at: string | null;
}
export interface ArtifactBaseInfo {
  effective: string; source: "db" | "env"; db_value: string | null; env_value: string;
  locked_by_jobs: number;
  checks: {
    api: { ok: boolean; reason: string | null };
    controller: ArtifactBaseControllerCheck;
    nodes: ArtifactBaseNodeCheck[];
  };
}

export interface Build {
  // source_path: 로컬 소스 빌드(슬라이스 33)의 소스 절대 경로. 옛 git 시절 행은
  // 저장소 URL 이 그대로 실린다(git_ref === "local" 로 판별). commit_sha 는
  // "-dirty" 접미가 붙을 수 있다(미커밋 변경이 있는 트리에서 빌드).
  build_id: string; source_path: string; git_ref: string; commit_sha: string | null;
  images: string[]; node_name: string; state: string; reason_code: string | null;
  tag: string; created_at: string; finished_at: string | null;
}

export interface ScanPath {
  id: number; username?: string; storage_name: string; path: string; created_at: string;
}
export interface HistogramBucket {
  // 구간 라벨은 서버가 모양 검사를 통과한 것만 넘긴다 — 경로처럼 생긴 라벨은 빠진다.
  bucket?: string; count?: number; bytes?: number;
  lower_inclusive?: number; upper_inclusive?: number;
  min_age_days?: number; max_age_days?: number;
}
export interface ScanPathStats {
  covered_by: { target: string; exact: boolean };
  // 리포트에 생성 시각이 없거나 수치가 아니면 null이다(경로가 섞여 들어오는 걸 막는다).
  generated_at_epoch: number | null;
  summary: Record<string, number>;
  file_size_histogram: HistogramBucket[];
  time_histograms: Record<string, HistogramBucket[]>;
  // 실 사용량(스캔 트리 파일 크기 합, 2026-08-23). RequestScanStats 와 같은
  // 계약: null = 모름 ≠ 0(빈 트리), 옵션(?) = 구형 서버 호환.
  total_bytes?: number | null;
  // 신 dscan(1b93d54)의 파손 경로 정확 총계·보관 상한. null = 구형 리포트의
  // "기록 없음"(null≠0 — 0은 파손 없음의 정상값), 옵션(?) = 구형 서버 호환.
  broken_paths_total?: number | null;
  broken_paths_limit?: number | null;
}

// 릴리스(롤아웃). 상태는 Pending → Applying → Applied/Failed 이고 잡/빌드의 종단
// 집합(Succeeded/Failed/...)과 겹치지 않는다 -- isTerminal을 그대로 쓰면 Applied가
// 비종단으로 읽힌다(useReleases.ts의 RELEASE_ACTIVE_STATES 주석 참고).
export interface Release {
  id: number; component: string; image: string; tag: string;
  digest: string | null; state: string; reason_code: string | null;
  // seq(배치 안 적용 순서)는 서버가 응답에서 뺀다 -- 내부 정렬용 컬럼이고, 화면은
  // 이미 서버가 ROLLOUT_ORDER로 정렬해 준 목록을 그대로 그린다.
  actor: string; applied_at: string;
}
export interface ReleaseTarget {
  component: string; kind: string; workload: string; container: string;
  repository: string;
  // 워크로드 읽기(observe)가 실패하면 서버가 null을 준다 -- 화면 전체를 죽이지
  // 않는 강등이므로 프론트도 "—"로 살려 보여준다.
  current_image: string | null;
  tags: string[];
}
// registry_ok=false면 tags가 전부 비어 있고 서버의 태그 존재 검증도 꺼진 상태다.
export interface ReleaseTargets { targets: ReleaseTarget[]; registry_ok: boolean }
export interface Releases { current: Record<string, Release>; history: Release[] }

export interface NodeMetricDisk { storage_name: string; used_pct: number | null }
export interface NodeMetricPoint {
  at: string; load1: number | null; load5: number | null; load15: number | null;
  mem_used_pct: number | null; net_rx_bps: number | null; net_tx_bps: number | null;
  disks: NodeMetricDisk[];
}
export interface NodeMetricSeries {
  node_name: string; reported_at: string; fresh: boolean; points: NodeMetricPoint[];
}
export interface NodeMetrics {
  window_hours: number; start: string; end: string; nodes: NodeMetricSeries[];
}
export interface StateCount { state: string; count: number }
export interface BreakdownRow { count: number; succeeded: number; failed: number }
// 숫자 요약(슬라이스 31): 평균/중앙값(p50)/p95. p50·p95 는 nearest-rank 실측값
// (백엔드 summarize_seconds -- 보간 없음). 응답에서 null = 표본 없음(0 아님).
export interface SecondsSummary {
  mean_seconds: number; p50_seconds: number; p95_seconds: number;
}
export interface JobMetrics {
  window_hours: number; bucket: "hour" | "day";
  by_state: StateCount[];
  by_tool: ({ tool: string | null } & BreakdownRow)[];
  by_storage: ({ storage: string | null } & BreakdownRow)[];
  by_requester: ({ requester_id: string } & BreakdownRow)[];
  failure_reasons: { reason_code: string; count: number }[];
  // 계획 거부(results 집계): 계획 단계 거부는 data_jobs 가 생기기 전의 종단이라
  // 위의 어떤 data_jobs 파생 필드에도 안 잡힌다 -- 별도 필드가 유일한 원천이다.
  // failure_reasons(잡 실패)와 절대 섞지 않는다(라벨 거짓말 방지). 0 = 거부 없음
  // (정상값 -- null 아님).
  plan_rejected: number;
  plan_rejection_reasons: { reason_code: string; count: number }[];
  throughput: { bucket: string; count: number }[];
  // duration = created_at -> updated_at 의 **전체 수명**(제출·확인(사람)·스케줄
  // 대기 + 실행 전부 합산)이다 -- 화면 라벨은 「전체 수명 분포」(슬라이스 31
  // 라벨 정직화). 필드명은 소비자 호환으로 유지한다.
  duration_histogram: { bucket: string; count: number }[];
  duration_summary: SecondsSummary | null;
  // 제출 대기(슬라이스 17): created_at -> 첫 비-Pending 전이. Volcano 큐 대기가
  // 아니라 DMS 내부 픽업 지연이다(설계 §2.4 -- 그래서 이름이 "제출 대기"다).
  // excluded = NULL(백필 불가분·아직 Pending)로 집계에서 빠진 건수.
  submit_wait_histogram: { bucket: string; count: number }[];
  submit_wait_counted: number;
  submit_wait_excluded: number;
  // 스케줄 대기(슬라이스 20): execution vcjob 제출(exec_submitted_at) -> 스테퍼가
  // 처음 RUNNING 을 관측한 틱. 제출 대기(DMS 픽업 지연)와 다른 것을 재며,
  // Volcano 큐 대기의 **근사**다(스테퍼 틱 5s + vcjob status 갱신 지연 포함 --
  // 설계 §2.2). excluded = NULL(과거 잡: 백필 없음 §2.5, Running 미도달/한 틱
  // 완료 §2.6, 스텁 백엔드 §4) 제외 건수 -- 0 과 절대 같지 않다.
  sched_wait_histogram: { bucket: string; count: number }[];
  sched_wait_counted: number;
  sched_wait_excluded: number;
  // 실행시간(슬라이스 31, 방법 A: 스키마 무변경 파생 계산): epoch(updated_at)
  // - epoch(exec_submitted_at) - sched_wait_seconds = 첫 RUNNING 관측 -> 종단의
  // **근사**(스테퍼 틱 오차 -- sched_wait 와 같은 규약). 버킷은 duration 과 같은
  // 축(나란히 비교). excluded = 종단인데 재료 NULL 인 잡(슬라이스 20 이전 잡·
  // 한 틱 완료·실행 미도달) -- 0 과 절대 같지 않다.
  exec_runtime_histogram: { bucket: string; count: number }[];
  exec_runtime_counted: number;
  exec_runtime_excluded: number;
  exec_runtime_summary: SecondsSummary | null;
  files_total: number | null; bytes_total: number | null;
}
export interface InfraComponent {
  component: string; kind: string; workload: string;
  image: string | null; ready: number | null; desired: number | null;
  verdict: "applied" | "progressing" | "failed" | null; detail: string | null;
  // 이미지에 동봉된 "이 이미지를 만든 소스 트리"의 매니페스트 image 값.
  // null = 동봉 없음/파싱 실패 -- 비교 자체를 하지 않는다(무배지).
  manifest_image: string | null;
}
export interface InfraMetrics {
  components: InfraComponent[];
  // live = 유효값(DB 오버라이드 → env). source 가 "db" 면 릴리스의 job-image 가
  // 설정한 값이라 kubectl apply 로 되돌아가지 않는다(문구 분기 근거).
  job_image: { live: string | null; manifest: string | null; source?: "db" | "env" };
}

// 레지스트리 이미지 관리(슬라이스 34). in_use = 지금 배포돼 도는(또는 매니페스트가
// 가리키는) 태그라 삭제가 막힌다. reachable=false 는 "조회 실패"로, tags=[](태그
// 0개)와 다르다(null≠0).
export interface RegistryTag { tag: string; in_use: boolean; }
export interface RegistryRepo {
  repository: string; reachable: boolean; tags: RegistryTag[];
}
export interface RegistryImages { registry: string; repositories: RegistryRepo[]; }

// 큐 현황(슬라이스 17). null 은 서버가 "알 수 없음"(403/CRD 부재)을 명시적으로
// 보낸 것이다 -- []("비었음")와 절대 같지 않다(설계 §4). 여기서 ?? [] 로 접으면
// 권한 누락이 "큐가 한가함"으로 렌더된다.
// 필드는 queue_reader.py 의 read_queue/read_podgroups 가 만드는 dict 그대로다:
// name 은 리더가 "" 로 채워 늘 문자열이고, phase/min_member/created_at 은 k8s
// 오브젝트에서 그대로 온 값이라 결측이면 null. wait_seconds 는 라우트가 계산해
// 넣는다(시각이 깨진 항목만 null).
export interface QueuePodgroup {
  name: string; phase: string | null; min_member: number | null;
  created_at: string | null; wait_seconds: number | null;
}
export interface QueueMetrics {
  queue: { name: string; state: string | null } | null;
  podgroups: QueuePodgroup[] | null;
}

// 포탈 메일 설정(2026-10-01, GET /api/admin/mail-settings). 적용값 = 포탈 > env > 기본값(칸별).
// 릴레이 토큰 값은 절대 오지 않는다 -- token 은 상태만.
export type MailBackend = "stub" | "knox_relay";
export interface MailSettingsFields {
  backend: string | null; relay_scheme: string | null; relay_host: string | null;
  relay_port: number | null; timeout_seconds: number | null; service_name: string | null;
}
export interface MailSettings {
  backend: string; relay_scheme: string; relay_host: string; relay_port: number;
  relay_url: string; timeout_seconds: number; service_name: string;
  sources: Record<string, "portal" | "env">;
  portal: MailSettingsFields;           // 포탈에 저장된 값(null = 그 칸은 env·기본값)
  env: { backend: string; relay_scheme: string; relay_host: string | null; relay_port: number;
         timeout_seconds: number; service_name: string;
         relay_url: string };          // env DMS_MAIL_RELAY_URL 원문(포탈 주소 칸이 다 비면 이것을 그대로 쓴다)
  token: { configured: boolean; source: "portal" | "env" | null; unreadable: boolean;
           env_configured: boolean;
           env_unbound: boolean };      // env 키가 있지만 포탈이 주소를 정해 env 키를 쓰지 않는 중
  // 릴레이 주소를 포탈 칸으로 조합했나("portal") env DMS_MAIL_RELAY_URL 그대로인가("env").
  endpoint_source: "portal" | "env";
  email_domain: string;
  backends: string[];
  updated_at: string | null; updated_by: string | null;
}
// 연결 확인·테스트 메일 결과 -- 실패도 200 + ok:false + reason(mailer.MailerError 사유).
export interface MailCheckResult {
  ok: boolean; reason?: string; detail?: string; retry_after?: number | null;
  relay_url?: string; to?: string;
}
// 로그인 전 화면용(GET /api/auth/mail-info) -- 비밀 없음.
export interface MailInfo { email_domain: string; delivery: string }
// 포탈 서브네임(2026-10-02, GET /api/portal-info -- 공개). null = 서브네임 없음.
export interface PortalInfo { subtitle: string | null }
