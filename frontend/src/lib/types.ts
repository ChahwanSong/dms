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
  // 무한 스크롤 커서(슬라이스 39): 현존 행 기준 단조 증가(최신 요청을 삭제하면 다음 제출이 그 번호를 다시 받는다).
  // 다음 쪽은 ?before=<이 값>.
  commit_order: number;
  // 배치 자식이면 그 배치 id(서버 requests.batch_id). 배치 자식은 단건 컨펌을 못 한다 -- 배치 확인으로만 실행된다
  // (2026-10-07, 409 batch_child_confirm_via_batch). 옵션(?) = 구형 fixture 호환. null/부재 = 단건 요청.
  batch_id?: string | null;
  // 요청을 만든 인증 방식(requests.auth_method): "session" | "token" | null(옛 행). token 은 root 자격이 없다
  // (identity.privilege_eligible). 요청 상세 「요청 내용」이 배치 자식의 root 여부를 설명할 때만 읽는다.
  auth_method?: string | null;
  // 사용량 분석의 지점(성공 scan **잡**)을 가진 요청인가(목록 API, 2026-10-09). 작업 삭제 확인 창의 사용량 경고가 쓴다 --
  // 요청 상태로는 판정할 수 없다(취소 경합으로 「요청 Cancelled · scan 잡 Succeeded」가 생기고 사용량 분석은 잡 상태를
  // 본다). 옵션(?) = 상세 응답·구형 fixture 에는 없다 -- 없으면 요청 상태로 근사한다(isSucceededScan).
  has_succeeded_scan?: boolean;
  // 배치 요약(목록 API, 2026-10-10) -- **관리자 응답의 배치 자식 행에만** 실린다(BatchesRepository.list_summaries).
  // 작업 목록의 「배치」 열과 배치 단위 삭제(묶음 선택·확인 창의 수 = 삭제 CAS 의 expected_request_count)가 쓴다.
  // 전부 옵션(?) = 비관리자·단건 행·구형 응답·기존 fixture 에는 없다. batch_exists 가 boolean 이 아니면 「요약 없음」 --
  // 묶음 선택 불가(fail-closed, JobsList batchBlock). 수는 서버가 센다(목록 한 쪽엔 배치의 자식이 일부만 있다).
  batch_exists?: boolean;                 // false = 배치 기록만 지워진 묶음(dangling batch_id)
  batch_name?: string | null;             // null = 이름 없음(또는 배치 행 없음)
  batch_status?: string | null;           // 배치 행이 없으면 null
  batch_request_count?: number;           // 재실행 이력을 포함한 전체 자식 수(capped 면 상한 + 1 에서 멈춘 값)
  // 서버가 세기를 멈췄다(2026-10-11 -- 폴링마다 큰 배치 전부를 세지 않는다). 2026-11-19 부터 목록의 세기 상한은 min(5000, 삭제
  // 상한)이라 뜻이 「목록이 센 최대(limits.list_count_cap)를 넘음 -- 정확한 수 모름」이다(상한 초과 판정은 count > limit 하나 --
  // batchGroups.batchBlock). 아래 두 수는 null(세지 않았다 -- 모름). 정확한 수는 확인 창의 미리보기가 센다.
  batch_request_count_capped?: boolean;
  // 배치 단위 삭제의 자식·항목 상한(2026-11-19 -- 자식 상한은 설정 DMS_BATCH_DELETE_MAX_CHILDREN, 기본 50000·항목 10000). 포탈은
  // 상수를 두지 않고 이 값으로 판정한다(batchGroups.batchBlock -- 숫자가 아니면 「정보 없음」, fail-closed).
  batch_request_count_limit?: number;
  batch_item_count_limit?: number;
  batch_live_request_count?: number | null;     // 비종단 자식 수 -- 0 은 정상값(모름이 아니다). null = capped
  batch_succeeded_scan_count?: number | null;   // 성공 scan 잡을 가진 자식 수(비 scan 배치는 0 -- 확정값). null = capped
  // 배치 항목 수(2026-10-11) -- 배치 단위 삭제는 항목 수에도 상한(batch_item_count_limit)이 있어 넘으면 too_large. null = 모름
  // (배치 행이 없는 묶음·변조된 값) -- 모르면 막지 않고 서버가 판정한다(자식 수 상한이 주 게이트다).
  batch_item_count?: number | null;
  // 그 배치 자식의 최대 commit_order(2026-11-19 -- 삭제 CAS 의 둘째 열쇠, 미리보기가 정본). capped·자식 0 이면 null.
  batch_max_commit_order?: number | null;
  // 백그라운드 배치 단위 삭제 진행(2026-11-19 -- 묘비). null = 아님, 부재 = 옛 서버.
  batch_deleting?: BatchDeletingSummary | null;
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
// 작업(요청) 선택 삭제(2026-10-08) -- POST /api/admin/requests:delete 의 **부분 성공** 본문(items:rerun 관례).
// 전부 skipped 여도 200 이다(전체 실패 403·422·503 은 ApiError). job_ids 는 지운 요청의 잡 id -- 화면이 잡 키
// 캐시(아티팩트·로그)를 지우는 데 쓴다. purge_pending = 응답 시점의 정리 대기 행 수(결과 파일·파드 정리는 컨트롤러가
// 비동기로 한다). null = 세지 못함(삭제 도중 DB 장애 -- 지운·제외 목록은 그래도 온다). 화면은 이 값 대신 정리 현황
// (GET /api/admin/request-purges)을 본다.
//
// 배치 단위(2026-10-10): 본문에 batches 를 실은 호출이면 deleted_batches·skipped_batches 가 온다(빈 목록이어도). 옵션(?) =
// batches 를 싣지 않은 옛 호출·가로채 만든 응답(e2e 06)에는 없다. 배치 하나 = 트랜잭션 하나(전부 아니면 전무) --
// request_ids·job_ids 는 그 배치에서 지운 자식 요청·잡 전부(캐시 제거용), dangling = 배치 행 없이 자식만 남은 묶음이었다.
// skipped_batches.request_id = 문제가 된 자식(자식 게이트 사유일 때), 배치 수준 사유면 null.
// 백그라운드 배치 단위 삭제의 진행(목록 요약의 batch_deleting -- 2026-11-19). expected = 묘비가 센 수, deleted = 지운 수.
export interface BatchDeletingSummary {
  expected: number; deleted: number; requested_at: string; requested_by: string;
  last_error: string | null; blocked_request_id: string | null;
}
// 배치 상세·정리 현황의 진행 행(서버 batch_deletions -- 2026-11-19).
export interface BatchDeletionRow {
  batch_id: string; name?: string | null; dangling: boolean; requested_by: string; requested_at: string;
  expected_request_count: number; deleted_request_count: number; last_error: string | null;
  blocked_request_id: string | null; updated_at: string;
}
// 거절 줄의 상한 숫자(2026-11-19 -- 상한 사유일 때만 실린다: limit·count|count_at_least).
export interface SkippedBatch {
  batch_id: string; reason: string; request_id: string | null;
  limit?: number | null; count?: number | null; count_at_least?: number | null;
}
export interface DeleteRequestsResult {
  deleted: { request_id: string; job_ids: string[] }[];
  skipped: { request_id: string; reason: string }[];
  purge_pending: number | null;
  deleted_batches?: { batch_id: string; request_ids: string[]; job_ids: string[]; dangling: boolean }[];
  // 백그라운드로 지우기 시작했거나 이미 지우는 중(2026-11-19 -- 자식 id 는 싣지 않는다). 부재 = 옛 서버.
  deleting_batches?: { batch_id: string; request_count: number; already: boolean }[];
  skipped_batches?: SkippedBatch[];
}
// POST /api/admin/requests:delete-preview(2026-11-19) -- 확인 창이 열릴 때 1회.
export type DeleteVerdict = "sync" | "async" | "blocked" | "deleting" | "gone";
export interface DeletePreviewBatch {
  batch_id: string; exists: boolean; name: string | null; status: string | null; operation: string | null;
  request_count: number; request_count_capped: boolean; max_commit_order: number | null;
  item_count: number | null; item_count_capped: boolean; verdict: DeleteVerdict; reason: string | null;
  request_id: string | null; limit: number | null; count: number | null; count_at_least: number | null;
  deleting: BatchDeletionRow | null;
  live_request_count?: number | null; succeeded_scan_count?: number | null;
}
export interface DeletePreview { limits: unknown; batches: DeletePreviewBatch[] }
// GET /api/admin/request-purges -- 정리 대기 현황(유일한 운영 표면). stalled = last_error 가 있는 행 수(실패 백오프
// 중이거나 오래 대기). items 는 오래된 순 최대 50건. last_error null = 지연 사유 없음(정상 대기).
export interface PurgeStatusItem {
  request_id: string; stage: string; attempts: number; last_error: string | null;
  requested_at: string; requested_by: string; next_attempt_at: string;
}
export interface PurgeStatus {
  pending: number; stalled: number; oldest_requested_at: string | null; items: PurgeStatusItem[];
  // 2026-11-19: 삭제 상한(서버 delete_limits -- lib/deleteLimits.parseLimits 로 읽는다)·진행 중인 백그라운드 배치 삭제. 부재 = 옛 서버.
  limits?: unknown;
  batch_deletions?: BatchDeletionRow[];
}
// 플래너가 배치 시점에 확정한 워커 배치(planner.py: resolve_fanout 결과 + 후보·
// 신원). 화면이 읽는 건 수치 몇 개와 identity(요청 상세 「요청 내용」의 실행 권한·신원·
// 보조 그룹)다. candidates·priority_class 는 요청 내용 카드가 읽는다(requestSpec 이 정규화 --
// 모양을 믿지 않아 unknown). rejections·queue 는 표시하지 않는다(BACKLOG).
// node_count 는 전 도구 공통(총 노드), source_count/destination_count 는 양면
// 배치(nsync)에서만 실린다 — resolve_fanout 의 두 분기가 그대로 모양이 된다.
// 전부 옵셔널: 구버전 응답·미기록에서 키가 없을 수 있고, 없음(모름)과 0(정상값)은
// 다른 사실이다(null≠0).
export interface WorkerPool {
  node_count?: number | null;
  process_count?: number | null;
  source_count?: number | null;
  destination_count?: number | null;
  // 노드 CPU 상한(2026-10-16, placement.resolve_fanout·stepper 재클램프): 실제 노드당 프로세스 / 정책·요청 min(클램프 전) /
  // 고른 노드별 상한(코어, null = 모름) / 상한을 전부 알았나. 이 기능 전에 계획된 잡은 키가 없다(모름 -- 표시 생략).
  procs_per_node?: number | null;
  procs_per_node_requested?: number | null;
  node_cpu_caps?: Record<string, number | null> | null;
  cpu_cap_known?: boolean | null;
  identity?: WorkerPoolIdentity | null;
  priority_class?: unknown;
  candidates?: unknown;
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
// summary.json v2(2026-10-27 d192, 러너 _build_summary · parsers.parse_tool_detail): 핵심 3키(returncode·files·bytes)는
// 그대로이고 나머지는 전부 선택이다 -- 없음 = 모름 또는 그 도구에 해당 없음(0 으로 뭉개지 않는다, null≠0). 옛 잡은 3키만.
// files 의 뜻(도구·단계별): nsync 미리보기 = 변경 예정 합, nsync 실행 = 실제 반영 합(done), dsync 미리보기 = 소스 전체 walk 수,
// dsync 실행 = 마지막 블록의 Items − 실패한 복사(mknod 실패로 이미 빠진 것은 빼지 않는다 -- 비정상 종료면 null), drm 미리보기 =
// 지울 대상 walk 수, drm 실행 = 실제로 지운 수. 화면은 lib/toolSummary 로 읽는다.
export interface ToolActionCounts {
  copy?: number; mkdir?: number; symlink_update?: number; meta_update?: number; remove?: number;
  skipped_dst_only?: number;
}
export interface ToolFailureSample { op: string; path: string; errno: number | null }
export interface ToolSummary {
  returncode?: number | null; files?: number | null; bytes?: number | null;
  detail_version?: number; tool?: string; dryrun?: boolean; quiet?: boolean;
  planned?: ToolActionCounts; done?: ToolActionCounts;
  copied_bytes?: number; planned_bytes?: number; source_items?: number;
  copied_items?: number; copy_failed?: number; removed?: number; remove_failed?: number; target_items?: number;
  // dsync: mkdir()·symlink()·mknod() 를 못 한 항목 수(마지막 Items 에 이미 빠져 있다 -- mknod 실패는 copy_failed 와 겹친다).
  create_failed?: number;
  type_changes?: { to_dir?: number; from_dir?: number };
  type_change_needs_delete?: boolean; type_change_refused?: number; skipped_special?: number;
  failures?: { total?: number; by_op?: Record<string, number> };
  failure_samples?: ToolFailureSample[];
  not_attempted?: number; stopped_early?: boolean;
  stop_cause?: "max_errors" | "destination_full" | "destination_read_only" | string;
  // scan_errors = nsync 의 스캔 오류 수(소스·목적지 합), source_scan_errors = 그중 소스 쪽(nsync 가 삭제를 끌 때만 찍는다),
  // scan_incomplete = dsync 의 소스 walk 오류 표식(수를 찍지 않는다 -- 수로 읽지 않는다).
  scan_errors?: number; source_scan_errors?: number; scan_incomplete?: boolean; delete_disabled?: boolean;
  metadata_ignored?: number; metadata_failed?: number; noatime_fallbacks?: number;
  tool_errors?: number; aborted?: boolean; reason?: string;
  // 실행 성공인데 summary.json 을 못 읽었을 때 stepper 가 대신 싣는 표식(개수 모름).
  summary_unavailable?: boolean;
  // drm 전용(2026-10-28 d194): 러너가 drm 직전 lstat 으로 본 삭제 대상 자신의 종류 -- 미리보기·실행 summary 둘 다. link = 대상
  // 자신이 심볼릭 링크라 링크만 지운다(가리키는 곳은 지우지 않는다). 없음 = 모름(옛 러너·대상 없음·rm 아님). 실행은 미리보기의
  // 종류와 다르면 도구 전에 거부한다(target_changed_since_preview). 화면은 toolSummary.readSummary 의 targetKind 로 읽는다.
  // 러너 lstat(d194): link·dir·file·other, 미리보기 때 대상이 없으면 "missing"(묶기용 -- 화면은 0 = 대상 없음으로 이미 말한다)
  target_kind?: "link" | "dir" | "file" | "other" | "missing" | string;
}
export interface DataJob {
  job_id: string; request_id: string; operation: string; state: string;
  reason_code: string | null; preview_fingerprint: string | null;
  preview_expires_at: string | null; result_summary: unknown;
  // 미리보기(dry-run) summary.json 사본(2026-09-17): 핵심 3키 + d192 상세 키(ToolSummary, 옛 잡은 3키만). 개수는
  // 모르면 null. 지문(preview_fingerprint)이 이 객체의 해시라 컨펌 창이 "무엇을 컨펌하는지" 를 보여줄 수 있다. 이전 잡은 null.
  preview_summary?: ToolSummary | null;
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
  // 계획 시점 전제(planner: {requester_id, owner, operation}). 요청 상세가 실행 신원 이름의 두 번째 출처로 읽는다.
  precondition?: unknown;
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
  // root 실행 자격(2026-10-12, routes_accounts._with_root). root_source: config = 배포 설정(DMS_PRIVILEGED_REQUESTERS --
  // 화면에서 못 바꾼다), granted = 계정 화면에서 부여, null = 없음. root_eligible = 지금 효력(허용 설정 + admin + 활성 +
  // 출처). 옵션(?) = 구형 서버.
  root_source?: "config" | "granted" | null;
  root_eligible?: boolean;
  // 저장된 부여 플래그 -- env 와 겹쳐도 보인다(env 에서 빠지면 부여로 효력이 남는다 -- 화면이 말하고 뺄 수 있게).
  root_granted?: boolean;
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
  // 같은 장비 식별자(2026-11-25): 호스트 /etc/machine-id 의 HMAC-SHA256 앞 16hex(원문 아님). host_root 모드 에이전트만 채우고
  // 모르면 null, 옛 에이전트는 키 없음. 화면은 직접 비교하지 않는다 -- 서버가 renamed_to 로 판정해 준다.
  machine_key?: string | null;
}
export interface NodeInfo {
  node_name: string; reported_at: string; fresh: boolean; report: NodeReportBody;
  // 노드 배치 제외(2026-10-02): 관리자가 뺀 노드면 사유·누가·언제, 아니면 null. 옵션(?) = 구형 서버·fixture 호환.
  exclusion?: NodeExclusion | null;
  // 새 이름 힌트(2026-11-25, 서버 node_forget.rename_hints): 리포트 지연 행과 같은 machine_key 를 보고하는 이름이 다른 정상 노드가
  // 정확히 하나일 때만 그 이름, 나머지(정상 행 포함)는 null. 옵션(?) = 구형 서버 호환. 화면은 리포트 지연 행에서만 그린다.
  renamed_to?: string | null;
}
// 노드 기록 삭제(DELETE /api/admin/nodes/{name}, 2026-11-25) 응답.
export interface ForgetNodeResult {
  deleted: string; reported_at: string; reports_deleted: number; exclusion_deleted: boolean;
}
export interface NodeExclusion {
  node_name: string; reason: string | null; created_by: string; created_at: string;
}
export interface NodeReport { reported_at: string; report: NodeReportBody }
export interface Batch {
  // 백그라운드 배치 단위 삭제 진행(2026-11-19 -- 상세 응답, status Deleting 일 때만 객체). 부재 = 옛 서버.
  deleting?: BatchDeletionRow | null;
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
  // 자동 확인(sync 2026-10-11, rm 2026-10-21): 켜져 있으면 미리보기가 끝나는 순간 「배치 확인」 없이 실행된다. auto_confirm_by = 켠
  // 관리자. 부재(옛 서버·fixture)는 꺼짐으로 읽는다(=== true 로만 켜짐 판정).
  auto_confirm?: boolean;
  auto_confirm_by?: string | null;
  // 상세 GET 만: 자동 확인이 켜진 진행 중 sync·rm 배치가 지금 확인하면 보류될 사유 코드(null = 곧 자동 실행). 서버가
  // orchestrator 와 같은 술어로 판정한다(batch_orchestrator.auto_confirm_hold).
  auto_confirm_hold?: string | null;
  // 대상 스토리지(2026-11-16, 목록·summary): 첫 항목(MIN(seq)) payload 에서 서버가 뽑은 값 -- 배치는 스토리지 한 종류(sync 는 한
  // 쌍)라 첫 항목이 배치의 스토리지다. scan/rm 은 storage, sync 는 source_storage·destination_storage. null = 모름(항목 0개·깨진
  // payload·문자열 아님). 옵션(?) = 옛 서버·기존 fixture 호환.
  storage?: string | null;
  source_storage?: string | null;
  destination_storage?: string | null;
  // 배치 스케줄 요약(2026-11-16, 목록·summary 만 -- view 없음·all 응답엔 없다). null = 스케줄 없음, 부재 = 모름(옛 서버) --
  // 화면은 null 일 때만 「스케줄 추가」를 권한다.
  schedule?: BatchScheduleSummary | null;
}
// 배치 응답에 실리는 스케줄 요약(routes_batches._with_schedules -- BatchSchedule 행의 일부). enabled 는 INTEGER 컬럼이라 0/1 일
// 수도 있다(scheduleEnabled 로만 판정). next_run_at null = 꺼짐·일시정지, last_* null = 아직 판정한 회차 없음.
export interface BatchScheduleSummary {
  schedule_id: string; kind: string; spec: Record<string, unknown> | null;
  enabled: boolean | number; next_run_at: string | null;
  last_run_at: string | null; last_outcome: string | null; last_reason_code: string | null;
  paused_reason_code: string | null;
  // 회차 보존 정리(2026-11-16 -- 스케줄이 있는 배치는 최근 keep_rounds 회차만 남고 오래된 회차의 자식은 컨트롤러가 정리한다).
  // pruned_total null = 아직 정리 없음(0 과 다르다), pruned_at = 마지막 정리 시각(UTC). keep_rounds = 서버 설정
  // DMS_BATCH_SCHEDULE_KEEP_ROUNDS(배치 상세 「최근 K회차만 보존」 안내). 옵션(?) = 옛 서버·기존 fixture.
  pruned_at?: string | null; pruned_total?: number | null; keep_rounds?: number | null;
}
// 실행 설정의 direct 안내 재료(2026-11-16, ?view=summary): options.direct === true 일 때만 서버가 센다(아니면 null). 판정은
// 화면 directNote 와 같은 규칙(도구 = job_tool 먼저, 없으면 미리보기 summary v2 의 tool; undecided = 도구 없음 ∧ 항목이 거부·
// 실패·취소가 아님).
export interface BatchDirectTools { tools_known: number; nsync: number; undecided: number }
// 상세 머리(2026-11-16, GET /api/admin/batches/{id}?view=summary): 배치 행 + 집계. items 는 싣지 않는다 -- 항목은 쪽 조회
// (?view=items)와 대화상자의 1회 조회(?view=all·payloads)가 따로 받는다. 집계 키가 옵션(?)인 이유: 롤링 중 옛 파드는 view 를
// 모르고 옛 전체 응답(items 포함)을 준다 -- 화면(useBatch)이 그 items 에서 파생해 물러선다.
export interface BatchSummary extends Batch {
  item_total?: number;
  // 상태별 항목 수 -- 0 도 값이다(없는 상태 키는 0 으로 읽는다).
  status_counts?: Record<string, number>;
  first_payload?: Record<string, unknown> | null;
  // sync·rm 이 Previewing·PreviewReady 일 때만 센다(확인 단계 배너 재료) -- 그 밖 null(= 말할 것 없음, 0 과 다르다).
  executing_count?: number | null;
  direct_tools?: BatchDirectTools | null;
  items?: BatchItem[];
}
// 항목 한 쪽(?view=items&offset=&limit=&q=&status=): total 은 거른 뒤의 수(쪽 수·「검색 결과 N개」의 재료).
export interface BatchItemsPage { total: number; offset: number; limit: number; items: BatchItem[] }
// 가벼운 전 항목(?view=payloads) -- 현재 항목 CSV·rm 겹침 즉답용. 조인 필드는 없다.
export interface BatchPayloadItem { seq: number; payload: Record<string, unknown>; status: string }
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
  // job_state === "ConfirmPending" = 미리보기를 마치고 배치 확인을 기다리는 항목. preview_summary.files 의 뜻은 도구별이다
  // (같은 노드 dsync = dry-run 이 훑은 소스 전체 항목 수, 노드 간 nsync = 계획된 변경 수 -- 이미 맞는 항목은 빠진다,
  // drm = 지울 대상). files_count(위)는 실행 결과만 담는다(nsync = 실제 반영 합, dsync = 만든 항목 − 복사 실패, drm = 지운 수
  // -- 미리보기 실패의 수는 승격하지 않는다, d192). null = 모름(dsync dryrun 의 bytes 는 null -- 0 으로 뭉개지 않는다).
  job_state?: string | null;
  // d192: 미리보기 수의 뜻은 도구마다 다르다(nsync = 변경 예정, dsync = 소스 전체 walk 수, drm = 지울 대상) -- 화면은
  // job_tool(자식 잡의 실제 도구, 서버 조인) 을 먼저, 없으면 preview_summary.tool(v2 러너만 싣는다)로 가른다.
  preview_summary?: ToolSummary | null;
  preview_expires_at?: string | null;
  job_tool?: string | null;
}
export interface BatchDetail extends Batch { items: BatchItem[] }
// 배치 스케줄러(2026-11-16): 배치당 하나, 정해진 KST 시각에 그 배치의 「전체 재실행」. 서버 GET /api/admin/batch-schedules 의 행.
// kind/spec 은 서버 batch_scheduler 가 검증한 값이지만 화면은 DB 신뢰 경계로 읽는다(깨진 spec = 「알 수 없는 주기」,
// lib/batchSchedule). enabled 는 INTEGER 컬럼이라 0/1 일 수도 있다(scheduleEnabled 로만 판정).
export interface BatchSchedule {
  schedule_id: string; batch_id: string;
  kind: string; spec: Record<string, unknown> | null;
  enabled: boolean | number;
  next_run_at: string | null;              // UTC — 꺼졌거나 일시정지면 null
  armed_by: string;                        // 마지막으로 만들거나 고치거나 켠 관리자(회차마다 자격을 다시 본다)
  created_by: string; created_at: string; updated_at: string;
  // 마지막 회차 판정. null = 아직 한 번도 판정하지 않았다(실패가 아니다).
  last_run_at: string | null; last_outcome: string | null; last_reason_code: string | null;
  paused_reason_code: string | null;       // 자동 일시정지 사유(사람이 끄면 null)
  // 동의 지문이 저장돼 있는가(파괴적 스케줄을 동의하고 무장했다). 지문 원문은 응답에 싣지 않는다.
  acknowledged_irreversible?: boolean;
  // 「되돌릴 수 없음」 동의가 필요한 스케줄인가(rm, sync+delete — 서버 batch_scheduler.is_destructive 를 **지금** 배치로 판정).
  // 행의 최상위 필드다(배치 요약 안이 아니다). null = 배치 행이 없어 모름, 부재 = 화면이 연산·옵션으로 판정(isDestructiveBatch).
  destructive?: boolean | null;
  // 배치 요약. null = 배치 행이 없다(정상 경로에선 배치 삭제가 스케줄도 지운다).
  batch: BatchScheduleBatch | null;
  // 회차 보존(2026-11-16 사용자 결정 「최근 10회차만 누적」 -- 서버 DMS_BATCH_SCHEDULE_KEEP_ROUNDS, 기본 10): 최근 keep_rounds
  // 회차만 남고 오래된 회차의 작업은 컨트롤러 batch-round-prune 이 정리한다. pruned_total null = 아직 정리한 적 없음(0 과
  // 다르다), pruned_at = 마지막으로 정리한 시각(UTC). child_count_limit = 배치 단위 삭제의 자식 상한(서버 설정
  // DMS_BATCH_DELETE_MAX_CHILDREN, 기본 50000 -- 2026-11-19; 「항목 × K > 상한」 예측 경고의 기준, 포탈은 상수를 두지 않는다).
  // 옵션(?) = 옛 서버·fixture.
  keep_rounds?: number | null; pruned_at?: string | null; pruned_total?: number | null;
  child_count_limit?: number | null;
}
export interface BatchScheduleBatch {
  batch_id?: string;
  // item_count null = 모름(변조된 값) — 0(항목 없음)과 다르다.
  name: string | null; operation: string; status: string; item_count: number | null;
  auto_confirm?: boolean;
  options?: Record<string, unknown> | null;
  // 대상 스토리지(첫 항목 payload — 서버가 모양 투영). null = 모름.
  storage?: string | null; source_storage?: string | null; destination_storage?: string | null;
}
// 회차 기록(GET /api/admin/batch-schedules/{id}/runs, 최근 순). requeued·prev_* 는 fired 일 때만 값이 있다 -- null = 해당 없음
// (0 은 「0개」라는 정상값이다).
export interface BatchScheduleRun {
  run_id: number; schedule_id: string; batch_id: string;
  scheduled_for: string; at: string; outcome: string; reason_code: string | null;
  batch_status: string | null;
  requeued: number | null;
  prev_status: string | null; prev_item_count: number | null; prev_succeeded: number | null; prev_failed: number | null;
}
// 추가·수정 창의 서버 미리보기(POST /api/admin/batch-schedules:preview). next_runs = 다음 5회(UTC). keep_rounds·child_count_limit
// 은 늘(서버 설정), prune_preview 는 batch_id 를 실었을 때만 온다(2026-11-16 회차 보존 -- 옛 서버엔 셋 다 없다, 옵션).
export interface SchedulePreview {
  next_runs: string[];
  keep_rounds?: number | null; child_count_limit?: number | null; prune_preview?: PrunePreview | null;
}
/** 저장하면 곧 정리될 작업 예측(서버 BatchSchedulesRepository.prune_preview -- 실제 정리와 같은 경계 계산). would_prune 은 막힌
 *  작업(비종단·조용한 창·항목 참조)까지 세는 근사치라 화면은 「약」으로 말하고, capped = 서버가 상한 + 1 에서 세기를 멈췄다.
 *  rounds_recorded = 기록된 회차 경계 수(null = 모름 -- 0 과 다르다). */
