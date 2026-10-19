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
  // 등록하면 같은 이름의 옛 scan 기록이 더는 「미등록」이 아니다(사용량 분석 미등록 스토리지 목록, 2026-10-12) -- 사용량
  // 분석의 스토리지 탭(["usage-storages"], 2026-10-20)도 새 이름의 빈 탭이 생기거나 미등록 탭이 등록 탭으로 옮겨 간다.
  return useMutation({ mutationFn: (b: StorageCreateBody) => apiSend("POST", "/api/admin/storages", b),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["storages"] }),
                                  qc.invalidateQueries({ queryKey: ["usage-unregistered"] }),
                                  qc.invalidateQueries({ queryKey: ["usage-storages"] })]) });
};
export const useUpdateStorage = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { name: string; body: StorageUpdateBody }) =>
    apiSend("PUT", `/api/admin/storages/${v.name}`, v.body),
    // 사용량 분석 탭의 메타 줄(마운트·타입)도 요약에서 읽는다.
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: ["storages"] }),
                                  qc.invalidateQueries({ queryKey: ["usage-storages"] })]) });
};
export const useDeleteStorage = () => {
  const qc = useQueryClient();
  // 삭제는 서버가 그 스토리지가 낀 sync 허용 쌍도 함께 지운다(StoragesRepository.delete) -- 쌍 목록도
  // 다시 읽어야 정책 화면이 사라진 쌍을 "사용자 미적용" 으로 남겨 보이지 않는다.
  return useMutation({ mutationFn: (name: string) => apiSend("DELETE", `/api/admin/storages/${name}`),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: ["storages"] }),
      qc.invalidateQueries({ queryKey: ["sync-pairs"] }),
      qc.invalidateQueries({ queryKey: ["user-sync-pairs"] }),
      qc.invalidateQueries({ queryKey: ["usage-unregistered"] }),   // 지운 스토리지의 scan 기록이 「미등록」이 된다
      qc.invalidateQueries({ queryKey: ["usage-storages"] }),       // 그 탭이 미등록 탭으로 옮겨 가거나(기록 있음) 사라진다
    ]) });
};
