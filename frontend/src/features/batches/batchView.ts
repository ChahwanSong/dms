// 배치 상세의 서버 응답 → 화면 모양(2026-11-16 상세 쪽 조회). 상세는 이제 머리(?view=summary)·항목 한 쪽(?view=items)·
// 대화상자의 1회 조회(?view=all·payloads)로 나눠 받는다 -- 항목 수천 개 배치를 2.5초마다 통째로(수 MiB, 비압축) 받아 전 행을
// 다시 그리던 것이 화면이 멈춘 원인이었다(사용자 보고 「수백 개를 넘으면 느려지고 응답 없음」). 롤링 중 옛 파드는 view 를 모르고
// 옛 전체 응답(items 포함)을 주므로 여기서 그 items 로 같은 모양을 파생해 물러선다 -- 화면은 어느 파드의 응답인지 모른 채 한
// 모양만 본다. 이름·모양은 dms-ssc 라인과 같다(골라서 cherry-pick -- 이름을 바꾸지 마라). 응답 타입은 이 파일에 둔다(dms-k8s 는
// lib/types 의 Batch 에 스토리지 세 키만 더했다 -- 다른 작업과 파일이 겹치지 않게).
import { readSummary, summaryTool } from "../../lib/toolSummary";
import { searchTokens, matchRanges } from "../../components/ui/Highlight";
import { storageLabel } from "../usage/usageTabs";
import type { Batch, BatchItem } from "../../lib/types";

// 「대상 스토리지」 글자(2026-11-16 사용자 요청 -- 목록의 「작업」 열 오른쪽). 서버가 첫 항목 payload 에서 읽을 때 파생한 값이다
// (배치는 스토리지 한 종류·한 쌍 -- domain.validate_batch). scan·rm 은 스토리지 이름, sync 는 「출발 → 도착」(같아도 화살표 --
// 정보가 같고 규칙이 하나다). 모르면(null·부재 -- 항목 0개·깨진 payload·옛 서버) null 이라 칸은 「—」(빈 글자로 접지 않는다).
// 빈 문자열 이름(변조·구형)은 사용량 분석과 같은 표기(storageLabel 「(이름 없음)」) -- 빈 칸은 「모름」처럼 읽힌다.
export function batchStorageText(b: Pick<Batch, "operation" | "storage" | "source_storage" | "destination_storage">):
    string | null {
  const name = (v: string | null | undefined) => (typeof v === "string" ? storageLabel(v) : null);
  if (b.operation === "sync") {
    const src = name(b.source_storage), dst = name(b.destination_storage);
    return src === null && dst === null ? null : `${src ?? "—"} → ${dst ?? "—"}`;
  }
  return name(b.storage);
}

// direct 옵션 안내의 재료(서버 view=summary 의 direct_tools -- routes_batches._direct_tool_counts). 도구를 아는 항목 수·그중
// nsync 수·도구를 모르고 앞으로 돌 수 있는 항목 수(거부·실패·취소로 끝난 항목은 앞으로도 돌지 않아 미정에 넣지 않는다).
export interface BatchDirectTools { tools_known: number; nsync: number; undecided: number }
// 상세 머리 원문(GET /api/admin/batches/{id}?view=summary): 배치 행 + 집계. items 는 싣지 않는다. 집계 키가 옵션(?)인 이유: 롤링 중
// 옛 파드는 view 를 모르고 옛 전체 응답(items 포함)을 준다 -- headerOf 가 그 items 에서 파생해 물러선다.
export interface BatchSummary extends Batch {
  item_total?: number;
  // 상태별 항목 수 -- 0 도 값이다(없는 상태 키는 0 으로 읽는다).
  status_counts?: Record<string, number>;
  first_payload?: Record<string, unknown> | null;
  // sync·rm 이 Previewing·PreviewReady 일 때만 센다(확인 단계 배너 재료) -- 그 밖 null(= 말할 것 없음, 0 과 다르다).
  executing_count?: number | null;
  direct_tools?: BatchDirectTools | null;
  items?: BatchItem[];
}
// 항목 한 쪽(?view=items&offset=&limit=&q=&status=): total 은 거른 뒤의 수(쪽 수·「검색 결과 N개」의 재료). filters 는 서버가
// 받은 거르기의 메아리(dms-k8s) -- 옛 파드의 전체 응답과 가르는 표지다.
export interface BatchItemsPage {
  total: number; offset: number; limit: number; items: BatchItem[];
  filters?: { q: string | null; status: string | null };
}
// 가벼운 전 항목(?view=payloads) -- 현재 항목 CSV·rm 겹침 즉답용. 조인 필드는 없다.
export interface BatchPayloadItem { seq: number; payload: Record<string, unknown>; status: string }

