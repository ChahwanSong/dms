import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import { postWithSealedPassword } from "../../lib/passwordTransport";
import type { Me } from "../../lib/types";

export const useMe = () =>
  useQuery({ queryKey: ["auth", "me"], queryFn: () => apiGet<Me>("/api/auth/me") });

// 비밀번호를 보내는 훅은 전부 postWithSealedPassword 를 쓴다(2026-09-07 전송 봉인)
// -- 평문 password 는 와이어에 실리지 않는다. 429 login_rate_limited 는 request()
// 가 ApiError 로 올리고 화면이 reasonText 로 보여준다(별도 처리 없음).
export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { username: string; password: string }) =>
      postWithSealedPassword<Me>("/api/auth/login", "login", b),
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
      postWithSealedPassword<{ username: string }>("/api/auth/signup", "signup", b),
  });

export const usePasswordReset = () =>
  useMutation({
    mutationFn: (b: { username: string; password: string; code: string }) =>
      postWithSealedPassword<{ username: string }>(
        "/api/auth/password-reset", "password_reset", b),
  });

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", "/api/auth/logout"),
    onSettled: () => qc.clear(),
  });
}
