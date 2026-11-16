import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Search } from "lucide-react";
import { useBatch, useBatchAll, useBatchItems, useBatchPayloads, useConfirmBatch, useRerunFailed, useRequestScanStats,
         useCancelBatch, useRescanBatch, useUpdateBatch, useDeleteBatch,
         useUpdateBatchItem, useDeleteBatchItem, useDeleteBatchItems,
         useAddBatchItem, useReplaceBatchItems, useRerunBatchItems,
         useDisableAutoConfirm, needsBatchConfirm, autoConfirmable } from "./useBatches";
import { BATCH_ITEMS_PAGE_SIZE, directToolsOf, ITEM_NOT_RUN, type BatchHeader } from "./batchView";
import { Pager } from "../../components/ui/Pager";
import { parseItemsCsv, serializeItemsCsv,
         type ItemsCsvOp, type ScanRow, type SyncRow } from "../../lib/csv";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { StatusPill } from "../../components/ui/StatusPill";
import { batchStateHelp } from "../../lib/stateHelp";
import { Button } from "../../components/ui/Button";
import { CancelButton } from "../../components/ui/CancelButton";
import { field } from "../jobs/formFields";
import { BatchExecutionSettingsDialog } from "./BatchExecutionSettingsDialog";
import { batchJobsPath, useRequestJobs } from "../jobs/useJobs";
import { reasonText, ApiError } from "../../lib/api";
import { absSummary, pathSummary, relativePathProblem } from "../../lib/storagePaths";
import { rmTargetOverlaps, type RmTargetEntry } from "../../lib/rmTargets";
import { toolSummary } from "../../lib/jobTool";
import { useStorageRoots } from "../storages/useUserStorages";
import { batchPillVariant, batchStatusLabel } from "../../lib/jobState";
import { chownHasName, syncOwnership } from "../../lib/syncOwnership";
import { kstStampEpoch, kstStampOrDash, untilText } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import { nextRunStamp, schedulableOp, scheduleEnabled, scheduleSummary } from "../../lib/batchSchedule";
import { kindsText, readSummary, RM_LINK_ONLY, sumKinds, summaryTool, type ActionKind } from "../../lib/toolSummary";
import { SYNC_OPTION_HELP } from "../jobs/optionRules";
import { humanBytes } from "../usage/usageStats";
import { SizeHistogram, TempHistogram, sizeHistogramTotal, tempAxes } from "../usage/ScanHistograms";
import type { Batch, BatchDirectTools, BatchItem, BatchPayloadItem } from "../../lib/types";

// 항목 행의 좌측 고정폭 합계 = 체크박스(w-5=1.25rem) + gap-3(0.75rem) +
// 순번(w-8=2rem) + gap-3(0.75rem). 펼침 상세·행 오류 문구를 이만큼 들여써야 행
// 요약 텍스트와 세로선이 맞는다 — 숫자를 각 자리에 흩뿌리면 열이 하나 늘 때마다
// 어긋난 들여쓰기가 남는다(선택 체크박스 추가 때 실제로 밀렸다).
const ITEM_INDENT = "ml-[4.75rem]";
// 펼침 패널의 섹션 구획: 요청 정보 dl · 데이터 온도 · 파일 크기 분포 · 요약이
// 여백만으로 이어져 한 덩어리로 읽혔다(사용자 지적 — "구분이 안 된다"). 섹션마다
// 위 구분선 + 상하 여백을 주고 소제목을 한 단 굵게 해서 경계를 눈으로 잡는다.
// 첫 섹션의 선은 행 요약과 상세의 경계 구실도 하므로 예외를 두지 않는다(first:
// 변형이 없어야 섹션이 늘어도 규칙이 그대로다). 선 색은 DS 토큰 border-line --
// 항목 행의 border-black/5 보다 한 단 진해 "행 구분"과 "섹션 구분"이 안 섞인다.
const PANEL_SECTION = "mt-4 border-t border-line pt-3";
const PANEL_TITLE = "font-semibold text-sm mb-2";
// 펼침 패널 안 dl(요청 정보·요약)의 정렬 기준 한 벌. 실측(d64): 요청 정보는
// grid-cols-[7rem_1fr](고정폭 라벨 열)인데 요약은 grid-cols-2 + max-w-md 라 값
// 열이 라벨에서 14rem 떨어진 자리 — 즉 패널 중앙 — 에서 시작했다. 히스토그램
// 섹션들이 전부 패널 왼쪽 끝에서 시작하는데 요약만 값이 가운데로 밀려 보인
// 이유다(사용자 지적). 라벨 열 폭은 9rem: 요약 키(total_entries 등 서버가 주는
// 원문 키)가 7rem 을 넘어 줄바꿈되는 걸 막으면서, 헤더 카드의 실행 설정 dl
// (grid-cols-[9rem_1fr])과도 같은 눈금이 된다. 두 dl 이 한 상수를 공유해야
// 섹션이 늘어도 기준이 한 곳에 남는다(ITEM_INDENT·PANEL_SECTION 과 같은 규약).
const PANEL_DL = "grid grid-cols-[9rem_1fr] gap-y-1 text-sm";
// 반복 스케줄이 켜진 배치의 취소 안내(버튼 title + 버튼 줄 아래 한 줄).
const REPEAT_CANCEL_NOTE = "이번 회차만 중단합니다 — 다음 예정 시각에 다시 실행됩니다. 반복을 멈추려면 스케줄을 끄세요.";
// 선택 불가 체크박스에 다는 사유(목록 화면 ACTIVE_HINT 관례 — disabled 로 끝내지
// 않고 이유와 동선을 남긴다). 표시 게이트일 뿐이고 진짜 차단은 서버 409 다.
const ITEM_LOCK_HINT = "진행 중인 배치에선 대기(Queued) 항목만 삭제할 수 있습니다";
// 재실행 가능 항목 = 서버 routes_batches._TERMINAL_ITEM 의 거울(표시 게이트일
// 뿐이고 진짜 판정은 서버의 SQL 원자 가드다 — 화면은 폴링 스냅샷을 본다).
const RERUNNABLE_ITEM = new Set(["Succeeded", "Failed", "Rejected", "Cancelled"]);
const RERUN_LOCK_HINT = "완료·실패·취소된 항목만 재실행할 수 있습니다";

// 항목 payload 조립: 스토리지는 입력받지 않는다 — 배치는 단일 스토리지 동질성
// 계약이라 기존 payload(수정) 또는 첫 항목 payload(추가·교체)에서 물려받는 것이
// 정직하다. 추가 팝업과 행 수정이 같은 조립을 쓰도록 모듈 스코프에 둔다.
const itemBody = (isSync: boolean, p: Record<string, unknown>,
                  path: string, dst: string) => isSync
  ? { source_storage: p.source_storage, source: path,
      destination_storage: p.destination_storage, destination: dst }
  : { storage: p.storage, target: path };


// payload 필드 결손 방어 + 대상 요약: 구 대시보드 최근 작업 카드의 summarize
// 관례 미러(scan/rm: storage:target, sync: src → dst). ?? 로만 접는다 — truthy
// 검사는 ""(빈 경로)를 "모름"으로 뭉갠다.
const part = (v: unknown) => String(v ?? "—");
function summarizeItem(operation: string | undefined,
                       p: Record<string, unknown>): string {
  const body = operation === "sync"
    ? `${part(p.source_storage)}:${part(p.source)} → ${part(p.destination_storage)}:${part(p.destination)}`
    : `${part(p.storage)}:${part(p.target)}`;
  return `${operation ?? "—"} · ${body}`;
}

// CSV 대화상자 연산: 항목 **모양**만 가른다(sync = 쌍, 나머지 = 대상 하나). 모르는 문자열은 1열(scan)로 읽는다 — 예전
// `isSync ? "sync" : "scan"` 과 같은 결이고, rm(2026-10-21)은 이름을 그대로 넘겨 교체 안내를 rm 문맥으로 말한다.
const csvOp = (op: string | undefined): ItemsCsvOp => (op === "sync" ? "sync" : op === "rm" ? "rm" : "scan");
// rm 미리보기 수 문구(단건 previewSummaryText·StageResults 와 같은 뜻): drm walk 는 대상 자신을 세므로 0 = 대상 경로를
// 찾지 못함(오타·이미 삭제·권한), 수가 아니면 모름 — null≠0 이라 둘을 다른 말로 한다.
const rmCount = (n: unknown): string => (typeof n !== "number" ? "모름"
  : n === 0 ? "0개(대상 경로를 찾지 못함)" : `${n.toLocaleString("ko-KR")}개`);
// rm 항목의 미리보기가 「대상 자신이 심볼릭 링크」라고 했나(target_kind, 2026-10-28 d194 -- drm 은 그 링크만 지운다). 수 옆의
// 표지·확인 창의 「링크 항목」 줄이 같은 판정을 쓴다. 모르면(옛 러너·대상 없음) false -- 링크라고 지어내지 않는다.
const rmLinkItem = (it: BatchItem): boolean => readSummary(it.preview_summary).targetKind === "link";
// 수 옆 표지(대상 경로를 못 찾은 0 에는 붙이지 않는다 -- 그쪽이 더 센 신호다).
const rmCountWithKind = (it: BatchItem): string => {
  const n = it.preview_summary?.files;
  return `${rmCount(n)}${rmLinkItem(it) && n !== 0 ? `(${RM_LINK_ONLY})` : ""}`;
};
const nf = (n: number) => n.toLocaleString("ko-KR");
// sync 항목 결과 수(files_count)의 이름(2026-10-28 리뷰 portal-batch-29·contract-37). 「반영한 항목」은 d192 이후의 뜻(nsync
// 완료 액션 합·dsync 복사 성공 수, 미리보기 실패는 승격하지 않음)일 때만 참이다 -- 그 전 기록의 files_count 는 nsync 계획 합
// (실패한 실행 포함)·dsync 의 실패 포함 Items·미리보기 실패에서 승격된 계획 수일 수 있다(C8: 1개 반영인데 4). 배치 항목 API 는
// 결과 summary 를 싣지 않고 files_count 만 조인하므로, 같은 자식 잡의 **미리보기** summary 가 v2(detail_version)인지를 「이
// 잡은 d192 이후 러너로 돌았다」의 대리 신호로 쓴다 -- 실행은 그 미리보기 뒤에 시작하니 롤백이 아니면 더 옛 러너로 돌 일이
// 없다. 대리 신호가 없으면(옛 미리보기·미리보기 summary 없음) 요청 상세 실행 결과의 옛 요약 라벨과 같은 중립 「항목 수」.
const syncCountLabel = (it: BatchItem): string =>
  (readSummary(it.preview_summary).detailed ? "반영한 항목" : "항목 수");
// sync 항목 펼침의 미리보기 행(2026-10-27 d192): 확인 창 합계와 같은 뜻 구분(previewTotals) -- nsync 는 「변경 예정」과
// 종류별 수, dsync 는 「소스 전체」(변경 수 아님), 도구를 모르는 옛 항목은 뜻을 가르지 않고 수만. 모르는 수는 「모름」.
// 도구는 서버 조인 job_tool(자식 잡의 실제 도구) 먼저, 없으면 summary v2 의 tool(옛 미리보기도 가른다 -- summaryTool 한 판정,
// 백엔드 batch_orchestrator 의 files_by_tool 과 같은 순서).
function SyncPreviewRow({ summary, jobTool }: { summary: unknown; jobTool?: string | null }) {
  const s = readSummary(summary);
  const tool = summaryTool(s, jobTool);
  if (tool === "nsync") {
    const n = s.files ?? sumKinds(s.planned);
    return (<>
      <dt className="text-muted">변경 예정(미리보기)</dt>
      <dd>{n === null ? "모름" : `${nf(n)}개${s.planned !== null ? ` — ${kindsText(s.planned)}` : ""}`}</dd>
    </>);
  }
  if (tool === "dsync") {
    const n = s.sourceItems ?? s.files;
    return (<>
      <dt className="text-muted">소스 전체(미리보기)</dt>
      <dd>{n === null ? "모름" : `${nf(n)}개(변경 수는 실행 후 확정)`}</dd>
    </>);
  }
  return (<>
    <dt className="text-muted">미리보기 수</dt>
    <dd className="tabular-nums">{s.files === null ? "모름" : nf(s.files)}</dd>
  </>);
}
// 기존 항목의 rm 대상 → 겹침 즉답 재료(lib/rmTargets). 대상이 문자열이 아니면(DB 변조) 판정에서 뺀다 — 서버도 형식이
// 틀린 대상은 겹침에서 건너뛴다.
const rmEntriesOf = (items: BatchPayloadItem[], skipSeq?: number): RmTargetEntry[] =>
  items.flatMap((it) => (it.seq !== skipSeq && typeof it.payload?.target === "string"
    ? [{ label: `기존 #${it.seq}`, target: it.payload.target }] : []));

