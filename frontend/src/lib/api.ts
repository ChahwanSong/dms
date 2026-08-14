export const REASON_MESSAGES: Record<string, string> = {
  // planner / identity / placement
  identity_denied: "차단 목록에 있는 신원입니다",
  ldap_not_configured: "LDAP이 설정되지 않았습니다",
  ldap_unavailable: "LDAP에 연결할 수 없습니다",
  ldap_identity_not_found: "LDAP에서 사용자를 찾을 수 없습니다",
  missing_policy: "해당 도구의 정책이 없습니다",
  policy_disabled: "해당 도구의 정책이 비활성 상태입니다",
  // stepper / controller
  orphan_recovery: "컨트롤러 재시작으로 상태를 복구했습니다",
  preflight_failed: "사전 점검에 실패했습니다",
  execution_failed: "실행에 실패했습니다",
  empty_preview: "대상이 없습니다",
  preview_timed_out: "미리보기가 제한 시간을 넘겼습니다",
  preview_failed: "미리보기에 실패했습니다",
  execution_recheck_failed: "실행 직전 재점검에 실패했습니다",
  // 복합 접두 (stepper.py 가 f"{prefix}:{ExecutionError.reason_code}" 형태로 발생시킨다)
  preflight_submit_failed: "사전 점검을 시작하지 못했습니다",
  execution_submit_failed: "실행을 시작하지 못했습니다",
  preview_submit_failed: "미리보기를 시작하지 못했습니다",
  execution_recheck_submit_failed: "실행 직전 재점검을 시작하지 못했습니다",
  // 인증/계정
  not_authenticated: "로그인이 필요합니다",
  admin_required: "관리자 권한이 필요합니다",
  admin_token_required: "관리자 토큰이 필요합니다",
  invalid_token: "토큰이 올바르지 않습니다",
  invalid_actor: "요청자 표기(x-dms-actor)가 올바르지 않습니다",
  account_exists: "이미 존재하는 계정입니다",
  invalid_username: "사용자명이 올바르지 않습니다",
  // job / batch
  job_not_found: "잡을 찾을 수 없습니다",
  batch_not_found: "배치를 찾을 수 없습니다",
  batch_not_confirmable: "확인할 수 없는 상태의 배치입니다",
  no_failed_items: "실패한 항목이 없습니다",
  no_preview_fingerprint: "아직 미리보기가 준비되지 않았습니다",
  empty_batch: "배치에 항목이 없습니다",
  invalid_batch_operation: "배치로 지원하지 않는 연산입니다",
  invalid_max_concurrency: "동시 실행 수 값이 올바르지 않습니다",
  batch_storage_mixed: "배치의 스토리지가 섞여 있습니다 — 한 배치는 하나의 스토리지만",
  invalid_node_count: "노드 수 값이 올바르지 않습니다",
  invalid_procs_per_node: "노드당 프로세스 수 값이 올바르지 않습니다",
  batch_not_rescannable: "재실행할 수 없는 상태의 배치입니다 — 완료·취소된 배치만 가능합니다",
  // 항목 편집(수정·삭제·추가). reasonCodes.json 과 같은 커밋 — 양방향 계약
  // (reasonCodes.test.ts / test_reason_codes_coverage.py) 조건이다.
  batch_item_not_found: "배치 항목을 찾을 수 없습니다",
  batch_item_not_editable: "수정할 수 없는 항목입니다 — 이미 실행됐거나 실행 중입니다",
  batch_not_deletable: "삭제할 수 없는 상태의 배치입니다 — 먼저 취소하세요",
  batch_items_not_replaceable: "항목을 교체할 수 없는 상태의 배치입니다 — 완료·취소된 배치만 가능합니다",
  invalid_batch_name: "배치 이름이 올바르지 않습니다 — 120자 이하로 입력하세요",
  invalid_storage: "스토리지 설정이 올바르지 않습니다",
  invalid_node_name: "노드 이름이 올바르지 않습니다",
  agent_node_identity_mismatch: "에이전트 노드 신원이 일치하지 않습니다",
  terminate_failed: "종료 요청에 실패했습니다",
  invalid_job_id: "잡 ID가 올바르지 않습니다",
  invalid_batch: "배치 값이 올바르지 않습니다",
  invalid_credentials: "사용자명 또는 비밀번호가 올바르지 않습니다",
  fingerprint_mismatch: "미리보기가 변경되었습니다. 다시 확인해 주세요",
  preview_expired: "미리보기가 만료되었습니다. 다시 제출해 주세요",
  not_confirmable: "이미 처리된 작업입니다",
  already_terminal: "이미 처리된 작업입니다",
  privileged_not_authorized: "권한 있는 요청자가 아닙니다",
  resource_conflict: "동일 대상에 진행 중인 작업이 있습니다",
  no_eligible_nodes: "실행 가능한 노드가 없습니다",
  no_ready_sync_candidate: "실행 가능한 노드가 없습니다",
  storage_exists: "이미 존재하는 스토리지입니다",
  storage_in_use: "사용 중인 스토리지는 삭제할 수 없습니다 (비활성화하세요)",
  storage_not_found: "스토리지를 찾을 수 없습니다",
  maintenance_mode: "유지보수 중입니다 — 새 작업 제출이 일시 중단되었습니다",
  http_422: "입력값이 올바르지 않습니다",
  invalid_policy: "정책 값이 올바르지 않습니다",
  invalid_priority: "우선순위 값이 올바르지 않습니다",
  invalid_denylist_subject_type: "대상 유형이 올바르지 않습니다",
  policy_not_found: "정책을 찾을 수 없습니다",
  artifact_not_found: "아티팩트를 찾을 수 없습니다",
  artifact_too_large: "파일이 다운로드 상한을 넘습니다 — 관리자에게 문의하세요",
  invalid_phase: "알 수 없는 실행 단계입니다",
  log_ref_not_found: "이 단계의 로그 참조가 없습니다",
  log_not_available: "이 단계는 파드 로그를 제공하지 않습니다 — 아티팩트를 확인하세요",
  unsafe_path: "경로가 올바르지 않습니다",
  scan_admin_only: "scan 실행은 관리자만 가능합니다",
  account_disabled: "계정이 비활성화되었습니다",
  node_not_found: "노드를 찾을 수 없습니다",
  requester_disabled: "요청자 계정이 비활성화되었습니다",
  // 브리프 BACKEND_CODES 목록에는 없지만 src/dms/ grep으로 확인한, 현재도 실제로
  // 발생하는 코드들 -- 삭제하면 ScanPaths.test.tsx / AccountsList.test.tsx 가
  // 깨진다(no_covering_scan, cannot_lock_self는 기존 테스트가 한국어 문구를 직접
  // 단언하고 있었다). 브리프의 목록이 백엔드 실제 코드 전수조사와 어긋나 있어
  // "죽은 키" 판정에서 제외했다 -- task-1-report.md 참고.
  artifact_forbidden: "허용되지 않은 아티팩트 경로입니다",
  invalid_artifact_name: "아티팩트 이름이 올바르지 않습니다",
  rm_recursive_required: "삭제는 재귀 옵션이 필요합니다",
  rm_root_forbidden: "관리 루트 자체는 삭제할 수 없습니다",
  unknown_option: "지원하지 않는 옵션입니다",
  invalid_option: "옵션 값이 올바르지 않습니다",
  storage_missing: "등록되지 않은 스토리지입니다",
  storage_disabled: "비활성 스토리지입니다",
  storage_not_ready: "스토리지가 준비되지 않았습니다",
  missing_storage: "스토리지를 선택하세요",
  missing_source_storage: "소스 스토리지를 선택하세요",
  missing_destination_storage: "목적지 스토리지를 선택하세요",
  sync_destination_inside_source: "목적지가 소스 하위 경로일 수 없습니다",
  invalid_owner_username: "사용자명이 올바르지 않습니다",
  invalid_operation: "지원하지 않는 연산입니다",
  // 슬라이스 24 파괴적 경로 fail-closed. reasonCodes.json 과 같은 커밋 -- 양방향
  // 계약(reasonCodes.test.ts / test_reason_codes_coverage.py) 조건이다.
  unknown_tool: "허용되지 않은 도구입니다 — 관리자에게 문의하세요",
  storage_missing_at_step: "잡 진행 중 스토리지 정의가 사라졌습니다 — 관리자에게 문의하세요",
  cancel_failed: "취소에 실패했습니다 — 실행 중인 작업을 종료하지 못했습니다",
  batch_not_cancelable: "취소할 수 없는 상태의 배치입니다",
  request_not_found: "요청을 찾을 수 없습니다",
  cancelled_by_user: "사용자가 취소했습니다",
  cancelled_by_batch: "배치 취소로 종료되었습니다",
  scan_path_exists: "이미 등록된 경로입니다",
  scan_path_not_found: "등록된 경로를 찾을 수 없습니다",
  no_covering_scan: "아직 이 경로를 커버하는 scan 결과가 없습니다",
  scan_report_too_large: "scan 리포트가 너무 커서 통계를 읽을 수 없습니다 — 관리자에게 문의하세요",
  // 요청 단위 scan-stats(항목별 데이터 온도). reasonCodes.json 과 같은 커밋 --
  // 양방향 계약(reasonCodes.test.ts / test_reason_codes_coverage.py) 조건이다.
  no_scan_report: "이 요청의 scan 리포트가 없습니다",
  account_not_found: "계정을 찾을 수 없습니다",
  invalid_role: "역할 값이 올바르지 않습니다",
  cannot_lock_self: "자기 계정의 역할 변경·비활성화는 할 수 없습니다",
  // 계정 위생(슬라이스 19) 안전장치 3종. reasonCodes.json 과 함께 갱신해야 양방향
  // 커버리지 테스트가 초록이다.
  cannot_delete_self: "자기 계정은 삭제할 수 없습니다",
  last_active_admin: "마지막 관리자는 삭제할 수 없습니다",
  account_has_active_requests: "진행 중인 요청이 있어 삭제할 수 없습니다",
  // 인그레스/프록시가 만든 401(백엔드까지 못 간 경우)은 본문이 JSON이 아니라서
  // detail을 못 읽는다 -- request()가 이때 http_401을 합성한다.
  http_401: "인증이 필요합니다",
  http_500: "서버 오류가 발생했습니다",
  http_503: "서비스를 일시적으로 사용할 수 없습니다",
  build_node_not_set: "빌드 노드가 지정되지 않았습니다 — 컨트롤 상태에서 먼저 지정하세요",
  build_in_progress: "이미 진행 중인 빌드가 있습니다",
  unknown_image: "빌드할 이미지를 선택해 주세요",
  invalid_git_ref: "git ref 형식이 올바르지 않습니다",
  // 슬라이스 21 동기 검증 2종(제출 시 422). reasonCodes.json 과 같은 커밋.
  invalid_repo_url: "저장소 URL에서 호스트를 읽을 수 없습니다 — https://호스트/경로 형식으로 입력하세요",
  build_node_report_stale: "빌드 노드의 에이전트 리포트가 오래되었습니다 — 노드가 내려갔을 수 있습니다",
  build_not_found: "빌드를 찾을 수 없습니다",
  build_failed: "빌드가 실패했습니다 — 로그를 확인하세요",
  submit_failed: "빌드 파드를 만들지 못했습니다",
  // 빌드 폴링과 잡 로그 조회 409(슬라이스 25 재사용)가 같은 코드를 낸다 --
  // 공유 코드의 문구는 문맥 중립이어야 한다("빌드" 금지, §2.2).
  poll_failed: "상태를 확인하지 못했습니다",
  unknown_build_node: "등록된 에이전트 노드 중에서 선택해 주세요",
  build_timeout: "빌드가 제한 시간을 넘겨 중단되었습니다",
  // 슬라이스 21 빌드 프리플라이트/회수 세분화. reasonCodes.json 과 같은 커밋 --
  // 양방향 계약(reasonCodes.test.ts / test_reason_codes_coverage.py) 조건이다.
  build_stuck_pending: "빌드 파드가 스케줄되지 못한 채 제한 시간을 넘겼습니다 — 빌드 노드 상태·여유를 확인하세요",
  build_preflight_timeout: "빌드 적합성 확인이 제한 시간을 넘겼습니다 — 빌드 노드가 내려갔거나 프로브가 스케줄되지 못했을 수 있습니다",
  build_preflight_failed: "빌드 적합성 확인에 실패했습니다 — 로그를 확인하세요",
  build_node_no_egress: "빌드 노드에서 인터넷으로 나갈 수 없습니다 — 운영자가 인터넷을 아직 열지 않았을 수 있습니다",
  build_registry_unreachable: "빌드 노드에서 이미지 레지스트리에 연결할 수 없습니다",
  build_node_disk_low: "빌드 노드의 디스크 여유가 부족합니다 — 로그의 실측 수치를 확인하세요",
  invalid_build_ref: "빌드 참조가 올바르지 않습니다 — 관리자에게 문의하세요",
  rollout_in_progress: "이미 진행 중인 롤아웃이 있습니다",
  patch_failed: "워크로드 패치에 실패했습니다 — 컨트롤러 RBAC/로그를 확인하세요",
  observe_failed: "워크로드 상태를 읽지 못했습니다",
  rollout_failed: "롤아웃이 실패했습니다",
  rollout_timeout: "롤아웃이 제한 시간을 넘겨 실패했습니다",
  rollout_aborted: "앞 컴포넌트 실패로 롤아웃이 중단되었습니다",
  workload_not_found: "대상 워크로드를 찾을 수 없습니다",
  // 두 곳에서 난다: 롤아웃 컨트롤러가 모르는 component 행을 만났을 때(내부 오류)와
  // 릴리스 제출 폼이 빈 선택/중복 선택을 보냈을 때 -- 양쪽에서 읽히는 문구를 쓴다.
  unknown_component: "알 수 없는 컴포넌트이거나 중복 선택입니다",
  unknown_tag: "레지스트리에 없는 태그입니다",
  same_tag: "현재 태그와 같습니다 — IfNotPresent라 재적용해도 아무 일도 일어나지 않습니다",
  // 프론트 전용 코드다 -- 백엔드는 이걸 detail로 내지 않고 targets 응답의
  // registry_ok=false 로만 알린다(태그 목록은 빈 배열이 되고 존재 검증이 꺼진다).
  // 그래도 문구를 여기에 두는 이유: 화면이 한국어를 하드코딩하지 않는다는 규칙을
  // 릴리스 화면만 예외로 두면 문구가 두 곳으로 갈라진다.
  registry_unreachable: "레지스트리에 연결할 수 없어 태그 목록이 비어 있습니다",
  // 프론트 전용 코드다(registry_unreachable 과 같은 관례) -- 백엔드는 detail 이
  // 아니라 제출 202 응답의 tag_verified:false 필드로 알린다(슬라이스 28).
  tag_unverified: "레지스트리가 응답하지 않아 태그 존재를 확인하지 못한 채 접수되었습니다 — 태그가 틀리면 ImagePullBackOff 로 드러납니다",
  // 아티팩트 base 설정(슬라이스 18). 정규화 3종 + 즉석 검증 3종 + 잠금 1종 --
  // reasonCodes.json 과 함께 갱신해야 양방향 커버리지 테스트가 초록이다.
  artifact_base_not_absolute: "아티팩트 경로는 절대 경로여야 합니다",
  artifact_base_traversal: "아티팩트 경로에 .. 세그먼트를 쓸 수 없습니다",
  artifact_base_scheme_in_path: "경로 중간에 file:// 를 쓸 수 없습니다",
  artifact_base_missing: "경로가 존재하지 않습니다",
  artifact_base_not_directory: "경로가 디렉터리가 아닙니다",
  artifact_base_not_writable: "경로에 쓸 수 없습니다",
  artifact_base_locked: "잡 이력이 있어 아티팩트 경로를 바꿀 수 없습니다",
  // 빌드 실패 세분화(슬라이스 21 잔여). 지금까지 전부 build_failed 로 뭉개져
  // 운영자가 로그 단절만 보고 OOM 을 추측해야 했다 — 대응이 서로 다르다.
  build_oom_killed: "빌드가 메모리 한도를 넘어 종료됐습니다 — 빌드 봉투를 늘리거나 빌드를 줄이세요",
  build_evicted: "빌드가 노드에서 축출됐습니다 — 빌드 노드의 디스크·메모리를 확인하세요",
};

