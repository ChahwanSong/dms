// 배치 상세의 서버 응답 → 화면 모양(2026-11-16 상세 쪽 조회). 상세는 이제 머리(?view=summary)·항목 한 쪽(?view=items)·
// 대화상자의 1회 조회(?view=all·payloads)로 나눠 받는다 -- 항목 1만 개 배치를 2.5초마다 통째로(수 MiB) 받아 전 행을 다시
// 그리던 것이 화면이 멈춘 원인이었다(지도 map-batch-detail). 롤링 중 옛 파드는 view 를 모르고 옛 전체 응답(items 포함)을
// 주므로 여기서 그 items 로 같은 모양을 파생해 물러선다 -- 화면은 어느 파드의 응답인지 모른 채 한 모양만 본다.
import { readSummary, summaryTool } from "../../lib/toolSummary";
import type { Batch, BatchDirectTools, BatchItem, BatchItemsPage, BatchPayloadItem, BatchSummary } from "../../lib/types";

// 항목 종단 중 「실행되지 않은 것」(거부·실패·취소) -- 확인 창의 미실행 원인·direct 안내의 「미정」 제외가 같은 한 벌을 쓴다.
export const ITEM_NOT_RUN = ["Rejected", "Failed", "Cancelled"];

export const BATCH_ITEMS_PAGE_SIZE = 100;

// 화면이 보는 머리: 집계 키가 늘 있다(옛 파드면 items 에서 파생). items 는 싣지 않는다 -- 항목은 쪽 조회가 따로 받는다(머리에
// 남겨 두면 누군가 다시 「전 항목」으로 읽는다: hasItems·firstPayload 를 거른 쪽에서 파생하면 검색 0건이 「항목 없음」이 된다).
export interface BatchHeader extends Batch {
  item_total: number;
  status_counts: Record<string, number>;
  first_payload: Record<string, unknown> | null;
  executing_count: number | null;
  direct_tools: BatchDirectTools | null;
}

// direct 안내 재료 -- 서버 summary 의 direct_tools 와 같은 규칙(서버 _preview_tool 과 summaryTool 은 같은 순서: 자식 잡의 도구
// job_tool 먼저, 없으면 미리보기 summary v2 의 tool). options.direct 가 명시 true 일 때만 센다(아니면 null = 말할 것 없음).
// undecided = 도구를 모르는데 아직 돌 수 있는 항목(거부·실패·취소로 끝난 항목은 앞으로도 돌지 않아 미정에 넣지 않는다).
export function directToolsOf(options: Record<string, unknown> | undefined,
                              items: BatchItem[]): BatchDirectTools | null {
  if (options?.direct !== true) return null;
  let toolsKnown = 0, nsync = 0, undecided = 0;
  for (const it of items) {
    const tool = summaryTool(readSummary(it.preview_summary), it.job_tool);
    if (tool !== null) { toolsKnown += 1; if (tool === "nsync") nsync += 1; }
    else if (!ITEM_NOT_RUN.includes(it.status)) undecided += 1;
  }
  return { tools_known: toolsKnown, nsync, undecided };
}

// 미리보기 단계로 돌아온 배치에서도 이미 실행 중인 자식(Materialized + 잡 Executing)은 끝까지 돈다 -- 배너 재료.
const executingOf = (items: BatchItem[]) =>
  items.filter((it) => it.status === "Materialized" && it.job_state === "Executing").length;

const isCount = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const toolsShape = (v: unknown): BatchDirectTools | null => {
  if (typeof v !== "object" || v === null) return null;
  const t = v as Record<string, unknown>;
  return isCount(t.tools_known) && isCount(t.nsync) && isCount(t.undecided)
    ? { tools_known: t.tools_known, nsync: t.nsync, undecided: t.undecided } : null;
};

export function headerOf(r: BatchSummary): BatchHeader {
  const { items, ...row } = r;
  if (isCount(r.item_total)) {
    return {
      ...row, item_total: r.item_total,
      status_counts: typeof r.status_counts === "object" && r.status_counts !== null ? r.status_counts : {},
      first_payload: typeof r.first_payload === "object" && r.first_payload !== null ? r.first_payload : null,
      // null = 서버가 세지 않았다(확인 단계가 아님) -- 0 으로 접지 않는다.
      executing_count: isCount(r.executing_count) ? r.executing_count : null,
      direct_tools: toolsShape(r.direct_tools),
    };
  }
  // 옛 파드(view 무시 → 옛 전체 응답): 지금까지 화면이 items 로 하던 계산을 그대로 한다. items 마저 없으면(있을 수 없는 모양)
  // 항목 수는 배치 행의 카운터(서버 _recount 가 행에서 다시 센 값)로 둔다 -- 0 을 지어내 「항목 없음」을 말하지 않는다.
  const list = Array.isArray(items) ? items : [];
  const counts: Record<string, number> = {};
  for (const it of list) counts[it.status] = (counts[it.status] ?? 0) + 1;
  return {
    ...row, item_total: Array.isArray(items) ? list.length : r.item_count, status_counts: counts,
    first_payload: list[0]?.payload ?? null,
    executing_count: executingOf(list),
    direct_tools: directToolsOf(r.options, list),
  };
}

// 쪽 조회 조건. status = 항목 status 정확 일치(null = 전체), q = 경로 부분 문자열(빈 글자 = 거르지 않음).
export interface BatchItemsParams { page: number; q: string; status: string | null }

// 서버 view=items 의 거르기와 같은 규칙(옛 파드 물러서기 전용): q 는 target·source·destination 의 부분 문자열(대소문자 구분 --
// 사용량 경로 검색과 같다), status 는 정확 일치. 문자열이 아닌 경로는 맞지 않는다(지어내지 않는다).
export function itemMatches(it: BatchItem, q: string, status: string | null): boolean {
  if (status !== null && it.status !== status) return false;
  if (q === "") return true;
  return ["target", "source", "destination"].some((k) => {
    const v = it.payload?.[k];
    return typeof v === "string" && v.includes(q);
  });
}

export function pageOf(r: unknown, params: BatchItemsParams): BatchItemsPage {
  const o = (typeof r === "object" && r !== null ? r : {}) as Record<string, unknown>;
  if (isCount(o.total) && Array.isArray(o.items)) return o as unknown as BatchItemsPage;
  // 옛 파드는 view 를 무시하고 전체 배치를 준다 -- 같은 규칙으로 거르고 자른다.
  if (!Array.isArray(o.items)) throw new Error("항목 목록을 읽지 못했습니다");
  const hits = (o.items as BatchItem[]).filter((it) => itemMatches(it, params.q, params.status));
  const offset = (params.page - 1) * BATCH_ITEMS_PAGE_SIZE;
  return { total: hits.length, offset, limit: BATCH_ITEMS_PAGE_SIZE,
           items: hits.slice(offset, offset + BATCH_ITEMS_PAGE_SIZE) };
}

// ?view=payloads -- 옛 파드의 전체 응답도 items 가 (seq, payload, status) 를 품으므로 같은 자리에서 읽는다. 목록이 아니면
// 오류다: 빈 목록으로 접으면 현재 항목 CSV 가 「항목 없음」을, rm 겹침 즉답이 「겹침 없음」을 지어낸다.
export function payloadsOf(r: unknown): BatchPayloadItem[] {
  const items = typeof r === "object" && r !== null ? (r as { items?: unknown }).items : undefined;
  if (!Array.isArray(items)) throw new Error("항목 목록을 읽지 못했습니다");
  return items as BatchPayloadItem[];
}
