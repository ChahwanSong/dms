import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Account } from "../../lib/types";
export const useAccounts = () =>
  useQuery({ queryKey: ["accounts"], queryFn: () => apiGet<Account[]>("/api/admin/accounts") });
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
