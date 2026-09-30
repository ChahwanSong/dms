import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Storage } from "../../lib/types";
export const useStorages = () =>
  useQuery({ queryKey: ["storages"], queryFn: () => apiGet<Storage[]>("/api/admin/storages") });
// 사용 범위 플래그(storageScope.flagsFor). user_enabled 생략 = 서버가 현재 값 유지.
export interface StorageCreateBody {
  storage_name: string; mount_path: string; managed_root: string; backend_type: string;
  enabled?: boolean; user_enabled?: boolean;
}
export interface StorageUpdateBody {
  mount_path: string; managed_root: string; backend_type: string; enabled: boolean;
  user_enabled?: boolean;
}
export const useCreateStorage = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: StorageCreateBody) => apiSend("POST", "/api/admin/storages", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storages"] }) });
};
export const useUpdateStorage = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { name: string; body: StorageUpdateBody }) =>
    apiSend("PUT", `/api/admin/storages/${v.name}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storages"] }) });
};
export const useDeleteStorage = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (name: string) => apiSend("DELETE", `/api/admin/storages/${name}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storages"] }) });
};
