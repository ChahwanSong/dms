export const REASON_MESSAGES: Record<string, string> = {
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
  artifact_forbidden: "허용되지 않은 아티팩트 경로입니다",
  invalid_phase: "알 수 없는 실행 단계입니다",
  invalid_artifact_name: "아티팩트 이름이 올바르지 않습니다",
  log_ref_not_found: "이 단계의 로그 참조가 없습니다",
  log_not_available: "이 단계는 파드 로그를 제공하지 않습니다 — 아티팩트를 확인하세요",
};

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
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("dms:unauthorized"));
    let code = "http_401";
    try {
      const detail = (await res.json()).detail;
      code = typeof detail === "string" ? detail : "http_401";
    } catch { /* noop */ }
    throw new ApiError(401, code, REASON_MESSAGES[code] ?? code);
  }
  if (!res.ok) {
    let code = `http_${res.status}`;
    try {
      const detail = (await res.json()).detail;
      code = typeof detail === "string" ? detail : `http_${res.status}`;
    } catch { /* noop */ }
    throw new ApiError(res.status, code, REASON_MESSAGES[code] ?? code);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const apiGet = <T>(path: string) => request<T>("GET", path);
export const apiSend = <T>(method: string, path: string, body?: unknown) =>
  request<T>(method, path, body);
