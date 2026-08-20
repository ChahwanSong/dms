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
    onSuccess: () => qc.clear(),
  });
}

// 계정 셀프서비스(2026-08-20): 인증번호(4자리·5분 TTL) 발급 -> 검증 -> 완료.
// 이메일은 <아이디>@도메인 파생이라 서버가 계산해 돌려준다. stub 메일러(현행)
// 에선 stub_code 가 에코된다 -- 실메일 백엔드로 바뀌면 이 필드가 사라진다.
export interface CodeIssued { email: string; expires_in_seconds: number; stub_code?: string }
export const useRequestCode = () =>
  useMutation({
    mutationFn: (b: { username: string; purpose: "signup" | "password_reset" }) =>
      apiSend<CodeIssued>("POST", "/api/auth/verification-codes", b),
  });

export const useSignup = () =>
  useMutation({
    mutationFn: (b: { username: string; password: string; code: string }) =>
      apiSend<{ username: string }>("POST", "/api/auth/signup", b),
  });

export const usePasswordReset = () =>
  useMutation({
    mutationFn: (b: { username: string; password: string; code: string }) =>
      apiSend<{ username: string }>("POST", "/api/auth/password-reset", b),
  });

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", "/api/auth/logout"),
    onSettled: () => qc.clear(),
  });
}
