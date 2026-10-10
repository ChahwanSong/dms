import type { RequestRow } from "../../lib/types";
import { REQUEST_TERMINAL_STATES } from "../../lib/jobState";
import { isBatchTerminal } from "../batches/useBatches";

// 전체 작업의 배치 단위 삭제(2026-10-10, 관리자 전용). 배치 자식 행의 체크는 「배치 전체」 토글이다 -- 지우는 단위는
// 서버의 request_purges.delete_batch(그 배치의 자식 요청 전부 + 배치 항목 + 배치 행, 한 트랜잭션)이고, 화면이 보는
// 수는 목록 API 가 붙인 배치 요약(RequestRow.batch_*)이다. 목록 한 쪽(50건)엔 배치의 자식이 일부만 있으므로 불러온
// 행을 세지 않는다 -- 체크 하나에 화면 밖 자식까지 걸린다는 비대칭은 툴바·확인 창이 서버 수를 보이는 것으로 막는다.

/** 서버 MAX_DELETE_BATCHES 의 거울(routes_request_purge) -- 넘으면 툴바가 잠근다(진짜 차단은 서버 422). */
export const MAX_DELETE_BATCHES = 10;
/** 서버 MAX_BATCH_DELETE_CHILDREN 의 거울(request_purges) -- 넘는 배치는 선택 불가(진짜 차단은 서버
 *  batch_delete_too_large). 목록 요약도 이 수 + 1 에서 세기를 멈춘다(batch_request_count_capped). */
export const MAX_BATCH_CHILDREN = 1000;
/** 서버 MAX_BATCH_DELETE_ITEMS 의 거울(request_purges) -- 항목이 이보다 많은 배치도 선택 불가(자식은 적고 항목만 많은
 *  배치: 큰 CSV 를 일찍 취소). 목록 요약의 batch_item_count 로 판정한다(2026-10-11 검증 지적 -- 예전엔 선택 가능으로
 *  보였다가 서버만 batch_delete_too_large 를 냈다). */
export const MAX_BATCH_ITEMS = 10000;
/** 배치 id 형식(uuid4 hex) -- 서버 _BID_RE 와 같다. 형식 밖("" 포함)은 묶음 선택 불가(서버는 batch_not_found). */
export const BATCH_ID_RE = /^[0-9a-f]{32}$/;

/** 배치 묶음의 선택 가능 판정. ok 만 고를 수 있다. unknown = 요약이 없거나(비관리자·구형 응답) id 형식 이상 --
 *  모르면 고르지 못한다(fail-closed). awaiting = 확인 대기(PreviewReady) -- active 의 한 갈래지만 **스스로 끝나지
 *  않는다**(관리자가 확인하거나 취소해야 움직인다). 「끝난 뒤에」를 기다리라는 active 의 안내는 이 상태에서 사실과
 *  다르고 배치 화면의 「확인 대기」와도 말이 달랐다(2026-10-11 검증 지적) -- 사유를 따로 보인다. */
export type BatchBlock = "ok" | "active" | "awaiting" | "live" | "too_large" | "unknown";

/** 표시용 판정 -- **서버(delete_batch)와 같은 순서**로 본다(화면은 3초 낡은 스냅숏이고 서버가 재판정한다): 배치 행이
 *  있으면 종단(Completed·Cancelled)이어야 하고(아니면 PreviewReady 는 awaiting, 나머지(모르는 상태 포함)는 active --
 *  배치 행이 없는 묶음은 상태가 없다), 자식 수·항목 수가 상한 이하여야
 *  하고(서버의 batch_delete_too_large 가 자식 게이트보다 먼저다 -- 2026-10-11 에 순서를 맞췄다: 상한을 넘는 배치는
 *  서버가 세기를 멈춰 끝나지 않은 자식 수를 모른다. 항목 수는 모르면(null) 막지 않는다 -- 서버가 판정한다), 끝나지 않은
 *  자식이 0 이어야 한다(배치가 Cancelled 여도 취소 경합으로 Pending 자식이 남을 수 있다). 조용한 창(방금 끝난 자식)은
 *  화면이 모른다 -- 서버의 request_recently_finished 가 말한다. */
export function batchBlock(r: RequestRow): BatchBlock {
  if (typeof r.batch_id !== "string" || !BATCH_ID_RE.test(r.batch_id)) return "unknown";
  if (typeof r.batch_exists !== "boolean" || typeof r.batch_request_count !== "number") return "unknown";
  if (r.batch_exists && !(typeof r.batch_status === "string" && isBatchTerminal(r.batch_status))) {
    return r.batch_status === "PreviewReady" ? "awaiting" : "active";
  }
  if (r.batch_request_count_capped === true || r.batch_request_count > MAX_BATCH_CHILDREN) return "too_large";
  if (typeof r.batch_item_count === "number" && r.batch_item_count > MAX_BATCH_ITEMS) return "too_large";
  if (typeof r.batch_live_request_count !== "number" || typeof r.batch_succeeded_scan_count !== "number") {
    return "unknown";
  }
  if (r.batch_live_request_count !== 0) return "live";
  return "ok";
}

/** 고를 수 없는 배치 묶음의 **보이는** 사유(배치 칸 둘째 줄 -- 6rem·text-xs **한 줄**에 들어가게 짧게: 이 줄이 두 줄로
 *  접히면 배치가 끝나거나 다시 돌 때 행 높이가 바뀐다). 마우스 hover 의 title 만으론 키보드(disabled 는 Tab 이
 *  건너뛴다)·터치(375px) 사용자가 왜 못 고르는지 알 길이 없었다(2026-10-11 검증 지적). 긴 말(처방)은 JobsList 가 같은 줄의
 *  화면 낭독기 전용 글자로 덧붙인다(체크박스 설명). live 는 **이 행이 아니라** 그 배치의 다른 작업 얘기다 -- 「작업 진행
 *  중」은 같은 줄의 초록 Succeeded 와 모순돼 「이 작업이 진행 중」으로 읽혔다(2026-10-11 검증 지적). too_large 는 자식
 *  수·항목 수 어느 쪽 상한이든 같은 말이다(긴 말은 reasonText("batch_delete_too_large")). awaiting 은 배치 화면의
 *  「확인 대기」(batchStatusLabel)와 같은 말이다. */