export interface PrunePreview { rounds_recorded: number | null; would_prune: number; would_prune_capped?: boolean }
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
// 목록 쪽 응답(2026-11-16 -- scan-targets 에 offset 을 실으면, 0 포함). total = 필터에 맞는 대상 전체 수(쪽과 무관), windowed =
// 지표 정렬(bytes·files·hot)이고 total 이 window_limit 을 넘는다 = 창 밖 대상은 그 정렬의 어느 쪽에도 없다. offset 이 없으면
// 서버는 예전 배열(ScanTargetRow[])을 준다 -- 롤링 중 옛 파드도 offset 을 무시하고 배열을 준다(useUsage.toTargetPage).
export interface ScanTargetsPaged {
  rows: ScanTargetRow[]; total: number; offset: number; limit: number; window_limit: number; windowed: boolean;
  // 이 요청에서 작업 기록 저장소(artifact base)에 접근하지 못해 리포트를 못 읽은 행이 있다(2026-11-23). 값은 공개 코드
  // artifact_base_unavailable 하나. 부재·null = 그런 일 없음(정상 경로 바이트 동일).
  unreadable_reason?: string | null;
}
// 사용량 분석 전체 내보내기(GET /api/admin/usage/export). previous = 최신 직전 성공 scan(증감 계산용).
export interface UsageExportRow extends ScanTargetRow {
  latest: UsagePoint | null;
  // 직전 성공 scan -- 증감용 값만(서버가 행 크기를 줄인다). total_bytes null = 모름.
  previous: { job_id: string; request_id: string; finished_at: string | null; total_bytes: number | null } | null;
  // 2026-10-12: 행의 스토리지 정보(서버 routes_usage._storage_infos). 옵션(?) = 구형 서버 -- 포탈이 등록 목록으로 대신한다.
  storage?: UsageStorageInfo | null;
}
// 내보내기 행의 스토리지 정보. source: registered = 지금 등록된 값, last_known = 감사 기록의 마지막 설정(삭제된 스토리지),
// null = 모름(값도 null).
export interface UsageStorageInfo {
  registered: boolean; source: "registered" | "last_known" | null;
  backend_type: string | null; mount_path: string | null; managed_root: string | null;
}
// 사용량 분석의 스토리지 탭 요약(GET /api/admin/usage/storages, 2026-10-20). storages = 등록 스토리지 ∪ 성공 scan 이
// 있는 이름(등록 이름순 → 미등록 이름순). 등록인데 기록이 없으면 target_count·succeeded_scan_count 0 · last_scan_at null
// (진짜 0). 미등록이면 backend_type·mount_path 는 감사 기록의 마지막 설정(source last_known, 모르면 null).
// window_limit = 지표 정렬(실 사용량·파일 수·hot)이 값을 계산하는 창(최근 스캔 순) -- 대상 수가 이걸 넘을 때만 화면이 말한다.
export interface UsageStorageSummary {
  storage_name: string; registered: boolean; source: "registered" | "last_known" | null;
  backend_type: string | null; mount_path: string | null;
  target_count: number; succeeded_scan_count: number; last_scan_at: string | null;
}
export interface UsageStorages { window_limit: number; storages: UsageStorageSummary[] }
// 스토리지 합계(GET /api/admin/usage/storage-totals?storage=, 2026-10-21 -- 서버 routes_usage.storage_totals 계약). 최상위 대상
// (다른 대상 아래가 아닌 것)마다 최신 성공 scan 값의 합. 지표마다 known/unknown(대상 수) -- known 0 이면 sum null(기록 없음 ≠ 0).
// pending = 이번 호출의 읽기 예산 밖이라 요약을 아직 안 읽은 대상(리포트 값은 모름으로 셈, 용량만 DB 값), progressed = 이번 호출이
// 다음 호출의 출발점을 바꿨다(새로 읽었거나 못 읽은 것을 서버가 기억해 다음 호출이 그 뒤를 연다 -- 포탈은 pending > 0 && progressed
// 동안 다시 부른다). filesystem = 그 스토리지가 Ready 마운트인 노드의 df.
export interface UsageTotalMetric { sum: number | null; known: number; unknown: number }
export interface UsageStorageFilesystem {
  total_bytes: number; used_bytes: number; node_name: string; reported_at: string; fresh: boolean;
  node_count: number; consistent: boolean;
}
export interface UsageStorageTotals {
  storage_name: string; computed_at: string; truncated: boolean; window_limit: number;
  target_count: number; summed_target_count: number; nested_target_count: number; invalid_target_count: number;
  unreadable_count: number; pending_count: number; progressed: boolean;
  // base 접근 불가로 이번 호출이 읽기를 멈췄다(남은 미스는 pending 으로 셈 -- 2026-11-23). 부재·null = 정상.
  unreadable_reason?: string | null;
  total_bytes: UsageTotalMetric; total_files: UsageTotalMetric; total_directories: UsageTotalMetric;
  total_symlinks: UsageTotalMetric; total_other: UsageTotalMetric; total_entries: UsageTotalMetric;
  scan_errors: UsageTotalMetric; broken_paths_total: UsageTotalMetric;
  hot_ratio: { value: number | null; hot_bytes: number | null; total_bytes: number | null; known: number; unknown: number };
  file_size_histogram: { buckets: HistogramBucket[]; known: number; unknown: number };
  oldest_scan_at: string | null; newest_scan_at: string | null;
  filesystem: UsageStorageFilesystem | null;
}
// 사용량 분석의 미등록 스토리지(GET /api/admin/usage/unregistered-storages, 2026-10-12). target_count·succeeded_scan_count·
// last_scan_at = 사용량 화면 기준(성공 scan -- 실패 scan 만 남은 이름은 0·null), request_count = 정리하면 지워질 작업 수
// (단건 + batches 의 자식). mixed_batches = 다른 스토리지·다른 작업이 섞여 정리가 건너뛸 배치(수에 넣지 않는다).
export interface UnregisteredStorage {
  storage_name: string; target_count: number; succeeded_scan_count: number; last_scan_at: string | null;
  single_count: number; batches: { batch_id: string; request_count: number }[]; request_count: number;
  mixed_batches: { batch_id: string; request_count: number }[];
  last_known: { backend_type: string | null; mount_path: string | null; managed_root: string | null } | null;
}
// POST /api/admin/usage/unregistered-storages:purge 한 회차의 응답(부분 성공 모델 -- 작업 삭제와 같은 줄 모양).
export interface PurgeStorageHistoryResult {
  storage_name: string;
  deleted: { request_id: string; job_ids: string[] }[];
  deleted_batches: { batch_id: string; request_ids: string[]; job_ids: string[]; dangling: boolean }[];
  skipped: { request_id: string; reason: string }[];
  skipped_batches: SkippedBatch[];
  // 백그라운드로 정리하기 시작한 배치(2026-11-19). 부재 = 옛 서버.
  deleting_batches?: { batch_id: string; request_count: number; already: boolean }[];
  remaining: { requests: number; batches: number } | null;
  // 남은 것 중 다음 회차가 시도할 것(이번 본문의 exclude_* 와 이번 회차에 건너뛴 것을 뺀 수). null = 모름.
  remaining_untried: { requests: number; batches: number } | null;
  purge_pending: number | null;
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
  // 목록 행(latest)에만: 이 요청에서 base 접근 불가로 못 읽은 행(2026-11-23). 키가 없으면 정상(바이트 동일).
  unreadable_reason?: string | null;
}
export interface UsageHistory {
  storage_name: string; target: string; points: UsagePoint[];
  skipped_unreadable: number;
  // skipped 의 원인이 base 접근 불가면 "artifact_base_unavailable", 개별 리포트 문제·정상이면 null. 옵션(?) = 구형 서버.
  unreadable_reason?: string | null;
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
  // 컨트롤러 파드가 뜬 노드(2026-11-23 -- 두 관점 엇갈림 상자가 노드를 짚는다). 옵션(?) = 구형 서버, null = 모름.
  node_name?: string | null;
}
// API 파드 자체 점검(2026-11-23 사고: dms-api 가 공유 스토리지 미마운트 노드에 떠 아티팩트를 못 읽었는데 컨트롤러 점검은
// 정상이었다 -- 설계 apibase v2 §F). 새 필드는 전부 옵션(?) = 구형 서버(옛 checks.api 는 {ok, reason} 뿐) 호환.
// ok = availability.ok ∧ posture 없음, reason = availability.reason ?? posture(호환 -- hopSummary 는 ok 하나만 센다).
export interface ArtifactBaseAvailability {
  ok: boolean; reason: string | null;
  // jobs_found null = 확인할 최근 잡이 없어 판정 생략(모름 ≠ 0).
  jobs_checked: number; jobs_found: number | null;
}
// 시작 점검 상태(서버 base_monitor.gate_state): off = 주기 점검 꺼짐, disabled = 비상구(env 게이트 0), waiting = Ready 를 막는
// 중, passed = 정상으로 Ready, started_degraded = 형제 파드가 없어 실패인 채 Ready(부분 장애가 전면 장애가 되지 않게).
export type ApiGateState = "off" | "disabled" | "waiting" | "passed" | "started_degraded";
export interface ArtifactBaseApiCheck {
  ok: boolean; reason: string | null;
  availability?: ArtifactBaseAvailability | null;
  posture?: string | null;              // 소유자·o+w·정적 검사 사유(가용성과 별개 -- 게이트·503 에 쓰지 않는다)
  source?: "monitor" | "instant";       // monitor = 주기 점검 스냅숏, instant = 조회 시 즉석
  instance?: string | null; node_name?: string | null; checked_at?: string | null;
  fstype?: string | null; local_fs?: boolean | null;
  interval_seconds?: number | null;
  gate?: { enabled: boolean; state: ApiGateState | null; at: string | null } | null;
}
// API 파드별 최근 점검(서버 api_base_checks 24h 행 -- this_pod 먼저, 실패·waiting, 최근 확인순). ok null = 아직 점검 전.
export interface ArtifactBaseApiPod {
  instance: string; node_name: string | null; uri: string | null;
  ok: boolean | null; reason: string | null; posture: string | null;
  fstype: string | null; local_fs: boolean | null; gate_state: ApiGateState | null;
  started_at: string; checked_at: string | null; ready_at: string | null;
  fresh: boolean; this_pod: boolean;
}
export interface ArtifactBaseInfo {
  effective: string; source: "db" | "env"; db_value: string | null; env_value: string;
  locked_by_jobs: number;
  checks: {
    api: ArtifactBaseApiCheck;
    controller: ArtifactBaseControllerCheck;
    nodes: ArtifactBaseNodeCheck[];
    api_pods?: ArtifactBaseApiPod[];
  };
}
// GET /api/admin/artifact-base/health -- 요청을 받은 API 파드의 메모리 스냅숏만(가벼움, 사용량 화면의 복구 감시용).
// ok null = 아직 점검 전(모름).
export interface ArtifactBaseHealth {
  ok: boolean | null; reason: string | null; checked_at: string | null;
  instance: string | null; node_name: string | null;
}

