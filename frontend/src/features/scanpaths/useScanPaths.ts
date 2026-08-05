import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ScanPath, ScanPathStats } from "../../lib/types";

export const useScanPaths = () =>
  useQuery({ queryKey: ["scan-paths"],
             queryFn: () => apiGet<ScanPath[]>("/api/user/scan-paths") });

export const useAddScanPath = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { storage_name: string; path: string }) =>
      apiSend<ScanPath>("POST", "/api/user/scan-paths", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scan-paths"] }),
  });
};

export const useDeleteScanPath = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiSend("DELETE", `/api/user/scan-paths/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scan-paths"] }),
  });
};

export const useScanPathStats = (id: number, enabled: boolean) =>
  useQuery({
    queryKey: ["scan-path-stats", id],
    queryFn: () => apiGet<ScanPathStats>(`/api/user/scan-paths/${id}/stats`),
    enabled,
  });
