import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { ArtifactEntry, ArtifactFile, JobLogs } from "../../lib/types";

export const useArtifacts = (jobId: string) =>
  useQuery({ queryKey: ["artifacts", jobId],
             queryFn: () => apiGet<ArtifactEntry[]>(`/api/user/jobs/${jobId}/artifacts`) });

export const useArtifactFile = (jobId: string, phase: string, name: string, enabled: boolean) =>
  useQuery({
    queryKey: ["artifact", jobId, phase, name],
    queryFn: () => apiGet<ArtifactFile>(`/api/user/jobs/${jobId}/artifacts/${phase}/${name}`),
    enabled,
  });

export const useJobLogs = (jobId: string, phase: string, enabled: boolean) =>
  useQuery({
    queryKey: ["joblogs", jobId, phase],
    queryFn: () => apiGet<JobLogs>(`/api/user/jobs/${jobId}/logs?phase=${phase}`),
    enabled,
  });
