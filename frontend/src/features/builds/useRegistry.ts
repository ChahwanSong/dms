import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiSend } from "../../lib/api";
import type { RegistryImages } from "../../lib/types";

// 레지스트리 이미지 목록. 폴링하지 않는다 -- 태그는 빌드/삭제 뮤테이션이 무효화할
// 때만 바뀐다(그 사이 자동으로 늘거나 줄지 않는다). registry.py 가 캐시를 두지
// 않는 것과 같은 이유로 여기서도 stale 을 짧게 본다.
export const useRegistryImages = () =>
  useQuery({
    queryKey: ["registry-images"],
    queryFn: () => apiGet<RegistryImages>("/api/admin/registry/images"),
  });

export interface RegistryTarget { repository: string; tag: string; }
export interface BulkDeleteRegistryResult {
  ok: RegistryTarget[]; failed: { target: RegistryTarget; message: string }[];
}
const _deleteImage = (t: RegistryTarget) =>
  apiSend("DELETE", `/api/admin/registry/images/${t.repository}/${encodeURIComponent(t.tag)}`);

// 다중 선택 삭제. 빌드 이력·배치 목록과 같은 계약(Promise.allSettled 로 부분 실패를
// 데이터로). 사용 중 태그는 서버가 409 로 거절하므로, 한 건 거절이 나머지 삭제를
// 막지 않는다.
export const useDeleteRegistryImages = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (targets: RegistryTarget[]): Promise<BulkDeleteRegistryResult> => {
      const settled = await Promise.allSettled(targets.map(_deleteImage));
      const r: BulkDeleteRegistryResult = { ok: [], failed: [] };
      settled.forEach((s, i) => {
        if (s.status === "fulfilled") r.ok.push(targets[i]);
        else r.failed.push({ target: targets[i], message: s.reason instanceof ApiError
                             ? s.reason.message : String(s.reason) });
      });
      return r;
    },
    // 목록 화면에서 쏘는 삭제라 목록 쿼리는 활성 -- invalidate 로 재조회가 돌고
    // 프라미스를 돌려줘 그 착지까지 기다린다(결과 문구가 뜰 때 태그가 이미 사라져 있다).
    onSettled: () => qc.invalidateQueries({ queryKey: ["registry-images"] }),
  });
};