// 옵션 humanize: scan/sync 옵션은 평평한 스칼라 맵(batch_files 등)이라 키=값
// 나열이 JSON 원문보다 읽기 쉽다. 비스칼라 값만 JSON 으로 접는다(방어 — 현행
// 계약엔 없지만 [object Object] 를 찍는 것보다 낫다). 빈 옵션은 "없음" — 빈
// 문자열·"{}" 는 화면에서 "모름"처럼 읽힌다. undefined 는 fixture 호환일 뿐,
// 실서버는 options 를 늘 보낸다(types.ts Batch 주석).
function humanizeOptions(options: Record<string, unknown> | undefined): string {
  const entries = Object.entries(options ?? {});
  if (entries.length === 0) return "없음";
  return entries
    .map(([k, v]) =>
      `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

// 실행 제어 설정(읽기 전용): 배치 생성 시 고른 값이 상세에서 안 보여 "무슨
// 설정으로 돌았나"를 알 수 없었다. null = 정책 기본(null≠0) — 미지정을 0·빈값
// 으로 뭉개지 않고 명시 문구로 말한다. 접이식(details)을 택한 이유: 상시 참조
// 정보가 아니라(사후 확인용) 항상 펼치면 항목 목록이 설정 행수만큼 매번 밀린다
// — 헤더 카드에 summary 한 줄로 접어 두고 필요할 때 연다. 실행 권한 표기는
// 기존 헤더의 "특권 실행(root)" 문구 재사용 — 여기 중복하지 않는다.
// direct 는 dsync 만 적용하고 nsync 는 받기만 한다(포크 db1e33c -- 괴리 [22]). 배치 API 엔 도구가 없어(배치 시점에 갈린다)
// 항목의 도구(summaryTool -- 서버 조인 job_tool 먼저, 없으면 미리보기 summary v2 의 tool)로 센다 -- 둘 다 없는 항목(미리보기
// 전·잡 없음)은 모름이라 세지 않는다.
// null = 말할 것 없음(direct 꺼짐 또는 nsync 로 기록된 항목 없음).
// 셈(도구가 확인된 수·nsync 수·미정 수)은 2026-11-16 부터 화면이 전 항목을 들고 있지 않아 서버 summary 의 direct_tools 가 준다
// (같은 규칙 -- batchView.directToolsOf 가 옛 파드 응답·확인 창의 전체 조회에서 같은 셈을 한다). 여기는 문장만 만든다.
// 「모두」는 **모든 항목**의 도구를 알 때만 말한다(2026-10-28 리뷰 portal-batch-28): 도구는 항목마다 배치(placement) 시점에
// 갈리고(공존 노드가 있으면 dsync) 아직 materialize 되지 않은 항목은 도구가 없다 -- 동시 실행 상한 1 로 미리보기 중인 배치의
// 첫 항목만 nsync 로 정해졌을 때 「항목이 모두 nsync」라 하면 뒤의 dsync 항목(O_DIRECT 적용)까지 아니라고 말하게 된다. 도구를
// 모르는 항목이 있으면 도구가 확인된 항목 중의 수로만 말하고 나머지는 미정이라고 밝힌다. 도구 없이 끝난 항목(거부·실패·취소
// -- 잡이 만들어지지 않았다)은 앞으로도 돌지 않으니 「미정」에 넣지 않는다(넣으면 끝난 배치에서도 「모두」를 영영 못 말한다).
function directNote(b: Batch, tools: BatchDirectTools | null): string | null {
  if (b.options?.direct !== true || tools === null) return null;
  const { tools_known: known, nsync, undecided } = tools;
  if (nsync === 0) return null;
  // 현재형으로 말한다 -- 확인 창(실행 전)과 실행 설정(실행 후) 둘 다 같은 배치(placement)의 도구다.
  if (undecided > 0) {
    return `도구가 확인된 항목 ${known}개 중 nsync 로 실행되는 ${nsync}개에는 direct 가 적용되지 않습니다(dsync 만 `
      + `O_DIRECT 로 읽고 씁니다) — 나머지 ${undecided}개 항목은 아직 도구가 정해지지 않았습니다`;
  }
  return nsync === known
    ? "direct 는 적용되지 않습니다 — 항목이 모두 nsync 로 실행됩니다(dsync 만 O_DIRECT 로 읽고 씁니다)"
    : `direct 는 nsync 로 실행되는 항목(${nsync}개)에는 적용되지 않습니다(dsync 만 O_DIRECT 로 읽고 씁니다)`;
}

function BatchSettings({ b }: { b: BatchHeader }) {
  const direct = directNote(b, b.direct_tools);
  return (
    <details className="mt-2">
      <summary className="text-sm text-muted cursor-pointer">실행 설정</summary>
      <dl className="mt-2 grid grid-cols-[9rem_1fr] gap-y-1 text-sm max-w-xl">
        <dt className="text-muted">우선순위</dt>
        <dd>{b.priority ?? "정책 기본"}</dd>
        <dt className="text-muted">노드 수</dt>
        <dd className="tabular-nums">{b.node_count ?? "정책 기본"}</dd>
        <dt className="text-muted">노드당 프로세스</dt>
        <dd className="tabular-nums">{b.procs_per_node ?? "정책 기본"}</dd>
        <dt className="text-muted">동시 실행 상한</dt>
        <dd className="tabular-nums">{b.max_concurrency}</dd>
        <dt className="text-muted">옵션</dt>
        <dd className="min-w-0">
          <span className="font-mono text-xs break-all">{humanizeOptions(b.options)}</span>
          {direct !== null && <span className="block text-xs text-muted">{direct}</span>}
        </dd>
        {/* 실행 신원(라벨 정정 2026-08-16 — owner_username 은 소유자 기록이 아니라
            잡의 실행 신원이다, identity.resolve_job_identity)은 있을 때만 —
            없으면 행 생략(빈칸 소음 방지, 메모 관례) */}
        {b.owner_username && (<>
          <dt className="text-muted">실행 신원</dt>
          <dd>{b.owner_username}</dd>
        </>)}
        {/* 실행 확인 방식(자동 확인 sync 2026-10-11·rm 2026-10-21) — scan 은 확인 단계가 없어 행 생략. */}
        {needsBatchConfirm(b.operation) && (<>
          <dt className="text-muted">실행 확인</dt>
          <dd className={b.operation === "rm" && b.auto_confirm === true ? "text-bad" : undefined}>
            {b.auto_confirm === true && autoConfirmable(b.operation)
              ? `${b.operation === "rm" ? "자동 — 미리보기가 끝나면 확인 없이 삭제" : "자동 — 미리보기가 끝나면 바로 실행"}${
                  b.auto_confirm_by ? ` (켠 사람 ${b.auto_confirm_by})` : ""}`
              : "직접 — 미리보기 후 「배치 확인」"}</dd>
        </>)}
      </dl>
      {/* 2026-10-02 부터 종단 배치는 실행 설정을 바꿀 수 있어(실행 설정 변경) 이 값이 이미 끝난 항목이 실제로 돈 값과
          다를 수 있다(적대적 리뷰) — 항목별 실제 값은 자식 요청 payload 에 남는다. 언제나 참인 문장으로 말한다. */}
      <p className="mt-1 text-xs text-muted">다음 실행에 쓰이는 값입니다 — 항목별 실제 실행 값은 각 요청 상세에 남습니다.</p>
    </details>
  );
}

// 실행 도구 행(요청 정보 dl 안): 배치 API 는 tool 을 싣지 않는다 — list_items_detail
// 의 조인은 요청 상태·파일 수·완료 시각뿐이다(실측). 그래서 **펼친 항목에 한해**
// 자식 요청의 잡을 조회한다(ItemScanStats 와 같은 lazy 계약: 마운트가 1차 게이트,
// enabled 가 2차). 요청당 잡이 여럿일 수 있어(취소·재실행 경로) 최신 잡을 읽는다 —
// 서버 list_jobs 가 created_at DESC 정렬이라 [0] 이 최신이고, 이는 파일 수 조인이
// 최신 잡을 고르는 기준과 같다.
// 모름(계획 전·잡 없음·조회 실패)이면 행 자체를 안 그린다: 같은 dl 의 절대경로 행과
// 같은 조건부 규약이고, 요청 상세 잡 카드와도 같은 규칙이다(null≠0 — "—" 는
// "도구 없이 돌았다"로 읽히는 거짓 표시다).
function ItemTool({ requestId }: { requestId: string | null }) {
  const q = useRequestJobs(requestId ?? "", requestId !== null);
  const text = toolSummary(q.data?.[0]);
  if (text === null) return null;
  return (<>
    <dt className="text-muted">도구</dt>
    <dd>{text}</dd>
  </>);
}

// 항목별 데이터 온도 섹션: 펼친 항목에서만 마운트된다(= lazy 조회의 1차 게이트,
// 훅 enabled 가 2차로 성공 요청만 통과시킨다). 성공 요청만 리포트를 가지므로
// 미성공 항목은 조회 없이 "리포트 없음"이 정직하고, 404 no_scan_report(성공인데
// 리포트 부재·유실)도 화면엔 같은 사실이라 같은 문구다 — 그 외 오류(503 등)는
// 사유 문구를 그대로 보인다. 온도 토글 상태는 컴포넌트 지역(단일 펼침이라 행
// 전환 시 atime 기본으로 리셋되는 게 자연스럽다).
function ItemScanStats({ requestId, succeeded }: {
  requestId: string | null; succeeded: boolean;
}) {
  const q = useRequestScanStats(requestId, succeeded);
  const [tempKey, setTempKey] = useState("atime");
  const noReport = !succeeded
    || (q.isError && (q.error as ApiError).code === "no_scan_report");
  if (noReport) {
    return <section className={PANEL_SECTION}><p className="text-muted text-sm">리포트 없음</p></section>;
  }
  if (q.isError) {
    return <section className={PANEL_SECTION}><p className="text-bad text-sm">{(q.error as ApiError).message}</p></section>;
  }
  const stats = q.data;
  if (!stats) return null;               // 로딩 — 짧은 창이라 문구 없이 둔다
  // 차트 몸통(막대·캡션)은 사용량 상세와 공용(usage/ScanHistograms, 2026-11-16) -- 섹션 틀·축 버튼·요약은 여기 것이다.
  const tempKeys = tempAxes(stats.time_histograms);
  const sizeTotal = sizeHistogramTotal(stats.file_size_histogram);
  return (<>
    <section className={PANEL_SECTION}>
      <h3 className={PANEL_TITLE}>데이터 온도(hot/cold)</h3>
      {/* 언제 찍힌 숫자인지 없이 보여주는 건 부정직이다(ScanPaths 관례 미러) */}
      <p className="text-muted text-xs mb-2">
        {typeof stats.generated_at_epoch === "number"
          ? `scan 리포트 생성: ${kstStampEpoch(stats.generated_at_epoch)}`
          : "scan 리포트 생성 시각을 알 수 없습니다"}
      </p>
      {/* 실 사용량(2026-08-23): 서버가 온도 히스토그램에서 투영한 파일 크기 합.
          클라이언트 재합산(차트 캡션의 총 용량)과 출처는 같지만 서버 한 값을 믿는다 --
          화면마다 다시 더하면 규칙(mtime 우선·오염 부적격)이 흩어진다.
          null/부재는 모름(구형 서버·오염 리포트)이라 "—"(null≠0). */}
      <p className="text-sm mb-2">
        실 사용량(파일 크기 합):{" "}
        <strong>{typeof stats.total_bytes === "number"
          ? humanBytes(stats.total_bytes) : "—"}</strong>
      </p>
      <div className="flex gap-2 mb-2">
        {tempKeys.map((k) => (
          <Button key={k} type="button"
                  variant={tempKey === k ? "outline" : "ghost"}
                  onClick={() => setTempKey(k)}>{k}</Button>
        ))}
      </div>
      <TempHistogram buckets={stats.time_histograms[tempKey]} axis={tempKey} />
    </section>
    <section className={PANEL_SECTION}>
      {/* 총 개수는 제목에(2026-10-02 사용자 요청 -- 히스토그램 밑 회색 캡션에 묻혀 있었다), 버킷별 개수는 막대에
          마우스를 올리면(온도 차트와 같은 valueLabels=false). 개수를 모르는 구간이 있으면(null) 총계를 말하지 않는다. */}
      <h3 className={PANEL_TITLE}>
        {sizeTotal !== null && sizeTotal > 0
          ? `파일 크기 분포(개수) · 총 ${sizeTotal.toLocaleString("ko-KR")}개` : "파일 크기 분포(개수)"}
      </h3>
      <SizeHistogram buckets={stats.file_size_histogram} />
    </section>
    <section className={PANEL_SECTION}>
      <h3 className={PANEL_TITLE}>요약</h3>
      <dl className={PANEL_DL}>
        {Object.entries(stats.summary).map(([k, v]) => (
          <Fragment key={k}>
            <dt className="text-muted">{k}</dt>
            <dd className="tabular-nums">{v}</dd>
          </Fragment>
        ))}
      </dl>
      {/* null = 구형 리포트(총계 미기록) — 0(파손 없음)과 구분해 말한다 */}
      <p className="text-muted text-sm mt-2">
        {typeof stats.broken_paths_total === "number"
          ? `파손 경로 ${stats.broken_paths_total}건`
          : "파손 경로: 기록 없음(구형 리포트)"}
      </p>
    </section>
  </>);
}

// 항목 편집 도구 3종(현재 항목 CSV·항목 추가·CSV로 전체 교체)은 **버튼 + 팝업**
// 이다(사용자 지시). 셋 다 가끔 쓰는 도구인데 인라인으로 카드 아래 세로로 늘어놓
// 으면 접이식이어도 입력·textarea 가 상시 자리를 차지해 항목 목록이 그만큼 밀렸다.
// 모달 프레임은 공용 Dialog 재사용(ConfirmDialog·DeleteBatchButton 선례) — 새
// 프레임을 만들지 않는다. 노출 조건(교체=종단 배치, 나머지=항목 존재)은 인라인
// 시절 그대로 트리거 버튼에 옮겨 붙였다: 표시 게이트일 뿐 진짜 차단은 서버다.

// CSV 붙여넣기로 항목 전체 교체(종단 배치만 노출 — 진짜 가드는 서버
// batch_items_not_replaceable 409). 교체 후에도 배치는 종단 유지 — 교체가 곧
// 실행은 아니다(재실행은 기존 「전체 재실행」 버튼 몫). 파일 업로드가 아니라
// textarea 붙여넣기인 이유: 운영 환경 브라우저는 파일 업로드가 불가하다(환경
// 제약) — 배치 생성(BatchCreate)의 CSV 붙여넣기 패턴을 미러한다. 동선:
// 붙여넣기 → parseItemsCsv 파싱(입력에서 즉시 파생) → 미리보기(행 수·오류) →
// 「교체」 확인 클릭 → PUT → 팝업 닫힘. 오류가 하나라도 있으면 교체 버튼 잠금
// (부분 반영 금지 — BatchCreate applyParsed 와 같은 계약). 빈 입력(초기 상태)은
// 미리보기·버튼 자체를 안 그린다 — "아직 안 붙여넣음"과 "0행 파싱"(헤더만)의 구분.
// 배치 레벨 스토리지: 배치 행엔 스토리지 메타가 없다(실측 — batches 테이블은
// 실행 제어·옵션만). 종단 배치는 항목이 반드시 있으므로 첫 항목 payload 에서
// 물려받는다(항목 추가와 같은 동질성 계약) — 항목 0개면 호출측이 이 컴포넌트
// 자체를 안 그린다(물려받을 스토리지가 없다).
function ReplaceItemsCsvDialog({ batchId, operation, firstPayload, chown, auto = false }: {
  batchId: string; operation: ItemsCsvOp;
  firstPayload: Record<string, unknown>; chown: string;
  // 자동 실행이 켜져 있고 보류가 아님(AddItemDialog 와 같은 식) -- rm 이면 「배치 확인」을 거친다는 약속이 거짓이 된다(리뷰).
  auto?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const replace = useReplaceBatchItems(batchId);
  const [text, setText] = useState("");
  // 닫힐 때마다 드래프트·오류를 비운다(DeleteBatchButton 선례) — "닫기"는
  // setOpen(false) 를 직접 불러 Radix onOpenChange 가 발화하지 않는다. 재오픈이
  // 지난 시도의 잔상(붙여넣은 CSV·실패 사유)으로 시작하면 오조작을 부른다.
  useEffect(() => { if (!open) { setText(""); replace.reset(); } }, [open]);
  // 상태가 아니라 파생: 입력이 곧 진실이라 파싱 결과를 별도 상태로 들고 있으면
  // 둘이 어긋날 수 있다. null = 빈 입력(미리보기 없음).
  const parsed = text.trim() === "" ? null : parseItemsCsv(operation, text);
  const rows = parsed?.rows ?? [];
  const errors = parsed?.errors ?? [];
  const items = rows.map((r) => operation === "sync"
    ? { source_storage: firstPayload.source_storage, source: (r as SyncRow).source,
        destination_storage: firstPayload.destination_storage,
        destination: (r as SyncRow).destination }
    : { storage: firstPayload.storage, target: (r as ScanRow).target });
  // rm 교체 즉답(생성 화면과 같은 규칙 — 서버 422 batch_rm_targets_overlap·rm_root_forbidden 의 미러).
  const rmProblems = operation === "rm"
    ? [...rows.flatMap((r, i) => {
        const p = relativePathProblem((r as ScanRow).target);
        return p === null ? [] : [`${i + 1}행: ${p}`];
      }),
       ...rmTargetOverlaps(rows.map((r, i) => ({ label: `${i + 1}행`, target: (r as ScanRow).target })))]
    : [];
  return (
    <Dialog open={open} onOpenChange={setOpen} title="CSV로 전체 교체"
            trigger={<Button variant="ghost">CSV로 전체 교체</Button>}>
      <div className="space-y-2">
        <p className="text-muted text-sm">기존 항목이 모두 사라지고 붙여넣은 CSV 로 대체됩니다.</p>
        {operation === "sync" && <SyncDestHint chown={chown} />}
        {operation === "rm" && (
          <p className="text-xs text-bad" role="note" aria-label="삭제 경로 교체">
            {auto
              ? "교체만으로는 아무것도 지워지지 않습니다 — 단, 자동 실행이 켜져 있어 재실행하면 미리보기가 끝나는 즉시 "
                + "「배치 확인」 없이 이 경로들이 root 로 삭제됩니다. 되돌릴 수 없습니다."
              : "교체만으로는 아무것도 지워지지 않습니다 — 재실행하면 미리보기 뒤 「배치 확인」을 거쳐 이 경로들이 root 로 "
                + "삭제됩니다."}
          </p>
        )}
        <label className="text-sm block">
          {`행 형식 — ${operation === "sync" ? "행당 source,destination" : "행당 경로 1개"}`}
          <textarea aria-label="교체 CSV" className={`${field} h-40 font-mono`}
                    placeholder={operation === "sync"
                      ? "team/dataset,backup/dataset\nprojects/alpha,backup/alpha"
                      : "team\nprojects/alpha"}
                    value={text}
                    onChange={(e) => { setText(e.target.value);
                                       replace.reset(); /* 이전 시도 잔상 제거 */ }} />
        </label>
        {/* 미리보기(행 수)와 확인 버튼은 입력이 있을 때만 — 무엇으로 바꾸는지
            보여주기 전의 교체 버튼은 오클릭 유도다. 0행(헤더만)도 잠금 — 서버
            empty_batch 재확인 전에 화면에서 정직하게 막는다. */}
        {parsed !== null && (
          <p className="text-sm text-muted">{`${rows.length}행 파싱됨`}</p>
        )}
        {errors.length > 0 && (
          <ul className="text-bad text-sm space-y-1">
            {errors.map((err, i) => <li key={i}>{err}</li>)}
          </ul>
        )}
        {rmProblems.length > 0 && (
          <ul aria-label="교체할 수 없는 삭제 경로" className="text-bad text-sm space-y-1">
            {rmProblems.map((err) => <li key={err}>{err}</li>)}
          </ul>
        )}
        {replace.isError && (
          <p className="text-bad text-sm">{(replace.error as ApiError).message}</p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          {parsed !== null && (
            <Button disabled={errors.length > 0 || rmProblems.length > 0 || rows.length === 0 || replace.isPending}
                    onClick={() => replace.mutate(items,
                      { onSuccess: () => setOpen(false) })}>교체</Button>
          )}
        </div>
      </div>
    </Dialog>
  );
}

// 현재 항목 CSV(읽기 전용): 항목들을 serializeItemsCsv 텍스트로 노출해 사용자가
// 직접 선택·복사한다. 「복사 버튼」이 아닌 이유: 운영 포탈은 http 비보안
// 컨텍스트라 navigator.clipboard 가 부재한다(BatchCreate downloadCsv 와 같은
// 제약 — clipboard API 금지). onFocus 전체 선택은 DOM Selection 이라 어디서나
// 동작한다. 헤더 포함 직렬화라 교체 팝업에 그대로 붙여넣으면 같은 항목이
// 복원된다(왕복 계약).
// 항목은 창을 **열 때** 한 번 받는다(2026-11-16 ?view=payloads -- 화면이 더는 전 항목을 들고 있지 않다). 왕복 계약이라 쪽·검색·
// 상태 거르기와 무관하게 늘 전 항목이다. 직렬화도 열린 동안만(본문은 Dialog 가 열렸을 때만 마운트된다) -- 예전엔 닫혀 있어도
// 매 렌더 전 항목을 직렬화했다.
function CurrentItemsCsvBody({ batchId, operation }: { batchId: string; operation: ItemsCsvOp }) {
  const q = useBatchPayloads(batchId, true);
  if (q.isError) return <p className="text-bad text-sm">{(q.error as Error).message}</p>;
  if (q.data === undefined) return <p className="text-muted text-sm">불러오는 중…</p>;
  const text = serializeItemsCsv(operation, q.data.map((it) => operation === "sync"
    ? { source: String(it.payload?.source ?? ""),
        destination: String(it.payload?.destination ?? "") }
    : { target: String(it.payload?.target ?? "") }));
  return (
    <textarea aria-label="현재 항목 CSV" readOnly value={text}
              className={`${field} h-40 font-mono`}
              onFocus={(e) => e.currentTarget.select()} />
  );
}
function CurrentItemsCsvDialog({ batchId, operation }: { batchId: string; operation: ItemsCsvOp }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="현재 항목 CSV"
            trigger={<Button variant="ghost">현재 항목 CSV</Button>}>
      <div className="space-y-2">
        {/* 클릭 시 전체 선택되는 건 화면에 안 쓰면 모르는 동작이라 한 줄로 말한다 */}
        <p className="text-muted text-sm">클릭하면 전체 선택됩니다 — 복사해서 「CSV로 전체 교체」에 그대로 붙여넣을 수 있습니다.</p>
        <CurrentItemsCsvBody batchId={batchId} operation={operation} />
        <div className="flex justify-end pt-1">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
        </div>
      </div>
    </Dialog>
  );
}

// sync 항목의 목적지 조건·소유 한 줄(2026-10-01 "목적지가 없는 경우" + 소유 안내를 운영자 화면 전부에 --
// 배치 생성(BatchCreate) 안내와 같은 규칙: preflight _DEST_CHECK, 소유는 lib/syncOwnership. 배치는 root 라
// chown 미지정이면 소스 그대로). 항목 추가·수정·CSV 교체가 모두 새 목적지를 정하는 곳이라 같은 줄을 단다.
function SyncDestHint({ chown }: { chown: string }) {
  // short 문구만 쓴다(chmod 는 short 에 나타나지 않는다) -- 상세 규칙은 배치 생성 화면의 안내가 말한다.
  return (
    <p className="text-xs text-muted" role="note" aria-label="목적지 조건과 소유권">
      목적지가 없으면 새로 만듭니다(상위 디렉토리는 이미 있어야 하고, 중간 디렉토리는 만들지 않음) · 소유:{" "}
      {syncOwnership({ chown, root: true, runAs: null, self: true }).short}
    </p>
  );
}

// 항목 추가(경로만 입력 — 스토리지는 첫 항목에서 물려받는다). 종단 배치에
// 추가하면 서버가 재활성화(scan→Running/sync·rm→Previewing)하고 신규 Queued 항목만
// 실행된다. 성공 시 팝업을 닫는다: 추가된 항목은 뒤의 목록에 나타나므로 화면에
// 남아 있을 이유가 없다(invalidate 는 훅 onSettled 몫). 항목이 없으면 물려받을
// 스토리지가 없어 호출측이 버튼 대신 안내를 보인다.
function AddItemDialog({ batchId, isSync, firstPayload, chown, status, auto, confirmFlow, isRm }: {
  batchId: string; isSync: boolean; firstPayload: Record<string, unknown>; chown: string; status: string;
  // 자동 확인이 켜져 있고 **보류되지 않은** 배치만 true -- 보류 중이면 종전처럼 사람이 확인한다(문구도 그 말).
  auto: boolean;
  // 확인 흐름(sync·rm — needsBatchConfirm): 진행 중·확인 대기 배치에 추가하면 다시 미리보기 → 「배치 확인」을 거친다.
  confirmFlow: boolean;
  // rm 배치면 기존 대상과의 겹침을 즉답한다(재료는 창을 열 때 받는다 -- 아래).
  isRm: boolean;
}) {
  const [open, setOpen] = useState(false);
  const add = useAddBatchItem(batchId);
  const [path, setPath] = useState("");
  const [dst, setDst] = useState("");
  useEffect(() => { if (!open) { setPath(""); setDst(""); add.reset(); } }, [open]);
  // rm 겹침 재료(2026-11-16): 화면이 전 항목을 들고 있지 않아 rm 창을 **열 때** 전 항목(종단 포함 -- 서버 validate_batch 와
  // 같은 집합)을 한 번 받는다(?view=payloads). 받는 동안은 「항목 추가」를 잠근다 -- 즉답 없이 눌리면 겹침을 서버 422 로만 알게
  // 된다. 받지 못하면(오류) 잠그지 않고 그 사실을 말한다: 최종 심판은 서버라 즉답이 없다고 막으면 추가 자체를 못 한다.
  const payloads = useBatchPayloads(batchId, open && isRm);
  // isFetching 까지 본다: 다시 열면 지난번 목록이 캐시에 남은 채 새로 받는다 -- 그 사이 낡은 목록으로 즉답하지 않게.
  const rmLoading = isRm && (payloads.data === undefined || payloads.isFetching) && !payloads.isError;
  // 받은 목록이 바뀔 때만 다시 만든다 -- 경로 칸 키 입력마다 전 항목을 훑지 않게(겹침 판정 자체는 입력마다 돈다).
  const rmExisting: RmTargetEntry[] | null = useMemo(
    () => (!isRm ? null : payloads.data ? rmEntriesOf(payloads.data) : []), [isRm, payloads.data]);
  // rm 즉답(2026-10-21): 관리 디렉토리 자신·「..」·「/」, 그리고 기존 항목과의 겹침(종단 항목 포함 — 서버 validate_batch 와
  // 같은 집합)을 누르기 전에 말한다. 서버 422(rm_root_forbidden·unsafe_path·batch_rm_targets_overlap)가 최종 심판이다.
  const rmProblems = rmExisting === null || path.trim() === "" ? []
    : (() => {
        const p = relativePathProblem(path);
        if (p !== null) return [p];
        return rmTargetOverlaps([...rmExisting, { label: "추가할 경로", target: path }], rmExisting.length);
      })();
  return (
    <Dialog open={open} onOpenChange={setOpen} title="항목 추가"
            trigger={<Button variant="ghost">항목 추가</Button>}>
      <div className="space-y-2">
        {isSync ? (<>
          <label className="text-sm block">추가할 소스 경로
            <input aria-label="추가할 소스 경로" className={field} value={path}
                   onChange={(e) => setPath(e.target.value)} />
          </label>
          <label className="text-sm block">추가할 목적지 경로
            <input aria-label="추가할 목적지 경로" className={field} value={dst}
                   onChange={(e) => setDst(e.target.value)} />
          </label>
          <SyncDestHint chown={chown} />
        </>) : (
          <label className="text-sm block">추가할 대상 경로
            <input aria-label="추가할 대상 경로" className={field} value={path}
                   onChange={(e) => setPath(e.target.value)} />
          </label>
        )}
        {rmProblems.length > 0 && (
          <ul aria-label="추가할 수 없는 삭제 경로" className="text-bad text-sm space-y-1">
            {rmProblems.map((m) => <li key={m}>{m}</li>)}
          </ul>
        )}
        {rmLoading && <p className="text-xs text-muted">기존 항목을 불러오는 중… — 겹침을 확인한 뒤 추가할 수 있습니다.</p>}
        {isRm && payloads.isError && (
          <p className="text-xs text-muted">기존 항목을 읽지 못해 겹침은 추가할 때 서버가 판정합니다.</p>
        )}
        {/* 확인 흐름 안내는 연산 모양(쌍/대상 하나)과 무관하다 -- sync·rm 둘 다(rm 2026-10-21, 문구는 그대로 참이다:
            삭제도 「실행」이다). scan 은 확인 단계가 없어 안내가 없다. */}
        {confirmFlow && (<>
          {/* 서버 계약(2026-10-07): 진행 중·확인 대기 확인 게이트 배치에 추가하면 배치가 미리보기 단계로 돌아간다 --
              새 경로가 사람 확인 없이 root 로 돌지 않게. 누르기 전에 그 결과를 말한다. */}
          {/* 자동 확인(2026-10-11)이 켜진 배치는 새 항목도 미리보기가 끝나면 확인 없이 실행된다 -- 아래 「다시 눌러야」
              문구가 거짓이 되므로 대신 이 사실을 말한다. */}
          {auto && (
            <p className={`text-xs ${isRm ? "text-bad" : "text-muted"}`}>
              {isRm
                ? "자동 실행이 켜진 배치입니다 — 추가한 경로도 미리보기가 끝나면 「배치 확인」 없이 삭제(root)됩니다. 되돌릴 수 없습니다."
                : "자동 실행이 켜진 배치입니다 — 추가한 항목도 미리보기가 끝나면 「배치 확인」 없이 실행(root)됩니다."}
            </p>
          )}
          {!auto && status === "PreviewReady" && (
            <p className="text-xs text-muted">
              확인 대기 중인 배치에 추가하면 배치가 다시 미리보기 단계로 돌아갑니다 — 새 항목의 미리보기가 끝나면
              「배치 확인」을 다시 눌러야 실행됩니다.
            </p>
          )}
          {!auto && status === "Running" && (
            <p className="text-xs text-muted">
              진행 중인 배치에 추가하면 배치가 다시 미리보기 단계로 돌아갑니다 — 이미 실행 중인 항목은 끝까지
              실행(root)되고, 실행을 기다리던 항목은 다시 확인할 때까지 시작되지 않습니다. 새 항목의 미리보기가 끝나면
              「배치 확인」을 다시 눌러야 나머지가 실행되며, 그 사이 미리보기가 만료된 항목은 거부(실패로 집계)됩니다.
            </p>
          )}
        </>)}
        {add.isError && (
          <p className="text-bad text-sm">{(add.error as ApiError).message}</p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          <Button disabled={add.isPending || rmProblems.length > 0 || rmLoading}
                  onClick={() => add.mutate(itemBody(isSync, firstPayload, path, dst),
                    { onSuccess: () => setOpen(false) })}>항목 추가</Button>
        </div>
      </div>
    </Dialog>
  );
}

// 배치 확인(2026-10-07): 예전엔 한 번 클릭이 곧 :confirm 이었다 -- 운영자는 무엇을 승인하는지(몇 개 항목이 root 로
// 도는지·delete 옵션·미리보기 만료)를 보지 못한 채 실행을 시작했다(원 설계 slice2 의 "집계 미리보기 + 확인"과도
// 달랐다). 이제 대화상자가 확인의 범위를 먼저 보이고 「실행 확인」에서만 보낸다. 항목별 확인은 없다 -- 이 1회 확인이
// 미리보기를 마친 항목 전부를 대표하고, 그 뒤 orchestrator 가 슬롯만큼씩 실행한다(서버 계약, 확인자는 감사 행).
// 확인 회차(preview_round): 대화상자를 연 순간의 회차를 고정해 보낸다. 연 뒤 배치가 다시 미리보기를 거쳐 확인 대기가
// 되면(항목 추가 등 -- 숨은 탭이라 폴링이 멈춰 그 사이를 못 봤어도) 회차가 달라 서버가 409 batch_preview_changed 로
// 거절하고, 화면도 회차가 바뀌면 「실행 확인」을 잠그고 다시 열라고 말한다 -- 운영자가 본 적 없는 항목이 확인에 묻지 않게.
// 2026-11-16 부터 화면이 전 항목을 들고 있지 않아 창을 열 때 전체를 한 번 받고(?view=all, useBatchAll), **그 응답**의 회차를
// 고정한다 -- 회차와 보여 준 항목이 같은 응답에서 와야 한다(낡은 항목 + 새 회차면 본 적 없는 항목을 승인한다). 받기 전엔
// 회차가 없어(null) 「실행 확인」이 잠긴다. 「바뀌었다」 판정은 폴링되는 머리(summary)의 회차와 비교한다.
// 항목 분류(행들이 전체 수와 맞아떨어지게): 실행할 것(ConfirmPending) / 실행 중(이미 확인됨) / 이미 성공 / 실행되지 않을 것.
// 실행 중·이미 성공도 자식 요청·잡 상태로 센다 -- 확인 대기 사이에 끝난 자식은 다음 틱 기록 전까지 항목이 Materialized 다.
// 실행되지 않을 것 = 항목 종단(거부·실패·취소) 또는 자식 요청 종단(성공 제외) 또는 미리보기 만료(잡 PreviewExpired).
// PreviewReady 배치는 orchestrator 가 굴리지 않아(list_active 밖) 만료된 자식의 항목이 Materialized 로 남는다 -- 그래서
// 항목 status 가 아니라 자식 요청·잡 상태로 세고, 원인도 항목별로 적는다(항목 행엔 사유가 아직 없다). 만료 판정은
// 서버가 기록한 상태만 본다(브라우저 시계로 미리 빼면 시계가 빠를 때 root 로 돌 항목을 "실행 안 됨" 으로 덜 보였다 --
// 만료와 stepper 틱 사이 몇 초는 "실행할 항목" 에 남아 더 보이는 안전한 쪽이고, 그 창은 만료 문구가 덮는다).
// 옛 응답(job_state·request_state 부재)은 모름이라 세지 않는다.
// ITEM_NOT_RUN(항목 종단 중 실행되지 않은 것)은 batchView 의 한 벌을 쓴다(direct 안내의 「미정」 제외와 같은 집합).
const REQUEST_NOT_RUN = ["Rejected", "Failed", "Cancelled", "Conflict"];
const REQUEST_TERMINAL = ["Succeeded", ...REQUEST_NOT_RUN];
// 자식 잡이 아직 미리보기 쪽인 상태(batch_orchestrator._PREVIEW_PHASE + 확인 대기·만료) -- 이 밖의 비종단은 실행 중이다.
const JOB_PREVIEW_SIDE = ["Pending", "Preflight", "PreviewRunning", "ConfirmPending", "PreviewExpired"];
const NOT_RUN_LIST_MAX = 10;

// 배치 확인의 미리보기 합계를 **뜻별로** 가른다(2026-10-27 d192, 포탈 괴리 [1]·[5] 의 배치 몫, C13). 예전엔 항목별
// preview_summary.files 를 한 「복사 대상」으로 더했는데 그 수는 도구마다 뜻이 다르다: nsync = 계획된 변경 수(삭제·디렉토리
// 생성·속성 갱신·타입 교체 포함, 이미 맞는 항목 제외), dsync = dry-run 이 훑은 소스 전체 항목 수(바뀔 수가 아니다), drm =
// 지울 대상 수. 그래서 항목의 도구(summaryTool -- job_tool 먼저, 없으면 summary v2 의 tool)로 묶어 따로 더하고, nsync 는
// 종류별(복사·삭제…) 합을 함께 보인다 -- 삭제 예정이 「복사 대상」에 숨지 않게(C13: 「복사 대상 7개」인데 실제로는 목적지 6개
// 삭제). 도구를 모르는 항목(job_tool·summary tool 둘 다 없음)은 뜻을 가를 수 없어 따로 센다(지어내지 않는다). 모르는
// 수(null)는 0 으로 더하지 않는다(null≠0).
// 경고 재료: type_change_needs_delete(nsync 가 「비어 있지 않은 디렉토리는 delete 없이는 바뀌지 않는다」고 미리 경고한 항목)와
// skipped_special(FIFO·소켓·장치 -- 복사하지 않는다). 특수 파일의 같은 이름 목적지 항목은 도구마다 다르게 다뤄진다(2026-10-28
// 리뷰 portal-batch-26·contract-39, optionRules SYNC_OPTION_HELP.delete 주석): nsync 는 그대로 두고, dsync 는 종류가 다르면
// 지운 뒤 아무것도 만들지 않는다. 그래서 nsync 항목에서 센 수(specialNsyncItems)를 따로 두어, 전부 nsync 일 때만 「그대로
// 둡니다」라고 말한다(지금 포크의 dsync dry-run 은 이 줄을 찍지 않아 실제로는 nsync 미리보기만 센다 -- 바뀌어도 거짓이 되지 않게).
interface ToolTotal { items: number; total: number }
interface PreviewTotals {
  nsync: ToolTotal & { kinds: Partial<Record<ActionKind, number>>; kindsUnknown: number; bytes: number; bytesKnown: number };
  dsync: ToolTotal;
  untyped: ToolTotal;
  unknown: number;
  typeChangeItems: number;
  typeChangeDirs: number;
  specialItems: number;
  specialNsyncItems: number;
  specials: number;
}
const KIND_KEYS: ActionKind[] = ["copy", "mkdir", "symlink_update", "meta_update", "remove"];

function previewTotals(runnable: BatchItem[]): PreviewTotals {
  const t: PreviewTotals = {
    nsync: { items: 0, total: 0, kinds: {}, kindsUnknown: 0, bytes: 0, bytesKnown: 0 },
    dsync: { items: 0, total: 0 }, untyped: { items: 0, total: 0 }, unknown: 0,
    typeChangeItems: 0, typeChangeDirs: 0, specialItems: 0, specialNsyncItems: 0, specials: 0,
  };
  for (const it of runnable) {
    const s = readSummary(it.preview_summary);
    const tool = summaryTool(s, it.job_tool);
    if (s.typeChangeNeedsDelete) {
      t.typeChangeItems += 1;
      if (s.typeFromDir !== null) t.typeChangeDirs += s.typeFromDir;
    }
    if (s.skippedSpecial !== null && s.skippedSpecial > 0) {
      t.specialItems += 1; t.specials += s.skippedSpecial;
      if (tool === "nsync") t.specialNsyncItems += 1;
    }
    if (tool === "nsync") {
      const n = s.files ?? sumKinds(s.planned);
      if (n === null) { t.unknown += 1; continue; }
      t.nsync.items += 1; t.nsync.total += n;
      if (s.planned === null) t.nsync.kindsUnknown += 1;
      else for (const k of KIND_KEYS) {
        const v = s.planned[k];
        if (typeof v === "number") t.nsync.kinds[k] = (t.nsync.kinds[k] ?? 0) + v;
      }
      const b = s.plannedBytes ?? s.bytes;
      if (b !== null) { t.nsync.bytes += b; t.nsync.bytesKnown += 1; }
    } else if (tool === "dsync") {
      const n = s.sourceItems ?? s.files;
      if (n === null) { t.unknown += 1; continue; }
      t.dsync.items += 1; t.dsync.total += n;
    } else {
      // tool 없음(옛 미리보기) 또는 예상 밖 도구 -- 수는 알지만 뜻을 가를 수 없다.
      if (s.files === null) { t.unknown += 1; continue; }
      t.untyped.items += 1; t.untyped.total += s.files;
    }
  }
  return t;
}

function notRunCause(it: BatchItem): string | null {
  if (it.status === "Succeeded") return null;
  if (ITEM_NOT_RUN.includes(it.status)) return it.reason_code ? reasonText(it.reason_code) : it.status;
  if (it.job_state === "PreviewExpired") return "미리보기 만료";
  if (it.request_state != null && REQUEST_NOT_RUN.includes(it.request_state)) return `요청 ${it.request_state}`;
  return null;
}

// rm 배치(2026-10-21): 같은 대화상자의 rm 갈래 -- 확인이 곧 root 삭제 시작이라 마찰을 여기 둔다. 무엇이 지워지는지(항목별
// 삭제 대상 수 + 지정 경로를 **전부** -- 「실행되지 않는 항목」의 10개 상한과 달리 삭제 검토는 누락이 없어야 한다, 목록만
// 스크롤), 대상 경로를 못 찾은 항목(미리보기 0), 그리고 「되돌릴 수 없음」 체크를 켜야만 주 버튼 「삭제 실행 확인」이 풀린다.
// 체크는 열 때마다 풀린다(지난 확인이 새 회차로 이어지지 않게). 버튼 색은 그대로 primary -- danger 는 「멈추는 취소」
// 전용 의미다(Button.tsx). sync 의 문자열·소유권 줄은 바꾸지 않는다(rm 에 「복사된 파일 소유」는 거짓이라 sync 로 좁힌다).
function ConfirmBatchDialog({ b, confirm, absOf }: {
  b: BatchHeader; confirm: ReturnType<typeof useConfirmBatch>; absOf: (it: BatchItem) => string | null;
}) {
  const [open, setOpen] = useState(false);
  // 연 횟수 -- 열 때마다 새 키로 전체를 다시 받는다(useBatchAll 주석). 지난 열람의 응답·회차를 이어 쓰지 않는다.
  const [nonce, setNonce] = useState(0);
  const onOpenChange = (o: boolean) => { setOpen(o); if (o) setNonce((n) => n + 1); };
  // 닫을 때(그리고 다시 확인 대기가 돼 새로 붙을 때) 지난 실패를 비운다(DeleteBatchButton 과 같은 이유 -- "취소"는
  // Radix onOpenChange 를 거치지 않는다). 변이는 페이지(BatchDetail)가 가진다: 409 뒤 배치가 Previewing 으로 돌아가면
  // 이 대화상자는 사라지는데, 그 거부 사유는 헤더가 이어서 보여야 하기 때문이다.
  useEffect(() => { if (!open) confirm.reset(); }, [open]);
  return (
    // rm 은 넓은 창(lg): 항목별 지정 경로 목록까지 실으면 md 폭에선 1280×900 에서도 버튼이 창 아래로 밀렸다(캡처 실측).
    <Dialog open={open} onOpenChange={onOpenChange} title="배치 확인" size={b.operation === "rm" ? "lg" : "md"}
            trigger={<Button>배치 확인</Button>}>
      {/* 무거운 본문(전 항목 분류·합계)은 열린 동안만 마운트된다 -- 예전엔 닫혀 있어도 확인 대기 내내 매 렌더 다시 셌다. */}
      <ConfirmBatchBody key={nonce} b={b} nonce={nonce} confirm={confirm} absOf={absOf}
                        onClose={() => setOpen(false)} />
    </Dialog>
  );
}

function ConfirmBatchBody({ b: header, nonce, confirm, absOf, onClose }: {
  b: BatchHeader; nonce: number; confirm: ReturnType<typeof useConfirmBatch>;
  absOf: (it: BatchItem) => string | null; onClose: () => void;
}) {
  const all = useBatchAll(header.batch_id, nonce);
  // 체크는 열 때마다 풀린다 -- 본문이 열 때마다 새로 마운트된다(key=nonce).
  const [ack, setAck] = useState(false);
  if (all.isError) {
    return (<>
      <p role="alert" className="text-bad text-sm">{(all.error as ApiError).message}</p>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>취소</Button>
      </div>
    </>);
  }
  if (all.data === undefined) {
    return (<>
      <p className="text-muted text-sm">확인할 항목을 불러오는 중…</p>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>취소</Button>
        {/* 회차를 모르는 동안(null) 잠근다 -- 기존 규칙 그대로 */}
        <Button disabled>{header.operation === "rm" ? "삭제 실행 확인" : "실행 확인"}</Button>
      </div>
    </>);
  }
  // 이 아래의 배치 필드·항목은 전부 **이 한 응답**(b) -- 회차와 같은 응답이다.
  const b = all.data;
  const round = b.preview_round ?? 0;
  const current = header.preview_round ?? 0;
  // 전체 응답이 이미 확인 대기가 아니면(받는 사이 항목 추가로 다시 미리보기) 회차는 같아도 그 항목들은 확인할 수 없다 --
  // 보내 봐야 서버가 409 batch_not_confirmable 이니 같은 경고로 잠근다(머리가 따라오면 창 자체가 사라진다).
  const changed = round !== current || b.status !== "PreviewReady";
  const isRm = b.operation === "rm";
  const items = b.items ?? [];
  const runnable = items.filter((it) => it.job_state === "ConfirmPending");
  const executing = items.filter((it) => it.status === "Materialized" && it.job_state != null
    && !JOB_PREVIEW_SIDE.includes(it.job_state)
    && !(it.request_state != null && REQUEST_TERMINAL.includes(it.request_state))).length;
  const done = items.filter((it) => it.status === "Succeeded"
    || (it.status === "Materialized" && it.request_state === "Succeeded")).length;
  const notRunItems = items.map((it) => ({ it, cause: notRunCause(it) }))
    .filter((x): x is { it: BatchItem; cause: string } => x.cause !== null);
  const expired = items.filter((it) => it.job_state === "PreviewExpired").length;
  // rm 삭제 대상 합계(drm walk 수 -- 항목별 dry-run 요약의 files): 값을 아는 항목만 더하고 모르는 수는 따로 말한다.
  // 전부 모르면 "모름" 이다(null≠0 -- 모름을 0개로 보이지 않는다). sync 는 도구마다 뜻이 달라 previewTotals 가 가른다.
  const known = runnable.map((it) => it.preview_summary?.files)
    .filter((n): n is number => typeof n === "number");
  const unknownFiles = runnable.length - known.length;
  const fileTotal = known.reduce((a, n) => a + n, 0);
  const totals = isRm ? null : previewTotals(runnable);
  const direct = isRm ? null : directNote(b, directToolsOf(b.options, items));
  // rm: 미리보기에서 대상 경로를 못 찾은 항목(0 -- walk 는 대상 자신을 세므로 빈 디렉토리도 1). 실행해도 지울 것이 없다.
  const notFound = isRm ? runnable.filter((it) => it.preview_summary?.files === 0).length : 0;
  // rm: 대상 자신이 심볼릭 링크인 항목(d194) -- 그 항목은 링크만 지운다. 「경로와 그 아래 모두」 경고의 예외라 따로 센다.
  const linkItems = isRm ? runnable.filter(rmLinkItem).length : 0;
  const expiries = runnable.map((it) => it.preview_expires_at).filter((x): x is string => !!x).sort();
  const earliest = expiries[0] ?? null;
  const del = b.options?.delete === true;
  // 소유권은 chown 지정 여부와 관계없이 늘 말한다: 비워 두면 root sync 가 목적지에 이미 있던 같은 경로 항목(최상위
  // 포함)의 소유·권한·시각을 소스 것으로 바꾼다 -- 2026-09-30 프로덕션 사고와 같은 종류라 승인 화면이 침묵하면 안 된다.
  const ownership = syncOwnership({ chown: String(b.options?.chown ?? ""), chmod: String(b.options?.chmod ?? ""),
                                    root: true, runAs: null, self: true }).long;
  return (<>
      <p className="text-sm">
        {isRm
          ? `확인하면 미리보기를 마친 항목 ${runnable.length}개의 경로가 동시 실행 상한 ${b.max_concurrency}개씩 차례로 `
            + "관리자 특권(root)으로 삭제됩니다 — 항목별 확인은 없고, 되돌릴 수 없습니다."
          : `확인하면 미리보기를 마친 항목 ${runnable.length}개가 동시 실행 상한 ${b.max_concurrency}개씩 차례로 관리자 `
            + "특권(root)으로 실행됩니다 — 항목별 확인은 없습니다."}
      </p>
      {isRm && (
        <p role="note" aria-label="삭제 경고"
           className="mt-3 rounded-lg border border-bad/30 bg-badbg px-3 py-2 text-sm text-bad">
          {/* 예외(2026-10-28 d194): 경로 자신이 심볼릭 링크인 항목은 drm 이 링크만 지운다 -- 늘 참인 규칙이라 링크 항목이
              없어도 말한다(목록의 표지·「링크 항목」 줄이 어느 항목인지 가리킨다). */}
          각 항목의 경로와 그 아래 파일·디렉토리가 모두 삭제됩니다(경로 자신이 심볼릭 링크인 항목은 링크만 지우고 가리키는 곳은
          지우지 않습니다). root 로 실행돼 파일 권한·소유와 관계없이 지워집니다.
        </p>
      )}
      {/* 자동 확인(2026-10-11)이 곧 같은 확인을 할 수 있다 -- 먼저 누르면 사람 확인으로 남고, 시스템이 먼저면 이 확인은
          거부되지만 배치는 이미 실행 중이다(헤더가 그렇게 말한다). */}
      {b.auto_confirm === true && !b.auto_confirm_hold && (
        <p className="mt-2 text-sm text-muted">
          자동 실행이 켜져 있어 시스템이 곧 같은 확인을 합니다 — 먼저 누르면 직접 확인으로 기록됩니다.
        </p>
      )}
      <dl className="mt-3 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted">실행할 항목</dt>
        <dd className="tabular-nums">{`${runnable.length} / 전체 ${items.length}`}</dd>
        {executing > 0 && (<>
          <dt className="text-muted">실행 중인 항목</dt>
          <dd>{`${executing}개(이미 확인됨 — 이번 확인과 관계없이 끝까지 실행)`}</dd>
        </>)}
        {done > 0 && (<>
          <dt className="text-muted">이미 완료된 항목</dt>
          <dd>{`${done}개(이전 실행에서 성공 — 다시 실행하지 않음)`}</dd>
        </>)}
        <dt className="text-muted">실행되지 않는 항목</dt>
        <dd>{notRunItems.length === 0 ? "없음" : (<>
          {`${notRunItems.length}개`}
          <ul aria-label="실행되지 않는 항목" className="mt-1 text-xs text-muted">
            {notRunItems.slice(0, NOT_RUN_LIST_MAX).map(({ it, cause }) => (
              <li key={it.seq}>{`#${it.seq} ${summarizeItem(b.operation, it.payload)}: ${cause}`}</li>
            ))}
            {notRunItems.length > NOT_RUN_LIST_MAX && <li>{`외 ${notRunItems.length - NOT_RUN_LIST_MAX}개`}</li>}
          </ul>
        </>)}</dd>
        {isRm && (<>
          <dt className="text-muted">삭제 대상</dt>
          <dd>{runnable.length === 0 ? "—"
            : known.length === 0 ? `모름(미리보기 ${runnable.length}개 항목 모두 수 미기록)`
            : `${fileTotal.toLocaleString("ko-KR")}개(대상 경로 포함 파일·디렉터리, 항목별 미리보기 합계${
                unknownFiles > 0 ? `, ${unknownFiles}개 항목은 모름` : ""})`}</dd>
        </>)}
        {/* 링크만 지우는 항목(d194): 데이터를 지우려고 링크를 넣었다면 확인 전에 알아야 한다. 「그 항목은 가리키는 곳을 지우지
            않는다」는 미리보기-실행 종류 묶기가 받친다(실행 직전 종류가 바뀐 항목은 도구 전에 거부 -- target_changed_since_preview).
            결과(「데이터가 남는다」)로 말하지 않는다: 같은 배치의 다른 항목이 그곳을 지우면 지워진다(겹침 판정은 경로 글자 기준). */}
        {linkItems > 0 && (<>
          <dt className="text-muted">링크 항목</dt>
          <dd>{`${linkItems}개(링크만 삭제 — 이 항목은 링크가 가리키는 곳을 지우지 않습니다. 같은 배치의 다른 항목이 그곳을 지우면 `
            + "지워집니다. 실행 직전에 링크가 아니게 바뀐 항목은 실행하지 않습니다)"}</dd>
        </>)}
        {notFound > 0 && (<>
          <dt className="text-muted">대상 경로 없음</dt>
          <dd className="text-bad">
            {`${notFound}개 항목 — 미리보기에서 찾지 못했습니다(오타·이미 삭제·권한). 확인해도 지울 것이 없습니다.`}
          </dd>
        </>)}
        <dt className="text-muted">동시 실행 상한</dt>
        <dd className="tabular-nums">{b.max_concurrency}</dd>
        <dt className="text-muted">미리보기 만료</dt>
        <dd>{earliest ? `${kstStampOrDash(earliest)}(가장 이른 항목) — 확인 시각이 아니라 각 항목의 실행 시작 기준입니다. `
          + "슬롯을 기다리다 만료된 항목은 실행되지 않고 거부(실패로 집계)됩니다." : "—"}</dd>
      </dl>
      {totals !== null && (
        // sync 미리보기 합계(previewTotals 주석): 항목 수 행들과 구획을 나눠 「무엇을 바꾸는가」를 한 상자에 모은다.
        <section aria-label="미리보기 합계" className="mt-3 rounded-lg border border-line px-3 py-2">
          <h3 className="text-sm font-medium">미리보기 합계</h3>
          <p className="text-xs text-muted">실행할 항목 기준 — 실행 도구마다 수의 뜻이 다릅니다.</p>
          <dl className="mt-1 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
            {runnable.length === 0 ? (<><dt className="text-muted">합계</dt><dd>—</dd></>)
              : totals.unknown === runnable.length ? (<>
                  <dt className="text-muted">합계</dt>
                  <dd>{`모름(미리보기 ${runnable.length}개 항목 모두 수 미기록)`}</dd>
                </>)
              : (<>
                  {totals.nsync.items > 0 && (<>
                    <dt className="text-muted">변경 예정(nsync)</dt>
                    <dd className="min-w-0">
                      {`${nf(totals.nsync.total)}개`}
                      {/* 종류별 합: 0 이 아닌 종류만(kindsText). 종류를 모르는 항목이 섞이면 그 수를 말한다. */}
                      {totals.nsync.kindsUnknown < totals.nsync.items && (
                        <span className="block">{kindsText(totals.nsync.kinds)}</span>
                      )}
                      <span className="block text-xs text-muted">
                        {`${totals.nsync.items}개 항목 합계${totals.nsync.kindsUnknown > 0
                          ? ` · ${totals.nsync.kindsUnknown}개 항목은 종류별 수 모름` : ""}${
                          // 수와 단위 사이는 줄바꿈 없는 공백(375 폭에서 「25」와 「B」가 갈라졌다)
                          totals.nsync.bytesKnown > 0 ? ` · 복사할 크기 ${humanBytes(totals.nsync.bytes).replace(" ", " ")}${
                            totals.nsync.bytesKnown < totals.nsync.items ? "(아는 항목만)" : ""}` : ""}`}
                      </span>
                    </dd>
                  </>)}
                  {totals.dsync.items > 0 && (<>
                    <dt className="text-muted">소스 전체(dsync)</dt>
                    <dd className="min-w-0">
                      {`${nf(totals.dsync.total)}개 — 변경 수는 실행 후 확정`}
                      <span className="block text-xs text-muted">
                        {`${totals.dsync.items}개 항목 합계 · 이미 같은 항목도 셉니다(바뀔 수가 아님)`}
                      </span>
                    </dd>
                  </>)}
                  {totals.untyped.items > 0 && (<>
                    <dt className="text-muted">도구 미기록</dt>
                    <dd className="min-w-0">
                      {`${nf(totals.untyped.total)}개`}
                      <span className="block text-xs text-muted">
                        {`${totals.untyped.items}개 항목 합계 — 이전 버전 미리보기라 변경 예정 수인지 소스 전체 수인지 가를 수 없습니다`}
                      </span>
                    </dd>
                  </>)}
                  {totals.unknown > 0 && (<>
                    <dt className="text-muted">수 모름</dt>
                    <dd>{`${totals.unknown}개 항목(미리보기 수 미기록 — 합계에 넣지 않음)`}</dd>
                  </>)}
                </>)}
          </dl>
        </section>
      )}
      {/* 타입 교체 경고(nsync 미리보기 WARN -- 포탈 괴리 [1]·[6]): 비어 있지 않은 디렉토리가 소스에서 파일·링크로 바뀌었고
          delete 가 꺼져 있으면 그 항목은 실행에서 실패한다(sync_type_change_refused). 미리보기는 성공(rc 0)이라 확인 창이
          말하지 않으면 확인 뒤에야 안다. dsync 미리보기는 이 경고를 내지 않는다(포크 미구현) -- 센 것만 말한다. */}
      {totals !== null && totals.typeChangeItems > 0 && (
        <p role="note" aria-label="타입 교체 경고"
           className="mt-3 rounded-lg border border-attn/30 bg-attnbg px-3 py-2 text-sm text-attn">
          {`${totals.typeChangeItems}개 항목에서 ${totals.typeChangeDirs > 0 ? `디렉토리 ${nf(totals.typeChangeDirs)}개가` : "디렉토리가"} `
            + "파일·링크로 바뀝니다 — 삭제(delete)를 켜지 않으면 비어 있지 않은 디렉토리는 바뀌지 않고 그 항목은 실패로 "
            + "남습니다(나머지는 반영)."}
        </p>
      )}
      {totals !== null && totals.specials > 0 && (
        <p role="note" aria-label="특수 파일" className="mt-2 rounded-lg bg-infobg px-3 py-2 text-sm">
          {`FIFO·소켓·장치 ${nf(totals.specials)}개(${totals.specialItems}개 항목)는 복사하지 않습니다 — `
            + (totals.specialNsyncItems === totals.specialItems
              ? "목적지의 같은 이름 항목은 그대로 둡니다."
              : "노드 간 실행(nsync)은 목적지의 같은 이름 항목을 그대로 두고, 한 노드 실행(dsync)은 그 이름의 목적지 항목이 "
                + "다른 종류면 지운 뒤 아무것도 만들지 않습니다.")}
        </p>
      )}
      {direct !== null && <p className="mt-2 text-xs text-muted">{direct}</p>}
      {isRm && runnable.length > 0 && (
        <div className="mt-3">
          <p className="text-sm font-medium">항목별 삭제 대상(미리보기)</p>
          {/* 지정 경로는 지금의 managed_root 에 글자 그대로 붙인 것(absOf -- 모르면 줄 생략, 링크는 풀지 않는다: 「실제 경로」라
              부르지 않는다 -- 괴리 [C2]). 긴 경로는 아무 데서나 끊는다(375 폭). */}
          <ul aria-label="항목별 삭제 대상"
              className="mt-1 max-h-60 space-y-1 overflow-y-auto rounded-lg border border-line px-3 py-2 text-xs">
            {runnable.map((it) => {
              const sm = it.preview_summary;
              const abs = absOf(it);
              const rc = typeof sm?.returncode === "number" && sm.returncode !== 0
                ? ` · dry-run 종료코드 ${sm.returncode}` : "";
              return (
                <li key={it.seq} className={`[overflow-wrap:anywhere] ${sm?.files === 0 || rc !== "" ? "text-bad" : ""}`}>
                  {`#${it.seq} ${pathSummary(b.operation, it.payload)} — `}
                  <span className="font-medium tabular-nums">{`${rmCountWithKind(it)}${rc}`}</span>
                  {abs !== null && <span className="block text-muted">{`지정 경로: ${abs}`}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {expired > 0 && (
        <p className="mt-2 text-sm text-bad">
          {`미리보기 만료 ${expired}개 — 확인해도 실행되지 않고 거부로 기록됩니다(항목 재실행으로 다시 미리보기).`}
        </p>
      )}
      {/* delete 문구는 제출 폼과 같은 한 출처(SYNC_OPTION_HELP -- 괴리 [3]·[28]: 「미러 동기화」만으로는 특수 파일·읽기
          오류의 예외를 말하지 않았다). nsync 항목의 삭제 예정 수는 위 합계의 「삭제」다. delete 가 꺼져 있어도 nsync 는 타입이
          바뀐 항목(파일↔디렉토리·링크)을 다시 만들려고 목적지 쪽을 먼저 지운다 -- 그 「삭제」가 delete 와 무관함을 말한다. */}
      {del && (
        <p role="note" aria-label="delete 옵션"
           className="mt-3 rounded-lg border border-bad/30 bg-badbg px-3 py-2 text-sm text-bad">
          {`delete 옵션: ${SYNC_OPTION_HELP.delete}`}
          {totals !== null && totals.nsync.items > 0 && (
            <span className="mt-1 block">
              {`nsync 항목의 삭제 예정 수는 위 「변경 예정」의 삭제입니다${totals.dsync.items > 0
                ? "(dsync 항목은 실행 후 확정)" : ""}.`}
            </span>
          )}
        </p>
      )}
      {!del && !isRm && totals !== null && (totals.nsync.kinds.remove ?? 0) > 0 && (
        <p className="mt-2 text-xs text-muted">
          {`delete 는 꺼져 있지만 위 삭제 ${nf(totals.nsync.kinds.remove ?? 0)}개는 타입 교체입니다 — 종류가 바뀐 항목(파일↔`
            + "디렉토리·링크)을 다시 만들려고 목적지 쪽을 먼저 지웁니다."}
        </p>
      )}
      {b.operation === "sync" && (
        <p className="mt-2 text-sm" role="note" aria-label="목적지 소유">{`소유권: ${ownership}`}</p>
      )}
      {changed && (
        <p role="alert" className="mt-3 text-bad text-sm">
          이 대화상자를 연 뒤 배치 내용이 바뀌었습니다(항목 추가·재실행으로 다시 미리보기됨) — 닫고 다시 열어 바뀐 내용을 검토하세요.
        </p>
      )}
      {/* 회차 변경 경고가 이미 떠 있으면 같은 뜻의 409 batch_preview_changed 는 겹쳐 보이지 않는다(경고 하나). */}
      {confirm.isError && !(changed && (confirm.error as ApiError).code === "batch_preview_changed") && (
        <p role="alert" className="mt-3 text-bad text-sm">{(confirm.error as ApiError).message}</p>
      )}
      {isRm && (
        <label className="mt-3 flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0" aria-label="되돌릴 수 없는 삭제임을 확인했습니다"
                 checked={ack} onChange={(e) => setAck(e.target.checked)} />
          {`위 경로 ${runnable.length}개를 root 로 삭제합니다 — 되돌릴 수 없음을 확인했습니다`}
        </label>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>취소</Button>
        {/* 미리보기 완료 0 으로 막지 않는다: 판정은 서버다(job_state 를 모르는 옛 응답이면 0 으로 보여 확인이 막힌다).
            회차가 바뀌었으면 잠근다 -- 보낸다 해도 서버가 409 다. rm 은 「되돌릴 수 없음」 체크 전에도 잠그고, 체크했을 때만
            동의(acknowledge_irreversible: true)를 싣는다 -- 서버도 rm 확인에 그 동의를 요구한다(옛 탭 차단, useConfirmBatch).
            회차를 모르는 동안(전체 조회 전)은 위의 불러오는 중 갈래가 잠근다. */}
        <Button disabled={confirm.isPending || changed || (isRm && !ack)}
                onClick={() => confirm.mutate(
                  { preview_round: round, ...(isRm && ack ? { acknowledge_irreversible: true as const } : {}) },
                  { onSuccess: onClose })}>{isRm ? "삭제 실행 확인" : "실행 확인"}</Button>
      </div>
  </>);
}

// 배치 삭제(종단 배치만 노출 — 진짜 가드는 서버 batch_not_deletable). 확인
// 다이얼로그 필수: 배치 행·항목 행이 사라지는 비가역 동작이다(자식 요청·잡은
// 감사 이력으로 보존 — repo.delete 주석). 성공 시 목록으로 이동 — 삭제된
// 배치의 상세는 404 라 머무를 곳이 아니다. 이 버튼은 **배치 기록만** 지운다(2026-10-10
// 결정 유지 — 배치 목록 정리 + 실행 이력 보존이라는 쓰임새). 작업까지 지우는 길은
// 전체 작업의 배치 단위 삭제(request_purges.delete_batch — 배치 기록도 함께 지운다)이고,
// 여기서 기록만 지운 뒤 남은 작업도 그 목록에서 「기록 없음」 묶음으로 같은 방식으로
// 지울 수 있다 — 두 화면의 「삭제」 뜻이 다르므로 지우기 전에 그 차이를 말한다.
// 기록을 먼저 지우면 배치 이름도 사라져 남은 작업은 「기록 없음 + id 12자」로만 보인다는
// 것도 말한다(2026-10-10 검증 지적). 「전체 작업에서」는 그 배치의 작업만 거른 목록
// (/jobs?batch=<id>)으로 가는 **링크**다(2026-10-11 검증 지적 — 오래된 배치의 작업은 무한
// 스크롤 수십 쪽 아래라 말로만 보내면 찾을 길이 없었다). 안내는 읽혀야 하는 글자라
// text-muted(장식 전용, 3.54:1)가 아니라 ink/70(4.94:1, AA).
// 스케줄이 있는 배치면 그 스케줄·회차 기록도 함께 지워진다(repositories/batches.delete -- 같은 트랜잭션)는 것을 말한다.
function DeleteBatchButton({ batchId, hasSchedule }: { batchId: string; hasSchedule: boolean }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteBatch(batchId);
  const navigate = useNavigate();
  // 닫힐 때마다 에러를 비운다(StoragesList DeleteButton 선례) — "취소"는
  // setOpen(false)를 직접 불러 Radix onOpenChange 가 발화하지 않는다.
  useEffect(() => { if (!open) del.reset(); }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="배치 삭제"
            trigger={<Button variant="ghost">배치 삭제</Button>}>
      <div className="text-sm text-ink/70 mb-3 space-y-1 break-keep">
        <p>
          배치 기록(항목 목록·이름·메모·실행 설정)만 삭제됩니다. 작업 기록은 남습니다 — 작업까지 지우려면{" "}
          <Link className="text-accent underline" to={batchJobsPath(batchId)}>전체 작업에서 이 배치의 작업</Link>을 골라
          배치 단위로 삭제하세요(배치 기록도 함께 지워집니다).
        </p>
        <p>기록을 먼저 지우면 남은 작업은 전체 작업에서 배치 이름 없이 「기록 없음 {batchId.slice(0, 12)}」로 표시됩니다.</p>
        {hasSchedule && <p>이 배치의 스케줄도 함께 삭제됩니다.</p>}
      </div>
      {del.isError && <p className="text-bad text-sm mb-2">{(del.error as ApiError).message}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
        <Button disabled={del.isPending}
                onClick={() => del.mutate(undefined,
                  { onSuccess: () => navigate("/admin/batches") })}>삭제 확인</Button>
      </div>
    </Dialog>
  );
}

// 항목 상태 칩(2026-11-16): 서버 view=items 의 status(항목 status 정확 일치)에 그대로 실린다. null = 전체. 라벨은 짧은 한국어 --
// 행의 상태 배지가 원문 상태(Queued 등)를 그대로 보이므로 칩과 짝이 읽힌다(대기=Queued·진행=Materialized …).
const ITEM_STATUS_CHIPS: readonly { label: string; status: string | null }[] = [
  { label: "전체", status: null }, { label: "대기", status: "Queued" }, { label: "진행", status: "Materialized" },
  { label: "성공", status: "Succeeded" }, { label: "실패", status: "Failed" }, { label: "거부", status: "Rejected" },
  { label: "취소", status: "Cancelled" },
];

// 항목 한 행(2026-11-16 분리 + memo). 예전엔 선택·드래프트·펼침이 전부 BatchDetail 상태라 키 입력·체크 하나·폴링마다 전 행이
// 다시 그려졌다(1만 항목 배치에서 화면이 멈춘 원인 -- 지도 map-batch-detail). 행은 원시 값과 고정된 핸들러(useCallback)만 받아
// 바뀐 행만 다시 그린다: 드래프트·저장 상태는 편집 중인 행에만 실리고(다른 행엔 빈 값), 항목 객체(it)는 react-query 구조적
// 공유로 내용이 같으면 같은 참조다.
const BatchItemRow = memo(function BatchItemRow({
  it, operation, isSync, chown, checked, selectable, bulkBusy, open, editing, armed, absPath,
  deletePending, deleteError, pathDraft, dstDraft, updatePending, updateError,
  onToggleSelect, onToggleOpen, onStartEdit, onArm, onDelete, onSave, onCancelEdit, onPathDraft, onDstDraft,
}: {
  it: BatchItem; operation: string | undefined; isSync: boolean; chown: string;
  checked: boolean; selectable: boolean; bulkBusy: boolean;
  open: boolean; editing: boolean; armed: boolean; absPath: string | null;
  deletePending: boolean; deleteError: string | null;
  pathDraft: string; dstDraft: string; updatePending: boolean; updateError: string | null;
  onToggleSelect: (seq: number) => void; onToggleOpen: (seq: number) => void; onStartEdit: (it: BatchItem) => void;
  onArm: (seq: number) => void; onDelete: (seq: number) => void;
  onSave: (it: BatchItem, path: string, dst: string) => void; onCancelEdit: () => void;
  onPathDraft: (v: string) => void; onDstDraft: (v: string) => void;
}) {
  return (
    <div className="border-t border-black/5 py-2">
      {/* 행 = 선택 체크박스 + 펼침 토글 버튼 + 삭제 버튼의 flex 형제. 토글이
          행 전체를 덮는 버튼이었는데, 버튼 안에 버튼은 불가(HTML)라 삭제를
          행에 올리려면 토글을 flex-1 로 줄이고 삭제를 옆에 둔다. 체크박스도
          토글의 **형제**라 체크 클릭이 펼침으로 새지 않는다(중첩이 아니라
          구조로 막는다 — stopPropagation 불필요). 항목은 표(Table)가 아니라
          리스트 구조라(위 카드 주석 — d54 결정) e2e L2(td 안 flex/버튼
          불변식)는 애초에 안 문다 — 실측: td 없음. */}
      <div className="flex items-center gap-3">
        {/* 삭제 가능한 항목만 선택 가능(canEditItem 재사용 — 행 삭제 버튼과
            같은 게이트). disabled 로 끝내지 않고 title 로 이유를 남긴다. */}
        <input type="checkbox" aria-label={`항목 ${it.seq} 선택`}
               className="h-5 w-5 shrink-0"
               checked={checked}
               disabled={!selectable || bulkBusy}
               title={selectable ? undefined : ITEM_LOCK_HINT}
               onChange={() => onToggleSelect(it.seq)} />
        <button type="button" aria-label={`항목 ${it.seq} 상세`}
                aria-expanded={open}
                onClick={() => onToggleOpen(it.seq)}
                className="flex min-w-0 flex-1 items-center gap-3 text-left">
          <span className="w-8 shrink-0 text-muted text-xs tabular-nums">{it.seq}</span>
          {/* 항목 제목은 행의 주인공이라 주변 본문(text-sm)·체크박스(h-5)와
              같은 단이다 — text-xs 로는 곁다리 메타처럼 작아 보였다(사용자
              지적). 경로·스토리지라 font-mono 는 유지(구분자·유사문자를
              또렷하게), 크기만 한 단 올린다. */}
          {/* 절대경로는 title 로만 — 본문에 이어 붙이면 한 줄이 두 배로
              길어져 truncate 가 정작 대상 경로를 먹는다(좁은 자리 규칙). */}
          <span className="min-w-0 flex-1 truncate font-mono text-sm"
                title={absPath ?? undefined}>
            {summarizeItem(operation, it.payload)}
          </span>
          <StatusPill state={it.status} />
        </button>
        {/* 수정도 행 버튼이다(삭제 왼쪽 — 사용자 지시 2026-08-15): 펼침
            안에 있으면 한 건 고치기가 펼침(1)→수정(2) 이 되는데, 무엇을
            고치는지는 행 요약(순번·대상)이 이미 말한다. 편집 폼은 행 바로
            아래에 열려(펼침과 무관) 대상과 입력이 붙어 있다. 행별 aria-label
            은 삭제 버튼 관례 미러 — 행이 여럿이라 무접두 "수정"은 모호하다.
            노출 조건은 canEditItem 재사용(표시 게이트, 진짜 차단은 서버 409). */}
        {selectable && !editing && (
          <Button variant="ghost" aria-label={`항목 ${it.seq} 수정`}
                  onClick={() => onStartEdit(it)}>수정</Button>
        )}
        {/* 삭제는 펼치지 않아도 보이는 행 버튼(펼침 안에서 행으로 이동).
            노출 조건은 기존 canEditItem 재사용(표시 게이트 — 진짜 차단은
            서버 409). aria-label 로 행별 이름을 부여해 테스트·스크린리더가
            어느 항목인지 안다.
            다중 선택이 생긴 뒤에도 이 버튼을 남기는 이유: 한 건 지우기가
            체크(1)→선택 삭제(2)→N개 삭제 확인(3) 세 클릭이 되는데, 행
            버튼은 삭제(1)→삭제 확인(2) 두 클릭이다. 가장 흔한 조작이
            느려지는 건 정리가 아니라 퇴보라 중복을 감수한다. */}
        {selectable && (armed
          ? <Button aria-label={`항목 ${it.seq} 삭제 확인`}
                    disabled={deletePending}
                    onClick={() => onDelete(it.seq)}>삭제 확인</Button>
          : <Button variant="ghost" aria-label={`항목 ${it.seq} 삭제`}
                    onClick={() => onArm(it.seq)}>삭제</Button>)}
      </div>
      {/* 삭제 실패 사유는 시도한 행 밑에만 — variables(마지막 mutate 의
          seq)로 행을 특정한다(전 행에 도배하면 어느 삭제가 실패했는지
          모른다). */}
      {deleteError !== null && (
        <p className={`text-bad text-sm mt-1 ${ITEM_INDENT}`}>{deleteError}</p>
      )}
      {/* 편집 폼은 펼침 **밖**이다 — 수정 버튼이 행으로 올라갔으니 폼도
          행 바로 아래에 열린다(펼치지 않은 행에서 눌러도 보인다). 펼친
          행에서 눌러도 같은 자리(행 밑, 상세 위)라 대상과 입력이 붙는다. */}
      {editing && (
        <div className={`mt-2 ${ITEM_INDENT} space-y-2 max-w-md`}>
          {isSync ? (<>
            <label className="text-sm block">소스 경로
              <input aria-label="소스 경로" className={field} value={pathDraft}
                     onChange={(e) => onPathDraft(e.target.value)} />
            </label>
            <label className="text-sm block">목적지 경로
              <input aria-label="목적지 경로" className={field} value={dstDraft}
                     onChange={(e) => onDstDraft(e.target.value)} />
            </label>
            <SyncDestHint chown={chown} />
          </>) : (
            <label className="text-sm block">대상 경로
              <input aria-label="대상 경로" className={field} value={pathDraft}
                     onChange={(e) => onPathDraft(e.target.value)} />
            </label>
          )}
          <div className="flex gap-2">
            <Button disabled={updatePending} onClick={() => onSave(it, pathDraft, dstDraft)}>항목 저장</Button>
            {/* 배치 취소("취소")·이름 편집("편집 취소")과 라벨이 겹치지 않게 */}
            <Button variant="ghost" onClick={onCancelEdit}>항목 편집 취소</Button>
          </div>
          {updateError !== null && (
            <p className="text-bad text-sm">{updateError}</p>
          )}
        </div>
      )}
      {/* 펼침 패널: 들여쓰기는 컨테이너 한 곳에서만 준다(예전엔 섹션마다
          ITEM_INDENT 를 되풀이했다) — 섹션이 늘어도 정렬이 한 곳에 남는다. */}
      {open && (
        <div className={ITEM_INDENT}>
          <section className={PANEL_SECTION}>
            <h3 className={PANEL_TITLE}>요청 정보</h3>
            <dl className={PANEL_DL}>
              {/* 요청 상태는 항목 상태(배치 시점 판정)와 다른 축 — 자식 요청의
                  현재 상태다. null = 아직 materialize 안 됨. */}
              <dt className="text-muted">요청 상태</dt>
              <dd>{it.request_state ?? "—"}</dd>
              <dt className="text-muted">사유</dt>
              <dd className="text-bad">{it.reason_code ? reasonText(it.reason_code) : "—"}</dd>
              {/* 무엇으로 돌았는지(dscan/dsync/nsync/drm) — sync 는 배치
                  시점에 도구가 갈리므로(공존 노드 없으면 nsync 폴백) 결과를
                  볼 때 화면이 말해 주지 않으면 확인할 길이 없다. */}
              <ItemTool requestId={it.request_id} />
              {/* null = 모름(잡 없음/미기록) — 0(파일 없음)은 정상값으로 그대로
                  표기한다(null≠0, ?? 로만 접는다). */}
              {/* 결과 수의 이름은 연산의 뜻으로(요청 상세 KPI 와 같은 이름 — requestOutcome): rm 「삭제한 항목」, sync
                  「반영한 항목」(2026-10-27 d192 -- files_count 는 이제 실행에서 실제로 반영된 수다: nsync 완료 액션 합·
                  dsync 복사 성공 수. 예전 「파일 수」는 nsync 의 계획 합을 실패한 실행에서도 그대로 보였다, 괴리 [2]·[4]),
                  scan 「파일 수」. 아래에 확인 때 본 미리보기 수를 도구별 뜻으로 함께 둔다. sync 의 「반영한 항목」은 d192
                  이후 러너로 돈 항목에만 붙인다(syncCountLabel -- 그 전 기록은 중립 「항목 수」). */}
              <dt className="text-muted">
                {operation === "rm" ? "삭제한 항목" : operation === "sync" ? syncCountLabel(it) : "파일 수"}
              </dt>
              <dd className="tabular-nums">{it.files_count ?? "—"}</dd>
              {operation === "rm" && it.preview_summary != null && (<>
                <dt className="text-muted">삭제 대상(미리보기)</dt>
                <dd className="tabular-nums">{rmCountWithKind(it)}</dd>
              </>)}
              {operation === "sync" && it.preview_summary != null && <SyncPreviewRow summary={it.preview_summary} jobTool={it.job_tool} />}
              <dt className="text-muted">완료 시각</dt>
              <dd className="text-muted">{kstStampOrDash(it.completed_at)}</dd>
              <dt className="text-muted">payload</dt>
              <dd className="font-mono text-xs break-all">{JSON.stringify(it.payload)}</dd>
              {/* payload 의 상대경로를 지금의 managed_root 로 해석한 결과.
                  뿌리를 모르면 줄 자체가 없다(거짓 경로 금지 — absOf 주석). */}
              {absPath !== null && (<>
                <dt className="text-muted">절대경로</dt>
                <dd className="font-mono text-xs break-all">{absPath}</dd>
              </>)}
              <dt className="text-muted">요청</dt>
              <dd>{it.request_id
                ? <Link className="text-accent" to={`/jobs/${it.request_id}`}>요청 상세</Link>
                : "—"}</dd>
            </dl>
          </section>
          {/* 항목별 데이터 온도: scan 배치만 — sync 항목엔 dscan 리포트가
              존재할 수 없어 섹션 자체가 거짓 약속이 된다. */}
          {operation === "scan" && (
            <ItemScanStats requestId={it.request_id}
                           succeeded={it.request_state === "Succeeded"} />
          )}
        </div>
      )}
    </div>
  );
});

// 머리 링크 줄의 배치 스케줄(2026-11-16 배치 스케줄러): 있으면 「반복 실행: 주기 · 다음 …」, 없으면(서버가 null 이라고 말할 때만
// -- 부재 = 옛 서버라 모른다) 스케줄을 걸 수 있는 연산(scan·sync·rm)에 「스케줄 추가」. 둘 다 배치 스케줄러의 ?batch= 로 간다
// (그 화면이 이 배치의 수정·추가 창을 연다 -- 자격(root 실행 자격) 잠금도 그 화면과 서버 403 몫이라 여기서 판정하지 않는다).
// 남은 시간이 흐르므로 이 잎만 30초마다 다시 그린다(useNow 관례).
function ScheduleLink({ b }: { b: BatchHeader }) {
  const s = b.schedule;
  const on = s != null && scheduleEnabled(s.enabled);
  const now = useNow(30_000, on && s?.next_run_at != null);
  const to = `/admin/batch-schedules?batch=${encodeURIComponent(b.batch_id)}`;
  if (s == null) {
    return s === null && schedulableOp(b.operation)
      ? <Link className="text-accent text-sm" to={to}>스케줄 추가</Link> : null;
  }
  const next = nextRunStamp(s.enabled, s.next_run_at);
  const state = on ? "" : s.paused_reason_code !== null ? "(자동 일시정지)" : "(꺼짐)";
  return (
    <Link className="text-accent text-sm" to={to}
          title={!on && s.paused_reason_code !== null ? reasonText(s.paused_reason_code) : undefined}>
      {`반복 실행${state}: ${scheduleSummary(s.kind, s.spec)} · 다음 ${
        next === "—" ? next : `${next} (${untilText(s.next_run_at, now)})`}`}
    </Link>
  );
}

export function BatchDetail() {
  const { batchId = "" } = useParams();
  const q = useBatch(batchId);
  const rerun = useRerunFailed(batchId);
  const cancel = useCancelBatch(batchId);
  const rescan = useRescanBatch(batchId);
  const confirm = useConfirmBatch(batchId);
  const autoOff = useDisableAutoConfirm(batchId);
  const update = useUpdateBatch(batchId);
  const b = q.data;
  // 이름·메모 인라인 편집: 열 때 현재값을 드래프트로 복사한다 — 폴링 리페치가
  // 편집 중 입력을 덮어쓰지 않게 편집 상태는 서버 상태와 분리한다.
  const [editing, setEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const startEdit = () => {
    setNameDraft(b?.name ?? ""); setNoteDraft(b?.note ?? ""); setEditing(true);
  };
  const save = () => update.mutate({ name: nameDraft, note: noteDraft },
                                   { onSuccess: () => setEditing(false) });
  // 항목 행 펼침: NodeMetricsSection 의 open 토글 관례 미러(단일 펼침) — 접힘이
  // 기본이라 목록이 컴팩트하고, 상세는 펼친 행에만 렌더된다. 데이터 온도도 펼친
  // 행 안에서 그 항목(요청) 단위로만 조회·렌더한다 — 배치 합산은 제거됐다
  // (사용자 결정: 배치 전체 집계는 필요 없다).
  const [openSeq, setOpenSeq] = useState<number | null>(null);
  // 항목 편집(수정·삭제·추가): 노출 조건은 표시 게이트일 뿐 — 진짜 차단은 서버다
  // (활성 배치는 Queued 만 SQL 원자 가드로 허용, 경합 패배·비허용은 409). 종단
  // 배치(Completed/Cancelled)는 전 항목 편집 가능 — legacy "편집 후 재실행" 흐름
  // (전체 재실행과 결합)이다.
  const terminal = b?.status === "Completed" || b?.status === "Cancelled";
  const cancellable = b?.status === "Running" || b?.status === "Previewing" || b?.status === "PreviewReady";
  const repeating = b?.schedule != null && scheduleEnabled(b.schedule.enabled);
  const canEditItem = useCallback((it: BatchItem) => terminal || it.status === "Queued", [terminal]);
  const isSync = b?.operation === "sync";
  // 미리보기 단계로 돌아온 sync 배치에서도 이미 실행 중인 자식은 끝까지 돈다(서버 _reopen_after_new_queued) -- 배너가
  // "아직 아무것도 안 돈다" 로 읽히지 않게 센다. 셈은 서버 summary(executing_count -- 확인 단계에서만 센다, 그 밖 null)가
  // 준다: 화면이 더는 전 항목을 들고 있지 않다. null(안 셌다)과 0 은 둘 다 「말할 것 없음」이지만 0 으로 접지 않고 가른다.
  const executing = typeof b?.executing_count === "number" && b.executing_count > 0 ? b.executing_count : null;
  const updateItem = useUpdateBatchItem(batchId);
  const deleteItem = useDeleteBatchItem(batchId);
  // 드래프트는 서버 상태와 분리(이름·메모 편집과 같은 이유 — 폴링 리페치가 입력을
  // 덮지 않는다). pathDraft = scan target / sync source.
  const [editSeq, setEditSeq] = useState<number | null>(null);
  // 행 삭제 2단 확인: 1단 클릭이 armedSeq 를 무장시키고, 같은 행의 2단 클릭이
  // DELETE 를 쏜다(오삭제 방지). 다른 행을 무장시키면 이전 무장은 자연 해제 —
  // 단일 상태라 동시 무장이 구조적으로 불가능하다. 배치 삭제의 Dialog 관례
  // 대신 2단 클릭을 쓴 이유: 행마다 Dialog 는 무겁고, 항목 삭제는 배치 삭제와
  // 달리 행 요약(순번·대상)이 바로 옆에 보여 확인 문맥이 이미 화면에 있다.
  const [armedSeq, setArmedSeq] = useState<number | null>(null);
  const [pathDraft, setPathDraft] = useState("");
  const [dstDraft, setDstDraft] = useState("");
  // 행 핸들러는 useCallback 으로 고정한다 -- 행(BatchItemRow)이 memo 라, 핸들러가 렌더마다 새로 만들어지면 키 입력·체크
  // 하나에 쪽의 전 행이 다시 그려진다(2026-11-16 이전의 멈춤 원인). mutate·setState 는 원래 고정이다.
  const startItemEdit = useCallback((it: BatchItem) => {
    setPathDraft(String((isSync ? it.payload.source : it.payload.target) ?? ""));
    setDstDraft(String(it.payload.destination ?? ""));
    setEditSeq(it.seq);
  }, [isSync]);
  const updateMutate = updateItem.mutate;
  const saveItem = useCallback((it: BatchItem, path: string, dst: string) => updateMutate(
    { seq: it.seq, item: itemBody(isSync, it.payload, path, dst) },
    { onSuccess: () => setEditSeq(null) }), [isSync, updateMutate]);
  const deleteMutate = deleteItem.mutate;
  const deleteOne = useCallback((seq: number) => deleteMutate(seq, { onSettled: () => setArmedSeq(null) }),
                                [deleteMutate]);
  const toggleOpen = useCallback((seq: number) => setOpenSeq((cur) => (cur === seq ? null : seq)), []);
  const cancelItemEdit = useCallback(() => setEditSeq(null), []);
  // 절대경로 조합용 스토리지 뿌리 맵. payload 에는 상대경로만 남으므로(서버 계약
  // 무변경) 화면이 **지금의** managed_root 로 조합한다 — 기록에 절대경로를 박아
  // 두면 스토리지 경로를 바꾼 뒤 없는 경로를 사실처럼 보인다. 관리자 응답에만
  // 뿌리가 실려 오므로 비관리자에겐 빈 맵 = 절대경로 표시 없음(거짓 경로 금지).
  const roots = useStorageRoots();
  const absOf = useCallback((it: BatchItem) => absSummary(b?.operation, it.payload, roots), [b?.operation, roots]);
  // 배치 전체의 사실(항목이 있는가·물려받을 스토리지)은 머리 집계에서 온다 -- **거른 쪽에서 파생하지 않는다**: 검색 0건이
  // 「항목이 없어 스토리지를 물려받을 수 없습니다」가 되고 도구 버튼이 사라지면 거짓이다.
  const firstPayload = b?.first_payload ?? undefined;
  const hasItems = b !== undefined && b.item_total > 0;
  // --- 항목 쪽 조회(2026-11-16): 쪽 크기 100, 경로 검색(300ms 디바운스 -- 사용량 경로 검색과 같은 모양), 상태 칩 ---
  // 쪽·검색·상태는 화면 상태다(URL 에 넣지 않는다 -- 사용량 검색·정렬 관례). 검색·상태가 바뀌면 1쪽으로.
  const [page, setPage] = useState(1);
  const [qInput, setQInput] = useState("");
  const [pathQ, setPathQ] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      const v = qInput.trim();
      if (v !== pathQ) { setPathQ(v); setPage(1); }
    }, 300);
    return () => clearTimeout(t);
  }, [qInput, pathQ]);
  const pickStatus = (st: string | null) => { setStatusFilter(st); setPage(1); };
  const itemsQ = useBatchItems(batchId, { page, q: pathQ, status: statusFilter }, b?.status);
  const pageItems = itemsQ.data?.items;
  const filtered = pathQ !== "" || statusFilter !== null;
  const total = itemsQ.data?.total;
  const pageCount = total === undefined ? 0 : Math.max(1, Math.ceil(total / BATCH_ITEMS_PAGE_SIZE));
  // 쪽을 넘기다 그 쪽 조회가 실패하면(일시 5xx·깨진 payload 가 든 쪽의 500 -- retry 없음) 오류 상태에선 자리표시가 없어 total 을
  // 모르고 pageCount 가 0 이 되어 쪽 넘김이 사라졌다 -- 종단 배치는 폴링도 멈춰 그 쪽에 갇혔다(2026-11-16 리뷰). 넘기던 때의 쪽
  // 수를 기억해(같은 검색·상태일 때만) 쪽 넘김을 남긴다(사용량 StoragePanel 의 pagerCount 와 같은 결).
  const filterKey = `${pathQ}\u0000${statusFilter ?? ""}`;
  const [paging, setPaging] = useState<{ key: string; count: number }>({ key: "", count: 0 });
  const pagerCount = pageItems === undefined && itemsQ.isError && paging.key === filterKey
    ? Math.max(paging.count, page) : pageCount;
  const goPage = (p: number) => { setPaging({ key: filterKey, count: pagerCount }); setPage(p); };
  // 목록이 줄어(삭제·교체·다른 세션) 지금 쪽이 범위 밖이 되면 마지막 쪽으로 -- 빈 쪽에 「검색 결과가 없습니다」를 보이지 않게.
  useEffect(() => {
    if (itemsQ.isPlaceholderData || total === undefined) return;
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount, total, itemsQ.isPlaceholderData]);
  // --- 항목 다중 선택 삭제(목록 화면 일괄 삭제의 항목판 — 같은 UX 언어) ---
  const bulkDelete = useDeleteBatchItems(batchId);
  // 선택 재실행: 같은 선택·같은 액션 바를 쓰는 두 번째 일괄 동작. 삭제와 달리
  // **종단 항목만** 대상이라(서버 부분 성공 모델) 대상이 하나도 없으면 잠근다.
  const bulkRerun = useRerunBatchItems(batchId);
  // 선택은 Set(행마다 includes 로 찾으면 쪽 크기의 제곱이다) + **쪽 단위**: 전체 선택 = 이 쪽의 선택 가능한 행, 쪽·검색·상태가
  // 바뀌면 비운다 -- 보이지 않는 항목이 선택에 남으면 「N개 삭제 확인」이 화면에 없는 것을 지운다(목록 화면 탭 전환과 같은 원칙).
  const [selected, setSelected] = useState<ReadonlySet<number>>(() => new Set());
  // 2단 확인: 1단이 무장하고 2단이 쏜다(행 삭제 armedSeq 와 같은 계약). 일괄
  // 삭제는 비가역 폭이 넓어 오클릭 방어가 더 필요하다.
  // 불리언이 아니라 **어느 동작이 무장했는지**를 담는다: 바에 무장 버튼이 둘이
  // 되면서, 상태가 하나여야 "삭제 확인"과 "재실행 확인"이 동시에 뜨는 것이
  // 구조적으로 불가능해진다(행 삭제 armedSeq 가 단일 상태인 것과 같은 이유).
  const [bulkArmed, setBulkArmed] = useState<null | "delete" | "rerun">(null);
  useEffect(() => {
    setSelected((prev) => (prev.size === 0 ? prev : new Set()));
    setBulkArmed(null);
  }, [page, pathQ, statusFilter]);
  // pageItems 를 그대로 dep 에 쓴다(`?? []` 를 dep 으로 쓰면 매 렌더 새 배열이라
  // 아래 정리 effect 가 무한 루프가 된다). react-query 의 구조적 공유 덕에 내용이
  // 같은 리페치는 같은 참조를 돌려준다.
  const deletableSeqs = useMemo(
    () => (pageItems ?? []).filter(canEditItem).map((it) => it.seq), [pageItems, canEditItem]);
  // 유령 선택 정리: 리페치로 이 쪽에서 사라진 항목(단건 삭제·다른 세션의 교체·상태 칩에서 벗어남)을 선택에서 뺀다 — 안
  // 그러면 화면에 없는 seq 에 DELETE 를 쏘고 404 를 "실패"로 보고한다. 바뀐 게 없으면 같은 참조를 돌려 재렌더 루프를 끊는다.
  // 편집·무장 중인 행이 사라져도(상태 칩으로 거른 쪽에서 상태가 바뀌었다) 그 상태를 정리한다 -- 보이지 않는 행의 폼·무장이
  // 남아 있다가 다시 나타나면 오조작을 부른다. 자리표시(앞 쪽)로는 판정하지 않는다.
  useEffect(() => {
    if (pageItems === undefined) return;
    const live = new Set(pageItems.map((it) => it.seq));
    setSelected((prev) => {
      const next = new Set([...prev].filter((s) => live.has(s)));
      return next.size === prev.size ? prev : next;
    });
    if (itemsQ.isPlaceholderData) return;
    setEditSeq((cur) => (cur !== null && !live.has(cur) ? null : cur));
    setArmedSeq((cur) => (cur !== null && !live.has(cur) ? null : cur));
  }, [pageItems, itemsQ.isPlaceholderData]);
  const allChecked = deletableSeqs.length > 0 && deletableSeqs.every((s) => selected.has(s));
  // indeterminate 는 속성이 아니라 DOM 프로퍼티라 ref 로만 설정된다.
  const allRef = useRef<HTMLInputElement>(null);
  const someChecked = selected.size > 0 && !allChecked;
  useEffect(() => { if (allRef.current) allRef.current.indeterminate = someChecked; },
            [someChecked]);
  // 선택이 바뀌면 무장을 풀고 직전 결과 문구도 지운다 — "2개 삭제 확인"이 무장된
  // 채 선택만 3개로 늘면 사용자가 확인한 것과 다른 것을 지우게 된다. 진행 중에는
  // reset 하지 않는다(비행 중인 mutation 의 결과를 삼킨다).
  const bulkDeleteReset = bulkDelete.reset;
  const bulkRerunReset = bulkRerun.reset;
  const disarm = useCallback(() => {
    setBulkArmed(null);
    if (!bulkDelete.isPending) bulkDeleteReset();
    if (!bulkRerun.isPending) bulkRerunReset();
  }, [bulkDelete.isPending, bulkRerun.isPending, bulkDeleteReset, bulkRerunReset]);
  const toggleRow = useCallback((seq: number) => {
    disarm();
    setSelected((p) => {
      const next = new Set(p);
      if (!next.delete(seq)) next.add(seq);
      return next;
    });
  }, [disarm]);
  const clearSelection = () => { setBulkArmed(null); setSelected(new Set()); };
  const bulkResult = bulkDelete.data;
  const rerunResult = bulkRerun.data;
  // 일괄 동작이 도는 동안엔 선택 조작을 잠근다(삭제 관례 미러 — 비행 중에 선택이
  // 바뀌면 결과 문구가 가리키는 대상이 화면과 어긋난다).
  const bulkBusy = bulkDelete.isPending || bulkRerun.isPending;
  // 선택 항목 중 재실행 가능한 것이 하나도 없으면 잠근다. 눌러도 서버가 전부
  // skipped 로 돌려줄 버튼을 누르게 두는 건 헛클릭이다. 반대로 **하나라도** 있으면
  // 선택 전부를 보낸다(가능한 것만 골라 보내면 나머지가 조용히 사라진다 — 못 한
  // 것은 서버가 항목별 사유로 말해야 한다).
  const rerunnableCount = useMemo(
    () => (pageItems ?? []).filter((it) => selected.has(it.seq) && RERUNNABLE_ITEM.has(it.status)).length,
    [pageItems, selected]);
  const selectedList = [...selected];
  return (
    <section className="space-y-4">
      {/* 이름이 있으면 이름이 헤더 — 축약 batch_id 는 식별자로 병기한다(사라지면
          운영자가 로그·API 와 대조할 열쇠를 잃는다). 없으면 기존 헤더 유지. */}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-bold">{b?.name ?? `배치 ${batchId.slice(0,12)}`}</h1>
        {b?.name && <span className="text-muted text-sm font-mono">{batchId.slice(0,12)}</span>}
        {/* 이 배치의 작업(재실행 이력 포함)만 거른 전체 작업 목록 -- 작업까지 지우는 배치 단위 삭제는 거기서 시작한다
            (2026-10-11 검증 지적: 작업 목록에 배치 필터·링크가 없어 오래된 배치의 작업을 찾을 수 없었다). */}
        <Link className="text-accent text-sm" to={batchJobsPath(batchId)}>전체 작업에서 이 배치의 작업 보기</Link>
        {b && <ScheduleLink b={b} />}
      </div>
      <Card>
        {/* 헤더 세 줄은 줄바꿈된다(2026-10-11 375 실측): 끝난 배치의 버튼 넷(편집·전체 재실행·실행 설정 변경·배치 삭제)이
            한 줄로 12~19px 넘쳤고, 자동 실행 표시가 붙으면 80px 까지 넘쳤다. */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {/* 배치 상태 전용 색(batchPillVariant — Completed=초록·진행=busy·
                Cancelled=neutral). 항목 pill 은 요청/잡 판정 축이라 기존 공유
                pillVariant 를 그대로 쓴다(variant 미지정). */}
            <StatusPill state={b?.status ?? "…"}
                        variant={b ? batchPillVariant(b.status) : undefined}
                        label={b ? batchStatusLabel(b.status) : undefined}
                        help={b ? batchStateHelp(b.status) : null} />
            <span className="text-muted text-sm">{b?.operation} · 성공 {b?.succeeded_count}/실패 {b?.failed_count}/전체 {b?.item_count}</span>
            {/* 특권 실행 문구는 로드되면 항상 -- 통일 게이트 후 배치는 전부 관리자
                특권(root) 실행이다. 행별 auth_method/owner 유무로 재판정하지 않는다:
                프론트는 allowlist 를 모르므로 판정 흉내가 더 큰 거짓말이 된다(구형
                비특권 행에는 이 단순 표시가 과잉이지만, 거짓 판정보다 낫다).
                한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례). */}
            {b && <span className="text-bad text-sm">특권 실행(root)</span>}
            {b?.owner_username && (
              <span className="text-muted text-sm">{`실행 신원 ${b.owner_username}`}</span>
            )}
            {/* 자동 확인(sync 2026-10-11, rm 2026-10-21)은 어느 상태에서나 보인다 -- 끝난 배치에서도 재실행이 확인 없이
                root 로 돈다는 뜻이라 접힌 「실행 설정」 안에만 두면 모른 채 재실행을 누른다(리뷰). rm 은 무엇이 생략되는지
                (확인 없이 삭제)까지 빨강으로 말한다 -- 「자동 실행」만으로는 지우는 배치임이 헤더에서 보이지 않는다. */}
            {autoConfirmable(b?.operation) && b?.auto_confirm === true && (b.operation === "rm" ? (
              <span className="text-bad text-sm" title="미리보기가 끝나면 「배치 확인」 없이 root 로 삭제 — 되돌릴 수 없음">
                {`자동 실행 — 미리보기가 끝나면 확인 없이 삭제${b.auto_confirm_by ? ` (켠 사람 ${b.auto_confirm_by})` : ""}`}
              </span>
            ) : (
              <span className="text-accent text-sm" title="미리보기가 끝나면 「배치 확인」 없이 실행">
                {`자동 실행${b.auto_confirm_by ? ` (켠 사람 ${b.auto_confirm_by})` : ""}`}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {/* 라벨은 짧게 「편집」 — 무엇을 편집하는지는 폼 안 입력 라벨("배치
                이름"/"메모")이 말한다(사용자 조정 2026-08-15). "메모 편집"은 폼이
                이름까지 고치는데 메모만 말해 어색했고 "이름·메모 편집"은 길다.
                PATCH {name, note} 계약·폼 구조는 무변경. */}
            {b && !editing && <Button variant="ghost" onClick={startEdit}>편집</Button>}
            {b?.status === "PreviewReady" && <ConfirmBatchDialog b={b} confirm={confirm} absOf={absOf} />}
            {b?.status === "Completed" && (b?.failed_count ?? 0) > 0 && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>실패분 재실행</Button>}
            {/* 전체 재실행(:rescan): 종단 배치 한정(서버 가드 미러) — 성공 item 포함
                전부 재큐잉(성장 모니터링). "실패분 재실행"(실패만)과 공존한다 */}
            {(b?.status === "Completed" || b?.status === "Cancelled") && <Button disabled={rescan.isPending} onClick={() => rescan.mutate()}>전체 재실행</Button>}
            {/* 실행 설정 변경(2026-10-02): 재실행 버튼들과 같은 종단 게이트(서버 409 batch_settings_locked 미러) --
                취소·완료 뒤 노드·프로세스 수·옵션을 바꾸고 다시 돌리는 동선 */}
            {b && terminal && <BatchExecutionSettingsDialog b={b} />}
            {(b?.status === "Completed" || b?.status === "Cancelled") && <DeleteBatchButton batchId={batchId} hasSchedule={b.schedule != null} />}
            {/* 자동 실행 끄기(2026-10-11): 진행 중 브레이크 — 미리보기를 보고 직접 확인하겠다. 종단 배치는 「실행 설정 변경」. */}
            {autoConfirmable(b?.operation) && b?.auto_confirm === true && !terminal && (
              <Button variant="ghost" disabled={autoOff.isPending} onClick={() => autoOff.mutate()}>자동 실행 끄기</Button>
            )}
            {cancellable && <CancelButton disabled={cancel.isPending} onClick={() => cancel.mutate()}
                                          title={repeating ? REPEAT_CANCEL_NOTE : undefined}>취소</CancelButton>}
          </div>
        </div>
        {/* 반복 스케줄이 켜진 배치의 취소(2026-11-16): 취소는 이번 회차만 멈춘다 -- 스케줄은 그대로라 다음 예정 시각에 다시 돈다.
            확인 창 없는 버튼이라 title 만으로는 터치 화면에서 안 보여 버튼 줄 아래 한 줄로 말한다. */}
        {cancellable && repeating && <p className="mt-2 text-xs text-ink/70 break-keep">{`취소는 ${REPEAT_CANCEL_NOTE}`}</p>}
        {/* 메모는 편집 밖에서도 보인다 — 없으면 행 자체 생략(빈칸 소음 방지).
            라벨("메모 ")은 붙이지 않는다(사용자 조정 2026-08-15): 헤더 카드에서
            이름 아래 한 줄은 문맥상 메모임이 자명한데, 접두어가 매번 내용 앞을
            가로막았다. 행이 아예 없으면 메모 없음이라는 사실도 그대로 읽힌다. */}
        {/* 실패분 재실행·전체 재실행의 거부(409·422)를 말한다 -- 말하지 않으면 버튼이 죽은 것처럼 보였다
            (2026-10-01: 이름 chown 옛 배치는 모두 422 chown_name_not_supported). 배치 확인의 거부는 확인 대기
            동안엔 대화상자가 말하고, 거부 뒤 배치가 Previewing 등으로 바뀌어 대화상자가 사라지면 여기서 이어 말한다
            (409 batch_not_confirmable -- 말하지 않으면 확인이 조용히 사라진 것처럼 보였다). */}
        {[rerun, rescan, autoOff, cancel, ...(b?.status !== "PreviewReady" ? [confirm] : [])].filter((m) => m.isError).map((m, i) => (
          // 자동 확인이 먼저 확인해 사람 확인이 409 로 진 경우(배치는 이미 실행 중) -- 「확인할 수 없는 상태」는 거짓 경보다.
          m === confirm && b?.auto_confirm === true && b.status === "Running"
            && (m.error as ApiError).code === "batch_not_confirmable"
            ? <p key={i} role="status" className="text-sm text-muted mt-2">이미 자동 확인돼 실행 중입니다.</p>
            : <p key={i} role="alert" className="text-bad text-sm mt-2">{(m.error as ApiError).message}</p>
        ))}
        {/* 끝난 자동 배치: 재실행·항목 추가는 미리보기 뒤 확인 없이 root 로 돈다 -- 누르기 전에 말한다(리뷰). */}
        {autoConfirmable(b?.operation) && b?.auto_confirm === true && terminal && (
          <p className={`mt-2 text-sm ${b.operation === "rm" ? "text-bad" : "text-muted"}`}>
            {b.operation === "rm"
              ? "자동 실행이 켜져 있어 재실행하거나 항목을 추가하면 미리보기가 끝나는 즉시 「배치 확인」 없이 삭제(root)됩니다 — "
                + "되돌릴 수 없습니다. 끄려면 「실행 설정 변경」에서 끄세요."
              : "자동 실행이 켜져 있어 재실행하거나 항목을 추가하면 미리보기가 끝나는 즉시 「배치 확인」 없이 실행(root)됩니다 — "
                + "끄려면 「실행 설정 변경」에서 끄세요."}
          </p>
        )}
        {b?.operation === "sync" && chownHasName(String(b?.options?.chown ?? "")) && (
          <p role="alert" aria-label="이름 chown 배치" className="mt-2 rounded-lg border border-bad px-3 py-2 text-sm text-bad">
            {`이 배치는 chown 에 이름(${String(b.options?.chown)})이 들어 있어 이대로는 실행할 수 없습니다 — 확인·재실행·`
              + "항목 추가와 남은 항목은 거부됩니다(이름은 작업 컨테이너에서 해석되지 않습니다). 배치를 취소(진행 중이면)한 뒤 "
              + "「실행 설정 변경」에서 chown 을 숫자 uid:gid 로 고치면 다시 실행할 수 있습니다."}
          </p>
        )}
        {/* sync 확인 단계 안내(2026-10-07): 미리보기 중·확인 대기는 사람이 다음에 무엇을 해야 하는지가 상태 배지만으론
            보이지 않았다. 확인 전에는 어떤 항목도 실행되지 않는다는 사실까지 말한다. */}
        {autoConfirmable(b?.operation) && b?.status === "Previewing" && b.auto_confirm === true && (
          <p className="mt-2 text-sm text-muted">
            {b.operation === "rm"
              ? (b.auto_confirm_hold
                  ? `항목별 미리보기(dry-run) 중입니다 — 자동 실행이 켜져 있지만 지금은 보류될 상태라, 끝나면 「배치 확인」을 `
                    + `눌러야 삭제됩니다: ${reasonText(b.auto_confirm_hold)}`
                  : "항목별 미리보기(dry-run) 중입니다 — 지울 항목을 세기만 합니다. 자동 실행이 켜져 있어 끝나면 「배치 확인」 "
                    + "없이 바로 삭제(root)됩니다. 지울 대상을 보고 직접 확인하려면 「자동 실행 끄기」를 누르세요.")
              : b.auto_confirm_hold
                ? `항목별 미리보기(dry-run) 중입니다 — 자동 실행이 켜져 있지만 지금은 보류될 상태라, 끝나면 「배치 확인」을 `
                  + `눌러야 실행됩니다: ${reasonText(b.auto_confirm_hold)}`
                : "항목별 미리보기(dry-run) 중입니다 — 자동 실행이 켜져 있어 끝나면 「배치 확인」 없이 바로 실행(root)됩니다. "
                  + "미리보기를 보고 직접 확인하려면 「자동 실행 끄기」를 누르세요."}
            {executing !== null && ` 이미 실행 중인 항목 ${executing}개는 끝까지 실행(root)됩니다.`}
          </p>
        )}
        {/* rm(2026-10-21)도 같은 확인 흐름 -- 문구만 「삭제」로(미리보기는 지울 항목을 세기만 한다). */}
        {needsBatchConfirm(b?.operation) && b?.status === "Previewing" && b.auto_confirm !== true && (
          <p className="mt-2 text-sm text-muted">
            {b.operation === "rm"
              ? (executing !== null
                  ? `미리보기 단계입니다 — 이미 확인돼 삭제 중인 항목 ${executing}개는 끝까지 실행(root)되고, 나머지는 확인을 `
                    + "기다립니다. 미리보기가 끝나면 「배치 확인」을 눌러야 나머지가 삭제됩니다."
                  : "항목별 미리보기(dry-run) 중입니다 — 지울 항목을 세기만 하고 아무것도 지우지 않습니다. 끝나면 "
                    + "「배치 확인」을 눌러야 삭제됩니다.")
              : executing !== null
                ? `미리보기 단계입니다 — 이미 실행 중인 항목 ${executing}개는 끝까지 실행(root)되고, 나머지는 확인을 `
                  + "기다립니다. 미리보기가 끝나면 「배치 확인」을 눌러야 나머지가 실행됩니다."
                : "항목별 미리보기(dry-run) 중입니다 — 끝나면 「배치 확인」을 눌러야 실행됩니다."}
          </p>
        )}
        {needsBatchConfirm(b?.operation) && b?.status === "Running" && (
          <p className="mt-2 text-sm text-muted">
            확인된 항목은 동시 실행 상한({b.max_concurrency})만큼씩 차례로 실행됩니다.
          </p>
        )}
        {/* 취소 처리 중(2026-10-16): 라우트가 자식마다 k8s 를 끝내는 동안, 또는 종료가 실패한 자식을 컨트롤러가 다시
            시도하는 동안. 버튼은 없다(이미 취소했다) -- 무엇을 기다리는지만 말한다. */}
        {b?.status === "Cancelling" && (
          <p className="mt-2 rounded-lg bg-infobg px-3 py-2 text-sm" role="status">
            취소하는 중입니다 — 새 항목은 시작되지 않고, 실행 중이던 항목의 작업을 종료하고 있습니다. 종료하지 못한 작업은
            컨트롤러가 1분쯤 뒤 다시 종료하고, 다 끝나면 「Cancelled」가 됩니다.
          </p>
        )}
        {b?.status === "PreviewReady" && b.auto_confirm === true && (
          <p className="mt-2 rounded-lg bg-infobg px-3 py-2 text-sm" role="status">
            {b.auto_confirm_hold
              ? `자동 실행이 보류됐습니다 — ${reasonText(b.auto_confirm_hold)}`
              : b.operation === "rm"
                ? "미리보기가 끝났습니다 — 자동 실행이 켜져 있어 곧 「배치 확인」 없이 삭제됩니다."
                : "미리보기가 끝났습니다 — 자동 실행이 켜져 있어 곧 「배치 확인」 없이 실행됩니다."}
          </p>
        )}
        {b?.status === "PreviewReady" && b.auto_confirm !== true && (
          <p className="mt-2 rounded-lg bg-infobg px-3 py-2 text-sm" role="status">
            {b.operation === "rm"
              ? (executing !== null
                  ? `미리보기가 끝났습니다 — 「배치 확인」에서 항목별 삭제 대상을 검토하고 확인해야 나머지가 삭제됩니다. 이미 `
                    + `확인돼 삭제 중인 항목 ${executing}개만 계속 돌고, 확인 전에는 다른 항목이 지워지지 않습니다.`
                  : "미리보기가 끝났습니다 — 「배치 확인」에서 항목별 삭제 대상을 검토하고 확인해야 삭제됩니다. 확인 전에는 "
                    + "아무것도 지워지지 않습니다.")
              : executing !== null
                ? `미리보기가 끝났습니다 — 「배치 확인」을 눌러야 나머지가 실행됩니다. 이미 확인돼 실행 중인 항목 ${executing}개만 `
                  + "계속 돌고, 확인 전에는 다른 항목이 실행되지 않습니다."
                : "미리보기가 끝났습니다 — 「배치 확인」을 눌러야 실행됩니다. 확인 전에는 어떤 항목도 실행되지 않습니다."}
          </p>
        )}
        {b?.note && !editing && <p className="text-muted text-sm mt-2">{b.note}</p>}
        {b && <BatchSettings b={b} />}
        {editing && (
          <div className="mt-3 space-y-2 max-w-md">
            <label className="text-sm block">배치 이름
              <input aria-label="배치 이름" maxLength={120} className={field}
                     value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} />
            </label>
            <label className="text-sm block">메모
              <input aria-label="메모" className={field}
                     value={noteDraft} onChange={(e) => setNoteDraft(e.target.value)} />
            </label>
            <div className="flex gap-2">
              <Button disabled={update.isPending} onClick={save}>저장</Button>
              {/* 배치 취소 버튼("취소")과 라벨이 겹치지 않게 "편집 취소" */}
              <Button variant="ghost" onClick={() => setEditing(false)}>편집 취소</Button>
            </div>
            {update.isError && (
              <p className="text-bad text-sm">{(update.error as ApiError).message}</p>
            )}
          </div>
        )}
      </Card>
      {/* 항목: 표 대신 리스트 + 행 펼침. 표(td) 안에 버튼·flex 를 넣으면 e2e L2
          (display=table-cell 불변식)가 무는 함정이라, 펼침 UI 는 표 밖 리스트가
          구조적으로 안전하다. 기본 행은 컴팩트(순번·대상 요약·상태), 상세(사유·
          파일 수·완료 시각·payload·요청 링크)는 펼친 행에만. */}
      <Card>
        {/* 카드 헤더 = 전체 선택 + 제목 + 도구 버튼 3종. 도구가 헤더에 모여 있어야
            항목 목록이 카드의 본문 전체를 쓴다(인라인 시절엔 목록 아래 세 영역이
            자리를 먹었다). */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            {/* 전체 선택은 **이 쪽의 선택 가능한 항목만** 토글한다 — 잠긴 항목까지 켜 두면 지울 수 없는 것을 골라 둔 셈이
                되어 매번 409 를 만들고, 다른 쪽까지 담으면 보이지 않는 것을 지운다. */}
            {hasItems && (
              <input ref={allRef} type="checkbox" aria-label="전체 선택"
                     className="h-5 w-5 shrink-0"
                     checked={allChecked}
                     disabled={deletableSeqs.length === 0 || bulkBusy}
                     onChange={() => { disarm();
                                       setSelected(allChecked ? new Set() : new Set(deletableSeqs)); }} />
            )}
            <h2 className="font-medium">항목</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {/* 노출 조건은 인라인 시절 그대로: CSV 계열·추가는 항목 존재(스토리지
                상속원), 교체는 종단 배치 한정. 항목 존재·상속원은 머리 집계다(거른 쪽이 아니다). */}
            {b && hasItems && (
              <CurrentItemsCsvDialog batchId={batchId} operation={csvOp(b.operation)} />
            )}
            {b && firstPayload && (
              <AddItemDialog batchId={batchId} isSync={isSync} status={b.status}
                             auto={b.auto_confirm === true && !b.auto_confirm_hold}
                             confirmFlow={needsBatchConfirm(b.operation)}
                             isRm={b.operation === "rm"}
                             firstPayload={firstPayload} chown={String(b.options?.chown ?? "")} />
            )}
            {b && terminal && firstPayload && (
              <ReplaceItemsCsvDialog batchId={batchId}
                                     operation={csvOp(b.operation)}
                                     auto={b.auto_confirm === true && !b.auto_confirm_hold}
                                     firstPayload={firstPayload} chown={String(b.options?.chown ?? "")} />
            )}
          </div>
        </div>
        {/* 찾기 줄(2026-11-16): 경로 검색 + 상태 칩. 검색칸은 사용량 경로 검색과 같은 모양(좁은 폭에선 자기 행, sm 이상 최대
            24rem). 칩은 줄바꿈된다 -- 일곱 개를 한 줄(Segmented)로 두면 375 에서 문서가 가로로 넘친다. 칩의 수는 머리 집계(쪽·
            검색과 무관한 배치 전체)이고 0 도 그대로 보인다(0 = 그 상태의 항목이 없다는 값). */}
        {b && hasItems && (
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
            <label className="relative min-w-0 flex-1 basis-full sm:max-w-sm sm:basis-auto">
              <span className="sr-only">항목 경로 검색</span>
              <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
              <input value={qInput} onChange={(e) => setQInput(e.target.value)} type="search"
                     placeholder="경로 검색"
                     className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-3 text-sm
                                placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
            </label>
            <div role="group" aria-label="항목 상태" className="flex flex-wrap gap-1">
              {ITEM_STATUS_CHIPS.map((c) => {
                const on = statusFilter === c.status;
                const n = c.status === null ? b.item_total : b.status_counts[c.status] ?? 0;
                return (
                  <button key={c.label} type="button" aria-pressed={on} onClick={() => pickStatus(c.status)}
                          className={`whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs ${
                            on ? "border-accent bg-accent/10 font-semibold text-accent" : "border-line text-muted hover:text-ink"}`}>
                    {`${c.label} ${n.toLocaleString("ko-KR")}`}
                  </button>
                );
              })}
            </div>
            {/* 거르는 중에만 건수(거르지 않으면 머리의 「전체 N」이 이미 말한다). 쪽·검색을 바꾸는 동안(앞 쪽이 자리표시)은
                옛 건수라 말하지 않는다. */}
            {itemsQ.isPlaceholderData
              ? <span className="whitespace-nowrap text-sm text-muted">불러오는 중…</span>
              : filtered && total !== undefined && (
                <span className="whitespace-nowrap text-sm text-muted">{`검색 결과 ${total.toLocaleString("ko-KR")}개`}</span>)}
          </div>
        )}
        {/* 액션 바 자리 예약: 선택 여부와 무관하게 같은 높이의 컨테이너를 늘 그리고
            내용만 바꾼다 — 선택 순간에 바가 생기면 아래 목록이 통째로 밀려(레이아웃
            점프) 방금 체크한 행이 커서 밑에서 사라진다. */}
        {hasItems && (
          <div className="mt-2 flex min-h-10 flex-wrap items-center gap-2 text-sm">
            {selected.size > 0 ? (<>
              <span className="mr-auto">{`${selected.size}개 선택됨`}</span>
              {bulkArmed === "delete"
                ? <Button disabled={bulkBusy}
                          onClick={() => bulkDelete.mutate(selectedList,
                            { onSettled: clearSelection })}>
                    {`${selected.size}개 삭제 확인`}
                  </Button>
                : <Button variant="outline" disabled={bulkBusy}
                          onClick={() => setBulkArmed("delete")}>선택 삭제</Button>}
              {/* 재실행도 삭제와 같은 2단 확인. 확인 라벨의 N 은 재실행 가능 수가
                  아니라 **선택 수**다 — 실제로 보내는 것이 선택 전부라 그 숫자가
                  사용자가 확인하는 대상과 일치한다(제외분은 결과가 말한다). */}
              {bulkArmed === "rerun"
                ? <Button disabled={bulkBusy}
                          onClick={() => bulkRerun.mutate(selectedList,
                            { onSettled: clearSelection })}>
                    {`${selected.size}개 재실행 확인`}
                  </Button>
                : <Button variant="outline"
                          disabled={bulkBusy || rerunnableCount === 0}
                          title={rerunnableCount === 0 ? RERUN_LOCK_HINT : undefined}
                          onClick={() => setBulkArmed("rerun")}>선택 재실행</Button>}
              <Button variant="ghost" disabled={bulkBusy}
                      onClick={clearSelection}>선택 해제</Button>
            </>) : (
              <span className="text-muted">항목을 선택하면 한 번에 삭제·재실행할 수 있습니다</span>
            )}
          </div>
        )}
        {/* 일괄 삭제 결과는 액션 바 **밖**이다: 완료 시 선택이 비어 바가 안내 문구로
            돌아가므로 바 안에 두면 결과가 같이 증발한다. 부분 실패를 뭉개지 않는 게
            이 블록의 존재 이유 — 성공 수·실패 수·항목별 사유를 각각 말한다. */}
        {bulkResult && (
          <div className="mt-2 text-sm" role="status">
            <p className={bulkResult.failed.length > 0 ? "text-bad" : "text-ok"}>
              {bulkResult.failed.length > 0
                ? `${bulkResult.ok.length}개 삭제됨 · ${bulkResult.failed.length}개 실패`
                : `${bulkResult.ok.length}개 삭제됨`}
            </p>
            {bulkResult.failed.map((f) => (
              <p key={f.seq} className="text-muted">{`항목 ${f.seq}: ${f.message}`}</p>
            ))}
          </div>
        )}
        {/* 재실행 결과도 액션 바 밖(삭제 결과와 같은 이유). "요청됨"인 이유: 이
            호출은 항목을 Queued 로 되돌릴 뿐이고 실제 실행은 orchestrator 가
            집어 간다 — "재실행됨"은 아직 일어나지 않은 일을 말하는 것이다.
            제외분은 서버가 준 사유 코드를 reasonText 로 옮긴다(원문 코드 노출 금지). */}
        {rerunResult && (
          <div className="mt-2 text-sm" role="status">
            <p className={rerunResult.skipped.length > 0 ? "text-bad" : "text-ok"}>
              {rerunResult.skipped.length > 0
                ? `${rerunResult.requeued}개 재실행 요청됨 · ${rerunResult.skipped.length}개 제외`
                : `${rerunResult.requeued}개 재실행 요청됨`}
            </p>
            {rerunResult.skipped.map((s) => (
              <p key={s.seq} className="text-muted">{`항목 ${s.seq}: ${reasonText(s.reason)}`}</p>
            ))}
          </div>
        )}
        {/* 선택 재실행 자체가 거부된 경우(422 등) -- 선택은 지워져도 사유는 남긴다(액션 바 밖) */}
        {bulkRerun.isError && (
          <p role="alert" className="mt-2 text-sm text-bad">{(bulkRerun.error as ApiError).message}</p>
        )}
        {/* 쪽 조회 상태: 첫 조회 중·실패(받아 둔 쪽이 없을 때만 -- 백그라운드 재조회 한 번의 실패로 목록을 지우지 않는다). */}
        {hasItems && pageItems === undefined && (itemsQ.isError
          ? <p className="text-bad text-sm mt-2">{(itemsQ.error as Error).message}</p>
          : <p className="text-muted text-sm mt-2">불러오는 중…</p>)}
        {/* 행은 머리가 온 뒤에만 그린다: 두 조회가 따로 떠서 항목이 먼저 오면 연산(요약 모양)·종단 여부(수정·삭제·선택
            게이트)를 모르는 채 행이 그려졌다가 바뀐다. */}
        {b !== undefined && (pageItems ?? []).map((it) => (
          <BatchItemRow key={it.seq} it={it} operation={b?.operation} isSync={isSync}
                        chown={String(b?.options?.chown ?? "")}
                        checked={selected.has(it.seq)} selectable={canEditItem(it)} bulkBusy={bulkBusy}
                        open={openSeq === it.seq} editing={editSeq === it.seq} armed={armedSeq === it.seq}
                        absPath={absOf(it)}
                        deletePending={armedSeq === it.seq && deleteItem.isPending}
                        deleteError={deleteItem.isError && deleteItem.variables === it.seq
                          ? (deleteItem.error as ApiError).message : null}
                        pathDraft={editSeq === it.seq ? pathDraft : ""}
                        dstDraft={editSeq === it.seq ? dstDraft : ""}
                        updatePending={editSeq === it.seq && updateItem.isPending}
                        updateError={editSeq === it.seq && updateItem.isError
                          ? (updateItem.error as ApiError).message : null}
                        onToggleSelect={toggleRow} onToggleOpen={toggleOpen} onStartEdit={startItemEdit}
                        onArm={setArmedSeq} onDelete={deleteOne} onSave={saveItem} onCancelEdit={cancelItemEdit}
                        onPathDraft={setPathDraft} onDstDraft={setDstDraft} />
        ))}
        {/* 검색 0건은 「항목 없음」이 아니다 -- 배치에는 항목이 있다(도구 버튼도 그대로). 지우는 길을 함께 둔다. */}
        {/* filtered 일 때만: 거르지 않았는데 0건이면 머리(항목 있음)와 쪽이 서로 다른 순간에 읽힌 것(다른 세션이 막 비웠다)이라
            「검색 결과」라 말할 것이 없다 -- 다음 폴링이 머리를 맞춘다. */}
        {hasItems && filtered && !itemsQ.isPlaceholderData && total === 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
            <span>검색 결과가 없습니다</span>
            <Button type="button" variant="ghost" className="py-1"
                    onClick={() => { setQInput(""); setPathQ(""); pickStatus(null); }}>검색 지우기</Button>
          </div>
        )}
        {/* 항목 0개: 도구 버튼(추가·CSV)이 전부 사라진 자리에 왜 없는지를 남긴다 —
            물려받을 스토리지가 없어 항목 추가 자체가 불가능하다(빈 배치는 삭제·
            재생성이 동선). */}
        {b && !hasItems && (
          <p className="text-muted text-sm mt-2">항목이 없어 스토리지를 물려받을 수 없습니다 — 항목 추가는 새 배치로 하세요</p>
        )}
        {/* 쪽 넘김은 목록 바로 아래(카드 안). 쪽을 넘기면 선택은 비워진다(위 선택 주석). */}
        {pagerCount > 1 && (
          <div className="mt-3 border-t border-black/5 pt-3">
            <Pager page={page} pageCount={pagerCount} onPage={goPage} label="항목 쪽" />
          </div>
        )}
      </Card>
    </section>
  );
}
