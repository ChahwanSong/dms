import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Storage } from "../../lib/types";
export const useStorages = () =>
  useQuery({ queryKey: ["storages"], queryFn: () => apiGet<Storage[]>("/api/admin/storages") });
export interface StorageCreateBody { storage_name: string; mount_path: string; managed_root: string; backend_type: string; }
export interface StorageUpdateBody { mount_path: string; managed_root: string; backend_type: string; enabled: boolean; }
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