// 항목 종단 중 「실행되지 않은 것」(거부·실패·취소) -- 확인 창의 미실행 원인·direct 안내의 「미정」 제외가 같은 한 벌을 쓴다
// (서버 routes_batches._ITEM_NOT_RUN 의 거울).
export const ITEM_NOT_RUN = ["Rejected", "Failed", "Cancelled"];

export const BATCH_ITEMS_PAGE_SIZE = 100;
// 주소 ?page= 의 상한(사용량 useUsage.PAGE_MAX 와 같은 값): 손으로 친 거대한 쪽(?page=1e19)은 offset 이 1e21 을 넘어 String() 이
// 「1e+21」을 만들고 서버 `offset: int` 가 422 를 낸다 -- 항목 쿼리가 데이터 없는 오류라 마지막 쪽 당김도 돌지 않아 주소를 고칠
// 때까지 목록이 오류로 굳는다. 상한으로 접으면 범위 밖 = 빈 쪽 → 마지막 쪽 당김이다.
export const BATCH_ITEMS_PAGE_MAX = 10_000_000;
// 주소의 쪽 값 → 쪽 번호(1 이상 정수, 없거나 숫자가 아니면 1, 상한 위는 상한으로 접는다).
export const parseItemsPage = (raw: string | null): number => {
  const n = raw !== null && /^\d+$/.test(raw) ? Number(raw) : 1;
  return Math.min(Math.max(1, n), BATCH_ITEMS_PAGE_MAX);
};

// 검색칸 문구 -- 사용량 분석 경로 검색(StoragePanel SEARCH_LABEL·SEARCH_RULE)과 같은 말이다(같은 판정 -- 서버
// batch_item_search 가 usage_attribution 의 PV 소속을 쓴다). 사용량 모듈을 import 하지 않는 이유: 배치 상세가 무거운 사용량
// 화면 모듈을 끌어오지 않게(ScanHistograms 를 따로 둔 것과 같은 이유).
export const ITEM_SEARCH_LABEL = "경로·PV 이름 검색";
export const ITEM_SEARCH_RULE = "여러 단어는 모두 들어간 것만 · 대소문자 무시 · 경로나 PV 이름 어디든";

// 항목 상태 칩(2026-11-16): 서버 view=items 의 status(항목 status 정확 일치)에 그대로 실린다. null = 전체. 라벨은 원문 상태 --
// 행의 상태 배지(StatusPill)가 원문(Queued 등)을 보이므로 칩과 배지가 같은 글자로 짝지어 읽힌다.
export const ITEM_STATUS_CHIPS: readonly { label: string; status: string | null }[] = [
  { label: "전체", status: null }, { label: "Queued", status: "Queued" }, { label: "Materialized", status: "Materialized" },
  { label: "Succeeded", status: "Succeeded" }, { label: "Failed", status: "Failed" }, { label: "Rejected", status: "Rejected" },
  { label: "Cancelled", status: "Cancelled" },
];
const CHIP_STATUSES: ReadonlySet<string> = new Set(ITEM_STATUS_CHIPS.flatMap((c) => (c.status === null ? [] : [c.status])));
// 주소의 status 값(사용자가 고칠 수 있다) → 칩 하나. 모르는 값은 전체(null)로 접는다 -- 서버는 모르는 값에 빈 결과를 주는데,
// 화면엔 그 값의 칩이 없어 왜 비었는지 말할 길이 없다.
export const chipStatus = (v: string | null): string | null => (v !== null && CHIP_STATUSES.has(v) ? v : null);