/** 사유 코드를 사용자에게 보일 문구로. 매핑이 없으면 원시 코드를 그대로 돌려준다
 *  -- 영문 코드라도 보이는 편이 "알 수 없는 오류"보다 진단에 쓸모 있다.
 *  stepper 는 f"{prefix}:{ExecutionError.reason_code}" 형태의 복합 코드를 만들므로
 *  정확 일치 조회만으로는 영원히 매핑되지 않는다 -- 접두를 번역하고 접미를 병기한다. */
export function reasonText(code: string | null | undefined): string {
  if (!code) return "";
  const direct = REASON_MESSAGES[code];
  if (direct) return direct;
  const sep = code.indexOf(":");
  if (sep > 0) {
    const prefix = REASON_MESSAGES[code.slice(0, sep)];
    if (prefix) return `${prefix} (${code.slice(sep + 1)})`;
  }
  return code;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    // 파싱은 한 벌이어야 한다 -- 과거 401 전용 분기가 같은 파싱을 복제해
    // 폴백·문구가 구조적으로 갈라질 수 있었다. 비 JSON 본문(인그레스/프록시가
    // 백엔드 앞에서 만든 401 등)은 detail 이 없으므로 http_<status> 를 합성한다.
    let code = `http_${res.status}`;
    try {
      const detail = (await res.json()).detail;
      code = typeof detail === "string" ? detail : `http_${res.status}`;
    } catch { /* noop */ }
    // dms:unauthorized 는 401 전용 계약이다 -- AuthContext 가 me 쿼리를 무효화하는
    // 소비자라, 403 등에서도 발화하면 권한 없는 화면마다 로그인으로 튕긴다.
    if (res.status === 401) window.dispatchEvent(new CustomEvent("dms:unauthorized"));
    throw new ApiError(res.status, code, reasonText(code));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const apiGet = <T>(path: string) => request<T>("GET", path);
export const apiSend = <T>(method: string, path: string, body?: unknown) =>
  request<T>(method, path, body);
