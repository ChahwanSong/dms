import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { StorageRoots } from "../../lib/storagePaths";
import type { UserStorage } from "../../lib/types";
export const useUserStorages = () =>
  useQuery({ queryKey: ["user-storages"],
             queryFn: () => apiGet<UserStorage[]>("/api/user/storages") });

// 스토리지 이름 → 관리 디렉토리(managed_root) 맵. 절대경로를 조합해 보여주는 화면
// (요청 상세·배치 항목·대시보드 최근 작업)이 공유한다 — 같은 쿼리 키를 쓰므로
// 화면당 요청은 여전히 한 번이다.
//
// **비관리자에겐 빈 맵**이다: 서버가 managed_root 를 관리자 응답에만 싣기 때문에
// (routes_storages) 여기서 따로 역할을 판정하지 않는다 — 화면이 신원을 흉내 내
// 판정하면 서버 계약과 두 벌이 되고, 그 순간 한쪽이 거짓말한다. 조회 실패·구형
// 서버도 같은 빈 맵이라 절대경로 줄이 그냥 생기지 않는다(거짓 경로 금지).
export const useStorageRoots = (): StorageRoots => {
  const q = useUserStorages();
  return useMemo(() => Object.fromEntries(
    (q.data ?? []).flatMap((s) =>
      typeof s.managed_root === "string" && s.managed_root !== ""
        ? [[s.storage_name, s.managed_root] as const] : [])), [q.data]);
};

// 스토리지 이름 → backend_type(등록 라벨) 맵 -- 보조 그룹 주의문(lib/groupCaveats)이 잡·폼의 스토리지 이름에서
// 종류를 찾는다. 같은 쿼리 키라 요청이 늘지 않는다. 비관리자 응답엔 관리자 전용 스토리지가 아예 없어
// (routes_storages.list_user_storages) 그 이름은 맵에 없다 -- 호출자(groupCaveatsFor)는 그 경우를 "모름" 으로
// 다뤄 일반 주의문으로 떨어뜨린다. 조회 실패·로딩 중도 같은 빈 맵이다.
export const useStorageBackends = (): Record<string, string> => {
  const q = useUserStorages();
  return useMemo(() => Object.fromEntries(
    (q.data ?? []).flatMap((s) =>
      typeof s.backend_type === "string" && s.backend_type !== ""
        ? [[s.storage_name, s.backend_type] as const] : [])), [q.data]);
};
