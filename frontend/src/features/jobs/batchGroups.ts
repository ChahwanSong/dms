import type { BatchDeletingSummary, BatchScheduleSummary, RequestRow } from "../../lib/types";
import { REQUEST_TERMINAL_STATES } from "../../lib/jobState";
import { isBatchTerminal } from "../batches/useBatches";

// 전체 작업의 배치 단위 삭제(2026-10-10, 관리자 전용). 배치 자식 행의 체크는 「배치 전체」 토글이다 -- 지우는 단위는
// 서버의 request_purges.delete_batch(그 배치의 자식 요청 전부 + 배치 항목 + 배치 행, 한 트랜잭션)이고, 화면이 보는
// 수는 목록 API 가 붙인 배치 요약(RequestRow.batch_*)이다. 목록 한 쪽(50건)엔 배치의 자식이 일부만 있으므로 불러온
// 행을 세지 않는다 -- 체크 하나에 화면 밖 자식까지 걸린다는 비대칭은 툴바·확인 창이 서버 수를 보이는 것으로 막는다.

/** 서버 MAX_DELETE_BATCHES 의 기본값(routes_request_purge) -- 서버 limits(lib/deleteLimits)가 있으면 그 값을 쓴다(옛 서버 대비).
 *  호출당 작업 합·배치당 작업·항목 상한은 상수로 두지 않는다(2026-11-19 -- 배치당 상한은 설정 DMS_BATCH_DELETE_MAX_CHILDREN 이라
 *  사이트마다 다르다): 목록 응답이 행마다 batch_request_count_limit·batch_item_count_limit 을, 정리 현황·미리보기가 limits 를 싣는다
 *  (tests/test_batch_delete_limits_mirror.py 가 거울이 없음을 본다). */
export const MAX_DELETE_BATCHES = 10;
/** 배치 id 형식(uuid4 hex) -- 서버 _BID_RE 와 같다. 형식 밖("" 포함)은 묶음 선택 불가(서버는 batch_not_found). */
export const BATCH_ID_RE = /^[0-9a-f]{32}$/;

/** 배치 묶음의 선택 가능 판정. ok 와 too_large 를 고를 수 있다(2026-11-19 -- 상한 초과는 조용히 잠그지 않는다: 고르면 툴바 상한
 *  줄이 「삭제 불가 — …」 alert 로 바뀌고 확인 창이 제출을 막는다). unknown = 요약이 없거나(비관리자·구형 응답) id 형식 이상 --
 *  모르면 고르지 못한다(fail-closed). awaiting = 확인 대기(PreviewReady) -- active 의 한 갈래지만 **스스로 끝나지 않는다**(관리자가
 *  확인하거나 취소해야 움직인다). deleting = 백그라운드 배치 단위 삭제 중(묘비 -- 2026-11-19). */
export type BatchBlock = "ok" | "active" | "awaiting" | "live" | "too_large" | "deleting" | "unknown";

/** 표시용 판정 -- **서버(delete_batch)와 같은 순서**로 본다(화면은 3초 낡은 스냅숏이고 서버가 재판정한다): 백그라운드로 지우는
 *  중이면 deleting(가장 먼저), 배치 행이 있으면 종단(Completed·Cancelled)이어야 하고(아니면 PreviewReady 는 awaiting, 나머지(모르는
 *  상태 포함)는 active), 자식 수·항목 수가 행에 실린 서버 상한 이하여야 하고(**count > limit 하나로** 판정한다 -- 2026-11-19: 목록은
 *  min(5000, 상한)에서 세기를 멈추므로 capped 는 「목록이 센 최대를 넘음 -- 정확한 수 모름」이고, 상한이 5000 보다 크면 상한 초과가
 *  아니다. 정확한 수와 판정은 확인 창의 미리보기가 받는다), 끝나지 않은 자식이 0 이어야 한다(capped 면 모른다 -- 미리보기가
 *  판정한다). 조용한 창(방금 끝난 자식)은 화면이 모른다 -- 서버의 request_recently_finished 가 말한다. */
