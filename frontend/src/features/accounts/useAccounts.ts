import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Account } from "../../lib/types";
export const useAccounts = () =>
  useQuery({ queryKey: ["accounts"], queryFn: () => apiGet<Account[]>("/api/admin/accounts") });
// 운영자 계정 생성(2026-08-20, 사용자 결정): 세션 admin 경로 -- 인증번호 없이
// 즉시 생성(관리자 권한이 곧 승인), 이메일은 서버가 <아이디>@도메인 파생 저장.
export const useCreateAccount = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { username: string; password: string; role: string }) =>
    apiSend("POST", "/api/admin/accounts", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }) });
};

export const useSetRole = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { username: string; role: string }) =>
    apiSend("PUT", `/api/admin/accounts/${v.username}/role`, { role: v.role }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }) });
};
export const useSetDisabled = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { username: string; disabled: boolean }) =>
    apiSend("PUT", `/api/admin/accounts/${v.username}/disabled`, { disabled: v.disabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }) });
};
// 하드 삭제(슬라이스 19). 서버가 자기 삭제·마지막 관리자·비종단 요청을 409 로 다시
// 강제하므로, 훅은 그 에러를 그대로 올려 다이얼로그가 한국어 사유를 표면화한다.
export const useDeleteAccount = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (username: string) =>
    apiSend("DELETE", `/api/admin/accounts/${username}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }) });
};
