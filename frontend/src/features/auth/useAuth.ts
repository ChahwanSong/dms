import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Me } from "../../lib/types";

export const useMe = () =>
  useQuery({ queryKey: ["auth", "me"], queryFn: () => apiGet<Me>("/api/auth/me") });

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { username: string; password: string }) =>
      apiSend<Me>("POST", "/api/auth/login", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["auth", "me"] }),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", "/api/auth/logout"),
    onSuccess: () => qc.clear(),
  });
}