export function batchBlock(r: RequestRow): BatchBlock {
  if (typeof r.batch_id !== "string" || !BATCH_ID_RE.test(r.batch_id)) return "unknown";
  if (r.batch_deleting != null && typeof r.batch_deleting === "object") return "deleting";
  if (r.batch_status === "Deleting") return "deleting";
  if (typeof r.batch_exists !== "boolean" || typeof r.batch_request_count !== "number") return "unknown";
  if (r.batch_exists && !(typeof r.batch_status === "string" && isBatchTerminal(r.batch_status))) {
    return r.batch_status === "PreviewReady" ? "awaiting" : "active";
  }
  // 상한은 서버가 행마다 싣는다(2026-11-16) -- 숫자가 아니면(옛 서버·변조) 모른다: 고르지 못한다(fail-closed).
  const childLimit = r.batch_request_count_limit, itemLimit = r.batch_item_count_limit;
  if (typeof childLimit !== "number" || typeof itemLimit !== "number") return "unknown";
  if (r.batch_request_count > childLimit) return "too_large";
  if (typeof r.batch_item_count === "number" && r.batch_item_count > itemLimit) return "too_large";
  if (r.batch_request_count_capped === true) return "ok";           // 수를 모른다 -- 미리보기가 판정한다
  if (typeof r.batch_live_request_count !== "number" || typeof r.batch_succeeded_scan_count !== "number") {
    return "unknown";
  }
  if (r.batch_live_request_count !== 0) return "live";
  return "ok";
}

/** 고를 수 없는(또는 상한을 넘는) 배치 묶음의 **보이는** 사유(배치 칸 둘째 줄 -- 칸 폭(7.5rem)·text-xs **한 줄**에 들어가게
 *  짧게: 이 줄이 두 줄로 접히면 배치가 끝나거나 다시 돌 때 행 높이가 바뀐다). 마우스 hover 의 title 만으론 키보드·터치 사용자가 왜
 *  못 고르는지 알 길이 없었다(2026-10-11 검증 지적). 긴 말(처방)은 JobsList 가 같은 줄의 화면 낭독기 전용 글자로 덧붙인다. live 는
 *  **이 행이 아니라** 그 배치의 다른 작업 얘기다. too_large 는 「삭제 불가·상한」(2026-11-19 -- 긴 말은 blockText: 「삭제 불가 —
 *  작업 N개가 … 상한 M개를 넘습니다」). deleting 의 진행률은 JobsList 가 「삭제 중 37%」로 그린다(이 표는 기본 글자). */
export const BATCH_BLOCK_LABEL: Record<Exclude<BatchBlock, "ok">, string> = {
  active: "배치 진행 중",
  awaiting: "배치 확인 대기",
  live: "미완료 작업 있음",
  too_large: "삭제 불가·상한",
  deleting: "삭제 중",
  unknown: "정보 없음",
};

/** 고를 수 있는 배치 묶음 -- 툴바·확인 창이 쓰는 모양(수는 전부 서버가 센 값). */
export interface BatchSummary {
  batch_id: string;
  /** 표시 이름: 배치 이름, 없으면 id 앞 12자(named=false -- 고정폭 글꼴로 보인다). */
  label: string;
  named: boolean;
  /** 자식 요청의 연산(배치의 연산과 같다). */
  operation: string;
  /** 배치 행이 있는가. false = 배치 기록만 지워진 묶음(「배치 기록 없음」). */
  exists: boolean;
  /** 배치 상태. 배치 행이 없으면 null. */
  status: string | null;
  /** 재실행 이력을 포함한 전체 자식 수 -- 삭제 CAS 의 expected_request_count(정본은 미리보기). null = 목록이 센 최대를 넘어
   *  정확한 수를 모름(request_count_at_least 가 하한 -- 「5,000개 이상」). */
  request_count: number | null;
  request_count_at_least?: number | null;
  /** 끝나지 않은 자식 수(0 은 정상값). null = 모름(capped). */
  live: number | null;
  /** 성공 scan 잡을 가진 자식 수(확인 창의 사용량 경고). null = 모름(capped -- 미리보기도 세지 않는다). */
  scans: number | null;
  /** 그 배치의 스케줄(2026-11-16) -- 배치 단위 삭제가 스케줄·회차 기록도 함께 지운다는 확인 창 안내의 재료. null·부재 = 없음
   *  (구형 응답엔 키가 없다 -- 스케줄 테이블이 없는 서버라 지울 것도 없다). */
  schedule?: BatchScheduleSummary | null;
  /** 상한 초과 묶음(block too_large)의 문구 재료(서버 사유 코드 + 숫자 -- lib/deleteLimits.blockText). ok 면 없음. */
  over?: { reason: string; limit: number; count: number | null; count_at_least: number | null } | null;
}
/** 목록의 배치 묶음. 고를 수 있는 묶음(ok·too_large)만 수를 싣는다 -- 고를 수 없는 묶음은 수를 모를 수 있다(요약 없음 -- 모름 ≠ 0
 *  이라 0 으로 짓지 않는다). deleting 은 진행(서버 batch_deleting)을 싣는다. */