// 마운트 현황(GET /api/admin/mounts, 2026-11-23 -- 서버 routes_mounts: DB 만 읽는 노드 × 스토리지 표). 셀 status:
// ready = 마운트됨(쓰기 가능), readonly = 마운트됐지만 쓰기 불가, missing = 없음(reason = 에이전트 사유), unreported = 그
// 스토리지 보고 없음(옛 프로브·에이전트 없음), disabled = 비활성 스토리지(문제 아님).
export type MountCellStatus = "ready" | "readonly" | "missing" | "unreported" | "disabled";
export interface MountCell {
  status: MountCellStatus; reason: string | null;
  exists?: boolean | null; is_mountpoint?: boolean | null; readable?: boolean | null; writable?: boolean | null;
}
export interface MountBaseApiCheck {
  instance: string; ok: boolean | null; reason: string | null; checked_at: string | null; gate_state: ApiGateState | null;
}
export interface MountBaseCell {
  // missing 의 reason 은 에이전트 사유 또는 "covering_storage_missing:<스토리지>"(base 를 덮는 스토리지가 그 노드에 없다).
  status: Exclude<MountCellStatus, "disabled">; reason: string | null;
  agent: { exists: boolean | null; writable: boolean | null; path_matches: boolean | null } | null;
  api: MountBaseApiCheck[];
  controller: { ok: boolean | null; reason: string | null; checked_at: string | null } | null;
}
export interface MountNode {
  node_name: string; has_agent: boolean; fresh: boolean; reported_at: string | null; probe_mode: string | null;
  schedulable: boolean | null; schedulable_reason: string | null; excluded: boolean;
  roles: ("api" | "controller")[];
  cells: Record<string, MountCell>;
  artifact_base: MountBaseCell | null;
}
export interface MountsOverview {
  generated_at: string;
  artifact_base: { uri: string | null; path: string | null; covering_storage: string | null } | null;
  storages: { storage_name: string; mount_path: string; enabled: boolean }[];
  nodes: MountNode[];
  summary: {
    node_count: number; fresh_node_count: number; storage_count: number; problem_cells: number; problem_nodes: number;
    stale_node_count?: number;
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
  // 서버가 정한 화면 순서(최근 빌드 → 숫자 고려 내림차순, 2026-10-08) -- 프론트는 다시 정렬하지 않는다.
  tags: string[];
  // 이 리포를 push 한 최근 성공 빌드의 태그 → 빌드 완료 시각(ISO). 옵셔널 = 구 서버 호환.
  built?: Record<string, string>;
  // 레지스트리 페이지 상한에 걸려 목록이 잘렸다(여기 없는 태그도 제출은 검증 안 됨으로 통과).
  tags_truncated?: boolean;
}
// registry_ok=false면 tags가 전부 비어 있고 서버의 태그 존재 검증도 꺼진 상태다.
export interface ReleaseTargets { targets: ReleaseTarget[]; registry_ok: boolean }
export interface Releases { current: Record<string, Release>; history: Release[] }

export interface NodeMetricDisk { storage_name: string; used_pct: number | null }
export interface NodeMetricPoint {
  at: string;
  // CPU 사용률·I/O 대기 %(2026-10-16): 에이전트가 /proc/stat 차분으로 계산, 서버가 0..100 검증. null = 모름(구형
  // 에이전트·재시작 후 첫 보고) -- 0 아님. 옵션(?) = 구 서버·fixture 호환. load1 은 사용률이 아니다(코어 수를 넘는다).
  cpu_used_pct?: number | null; cpu_iowait_pct?: number | null;
  load1: number | null; load5: number | null; load15: number | null;
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
// d192 통계 정의(서버 metrics 와 같은 한 벌): succeeded = Succeeded, failed = Failed+TimedOut(실행·미리보기 중 실패),
// rejected = Rejected(점검·계획 단계 거절), cancelled = Cancelled+PreviewExpired(취소·확인 만료). 옛 서버는 뒤 둘이 없다.
export interface BreakdownRow { count: number; succeeded: number; failed: number; rejected?: number; cancelled?: number }
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
  // d192: 처리량을 연산별로 나눈다(예전 files_total 은 sync 항목과 rm 삭제 수를 한 숫자에 섞었다). 창 안의 **모든 종단**
  // 잡 중 개수를 아는 실행만 더한다 -- files_count 는 이제 실행 결과만 담으므로(미리보기 실패 수는 승격하지 않는다) 실패·
  // 중단된 실행의 부분 처리도 실제 처리다. null = 아는 행이 없다(0 아님).
  sync_items_total: number | null; sync_bytes_total: number | null; rm_items_total: number | null;
}
export interface InfraComponent {
  component: string; kind: string; workload: string;
  image: string | null; ready: number | null; desired: number | null;
  verdict: "applied" | "progressing" | "failed" | null; detail: string | null;
  // 이미지에 동봉된 "이 이미지를 만든 소스 트리"의 매니페스트 image 값.
  // null = 동봉 없음/파싱 실패 -- 비교 자체를 하지 않는다(무배지).
  manifest_image: string | null;
}
// 대시보드의 아티팩트 경로 점검 요약(2026-11-23, metrics/infra -- 메모리·DB 만). api = 요청을 받은 파드의 스냅숏(null = 모름),
// api_pods_failing = 보고가 살아 있는(fresh) 다른 API 파드 중 실패 수. 옵션(?) = 구형 서버.
export interface InfraArtifactBase {
  api: { ok: boolean | null; reason: string | null; node_name: string | null; instance: string | null } | null;
  api_pods_failing?: number | null;
  // 그중 시작 게이트에 막혀 Ready 를 기다리는(서비스 밖) 수 — 옵션 = 구형 서버, null = 읽기 실패(모름).
  api_pods_failing_waiting?: number | null;
  controller: { pending: boolean; ok: boolean | null; reason: string | null; node_name: string | null } | null;
}
export interface InfraMetrics {
  components: InfraComponent[];
  artifact_base?: InfraArtifactBase | null;
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
// 2026-10-16(큐 현황 상세화): reason/message(가장 최근 condition -- 표시만, 분기 금지), priority_class,
// vcjob(소유 Volcano Job 이름)이 더해졌다. 옛 서버 응답엔 키가 없어 선택 필드다.
export interface QueuePodgroup {
  name: string; phase: string | null; min_member: number | null;
  created_at: string | null; wait_seconds: number | null;
  reason?: string | null; message?: string | null; priority_class?: string | null; vcjob?: string | null;
}

// 큐 현황 파이프라인(2026-10-16, routes_metrics.metrics_queue · queue_pipeline 모듈 docstring 이 권위). 단계 값 null =
// 그 단계의 재료 축을 못 읽음(모름) -- {count: 0} 과 다르다. oldest_seconds null 은 0 건(없음)이거나 시각이 깨진 것.
export type QueueStage =
  "planning" | "pickup" | "preflight" | "volcano_wait" | "running" | "confirm" | "batch";
export type QueueWaitStage = Exclude<QueueStage, "running">;
export interface QueueStageStat { count: number; oldest_seconds: number | null; }
export interface QueuePipeline {
  planning: QueueStageStat | null;
  pickup: QueueStageStat | null;
  preflight: QueueStageStat | null;
  volcano_wait: QueueStageStat | null;
  // includes_volcano_wait: PodGroup 을 못 읽어 Volcano 대기를 빼지 못한 수(화면이 그 사실을 표기한다)
  running: (QueueStageStat & { includes_volcano_wait: boolean }) | null;
  confirm: (QueueStageStat & {
    soonest_expiry: string | null; soonest_expiry_seconds: number | null;
  }) | null;
  // count = queued(아직 요청이 안 된 Queued 항목) + held(굴러가는 배치의 미리보기 마친 자식 -- 확인된 배치의 실행 슬롯·형제
  // 미리보기를 기다린다), batches = 행(배치) 수, preview_ready_batches = 배치 확인 대기 배치 수. 서버는 잡 축을 못 읽으면
  // 이 칸 전체를 null 로 낸다. queued·held 는 2026-10-16 이후 서버만 보낸다(옛 응답은 undefined).
  batch: (QueueStageStat & {
    batches: number; preview_ready_batches: number; queued?: number; held?: number | null;
  }) | null;
}
// 단계마다 쓰는 키가 다르다(서버 queue_pipeline 이 단계별로 채운다) -- 없는 키는 그 단계의 것이 아니다.
export interface QueueWaitDetail {
  note?: string | null;                 // planning: 가장 최근 계획 유예·오류 이벤트 종류
  message?: string | null;              // planning: 그 이벤트 message / volcano_wait: condition message
  podgroup?: string | null; phase?: string | null; min_member?: number | null;   // volcano_wait
  reason?: string | null; priority_class?: string | null; phase_ref?: string | null;
  expires_at?: string | null; expires_in_seconds?: number | null;              // confirm
  jobs?: number;                        // confirm(배치 묶음): 확인 대기 자식 수
  batch_status?: string | null; batch_name?: string | null;                    // confirm(배치 묶음)·batch
  queued?: number; max_concurrency?: number | null;                             // batch
  held?: number | null;                 // batch: 굴러가는 배치의 미리보기 마친 자식 수
  exec_preflight?: boolean;             // preflight: 확인 뒤 실행 전 재점검(실행 vcjob 제출 전)
}
export interface QueueWaitEntry {
  stage: QueueWaitStage;
  request_id: string | null;   // 배치 묶음·비대조 PodGroup 은 null
  job_id: string | null;
  operation: string | null; requester: string | null;
  priority: string | null;     // 배치의 null = 정책 기본
  batch_id: string | null;
  since: string | null; wait_seconds: number | null;
  detail: QueueWaitDetail;
}
export interface QueueControl { drain: boolean; maintenance: boolean; reason: string | null; }
// 큐 흐름(2026-10-16, queue_pipeline.build_flow 주석이 매핑의 권위) -- 최근 window_seconds 동안 단계 사이를 지나간 수.
// 키 = "<from>><to>". submit(직접 제출 유입)·done(성공 종단)은 흐름에만 있는 단계다. volcano_wait>running 은 전이로 남지 않아
// 키 자체가 없다(측정 없음 -- 0 이 아니다). exits = 실패·거부·취소·만료로 그 단계에서 떠난 수(volcano_wait 이탈은 실행 중에
// 접힌다). saturated 면 수치는 하한이다. 흐름 null = 못 읽음 -- 화면은 모르는 흐름을 움직임으로 꾸미지 않는다.
export type QueueFlowEdge =
  | "submit>planning" | "batch>planning" | "planning>pickup" | "pickup>preflight" | "preflight>volcano_wait"
  | "running>confirm" | "running>batch" | "confirm>preflight" | "batch>preflight" | "running>done";
export type QueueFlowExitStage = "planning" | "pickup" | "preflight" | "running" | "confirm" | "batch";
export interface QueueFlow {
  window_seconds: number;
  saturated: boolean;
  edges: Partial<Record<QueueFlowEdge, number>>;
  exits: Partial<Record<QueueFlowExitStage, number>>;
  done: number;
}
export interface QueueMetrics {
  queue: { name: string; state: string | null } | null;
  podgroups: QueuePodgroup[] | null;
  // 2026-10-16 -- 옛 서버(키 부재 = undefined)는 화면이 null(모름)과 똑같이 다룬다.
  control?: QueueControl | null;
  pipeline?: QueuePipeline | null;
  waiting?: QueueWaitEntry[] | null;
  waiting_truncated?: QueueWaitStage[] | null;
  flow?: QueueFlow | null;
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
