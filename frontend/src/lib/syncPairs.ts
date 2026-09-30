import type { UserStorage, UserSyncPairs } from "./types";

type Pair = UserSyncPairs["pairs"][number];

/** 사용자 sync 허용 쌍으로 소스·목적지 선택지를 서로 거른다(2026-09-30 사용자 요청: "src 선택 또는
    dst 선택 시 다른 한쪽의 선택지에서 불가 항목은 제외"). 반대쪽이 비어 있으면 어느 값과든 쌍이 되는
    스토리지를 모두, 골라져 있으면 그 값과 쌍이 되는 것만 남긴다.

    지금 고른 값은(허용 목록이 바뀌어 빠졌어도) 선택지에 남긴다 -- 셀렉트가 목록에 없는 값을 쥐면 화면은
    「선택하세요」를 보이는데, 같은 표시값을 다시 고르면 change 가 나지 않아 사용자가 그 값을 비울 수조차
    없다. 그런 조합은 pairAllowed 가 막는다(서버도 sync_pair_not_allowed 로 막는다). */
export function syncChoices(storages: UserStorage[], pairs: Pair[], source: string, destination: string):
    { sources: UserStorage[]; destinations: UserStorage[] } {
  const sources = storages.filter((s) => s.storage_name === source || pairs.some((p) =>
    p.source_storage === s.storage_name && (destination === "" || p.destination_storage === destination)));
  const destinations = storages.filter((s) => s.storage_name === destination || pairs.some((p) =>
    p.destination_storage === s.storage_name && (source === "" || p.source_storage === source)));
  return { sources, destinations };
}

export function pairAllowed(pairs: Pair[], source: string, destination: string): boolean {
  return pairs.some((p) => p.source_storage === source && p.destination_storage === destination);
}