export type BatchGroup =
  | (BatchSummary & { block: "ok" | "too_large" })
  | { batch_id: string; label: string; named: boolean; operation: string;
      block: Exclude<BatchBlock, "ok" | "too_large">; deleting?: BatchDeletingSummary | null };

/** 고를 수 있는 묶음인가(ok·too_large). */
export const pickable = (g: BatchGroup | undefined): g is BatchSummary & { block: "ok" | "too_large" } =>
  g !== undefined && (g.block === "ok" || g.block === "too_large");

/** 불러온 행 → batch_id 별 묶음(처음 나온 행의 값 -- 한 번의 폴링 응답 안에서 같은 배치의 행은 같은 요약을 갖는다).
 *  단건 행(batch_id null·부재)은 빠진다. 이상값("")도 묶음이 되지만 unknown 이라 고를 수 없다.
 *
 *  **불러온 행의 상태로 한 번 더 본다**(비용 0): 같은 배치의 불러온 행 중 끝나지 않은 것이 하나라도 있으면 서버 요약이
 *  「끝나지 않은 작업 0」이라 해도 live 로 내린다(2026-10-10 검증 지적 -- 행 SELECT 와 요약 집계는 한 읽기 트랜잭션이 아니다).
 *  상한 초과(too_large)는 그대로 둔다 -- 서버의 상한 판정이 자식 게이트보다 먼저다. */
export function groupBatches(rows: RequestRow[]): Map<string, BatchGroup> {
  const groups = new Map<string, BatchGroup>();
  const liveRow = new Set<string>();
  for (const r of rows) {
    if (r.batch_id != null && !REQUEST_TERMINAL_STATES.has(r.state)) liveRow.add(r.batch_id);
  }
  for (const r of rows) {
    if (r.batch_id == null || groups.has(r.batch_id)) continue;
    const bid = r.batch_id;
    const named = typeof r.batch_name === "string" && r.batch_name.trim() !== "";
    const label = named ? (r.batch_name as string) : bid.slice(0, 12);
    const base = { batch_id: bid, label, named, operation: r.operation };
    let block = batchBlock(r);
    if (block === "ok" && liveRow.has(bid)) block = "live";
    if (block !== "ok" && block !== "too_large") {
      groups.set(bid, { ...base, block, deleting: block === "deleting" ? (r.batch_deleting ?? null) : undefined });
      continue;
    }
    // ok·too_large 면 요약 필드는 타입이 맞다(batchBlock 이 확인했다). capped = 목록이 센 최대를 넘음(정확한 수 모름).
    const capped = r.batch_request_count_capped === true;
    const count = r.batch_request_count as number;
    const childLimit = r.batch_request_count_limit as number, itemLimit = r.batch_item_count_limit as number;
    let over: BatchSummary["over"] = null;
    if (block === "too_large") {
      over = count > childLimit
        ? { reason: "batch_delete_too_large", limit: childLimit, count: capped ? null : count,
            count_at_least: capped ? count : null }
        : { reason: "batch_delete_too_many_items", limit: itemLimit, count: r.batch_item_count ?? null,
            count_at_least: null };
    }
    groups.set(bid, {
      ...base, block,
      exists: r.batch_exists as boolean,
      status: typeof r.batch_status === "string" ? r.batch_status : null,
      request_count: capped ? null : count,
      request_count_at_least: capped ? count : null,
      live: typeof r.batch_live_request_count === "number" ? r.batch_live_request_count : null,
      scans: typeof r.batch_succeeded_scan_count === "number" ? r.batch_succeeded_scan_count : null,
      schedule: r.batch_schedule ?? null,
      over,
    });
  }
  return groups;
}

