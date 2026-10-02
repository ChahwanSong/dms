import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { PortalInfo } from "../../lib/types";

// 포탈 표시 이름(2026-10-02 사용자 요청): 메인 이름은 여기 상수, 서브네임("SSC", "DAI-CAE" 등)은 운영자가
// 관리 → 포탈 설정에서 정한다(서버 control_state.portal_subtitle, routes_portal). 사이드바·로그인 화면·브라우저
// 탭 제목이 이 한 곳을 읽는다.
export const PORTAL_NAME = "AI Storage Portal";

/** "AI Storage Portal - SSC" (서브네임이 없으면 메인 이름만). */
export function portalTitle(subtitle: string | null | undefined): string {
  return subtitle ? `${PORTAL_NAME} - ${subtitle}` : PORTAL_NAME;
}

// 공개 조회(로그인 전 화면도 그린다). 실패하면 서브네임 없이 그린다 -- 이름 하나 때문에 화면이 막히지 않게.
export const usePortalInfo = () =>
  useQuery({ queryKey: ["portal-info"], queryFn: () => apiGet<PortalInfo>("/api/portal-info"),
             staleTime: 5 * 60_000, retry: false });

export const useSavePortalSubtitle = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (subtitle: string | null) =>
      apiSend<PortalInfo>("PUT", "/api/admin/portal-settings", { subtitle }),
    onSuccess: (data) => { qc.setQueryData(["portal-info"], data); },
  });
};

/** 브라우저 탭 제목을 "메인 - 서브" 로 맞춘다(셸·로그인 화면이 부른다). */
export function usePortalDocumentTitle() {
  const subtitle = usePortalInfo().data?.subtitle ?? null;
  useEffect(() => { document.title = portalTitle(subtitle); }, [subtitle]);
  return subtitle;
}
