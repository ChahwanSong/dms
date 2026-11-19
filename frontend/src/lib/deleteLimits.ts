import { reasonText } from "./api";
import { n0 } from "./format";

// 삭제 화면의 상한 표시·「삭제 불가」 문구(2026-11-19 사용자 요청 「포탈에서 상한 값도 삭제 시 보여지고 값을 넘어서는 요청에 대해서
// 삭제 불가 메시지 띄우게 해줘」). 상한은 **서버 값으로만** 말한다 -- 서버 delete_limits(GET /api/admin/request-purges ·
// POST /api/admin/requests:delete-preview 의 limits)이고, 화면 상수는 옛 서버 대비 기본값(200·10)뿐이다. 배치당 작업 상한은 설정
// DMS_BATCH_DELETE_MAX_CHILDREN 이라 사이트마다 다르다 -- 숫자를 문구에 박지 않는다(tests/test_batch_delete_limits_mirror.py).
//
// blockText 는 **미리 판정과 서버 거절에 같은 함수**다 -- 툴바 상한 줄의 alert, 확인 창의 문제 상자, 결과 아래 제외 줄(서버의
// skipped_batches 가 실은 limit·count·count_at_least)이 같은 사유에 같은 문구를 보인다(「경합으로 서버가 거절해도 같은 사유 문구」).

export interface DeleteLimits {
  requests_per_call: number;
  batches_per_call: number;
  batch_request_count: number;
  batch_item_count: number;
  call_request_count: number;
  sync_request_count: number;
  list_count_cap: number;
}

const LIMIT_KEYS: (keyof DeleteLimits)[] = ["requests_per_call", "batches_per_call", "batch_request_count",
  "batch_item_count", "call_request_count", "sync_request_count", "list_count_cap"];

/** 서버 limits → DeleteLimits. 숫자가 아닌(또는 없는) 키가 하나라도 있으면 null(모름 -- 0 으로 짓지 않는다). */
export function parseLimits(x: unknown): DeleteLimits | null {
  if (typeof x !== "object" || x === null) return null;
  const o = x as Record<string, unknown>;
  for (const k of LIMIT_KEYS) {
    const v = o[k];
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
  }
  return Object.fromEntries(LIMIT_KEYS.map((k) => [k, o[k] as number])) as unknown as DeleteLimits;
}

/** 툴바·확인 창의 상한 한 줄 -- 「한 번에 작업 200개·배치 10개 · 배치당 작업 50,000개·항목 10,000개 · 합계 작업 100,000개」. */
export function limitsLine(l: DeleteLimits): string {
  return `한 번에 작업 ${n0(l.requests_per_call)}개·배치 ${n0(l.batches_per_call)}개 · 배치당 작업 ${n0(l.batch_request_count)}개`
    + `·항목 ${n0(l.batch_item_count)}개 · 합계 작업 ${n0(l.call_request_count)}개`;
}

/** 옛 서버(limits 없음)의 상한 줄 -- 목록 행의 배치 상한이 있으면 그 값으로, 없으면 말할 수 있는 것만. */
export function fallbackLimitsLine(requests: number, batches: number, batchRequests?: number, batchItems?: number): string {
  const head = `한 번에 작업 ${n0(requests)}개·배치 ${n0(batches)}개`;
  if (typeof batchRequests === "number" && typeof batchItems === "number") {
    return `${head} · 배치당 작업 ${n0(batchRequests)}개·항목 ${n0(batchItems)}개`;
  }
  return `${head}까지(배치당 상한은 서버가 판정합니다)`;
}

export interface BlockDetail { limit?: number | null; count?: number | null; count_at_least?: number | null }

const num = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

/** 「삭제 불가 — …」 한 줄. reason = 서버 사유 코드(상한 사유는 숫자를 끼운다), d = 숫자(없으면 옛 서버 -- 정적 문구).
 *  server: 서버가 제출 때 거절한 줄(호출 예산은 「이번에 지우지 않았습니다 — 다시 삭제하세요」로 말이 다르다). */
export function blockText(reason: string, d: BlockDetail = {}, opts: { server?: boolean } = {}): string {
  const limit = d.limit, count = d.count, atLeast = d.count_at_least;
  const over = (what: string) => {
    if (!num(limit)) return null;
    if (num(count)) return `삭제 불가 — ${what} ${n0(count)}개가 한 번에 지울 수 있는 상한 ${n0(limit)}개를 넘습니다`;
    if (num(atLeast)) {
      return `삭제 불가 — ${what}이 한 번에 지울 수 있는 상한 ${n0(limit)}개를 넘습니다(${n0(atLeast)}개 이상)`;
    }
    return `삭제 불가 — ${what}이 한 번에 지울 수 있는 상한 ${n0(limit)}개를 넘습니다`;
  };
  let text: string | null = null;
  switch (reason) {
    case "batch_delete_too_large": text = over("작업"); break;
    case "batch_delete_too_many_items": text = over("항목"); break;
    case "batch_delete_call_budget":
      if (num(limit) && num(count)) {
        text = opts.server
          ? `삭제 불가 — 고른 배치의 작업 합계 ${n0(count)}개가 한 번에 지울 수 있는 상한 ${n0(limit)}개를 넘어 이 배치는 이번에 지우지 않았습니다 — 다시 삭제하세요`
          : `삭제 불가 — 고른 배치의 작업 합계 ${n0(count)}개가 한 번에 지울 수 있는 상한 ${n0(limit)}개를 넘습니다 — 나눠서 지우세요`;
      }
      break;
    case "delete_selection_too_large":
      if (num(limit) && num(count)) text = `삭제 불가 — 단일 작업 ${n0(count)}개를 골랐습니다 · 한 번에 ${n0(limit)}개까지 지울 수 있습니다`;
      break;
    case "delete_batch_selection_too_large":
      if (num(limit) && num(count)) text = `삭제 불가 — 배치 ${n0(count)}개를 골랐습니다 · 한 번에 ${n0(limit)}개까지 지울 수 있습니다`;
      break;
    default:
      break;
  }
  return text ?? `삭제 불가 — ${reasonText(reason)}`;
}

/** 상한 사유(숫자를 끼우는 사유) -- 툴바 alert 의 대상. 그 밖의 막힘(진행 중·미완료 등)은 상한 문제가 아니다. */
export const LIMIT_REASONS = new Set(["batch_delete_too_large", "batch_delete_too_many_items",
  "batch_delete_call_budget", "delete_selection_too_large", "delete_batch_selection_too_large"]);
