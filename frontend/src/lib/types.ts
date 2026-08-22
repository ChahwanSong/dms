export type Role = "user" | "admin";
export interface Me { actor: string; role: Role }
export interface Transition {
  from_state: string | null; to_state: string;
  reason_code?: string | null; actor?: string; at: string;
}
export interface RequestRow {
  request_id: string; operation: string; requester_id: string; resource_key: string;
  priority: string; state: string; created_at: string; updated_at: string; payload: Record<string, unknown>;
  // 무한 스크롤 커서(슬라이스 39): 단조 증가. 다음 쪽은 ?before=<이 값>.
  commit_order: number;
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
// 신원). 화면이 읽는 건 **수치 몇 개**뿐이라 그것만 선언한다(identity·candidates·
// rejections 는 화면 계약이 아니다 — 필요해지면 그때 넓힌다).
// node_count 는 전 도구 공통(총 노드), source_count/destination_count 는 양면
// 배치(nsync)에서만 실린다 — resolve_fanout 의 두 분기가 그대로 모양이 된다.
// 전부 옵셔널: 구버전 응답·미기록에서 키가 없을 수 있고, 없음(모름)과 0(정상값)은
// 다른 사실이다(null≠0).
export interface WorkerPool {
  node_count?: number | null;
  process_count?: number | null;
  source_count?: number | null;
  destination_count?: number | null;
}
export interface DataJob {
  job_id: string; request_id: string; operation: string; state: string;
  reason_code: string | null; preview_fingerprint: string | null;
  preview_expires_at: string | null; result_summary: unknown;
  transitions: Transition[]; artifact_uri: string | null;
  // phase -> 실행 ref("pod/<name>" 등). 로그를 실제로 조회할 수 있는 phase가 정확히
  // 이 키들이다 — 뷰어는 하드코딩된 "preflight"가 아니라 여기에 맞춰 탭을 만든다.
  phase_refs?: Record<string, string> | null;
  // 실제로 무엇이 돌았는지(dscan/dsync/nsync/drm). null = 아직 계획 전(플래너가
  // 도구를 고르기 전) — 서버는 늘 이 두 키를 보낸다(SELECT 명시 컬럼). 옵션(?)은
  // 기존 fixture 무수정 컴파일용이다.
  tool?: string | null;
  worker_pool?: WorkerPool | null;
}
export interface ArtifactEntry { phase: string; name: string; size: number; modified_at: number }
// 목록은 상한(MAX_ENTRIES)이 있어 배열이 아니라 truncated 플래그를 동반한 객체다.
export interface ArtifactList { entries: ArtifactEntry[]; truncated: boolean }
export interface ArtifactFile {
  phase: string; name: string; size: number; truncated: boolean; content: string;
}
export interface JobLogs {
  phase: string; ref: string;
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
  enabled: number; status: string; status_detail: string | null;
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
  name: string; status: string; path?: string; version?: string; reason?: string | null;
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
  // null = 구형 리포트(총계 미기록) — 0(파손 없음)과 다르다(null≠0).
  broken_paths_total: number | null;
  broken_paths_limit: number | null;
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
}

export interface ControlState {
  maintenance: number;
  drain: number;
  reason: string | null;
  build_node_name: string | null;
  // 빌드 노드에서 DMS 저장소가 있는 절대 경로(로컬 소스 빌드). null = 미설정.
  build_source_path: string | null;
  changed_by: string | null;
  changed_at: string | null;
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
