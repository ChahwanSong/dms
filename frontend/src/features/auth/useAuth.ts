import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiSend } from "../../lib/api";
import { postWithSealedPassword } from "../../lib/passwordTransport";
import { resetNavCollapsed } from "../../lib/navState";
import type { Me } from "../../lib/types";

// /me 만 짧게 재시도한다(2026-11-04 기능 점검 #2): 5xx·네트워크 오류(fetch 거부 -- 아래 status 0)는 최대 2회, 0.5초·1초
// 뒤에 -- 롤아웃·연결 끊김 같은 한 번짜리 실패를 흡수하고, 그래도 실패하면 약 1.5초 안에 RequireRole·Home 의 「세션 확인에
// 실패했습니다 + 다시 시도」로 넘긴다(수십 초짜리 DB 전환은 재시도로 버티지 않는다 -- 화면이 정직하게 말하고 사람이 다시 누른다).
// 4xx 는 재시도하지 않는다: 401 은 진짜 「로그인 필요」이고(재시도는 dms:unauthorized → me 무효화와 겹쳐 요청만 늘린다),
// 다른 4xx 도 다시 물어 답이 바뀌지 않는다. 전역 기본값(queryClient retry:false)은 그대로다 -- 화면 조회들은 자기 오류를 보인다.
// 2026-11-11: request() 가 fetch 거부를 ApiError(status 0, network_error)로 바꾸므로 status 0(응답 없음)도 네트워크 쪽으로 재시도한다
// -- `status < 500` 만 보면 네트워크 오류가 4xx 처럼 재시도 없이 바로 오류 화면으로 갔다.
export const ME_RETRY_LIMIT = 2;
export const meRetry = (failureCount: number, error: unknown): boolean =>
  !(error instanceof ApiError && error.status > 0 && error.status < 500) && failureCount < ME_RETRY_LIMIT;
export const meRetryDelay = (failureCount: number): number => 500 * 2 ** failureCount;

export const useMe = () =>
  useQuery({
    queryKey: ["auth", "me"], queryFn: () => apiGet<Me>("/api/auth/me"),
    retry: meRetry, retryDelay: meRetryDelay,
  });

// 아이디 앞뒤 공백은 훅에서 지운다(2026-11-11) -- 인증번호 받기는 trim 한 값으로 발급받는데 가입·재설정 제출은 원문을 보내,
// 자동완성·복사로 끝 공백이 붙은 아이디는 서버가 코드를 못 찾아 「발급된 인증번호가 없습니다」만 끝없이 반복했다(로그인도 같은
// 이유로 401). 발급·제출·로그인을 이 한 함수로 맞춘다(계정 아이디 규칙에 공백은 없다). 봉인 AAD 도 이 값이라 서버 복호와 같다.
const withTrimmedUsername = <T extends { username: string }>(b: T): T => ({ ...b, username: b.username.trim() });

// 비밀번호를 보내는 훅은 전부 postWithSealedPassword 를 쓴다(2026-09-07 전송 봉인)
// -- 평문 password 는 와이어에 실리지 않는다. 429 login_rate_limited·503 login_busy 는 request()
// 가 ApiError(retryAfter 포함)로 올리고 화면이 apiErrorText 로 보여준다(별도 처리 없음). 서버는 과부하에 거절 대신
// 기다리게 하므로(2026-11-03) 이 요청은 수 초 걸릴 수 있다 -- 화면이 2초 뒤 대기 안내를 띄운다.
export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { username: string; password: string }) =>
      postWithSealedPassword<Me>("/api/auth/login", "login", withTrimmedUsername(b)),
    // 사이드바 접힘 리셋(2026-09-30): 로그인 직후엔 메뉴가 전부 펼쳐진다(lib/navState).
    onSuccess: () => { qc.clear(); resetNavCollapsed(); },
  });
}

// 계정 셀프서비스(2026-08-20): 인증번호(6자리(2026-11-03, 옛 4자리 대체)·5분 TTL) 발급 -> 검증 -> 완료.
// 이메일은 <아이디>@도메인 파생이라 서버가 계산해 돌려준다. stub 메일러(현행)
// 에선 stub_code 가 에코된다 -- 실메일 백엔드로 바뀌면 이 필드가 사라진다.
// delivery_uncertain(2026-10-01): 릴레이에 요청은 갔는데 응답을 못 받음 -- 서버는 코드를 저장했고 메일이 늦게
// 도착할 수 있다(오지 않으면 다시 요청).
export interface CodeIssued {
  email: string; expires_in_seconds: number; stub_code?: string; delivery_uncertain?: boolean;
}
export const useRequestCode = () =>
  useMutation({
    mutationFn: (b: { username: string; purpose: "signup" | "password_reset" }) =>
      apiSend<CodeIssued>("POST", "/api/auth/verification-codes", withTrimmedUsername(b)),
  });

export const useSignup = () =>
  useMutation({
    mutationFn: (b: { username: string; password: string; code: string }) =>
      postWithSealedPassword<{ username: string }>("/api/auth/signup", "signup", withTrimmedUsername(b)),
  });

export const usePasswordReset = () =>
  useMutation({
    mutationFn: (b: { username: string; password: string; code: string }) =>
      postWithSealedPassword<{ username: string }>(
        "/api/auth/password-reset", "password_reset", withTrimmedUsername(b)),
  });

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", "/api/auth/logout"),
    onSettled: () => { qc.clear(); resetNavCollapsed(); },
  });
}
