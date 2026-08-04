import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import { isTerminal } from "../../lib/jobState";
import type { RequestRow, RequestDetail, DataJob } from "../../lib/types";

export const useRequests = () =>
  useQuery({ queryKey: ["requests"], queryFn: () => apiGet<RequestRow[]>("/api/user/requests"),
            refetchInterval: 3000 });

export const useRequest = (id: string) =>
  useQuery({ queryKey: ["request", id], queryFn: () => apiGet<RequestDetail>(`/api/user/requests/${id}`) });

export const useRequestJobs = (id: string) =>
  useQuery({
    queryKey: ["request", id, "jobs"],
    queryFn: () => apiGet<DataJob[]>(`/api/user/requests/${id}/jobs`),
    refetchInterval: (q) => {
      const jobs = q.state.data as DataJob[] | undefined;
      return jobs && jobs.some((j) => !isTerminal(j.state)) ? 2000 : false;
    },
  });

export interface SyncBody {
  source_storage: string; source: string;
  destination_storage: string; destination: string;
  options: Record<string, boolean | number>; priority: string;
}
export const useSubmitSync = () =>
  useMutation({
    mutationFn: (b: SyncBody) =>
      apiSend<{ request_id: string; state: string }>("POST", "/api/user/requests",
        { operation: "sync", ...b }),
  });

export function useConfirmJob(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { jobId: string; fingerprint: string }) =>
      apiSend("POST", `/api/user/jobs/${v.jobId}:confirm`, { fingerprint: v.fingerprint }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["request", requestId, "jobs"] });
      qc.invalidateQueries({ queryKey: ["request", requestId] });
    },
  });
}
export function useCancelJob(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => apiSend("POST", `/api/user/jobs/${jobId}:cancel`),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["request", requestId, "jobs"] });
      qc.invalidateQueries({ queryKey: ["request", requestId] });
    },
  });
}
