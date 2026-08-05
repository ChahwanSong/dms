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
  invalid_policy: "정책 값이 올바르지 않습니다",
  invalid_denylist_subject_type: "대상 유형이 올바르지 않습니다",
  policy_not_found: "정책을 찾을 수 없습니다",
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
    try { code = (await res.json()).detail ?? code; } catch { /* noop */ }
    throw new ApiError(401, code, REASON_MESSAGES[code] ?? code);
  }
  if (!res.ok) {
    let code = `http_${res.status}`;
    try { code = (await res.json()).detail ?? code; } catch { /* noop */ }
    throw new ApiError(res.status, code, REASON_MESSAGES[code] ?? code);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const apiGet = <T>(path: string) => request<T>("GET", path);
export const apiSend = <T>(method: string, path: string, body?: unknown) =>
  request<T>(method, path, body);
