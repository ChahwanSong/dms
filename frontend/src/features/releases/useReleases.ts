import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { PillVariant } from "../../lib/jobState";
import type { Release, Releases, ReleaseTargets } from "../../lib/types";

// Pending/Applying이 릴리스의 비종단 상태다 -- jobState.ts의 isTerminal은
// Applied를 몰라서 쓸 수 없다(Applied가 비종단으로 읽혀 폴링이 안 멈춘다).
export const RELEASE_ACTIVE_STATES = new Set(["Pending", "Applying"]);

// 릴리스 전용 배지 매핑. buildPillVariant와 같은 이유로 공용 pillVariant를 고치지
// 않는다(M5) -- Pending은 잡/요청 화면에서 neutral로 고정돼 있고, Applied/Applying은
// 공용 매핑이 아예 모르는 릴리스만의 상태다.
export function releasePillVariant(state: string): PillVariant {
  if (state === "Applied") return "ok";
  if (state === "Failed") return "bad";
  if (RELEASE_ACTIVE_STATES.has(state)) return "busy";
  return "neutral";
}

// targets는 워크로드를 apiserver에서 직접 읽어(컴포넌트 3종 × 10초 타임아웃) 최악
// 30초가 걸린다 -- 절대 짧게 폴링하지 않는다. 화면 진입, 제출 직후, 그리고 롤아웃이
// 끝나는 순간(useRefreshTargetsOnSettle)에만 다시 읽는다. 진행 상태는 값싼
// /api/admin/releases 쪽에서 본다.
export const useReleaseTargets = () =>
  useQuery({
    queryKey: ["release-targets"],
    queryFn: () => apiGet<ReleaseTargets>("/api/admin/releases/targets"),
    refetchOnWindowFocus: false,
  });

export const RELEASE_POLL_MS = 5000;

/** 진행 중인 릴리스가 하나라도 있으면 폴링 간격을, 전부 종단이면 false를 준다.
 *  useQuery 옵션 안의 인라인 화살표가 아니라 이름 붙은 함수로 두는 이유: 폴링
 *  시작/정지는 설계 §8의 요구사항인데 인라인이면 테스트가 옵션 함수에 닿지 못해
 *  "폴링이 아예 시작되지 않는" 회귀가 그대로 통과한다. */
export function releaseRefetchInterval(data: Releases | undefined): number | false {
  const history = data?.history;
  return Array.isArray(history)
    && history.some((r) => RELEASE_ACTIVE_STATES.has(r.state)) ? RELEASE_POLL_MS : false;
}

export const useReleases = () =>
  useQuery({
    queryKey: ["releases"],
    queryFn: () => apiGet<Releases>("/api/admin/releases"),
    // 진행 중일 때만 폴링 -- useBuilds와 같은 관용구. 전부 종단이면 상태가 더
    // 바뀔 일이 없고, 제출이 쿼리를 무효화하면 폴링이 자동 재개된다.
    refetchInterval: (q) => releaseRefetchInterval(q.state.data as Releases | undefined),
  });

/** 롤아웃이 진행 중 -> 전부 종단으로 바뀌는 순간에만 targets를 한 번 다시 읽는다.
 *  제출 직후의 무효화는 아직 Pending이라 옛 이미지를 다시 가져올 뿐이다 -- 이게
 *  없으면 롤아웃이 끝나도 "현재 이미지"가 옛 값으로 남아 운영자가 적용 여부를
 *  화면에서 확인할 수 없다. 비싼 엔드포인트라 전이 순간 1회로 제한한다. */
export const useRefreshTargetsOnSettle = (active: boolean, ready: boolean) => {
  const qc = useQueryClient();
  const wasActive = useRef(false);
  useEffect(() => {
    if (!ready) return;   // 아직 이력을 못 읽었으면 active=false는 "모른다"는 뜻이다
    if (wasActive.current && !active) {
      qc.invalidateQueries({ queryKey: ["release-targets"] });
    }
    wasActive.current = active;
  }, [active, ready, qc]);
};

export interface SubmitReleasesBody { items: { component: string; tag: string }[] }
// tag_verified 는 옵셔널이다 -- 구 서버(d38 이전)와 겹치는 배포 순간에 필드가
// 없어도 배너 로직(false 일 때만 표시)이 조용히 꺼질 뿐 깨지지 않는다.
export interface SubmitReleasesResult { items: Release[]; tag_verified?: boolean }

export const useSubmitReleases = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: SubmitReleasesBody) =>
      apiSend<SubmitReleasesResult>("POST", "/api/admin/releases", b),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["releases"] });
      qc.invalidateQueries({ queryKey: ["release-targets"] });
    },
  });
};
