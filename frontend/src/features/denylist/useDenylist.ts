import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { DenyEntry } from "../../lib/types";
export const useDenylist = () =>
  useQuery({ queryKey: ["denylist"], queryFn: () => apiGet<DenyEntry[]>("/api/admin/identity-denylist") });
export const useDeny = () => {
  const qc = useQueryClient();
  // subject 는 사용자 입력이다 -- #(fragment 절단)·?(쿼리 흡수)가 경로를 바꿔
  // 다른 대상에 PUT/DELETE 가 나가는 wrong-target 이 실결함이라 세그먼트 단위로
  // 인코딩한다. subject_type 은 select 고정값이지만 규칙을 한 벌로 유지한다.
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string; reason: string | null }) =>
    apiSend("PUT", `/api/admin/identity-denylist/${encodeURIComponent(v.subject_type)}/${encodeURIComponent(v.subject)}`, { reason: v.reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
export const useAllow = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string }) =>
    apiSend("DELETE", `/api/admin/identity-denylist/${encodeURIComponent(v.subject_type)}/${encodeURIComponent(v.subject)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
