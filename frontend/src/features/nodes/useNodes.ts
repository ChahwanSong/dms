import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { NodeInfo, NodeReport } from "../../lib/types";
export const useNodes = () =>
  useQuery({ queryKey: ["nodes"], queryFn: () => apiGet<NodeInfo[]>("/api/admin/nodes"),
             refetchInterval: 10000 });
export const useNodeReports = (name: string, enabled: boolean) =>
  useQuery({ queryKey: ["node-reports", name],
             queryFn: () => apiGet<NodeReport[]>(`/api/admin/nodes/${name}/reports`),
             enabled });