// ---- 배치 칸의 이름 표시 ----

// 대략의 표시 폭(한글·한자·전각 = 2, 그 밖 = 1). text-sm 에서 한글은 ~1em, 숫자·라틴은 ~0.55em 이라 이 비율이 맞다.
const WIDE = /[ᄀ-ᅟ⺀-〾ぁ-㏿㐀-䶿一-鿿가-힣豈-﫿︰-﹏＀-｠￠-￦]/u;
const unitsOf = (s: string): number => [...s].reduce((n, ch) => n + (WIDE.test(ch) ? 2 : 1), 0);
/** 배치 칸 7.5rem(120px, 2026-10-25 -- 예전 6rem 96px 에서 넓혔다)·text-sm 한 줄에 확실히 들어가는 폭(한글 ~6자) -- 이보다
 *  길면 앞(text-sm)·뒤(text-xs) 두 줄로 나눈다. 6rem 때의 11·10 을 폭 비율(1.25)만큼 키우고 내림했다(여유를 남긴다). */
export const NAME_LINE_UNITS = 13;
const TAIL_UNITS = 12;

/** 배치 이름 → 표시용 앞·뒤 조각(짧아 한 줄에 들어가면 null). 뒤 조각은 이름의 **끝** TAIL_UNITS 폭(가능하면 낱말
 *  경계에서 시작) -- 접두가 같은 배치(「…이관(1차)」·「…이관(2차)」, 「d161 … 1007c」)를 가르는 것은 대개 끝이다. 한 줄
 *  truncate 는 이름 앞 5~6자만, 두 줄 line-clamp 도 앞 ~12자만 보여 끝이 늘 잘렸다(2026-10-10·10-11 검증 지적). 앞 조각은
 *  나머지 전부(화면이 한 줄 말줄임). 화면은 두 조각을 두 줄로 그린다(배치 칸은 모든 폭에서 7.5rem -- JobsList BATCH_W):
 *  앞 조각은 text-sm, 뒤 조각은 text-xs 이고, 고를 수 없는 묶음이면 뒤 조각 줄 자리에 사유 줄이 선다(칸이 상태와 무관하게
 *  두 줄 -- JobsList BatchCell). text-xs 라 TAIL_UNITS 폭은 7.5rem 에 여유 있게 들어간다(끝이 말줄임으로 잘리지 않는다). */
export function splitBatchName(name: string): { head: string; tail: string } | null {
  if (unitsOf(name) <= NAME_LINE_UNITS) return null;
  const chars = [...name];
  let start = chars.length;
  let used = 0;
  while (start > 0 && used + unitsOf(chars[start - 1]) <= TAIL_UNITS) {
    used += unitsOf(chars[start - 1]);
    start -= 1;
  }
  // 낱말 중간에서 시작하면 창 안의 첫 공백 뒤로 옮긴다(남는 뒤 조각이 너무 짧지 않을 때만).
  if (start > 0 && !/\s/u.test(chars[start - 1])) {
    const sp = chars.findIndex((ch, i) => i >= start && /\s/u.test(ch));
    if (sp !== -1 && unitsOf(chars.slice(sp + 1).join("")) >= 4) start = sp + 1;
  }
  const head = chars.slice(0, start).join("");
  const tail = chars.slice(start).join("").trim();
  if (head.trim() === "" || tail === "") return null;
  return { head: head.trimEnd(), tail };
}
