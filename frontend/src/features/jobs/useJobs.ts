import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
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
