import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { UserStorage } from "../../lib/types";
export const useUserStorages = () =>
  useQuery({ queryKey: ["user-storages"],
             queryFn: () => apiGet<UserStorage[]>("/api/user/storages") });