export const BATCH_BLOCK_LABEL: Record<Exclude<BatchBlock, "ok">, string> = {
  active: "배치 진행 중",
  awaiting: "배치 확인 대기",
  live: "미완료 작업 있음",
  too_large: "삭제 상한 초과",
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
  /** 재실행 이력을 포함한 전체 자식 수 -- 삭제 CAS 의 expected_request_count. */
  request_count: number;
  /** 끝나지 않은 자식 수(0 은 정상값). */
  live: number;
  /** 성공 scan 잡을 가진 자식 수(확인 창의 사용량 경고). */
  scans: number;
}
/** 목록의 배치 묶음. 고를 수 있는 묶음(ok)만 수를 싣는다 -- 고를 수 없는 묶음은 수를 모를 수 있다(요약 없음, 상한을
 *  넘어 세기를 멈춤 -- 모름 ≠ 0 이라 0 으로 짓지 않는다). */
export type BatchGroup =
  | (BatchSummary & { block: "ok" })
  | { batch_id: string; label: string; named: boolean; operation: string; block: Exclude<BatchBlock, "ok"> };

/** 불러온 행 → batch_id 별 묶음(처음 나온 행의 값 -- 한 번의 폴링 응답 안에서 같은 배치의 행은 같은 요약을 갖는다).
 *  단건 행(batch_id null·부재)은 빠진다. 이상값("")도 묶음이 되지만 unknown 이라 고를 수 없다.
 *
 *  **불러온 행의 상태로 한 번 더 본다**(비용 0): 같은 배치의 불러온 행 중 끝나지 않은 것이 하나라도 있으면 서버 요약이
 *  「끝나지 않은 작업 0」이라 해도 live 로 내린다. 목록 API 는 행 SELECT 와 요약 집계를 한 읽기 트랜잭션으로 묶지 않아,
 *  그 사이 자식이 끝나면 같은 응답 안에서 행은 Running 인데 요약은 0 이 된다 -- 그러면 바로 위 안내(「진행 중인 작업은
 *  선택할 수 없습니다」)와 모순되게 Running 행의 체크박스가 켜졌다(2026-10-10 검증 지적, 다음 폴링까지). 서버가 삭제 때
 *  재판정하므로 안전 문제는 아니고 화면의 정직함 문제다. 앞선 사유(진행 중 배치·상한 초과·요약 없음)는 그대로 둔다. */
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
    const block = batchBlock(r);
    if (block !== "ok" || liveRow.has(bid)) {
      groups.set(bid, { ...base, block: block === "ok" ? "live" : block });
      continue;
    }
    // ok 면 요약 필드는 전부 타입이 맞다(batchBlock 이 확인했다).
    groups.set(bid, {
      ...base, block,
      exists: r.batch_exists as boolean,
      status: typeof r.batch_status === "string" ? r.batch_status : null,
      request_count: r.batch_request_count as number,
      live: r.batch_live_request_count as number,
      scans: r.batch_succeeded_scan_count as number,
    });
  }
  return groups;
}

// ---- 배치 칸의 이름 표시 ----

// 대략의 표시 폭(한글·한자·전각 = 2, 그 밖 = 1). text-sm 에서 한글은 ~1em, 숫자·라틴은 ~0.55em 이라 이 비율이 맞다.
const WIDE = /[ᄀ-ᅟ⺀-〾ぁ-㏿㐀-䶿一-鿿가-힣豈-﫿︰-﹏＀-｠￠-￦]/u;
const unitsOf = (s: string): number => [...s].reduce((n, ch) => n + (WIDE.test(ch) ? 2 : 1), 0);
/** 6rem(96px)·text-sm 한 줄에 확실히 들어가는 폭(한글 ~5자) -- 이보다 길면 앞(text-sm)·뒤(text-xs) 두 줄로 나눈다. */
export const NAME_LINE_UNITS = 11;
const TAIL_UNITS = 10;

/** 배치 이름 → 표시용 앞·뒤 조각(짧아 한 줄에 들어가면 null). 뒤 조각은 이름의 **끝** TAIL_UNITS 폭(가능하면 낱말
 *  경계에서 시작) -- 접두가 같은 배치(「…이관(1차)」·「…이관(2차)」, 「d161 … 1007c」)를 가르는 것은 대개 끝이다. 한 줄
 *  truncate 는 이름 앞 5~6자만, 두 줄 line-clamp 도 앞 ~12자만 보여 끝이 늘 잘렸다(2026-10-10·10-11 검증 지적). 앞 조각은
 *  나머지 전부(화면이 한 줄 말줄임). 화면은 두 조각을 두 줄로 그린다(배치 칸은 모든 폭에서 6rem -- JobsList BATCH_W):
 *  앞 조각은 text-sm, 뒤 조각은 text-xs 이고, 고를 수 없는 묶음이면 뒤 조각 줄 자리에 사유 줄이 선다(칸이 상태와 무관하게
 *  두 줄 -- JobsList BatchCell). text-xs 라 TAIL_UNITS 폭은 6rem 에 여유 있게 들어간다(끝이 말줄임으로 잘리지 않는다). */
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
