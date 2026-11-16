import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Batch, BatchSchedule, BatchScheduleRun, SchedulePreview } from "../../lib/types";

// 배치 스케줄러(2026-11-16) 훅. 서버 routes_batch_schedules — 생성·수정·켜기는 배치 생성과 같은 특권 3중 게이트, 끄기·삭제는
// 관리자면 된다(유지보수 중에도 끌 수 있어야 한다). 화면의 버튼 잠금은 표시일 뿐 진짜 차단은 서버 403 이다.

export const useBatchSchedules = () =>
  useQuery({ queryKey: ["batch-schedules"], refetchInterval: 15_000,
             queryFn: () => apiGet<BatchSchedule[]>("/api/admin/batch-schedules") });

// 회차 기록(최근 50회). 키가 ["batch-schedules"] 접두라 스케줄 mutation 의 무효화가 열린 기록 창도 같이 다시 읽는다.
export const useBatchScheduleRuns = (id: string, enabled: boolean) =>
  useQuery({ queryKey: ["batch-schedules", id, "runs"], enabled,
             queryFn: () => apiGet<BatchScheduleRun[]>(`/api/admin/batch-schedules/${encodeURIComponent(id)}/runs?limit=50`) });

// 목록(최근 100건) 창 밖의 배치를 ?batch= 로 미리 채울 때의 보충 조회. view=summary 는 항목을 싣지 않는 가벼운 응답이다
// (모르는 옛 서버는 파라미터를 무시하고 전체를 준다 -- 배치 행 필드는 같다).
export const useBatchForSchedule = (id: string | null, enabled: boolean) =>
  useQuery({ queryKey: ["batch-schedule-target", id], enabled: enabled && id !== null,
             queryFn: () => apiGet<Batch>(`/api/admin/batches/${encodeURIComponent(id ?? "")}?view=summary`) });

// 다음 5회 미리보기 — 서버가 계산한다(말일 당기기·interval 슬롯을 화면이 따로 구현하지 않는다). 입력이 바뀌는 동안 직전
// 결과를 남겨 깜빡이지 않게 한다(호출측이 디바운스한 값을 넘긴다). 배치를 알면 batch_id 를 실어 서버가 그 배치의 회차 보존 예측
// (prune_preview — 저장하면 곧 정리될 작업 수, 2026-11-16)을 같이 계산하게 한다. 모르는 옛 서버는 batch_id 를 무시한다
// (pydantic 기본 extra=ignore — 미리보기는 그대로 온다).
export interface PreviewRequest { kind: string; spec: Record<string, unknown>; batch_id?: string }
export const useSchedulePreview = (req: PreviewRequest | null) =>
  useQuery({
    queryKey: ["batch-schedule-preview", req === null ? null : JSON.stringify(req)],
    enabled: req !== null,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    queryFn: () => apiSend<SchedulePreview>("POST", "/api/admin/batch-schedules:preview", req),
  });

// mutation 뒤 갱신: 스케줄 목록 + 배치 목록·상세(배치 응답에 스케줄 요약이 실리면 거기도 바뀐다). 무효화 promise 를 돌려줘
// 재조회가 착지할 때까지 isPending 이 유지된다(useBatches._refresh 와 같은 계약 — 「끝났다」 = 화면이 갱신된 시점).
const refresh = (qc: QueryClient) => Promise.all([
  qc.invalidateQueries({ queryKey: ["batch-schedules"] }),
  qc.invalidateQueries({ queryKey: ["batches"] }),
  qc.invalidateQueries({ queryKey: ["batch"] }),
]);

export interface CreateBatchScheduleBody {
  batch_id: string; kind: string; spec: Record<string, unknown>; enabled: boolean;
  // 파괴적 스케줄(rm, sync+delete)에서 동의 체크를 켰을 때만 싣는다(서버 422 batch_schedule_ack_required 의 짝).
  acknowledge_irreversible?: true;
}
export const useCreateBatchSchedule = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: CreateBatchScheduleBody) => apiSend<BatchSchedule>("POST", "/api/admin/batch-schedules", b),
    onSettled: () => refresh(qc),
  });
};

export interface UpdateBatchScheduleBody {
  kind: string; spec: Record<string, unknown>;
  acknowledge_irreversible?: true;
}
export const useUpdateBatchSchedule = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: UpdateBatchScheduleBody) =>
      apiSend<BatchSchedule>("PATCH", `/api/admin/batch-schedules/${encodeURIComponent(id)}`, b),
    onSettled: () => refresh(qc),
  });
};

// 사용 토글: 켜기 = :enable(특권 게이트 + 파괴적이면 새 동의, 다음 실행은 지금 이후 첫 슬롯 — 바로 쏘지 않는다), 끄기 =
// :disable. 목록의 어느 행이든 같은 훅 하나로 쏘므로 id 는 변수로 받는다.
export interface ToggleBatchScheduleVars { id: string; enabled: boolean; acknowledge?: boolean }
export const useToggleBatchSchedule = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled, acknowledge }: ToggleBatchScheduleVars) =>
      apiSend<BatchSchedule>("POST", `/api/admin/batch-schedules/${encodeURIComponent(id)}:${enabled ? "enable" : "disable"}`,
        enabled && acknowledge === true ? { acknowledge_irreversible: true } : {}),
    onSettled: () => refresh(qc),
  });
};

export const useDeleteBatchSchedule = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiSend("DELETE", `/api/admin/batch-schedules/${encodeURIComponent(id)}`),
    onSettled: () => refresh(qc),
  });
};
