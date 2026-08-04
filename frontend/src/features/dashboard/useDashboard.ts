import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { Node } from "../../lib/types";
export const useNodes = () =>
  useQuery({ queryKey: ["nodes"], queryFn: () => apiGet<Node[]>("/api/admin/nodes"),
            refetchInterval: 5000 });
