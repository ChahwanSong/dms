import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { SyncPair } from "../../lib/types";

// 사용자 sync 허용 쌍(2026-09-30 사용자 결정: "기본 전부 불가에 허용 쌍들을 추가"). 관리자 편집은
// 정책 화면(SyncPairsPanel). 강제는 서버의 제출·계획·컨펌 게이트(sync_pair_allowed)다. 단일 작업 화면의 소스·목적지 상호
// 필터(GET /api/user/sync-pairs)는 2026-10-26 LDAP 정리로 지웠다 -- 비면제 사용자는 PV 주소만 쓰고 PV↔PV 는 쌍을 보지 않는다(설계 D14).
export type SyncPairKey = Pick<SyncPair, "source_storage" | "destination_storage">;

export const useSyncPairs = () =>
  useQuery({ queryKey: ["sync-pairs"], queryFn: () => apiGet<SyncPair[]>("/api/admin/sync-pairs") });

// 성공·실패 모두 다시 읽는다(실패 = 다른 관리자가 먼저 바꿨을 수 있다 -- 화면이 서버 진실로 돌아온다).
// 무효화 promise 를 돌려줘 mutateAsync 가 재조회 뒤에 끝나게 한다 -- 편집기가 "요청 중" 표시를 걷을 때
// 옛 데이터로 체크가 한 번 되돌았다 다시 켜지는 깜빡임이 없다.
const refresh = (qc: QueryClient) => qc.invalidateQueries({ queryKey: ["sync-pairs"] });

export const useAddSyncPair = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (k: SyncPairKey) => apiSend<SyncPair>("POST", "/api/admin/sync-pairs", k),
    onSettled: () => refresh(qc) });
};

export const useRemoveSyncPair = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (k: SyncPairKey) => apiSend("DELETE",
      `/api/admin/sync-pairs/${encodeURIComponent(k.source_storage)}/${encodeURIComponent(k.destination_storage)}`),
    onSettled: () => refresh(qc) });
};