// direct 안내 재료 -- 서버 summary 의 direct_tools 와 같은 규칙(서버 _preview_tool 과 summaryTool 은 같은 순서: 자식 잡의 도구
// job_tool 먼저, 없으면 미리보기 summary v2 의 tool). options.direct 가 명시 true 일 때만 센다(아니면 null = 말할 것 없음).
// 옛 파드 물러서기·확인 창(view=all 의 전 항목)이 쓴다.
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

// 미리보기 단계로 돌아온 배치에서도 이미 실행 중인 자식(Materialized + 잡 Executing)은 끝까지 돈다 -- 배너 재료(옛 파드 물러서기).
const executingOf = (items: BatchItem[]) =>
  items.filter((it) => it.status === "Materialized" && it.job_state === "Executing").length;

const isCount = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const toolsShape = (v: unknown): BatchDirectTools | null => {
  if (typeof v !== "object" || v === null) return null;
  const t = v as Record<string, unknown>;
  return isCount(t.tools_known) && isCount(t.nsync) && isCount(t.undecided)
    ? { tools_known: t.tools_known, nsync: t.nsync, undecided: t.undecided } : null;
};

// 화면이 보는 머리: 집계 키가 늘 있다(옛 파드면 items 에서 파생). items 는 싣지 않는다 -- 항목은 쪽 조회가 따로 받는다(머리에
// 남겨 두면 누군가 다시 「전 항목」으로 읽는다: hasItems·firstPayload 를 거른 쪽에서 파생하면 검색 0건이 「항목 없음」이 된다).
export interface BatchHeader extends Batch {
  item_total: number;
  status_counts: Record<string, number>;
  first_payload: Record<string, unknown> | null;
  executing_count: number | null;
  direct_tools: BatchDirectTools | null;
}

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
  const first = list[0]?.payload;
  return {
    ...row, item_total: Array.isArray(items) ? list.length : r.item_count, status_counts: counts,
    first_payload: typeof first === "object" && first !== null ? first : null,
    executing_count: executingOf(list),
    direct_tools: directToolsOf(r.options, list),
  };
}

// 쪽 조회 조건. status = 항목 status 정확 일치(null = 전체), q = 검색어(빈 글자 = 거르지 않음 -- 서버가 토큰으로 나눈다).
export interface BatchItemsParams { page: number; q: string; status: string | null }

// 서버 view=items 의 거르기를 흉내 내는 옛 파드 물러서기 전용 판정: q 는 토큰(searchTokens -- 서버 search_tokens 의 거울)마다
// target·source·destination 중 하나에 대소문자 무시 부분 일치, 토큰 사이 AND. PV 이름은 화면이 모른다(서버만 판정한다) -- 롤링
// 배포의 짧은 창이라 경로만 본다. status 는 정확 일치. 문자열이 아닌 경로는 맞지 않는다(지어내지 않는다).
export function itemMatches(it: BatchItem, q: string, status: string | null): boolean {
  if (status !== null && it.status !== status) return false;
  const tokens = searchTokens(q);
  if (tokens.length === 0) return true;
  const hay = ["target", "source", "destination"].flatMap((k) => {
    const v = it.payload?.[k];
    return typeof v === "string" ? [v] : [];
  });
  return tokens.every((t) => hay.some((h) => matchRanges(h, [t]).length > 0));
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

export function pageOf(r: unknown, params: BatchItemsParams): BatchItemsPage {
  const o = isObj(r) ? r : {};
  // 새 파드: 봉투(total·items) + 거르기 메아리(filters). 메아리가 없는 봉투도 쪽으로 읽는다(dms-ssc 파드 -- 같은 봉투).
  if (isCount(o.total) && Array.isArray(o.items) && !("batch_id" in o)) return o as unknown as BatchItemsPage;
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
  const items = isObj(r) ? r.items : undefined;
  if (!Array.isArray(items)) throw new Error("항목 목록을 읽지 못했습니다");
  return items as BatchPayloadItem[];
}

// 쪽 수(1 이상). total 을 모르면 null -- 「1쪽」으로 지어내지 않는다(Pager 는 그리지 않는다).
export const itemPageCount = (total: number | undefined): number | null =>
  (total === undefined ? null : Math.max(1, Math.ceil(total / BATCH_ITEMS_PAGE_SIZE)));
