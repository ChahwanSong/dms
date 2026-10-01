import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiSend } from "../../lib/api";
import { sendWithSealedSecret } from "../../lib/passwordTransport";
import type { MailCheckResult, MailInfo, MailSettings, MailSettingsFields } from "../../lib/types";

// 포탈 메일 설정(2026-10-01, routes_mail_settings). 릴레이 토큰은 비밀번호와 같은 봉인 통로로만 보낸다
// (용도 mail_relay_token, AAD 사용자 자리 "mail_settings" -- 서버 SEAL_PURPOSE/SEAL_SUBJECT 와 같은 값).
export const MAIL_TOKEN_PURPOSE = "mail_relay_token" as const;
export const MAIL_TOKEN_SUBJECT = "mail_settings";

export const useMailSettings = () =>
  useQuery({ queryKey: ["mail-settings"],
             queryFn: () => apiGet<MailSettings>("/api/admin/mail-settings") });

// 저장 두 갈래: 칸 저장(fields 전부 + 새 키가 있으면 봉인) / 키만 지우기(clear_relay_token 만 -- 편집 중인
// 다른 칸을 함께 저장하지 않는다).
export type SaveMailSettings =
  // 보낸 칸만 바뀐다(null = 포탈 값을 지워 env·기본값으로). 키는 비면 그대로. 키를 보낼 때는 화면이 보던 적용
  // 주소(seenRelayUrl)를 같이 실어, 그 사이 다른 관리자가 주소를 바꿨으면 서버가 409 mail_settings_changed 로 막는다.
  | { fields: Partial<MailSettingsFields>; token?: string; seenRelayUrl?: string }
  | { clearToken: true };

export const useSaveMailSettings = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: SaveMailSettings) => {
      if ("clearToken" in v)
        return apiSend<MailSettings>("PUT", "/api/admin/mail-settings", { clear_relay_token: true });
      const body: Record<string, unknown> = { ...v.fields };
      if (v.token && v.token.trim() !== "") {
        if (v.seenRelayUrl !== undefined) body.seen_relay_url = v.seenRelayUrl;
        return sendWithSealedSecret<MailSettings>("PUT", "/api/admin/mail-settings", MAIL_TOKEN_PURPOSE,
                                                  MAIL_TOKEN_SUBJECT, "relay_token_enc", v.token.trim(), body);
      }
      return apiSend<MailSettings>("PUT", "/api/admin/mail-settings", body);
    },
    onSuccess: (data) => { qc.setQueryData(["mail-settings"], data); },
    // 다른 관리자가 그 사이 바꿨다(409) -- 화면을 서버 값으로 다시 불러온다(편집 중인 칸은 화면이 칸별로 지킨다).
    onError: (e) => {
      if (e instanceof ApiError && e.code === "mail_settings_changed")
        void qc.invalidateQueries({ queryKey: ["mail-settings"] });
    },
  });
};

export const useMailHealthCheck = () =>
  useMutation({ mutationFn: () => apiSend<MailCheckResult>("POST", "/api/admin/mail-settings/health-check") });

export const useMailTestSend = () =>
  useMutation({ mutationFn: (to: string) =>
    apiSend<MailCheckResult>("POST", "/api/admin/mail-settings/test-mail", { to }) });

// 로그인 전 화면(가입·재설정)의 "인증번호가 <아이디>@<도메인> 으로 전송됩니다" -- 도메인 하드코딩 대신.
export const useMailInfo = () =>
  useQuery({ queryKey: ["mail-info"], queryFn: () => apiGet<MailInfo>("/api/auth/mail-info"),
             staleTime: 5 * 60_000 });
