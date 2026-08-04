import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { AuditEntry } from "../../lib/types";
export const useAuditLog = () =>
  useQuery({ queryKey: ["audit"], queryFn: () => apiGet<AuditEntry[]>("/api/admin/audit-log") });
