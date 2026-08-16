import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useBatch, useConfirmBatch, useRerunFailed, useRequestScanStats,
         useCancelBatch, useRescanBatch, useUpdateBatch, useDeleteBatch,
         useUpdateBatchItem, useDeleteBatchItem, useDeleteBatchItems,
         useAddBatchItem, useReplaceBatchItems, useRerunBatchItems } from "./useBatches";
import { parseItemsCsv, serializeItemsCsv,
         type ScanRow, type SyncRow } from "../../lib/csv";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { StatusPill } from "../../components/ui/StatusPill";
import { BarChart } from "../../components/ui/BarChart";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import { useRequestJobs } from "../jobs/useJobs";
import { reasonText, ApiError } from "../../lib/api";
import { absSummary } from "../../lib/storagePaths";
import { toolSummary } from "../../lib/jobTool";
import { useStorageRoots } from "../storages/useUserStorages";
import { batchPillVariant } from "../../lib/jobState";
import type { Batch, BatchItem, HistogramBucket } from "../../lib/types";

// NodesList/JobStats/NodeMetrics 의 humanBytes 국소 사본 관례 -- 값이 bytes 대
// 전역(B~TiB)이라 KiB 단을 포함한다(NodeMetricsSection 판과 같은 단위 집합).
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2], ["KiB", 1024],
];
function humanBytes(bytes: number): string {
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${Math.round(bytes)} B`;
}

// 투영 버킷 -> BarChart 데이터. 라벨·값이 실존하는 버킷만 그린다 -- 값 없는
// 버킷을 0 으로 그리면 "빈 버킷"(정상값 0)과 "모름"이 섞인다(null≠0).
function toBars(buckets: HistogramBucket[] | undefined,
                key: "bytes" | "count",
                labelOf: (b: HistogramBucket) => string = (b) => b.bucket as string) {
  return (buckets ?? []).flatMap((b) =>
    typeof b.bucket === "string" && typeof b[key] === "number"
      ? [{ label: labelOf(b), value: b[key] as number }] : []);
}

// 크기 버킷 축 라벨용 초단축 표기(1024 단위). humanBytes 와 같은 단위 체계지만
// 소수·공백·"iB" 를 떼서 축 라벨 폭을 줄인다 -- 원본 라벨("[1073741824,4294967296]")
// 은 열 폭(max-w-16 = 4rem)에서 잘려 어느 구간인지 못 읽는다(사용자 지적). 축약이
// KiB 계열임은 차트 캡션이 말한다(K 를 1000 으로 읽는 오독 방지).
//
// 반올림 규칙: 한 자리 소수로 적되 그 자리가 "X.0" 이 될 값은 정수로 적는다.
// 실측(d63) dscan 버킷은 [직전 상한+1, 상한] 이라 하한이 늘 2의 거듭제곱+1 이다
// -- 그대로 한 자리로 적으면 "64.0K~1M" 처럼 의미 없는 .0 이 붙어 라벨만 넓어진다
// (1 바이트 차이를 소수 한 자리로는 어차피 못 보인다). 진짜 중간값(1.5K)은 그대로
// "1.5K" 로 남는다 -- 반올림 폭이 표기 해상도(0.05)를 넘지 않는 값만 접는다.
const LABEL_UNITS: [string, number][] = [
  ["T", 1024 ** 4], ["G", 1024 ** 3], ["M", 1024 ** 2], ["K", 1024],
];
function shortBytes(bytes: number): string {
  for (const [unit, size] of LABEL_UNITS) {
    if (bytes >= size) {
      const v = bytes / size;
      const round = Math.round(v);
      return `${Math.abs(v - round) < 0.05 ? round : v.toFixed(1)}${unit}`;
    }
  }
  return String(Math.round(bytes));
}
// 하한·상한은 서버 투영이 넘기는 수치 필드가 1순위다(모양 검사를 통과한 숫자).
// 없으면 구간 라벨("[0,4096]")에서 읽고, 그마저 아니면 원본 라벨을 그대로 둔다 --
// 모르는 모양을 추측해 다시 쓰면 화면이 없는 사실을 지어낸다. 상한 없음(열린
// 마지막 구간 "[1G,)")은 "1G~" -- 0(정상값)이나 임의 상한으로 뭉개지 않는다.
const RANGE_RE = /^[[(]\s*(\d+)\s*,\s*(\d*)\s*[\])]$/;
function sizeBucketLabel(b: HistogramBucket): string {
  const raw = b.bucket as string;
  const m = RANGE_RE.exec(raw);
  const lower = typeof b.lower_inclusive === "number" ? b.lower_inclusive
              : m ? Number(m[1]) : null;
  if (lower === null) return raw;
  const upper = typeof b.upper_inclusive === "number" ? b.upper_inclusive
              : m && m[2] !== "" ? Number(m[2]) : null;
  return upper === null ? `${shortBytes(lower)}~`
                        : `${shortBytes(lower)}~${shortBytes(upper)}`;
}

// 시간축별 캡션: 색의 의미(왼쪽 빨강=hot, 오른쪽 파랑=cold)를 축마다 그 축의
// 언어로 말한다. atime 만 근사 경고(relatime/open_noatime 는 접근이 atime 을
// 안 갱신할 수 있다) -- mtime/ctime 은 마운트 옵션과 무관해 경고가 거짓이 된다.
const TEMP_CAPTIONS: Record<string, string> = {
  atime: "왼쪽(빨강)=hot·최근 접근, 오른쪽(파랑)=cold — atime 기준 용량 비중. relatime/open_noatime 환경에선 근사",
  mtime: "왼쪽(빨강)=최근 수정, 오른쪽(파랑)=오래됨 — mtime 기준 용량 비중",
  ctime: "왼쪽(빨강)=최근 변경, 오른쪽(파랑)=오래됨 — ctime 기준 용량 비중",
};

// 9버킷 hot→cold 온도 팔레트(웜→쿨 자연 그라디언트). 정적 hex inline style --
// airgap 무관(런타임 외부 로드가 아니라 그냥 번들 안 문자열이다).
const TEMP_PALETTE = ["#dc2626", "#ea580c", "#f59e0b", "#eab308", "#84cc16",
                      "#22c55e", "#06b6d4", "#3b82f6", "#6366f1"];
// 첫 막대=빨강(hot)·끝 막대=파랑(cold)이 막대 수와 무관하게 유지되게 비례 사상
// 한다 -- 팔레트 인덱스 직결이면 9 미만 히스토그램이 전부 웜톤이 된다.
const tempColorOf = (n: number) => (i: number) =>
  TEMP_PALETTE[n <= 1 ? 0 : Math.round((i / (n - 1)) * (TEMP_PALETTE.length - 1))];

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

/** 리포트 생성 시각은 UTC로만 보여준다(ScanPaths 국소 사본 관례) — 스토리지·잡은
 *  UTC로 기록되고, 로컬시간으로 바꾸면 운영자·사용자가 다른 시각을 말하게 된다. */
function utcStamp(epoch: number) {
  return `${new Date(epoch * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

// payload 필드 결손 방어 + 대상 요약: 대시보드 RecentRequestsSection 의 summarize
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
function BatchSettings({ b }: { b: Batch }) {
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
        <dd className="font-mono text-xs break-all">{humanizeOptions(b.options)}</dd>
        {/* 실행 신원(라벨 정정 2026-08-16 — owner_username 은 소유자 기록이 아니라
            잡의 실행 신원이다, identity.resolve_job_identity)은 있을 때만 —
            없으면 행 생략(빈칸 소음 방지, 메모 관례) */}
        {b.owner_username && (<>
          <dt className="text-muted">실행 신원</dt>
          <dd>{b.owner_username}</dd>
        </>)}
      </dl>
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
  const tempKeys = ["atime", "mtime", "ctime"]
    .filter((k) => stats.time_histograms[k] !== undefined);
  const bars = toBars(stats.time_histograms[tempKey], "bytes");
  const cumTotal = bars.reduce((acc, b) => acc + b.value, 0);
  const sizeBars = toBars(stats.file_size_histogram, "count", sizeBucketLabel);
  const sizeTotal = sizeBars.reduce((acc, b) => acc + b.value, 0);
  return (<>
    <section className={PANEL_SECTION}>
      <h3 className={PANEL_TITLE}>데이터 온도(hot/cold)</h3>
      {/* 언제 찍힌 숫자인지 없이 보여주는 건 부정직이다(ScanPaths 관례 미러) */}
      <p className="text-muted text-xs mb-2">
        {typeof stats.generated_at_epoch === "number"
          ? `scan 리포트 생성: ${utcStamp(stats.generated_at_epoch)}`
          : "scan 리포트 생성 시각을 알 수 없습니다"}
      </p>
      <div className="flex gap-2 mb-2">
        {tempKeys.map((k) => (
          <Button key={k} type="button"
                  variant={tempKey === k ? "outline" : "ghost"}
                  onClick={() => setTempKey(k)}>{k}</Button>
        ))}
      </div>
      <BarChart data={bars}
                label={`데이터 온도(${tempKey}) 히스토그램`}
                formatValue={humanBytes}
                colorOf={tempColorOf(bars.length)}
                cumulative={{ format: humanBytes }}
                emptyText="집계된 버킷 없음" />
      {TEMP_CAPTIONS[tempKey] && (
        <p className="text-muted text-xs mt-1">{TEMP_CAPTIONS[tempKey]}</p>
      )}
      {/* 누적 캡션은 오버레이와 같은 조건(총합>0)으로만 -- 총합 0 이면 선이
          없는데 "선 = ..." 은 거짓 캡션이 된다. 총 용량 값(bars 합)도 여기서
          함께 말한다(선의 100% 가 몇 바이트인지). */}
      {cumTotal > 0 && (
        <p className="text-muted text-xs mt-1">
          {`선 = hot쪽부터의 누적 용량 비중 · 총 ${humanBytes(cumTotal)}`}
        </p>
      )}
    </section>
    <section className={PANEL_SECTION}>
      {/* 온도 차트와 같은 문법(값 라벨·누적 오버레이)을 쓰되 **색은 안 물려받는다**:
          크기는 hot/cold 축이 아니라 온도 그라디언트를 입히면 "작은 파일=hot"이라는
          거짓 의미가 생긴다. 크기 자체를 색으로 다시 말할 이유도 없다 — 양은 막대
          높이가, 순서는 x축이 이미 말한다. 그래서 단색 accent(BarChart 기본)다.
          누적은 용량이 아니라 **개수** 비중이라 format 도 개수 표기다. */}
      <h3 className={PANEL_TITLE}>파일 크기 분포(개수)</h3>
      <BarChart data={sizeBars} label="파일 크기 분포"
                cumulative={{ format: (n) => `${n}개` }}
                emptyText="집계된 버킷 없음" />
      {sizeBars.length > 0 && (
        <p className="text-muted text-xs mt-1">가로축 = 파일 크기 구간(K=KiB·M=MiB·G=GiB·T=TiB, 1024 단위)</p>
      )}
      {sizeTotal > 0 && (
        <p className="text-muted text-xs mt-1">
          {`선 = 작은 파일부터의 누적 개수 비중 · 총 ${sizeTotal}개`}
        </p>
      )}
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
// 제약) — 생성 위저드(BatchCreate)의 CSV 붙여넣기 패턴을 미러한다. 동선:
// 붙여넣기 → parseItemsCsv 파싱(입력에서 즉시 파생) → 미리보기(행 수·오류) →
// 「교체」 확인 클릭 → PUT → 팝업 닫힘. 오류가 하나라도 있으면 교체 버튼 잠금
// (부분 반영 금지 — BatchCreate applyParsed 와 같은 계약). 빈 입력(초기 상태)은
// 미리보기·버튼 자체를 안 그린다 — "아직 안 붙여넣음"과 "0행 파싱"(헤더만)의 구분.
// 배치 레벨 스토리지: 배치 행엔 스토리지 메타가 없다(실측 — batches 테이블은
// 실행 제어·옵션만). 종단 배치는 항목이 반드시 있으므로 첫 항목 payload 에서
// 물려받는다(항목 추가와 같은 동질성 계약) — 항목 0개면 호출측이 이 컴포넌트
// 자체를 안 그린다(물려받을 스토리지가 없다).
function ReplaceItemsCsvDialog({ batchId, operation, firstPayload }: {
  batchId: string; operation: "scan" | "sync";
  firstPayload: Record<string, unknown>;
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
  return (
    <Dialog open={open} onOpenChange={setOpen} title="CSV로 전체 교체"
            trigger={<Button variant="ghost">CSV로 전체 교체</Button>}>
      <div className="space-y-2">
        <p className="text-muted text-sm">기존 항목이 모두 사라지고 붙여넣은 CSV 로 대체됩니다.</p>
        <label className="text-sm block">
          {`행 형식 — ${operation === "scan" ? "행당 경로 1개" : "행당 source,destination"}`}
          <textarea aria-label="교체 CSV" className={`${field} h-40 font-mono`}
                    placeholder={operation === "scan"
                      ? "team\nprojects/alpha"
                      : "team/dataset,backup/dataset\nprojects/alpha,backup/alpha"}
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
        {replace.isError && (
          <p className="text-bad text-sm">{(replace.error as ApiError).message}</p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          {parsed !== null && (
            <Button disabled={errors.length > 0 || rows.length === 0 || replace.isPending}
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
function CurrentItemsCsvDialog({ operation, items }: {
  operation: "scan" | "sync"; items: BatchItem[];
}) {
  const [open, setOpen] = useState(false);
  const text = serializeItemsCsv(operation, items.map((it) => operation === "sync"
    ? { source: String(it.payload.source ?? ""),
        destination: String(it.payload.destination ?? "") }
    : { target: String(it.payload.target ?? "") }));
  return (
    <Dialog open={open} onOpenChange={setOpen} title="현재 항목 CSV"
            trigger={<Button variant="ghost">현재 항목 CSV</Button>}>
      <div className="space-y-2">
        {/* 클릭 시 전체 선택되는 건 화면에 안 쓰면 모르는 동작이라 한 줄로 말한다 */}
        <p className="text-muted text-sm">클릭하면 전체 선택됩니다 — 복사해서 「CSV로 전체 교체」에 그대로 붙여넣을 수 있습니다.</p>
        <textarea aria-label="현재 항목 CSV" readOnly value={text}
                  className={`${field} h-40 font-mono`}
                  onFocus={(e) => e.currentTarget.select()} />
        <div className="flex justify-end pt-1">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
        </div>
      </div>
    </Dialog>
  );
}

// 항목 추가(경로만 입력 — 스토리지는 첫 항목에서 물려받는다). 종단 배치에
// 추가하면 서버가 재활성화(scan→Running/sync→Previewing)하고 신규 Queued 항목만
// 실행된다. 성공 시 팝업을 닫는다: 추가된 항목은 뒤의 목록에 나타나므로 화면에
// 남아 있을 이유가 없다(invalidate 는 훅 onSettled 몫). 항목이 없으면 물려받을
// 스토리지가 없어 호출측이 버튼 대신 안내를 보인다.
function AddItemDialog({ batchId, isSync, firstPayload }: {
  batchId: string; isSync: boolean; firstPayload: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  const add = useAddBatchItem(batchId);
  const [path, setPath] = useState("");
  const [dst, setDst] = useState("");
  useEffect(() => { if (!open) { setPath(""); setDst(""); add.reset(); } }, [open]);
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
        </>) : (
          <label className="text-sm block">추가할 대상 경로
            <input aria-label="추가할 대상 경로" className={field} value={path}
                   onChange={(e) => setPath(e.target.value)} />
          </label>
        )}
        {add.isError && (
          <p className="text-bad text-sm">{(add.error as ApiError).message}</p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          <Button disabled={add.isPending}
                  onClick={() => add.mutate(itemBody(isSync, firstPayload, path, dst),
                    { onSuccess: () => setOpen(false) })}>항목 추가</Button>
        </div>
      </div>
    </Dialog>
  );
}

// 배치 삭제(종단 배치만 노출 — 진짜 가드는 서버 batch_not_deletable). 확인
// 다이얼로그 필수: 배치 행·항목 행이 사라지는 비가역 동작이다(자식 요청·잡은
// 감사 이력으로 보존 — repo.delete 주석). 성공 시 목록으로 이동 — 삭제된
// 배치의 상세는 404 라 머무를 곳이 아니다.
function DeleteBatchButton({ batchId }: { batchId: string }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteBatch(batchId);
  const navigate = useNavigate();
  // 닫힐 때마다 에러를 비운다(StoragesList DeleteButton 선례) — "취소"는
  // setOpen(false)를 직접 불러 Radix onOpenChange 가 발화하지 않는다.
  useEffect(() => { if (!open) del.reset(); }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="배치 삭제"
            trigger={<Button variant="ghost">배치 삭제</Button>}>
      <p className="text-sm text-muted mb-3">배치와 항목 목록이 삭제됩니다. 자식 요청·잡 이력은 남습니다.</p>
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

export function BatchDetail() {
  const { batchId = "" } = useParams();
  const q = useBatch(batchId);
  const confirm = useConfirmBatch(batchId);
  const rerun = useRerunFailed(batchId);
  const cancel = useCancelBatch(batchId);
  const rescan = useRescanBatch(batchId);
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
  const canEditItem = (it: BatchItem) => terminal || it.status === "Queued";
  const isSync = b?.operation === "sync";
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
  const startItemEdit = (it: BatchItem) => {
    setPathDraft(String((isSync ? it.payload.source : it.payload.target) ?? ""));
    setDstDraft(String(it.payload.destination ?? ""));
    setEditSeq(it.seq);
  };
  const saveItem = (it: BatchItem) => updateItem.mutate(
    { seq: it.seq, item: itemBody(isSync, it.payload, pathDraft, dstDraft) },
    { onSuccess: () => setEditSeq(null) });
  // 절대경로 조합용 스토리지 뿌리 맵. payload 에는 상대경로만 남으므로(서버 계약
  // 무변경) 화면이 **지금의** managed_root 로 조합한다 — 기록에 절대경로를 박아
  // 두면 스토리지 경로를 바꾼 뒤 없는 경로를 사실처럼 보인다. 관리자 응답에만
  // 뿌리가 실려 오므로 비관리자에겐 빈 맵 = 절대경로 표시 없음(거짓 경로 금지).
  const roots = useStorageRoots();
  const absOf = (it: BatchItem) => absSummary(b?.operation, it.payload, roots);
  const firstPayload = b?.items?.[0]?.payload;
  const hasItems = (b?.items ?? []).length > 0;
  // --- 항목 다중 선택 삭제(목록 화면 일괄 삭제의 항목판 — 같은 UX 언어) ---
  const bulkDelete = useDeleteBatchItems(batchId);
  // 선택 재실행: 같은 선택·같은 액션 바를 쓰는 두 번째 일괄 동작. 삭제와 달리
  // **종단 항목만** 대상이라(서버 부분 성공 모델) 대상이 하나도 없으면 잠근다.
  const bulkRerun = useRerunBatchItems(batchId);
  const [selected, setSelected] = useState<number[]>([]);
  // 2단 확인: 1단이 무장하고 2단이 쏜다(행 삭제 armedSeq 와 같은 계약). 일괄
  // 삭제는 비가역 폭이 넓어 오클릭 방어가 더 필요하다.
  // 불리언이 아니라 **어느 동작이 무장했는지**를 담는다: 바에 무장 버튼이 둘이
  // 되면서, 상태가 하나여야 "삭제 확인"과 "재실행 확인"이 동시에 뜨는 것이
  // 구조적으로 불가능해진다(행 삭제 armedSeq 가 단일 상태인 것과 같은 이유).
  const [bulkArmed, setBulkArmed] = useState<null | "delete" | "rerun">(null);
  // b?.items 를 그대로 dep 에 쓴다(`?? []` 를 dep 으로 쓰면 매 렌더 새 배열이라
  // 아래 정리 effect 가 무한 루프가 된다). react-query 의 구조적 공유 덕에 내용이
  // 같은 리페치는 같은 참조를 돌려준다.
  const deletableSeqs = useMemo(
    () => (b?.items ?? []).filter(canEditItem).map((it) => it.seq), [b?.items, terminal]);
  const liveSeqs = useMemo(
    () => new Set((b?.items ?? []).map((it) => it.seq)), [b?.items]);
  // 유령 선택 정리: 리페치로 사라진 항목(단건 삭제·다른 세션의 교체)을 선택에서
  // 뺀다 — 안 그러면 화면에 없는 seq 에 DELETE 를 쏘고 404 를 "실패"로 보고한다.
  // 부분집합이라 길이 비교로 동일성을 판정하고, 같으면 같은 참조를 돌려 재렌더
  // 루프를 끊는다.
  useEffect(() => {
    setSelected((prev) => {
      const next = prev.filter((s) => liveSeqs.has(s));
      return next.length === prev.length ? prev : next;
    });
  }, [liveSeqs]);
  const allChecked = deletableSeqs.length > 0 && selected.length === deletableSeqs.length;
  // indeterminate 는 속성이 아니라 DOM 프로퍼티라 ref 로만 설정된다.
  const allRef = useRef<HTMLInputElement>(null);
  const someChecked = selected.length > 0 && !allChecked;
  useEffect(() => { if (allRef.current) allRef.current.indeterminate = someChecked; },
            [someChecked]);
  // 선택이 바뀌면 무장을 풀고 직전 결과 문구도 지운다 — "2개 삭제 확인"이 무장된
  // 채 선택만 3개로 늘면 사용자가 확인한 것과 다른 것을 지우게 된다. 진행 중에는
  // reset 하지 않는다(비행 중인 mutation 의 결과를 삼킨다).
  const disarm = () => {
    setBulkArmed(null);
    if (!bulkDelete.isPending) bulkDelete.reset();
    if (!bulkRerun.isPending) bulkRerun.reset();
  };
  const toggleRow = (seq: number) => {
    disarm();
    setSelected((p) => (p.includes(seq) ? p.filter((x) => x !== seq) : [...p, seq]));
  };
  const clearSelection = () => { setBulkArmed(null); setSelected([]); };
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
    () => (b?.items ?? []).filter((it) => selected.includes(it.seq)
                                          && RERUNNABLE_ITEM.has(it.status)).length,
    [b?.items, selected]);
  return (
    <section className="space-y-4">
      {/* 이름이 있으면 이름이 헤더 — 축약 batch_id 는 식별자로 병기한다(사라지면
          운영자가 로그·API 와 대조할 열쇠를 잃는다). 없으면 기존 헤더 유지. */}
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">{b?.name ?? `배치 ${batchId.slice(0,12)}`}</h1>
        {b?.name && <span className="text-muted text-sm font-mono">{batchId.slice(0,12)}</span>}
      </div>
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {/* 배치 상태 전용 색(batchPillVariant — Completed=초록·진행=busy·
                Cancelled=neutral). 항목 pill 은 요청/잡 판정 축이라 기존 공유
                pillVariant 를 그대로 쓴다(variant 미지정). */}
            <StatusPill state={b?.status ?? "…"}
                        variant={b ? batchPillVariant(b.status) : undefined} />
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
          </div>
          <div className="flex gap-2">
            {/* 라벨은 짧게 「편집」 — 무엇을 편집하는지는 폼 안 입력 라벨("배치
                이름"/"메모")이 말한다(사용자 조정 2026-08-15). "메모 편집"은 폼이
                이름까지 고치는데 메모만 말해 어색했고 "이름·메모 편집"은 길다.
                PATCH {name, note} 계약·폼 구조는 무변경. */}
            {b && !editing && <Button variant="ghost" onClick={startEdit}>편집</Button>}
            {b?.status === "PreviewReady" && <Button disabled={confirm.isPending} onClick={() => confirm.mutate()}>배치 확인</Button>}
            {b?.status === "Completed" && (b?.failed_count ?? 0) > 0 && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>실패분 재실행</Button>}
            {/* 전체 재실행(:rescan): 종단 배치 한정(서버 가드 미러) — 성공 item 포함
                전부 재큐잉(성장 모니터링). "실패분 재실행"(실패만)과 공존한다 */}
            {(b?.status === "Completed" || b?.status === "Cancelled") && <Button disabled={rescan.isPending} onClick={() => rescan.mutate()}>전체 재실행</Button>}
            {(b?.status === "Completed" || b?.status === "Cancelled") && <DeleteBatchButton batchId={batchId} />}
            {(b?.status === "Running" || b?.status === "Previewing" || b?.status === "PreviewReady") && <Button variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>취소</Button>}
          </div>
        </div>
        {/* 메모는 편집 밖에서도 보인다 — 없으면 행 자체 생략(빈칸 소음 방지).
            라벨("메모 ")은 붙이지 않는다(사용자 조정 2026-08-15): 헤더 카드에서
            이름 아래 한 줄은 문맥상 메모임이 자명한데, 접두어가 매번 내용 앞을
            가로막았다. 행이 아예 없으면 메모 없음이라는 사실도 그대로 읽힌다. */}
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
            {/* 전체 선택은 **선택 가능한 항목만** 토글한다 — 잠긴 항목까지 켜 두면
                지울 수 없는 것을 골라 둔 셈이 되어 매번 409 를 만든다. */}
            {hasItems && (
              <input ref={allRef} type="checkbox" aria-label="전체 선택"
                     className="h-5 w-5 shrink-0"
                     checked={allChecked}
                     disabled={deletableSeqs.length === 0 || bulkBusy}
                     onChange={() => { disarm();
                                       setSelected(allChecked ? [] : deletableSeqs); }} />
            )}
            <h2 className="font-medium">항목</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {/* 노출 조건은 인라인 시절 그대로: CSV 계열·추가는 항목 존재(스토리지
                상속원), 교체는 종단 배치 한정. */}
            {b && hasItems && (
              <CurrentItemsCsvDialog operation={isSync ? "sync" : "scan"}
                                     items={b.items ?? []} />
            )}
            {b && firstPayload && (
              <AddItemDialog batchId={batchId} isSync={isSync}
                             firstPayload={firstPayload} />
            )}
            {b && terminal && firstPayload && (
              <ReplaceItemsCsvDialog batchId={batchId}
                                     operation={isSync ? "sync" : "scan"}
                                     firstPayload={firstPayload} />
            )}
          </div>
        </div>
        {/* 액션 바 자리 예약: 선택 여부와 무관하게 같은 높이의 컨테이너를 늘 그리고
            내용만 바꾼다 — 선택 순간에 바가 생기면 아래 목록이 통째로 밀려(레이아웃
            점프) 방금 체크한 행이 커서 밑에서 사라진다. */}
        {hasItems && (
          <div className="mt-2 flex min-h-10 flex-wrap items-center gap-2 text-sm">
            {selected.length > 0 ? (<>
              <span className="mr-auto">{`${selected.length}개 선택됨`}</span>
              {bulkArmed === "delete"
                ? <Button disabled={bulkBusy}
                          onClick={() => bulkDelete.mutate(selected,
                            { onSettled: clearSelection })}>
                    {`${selected.length}개 삭제 확인`}
                  </Button>
                : <Button variant="outline" disabled={bulkBusy}
                          onClick={() => setBulkArmed("delete")}>선택 삭제</Button>}
              {/* 재실행도 삭제와 같은 2단 확인. 확인 라벨의 N 은 재실행 가능 수가
                  아니라 **선택 수**다 — 실제로 보내는 것이 선택 전부라 그 숫자가
                  사용자가 확인하는 대상과 일치한다(제외분은 결과가 말한다). */}
              {bulkArmed === "rerun"
                ? <Button disabled={bulkBusy}
                          onClick={() => bulkRerun.mutate(selected,
                            { onSettled: clearSelection })}>
                    {`${selected.length}개 재실행 확인`}
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
        {(b?.items ?? []).map((it) => (
          <div key={it.seq} className="border-t border-black/5 py-2">
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
                     checked={selected.includes(it.seq)}
                     disabled={!canEditItem(it) || bulkBusy}
                     title={canEditItem(it) ? undefined : ITEM_LOCK_HINT}
                     onChange={() => toggleRow(it.seq)} />
              <button type="button" aria-label={`항목 ${it.seq} 상세`}
                      aria-expanded={openSeq === it.seq}
                      onClick={() => setOpenSeq(openSeq === it.seq ? null : it.seq)}
                      className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <span className="w-8 shrink-0 text-muted text-xs tabular-nums">{it.seq}</span>
                {/* 항목 제목은 행의 주인공이라 주변 본문(text-sm)·체크박스(h-5)와
                    같은 단이다 — text-xs 로는 곁다리 메타처럼 작아 보였다(사용자
                    지적). 경로·스토리지라 font-mono 는 유지(구분자·유사문자를
                    또렷하게), 크기만 한 단 올린다. */}
                {/* 절대경로는 title 로만 — 본문에 이어 붙이면 한 줄이 두 배로
                    길어져 truncate 가 정작 대상 경로를 먹는다(좁은 자리 규칙). */}
                <span className="min-w-0 flex-1 truncate font-mono text-sm"
                      title={absOf(it) ?? undefined}>
                  {summarizeItem(b?.operation, it.payload)}
                </span>
                <StatusPill state={it.status} />
              </button>
              {/* 수정도 행 버튼이다(삭제 왼쪽 — 사용자 지시 2026-08-15): 펼침
                  안에 있으면 한 건 고치기가 펼침(1)→수정(2) 이 되는데, 무엇을
                  고치는지는 행 요약(순번·대상)이 이미 말한다. 편집 폼은 행 바로
                  아래에 열려(펼침과 무관) 대상과 입력이 붙어 있다. 행별 aria-label
                  은 삭제 버튼 관례 미러 — 행이 여럿이라 무접두 "수정"은 모호하다.
                  노출 조건은 canEditItem 재사용(표시 게이트, 진짜 차단은 서버 409). */}
              {canEditItem(it) && editSeq !== it.seq && (
                <Button variant="ghost" aria-label={`항목 ${it.seq} 수정`}
                        onClick={() => startItemEdit(it)}>수정</Button>
              )}
              {/* 삭제는 펼치지 않아도 보이는 행 버튼(펼침 안에서 행으로 이동).
                  노출 조건은 기존 canEditItem 재사용(표시 게이트 — 진짜 차단은
                  서버 409). aria-label 로 행별 이름을 부여해 테스트·스크린리더가
                  어느 항목인지 안다.
                  다중 선택이 생긴 뒤에도 이 버튼을 남기는 이유: 한 건 지우기가
                  체크(1)→선택 삭제(2)→N개 삭제 확인(3) 세 클릭이 되는데, 행
                  버튼은 삭제(1)→삭제 확인(2) 두 클릭이다. 가장 흔한 조작이
                  느려지는 건 정리가 아니라 퇴보라 중복을 감수한다. */}
              {canEditItem(it) && (armedSeq === it.seq
                ? <Button aria-label={`항목 ${it.seq} 삭제 확인`}
                          disabled={deleteItem.isPending}
                          onClick={() => deleteItem.mutate(it.seq,
                            { onSettled: () => setArmedSeq(null) })}>삭제 확인</Button>
                : <Button variant="ghost" aria-label={`항목 ${it.seq} 삭제`}
                          onClick={() => setArmedSeq(it.seq)}>삭제</Button>)}
            </div>
            {/* 삭제 실패 사유는 시도한 행 밑에만 — variables(마지막 mutate 의
                seq)로 행을 특정한다(전 행에 도배하면 어느 삭제가 실패했는지
                모른다). */}
            {deleteItem.isError && deleteItem.variables === it.seq && (
              <p className={`text-bad text-sm mt-1 ${ITEM_INDENT}`}>{(deleteItem.error as ApiError).message}</p>
            )}
            {/* 편집 폼은 펼침 **밖**이다 — 수정 버튼이 행으로 올라갔으니 폼도
                행 바로 아래에 열린다(펼치지 않은 행에서 눌러도 보인다). 펼친
                행에서 눌러도 같은 자리(행 밑, 상세 위)라 대상과 입력이 붙는다. */}
            {editSeq === it.seq && (
              <div className={`mt-2 ${ITEM_INDENT} space-y-2 max-w-md`}>
                {isSync ? (<>
                  <label className="text-sm block">소스 경로
                    <input aria-label="소스 경로" className={field} value={pathDraft}
                           onChange={(e) => setPathDraft(e.target.value)} />
                  </label>
                  <label className="text-sm block">목적지 경로
                    <input aria-label="목적지 경로" className={field} value={dstDraft}
                           onChange={(e) => setDstDraft(e.target.value)} />
                  </label>
                </>) : (
                  <label className="text-sm block">대상 경로
                    <input aria-label="대상 경로" className={field} value={pathDraft}
                           onChange={(e) => setPathDraft(e.target.value)} />
                  </label>
                )}
                <div className="flex gap-2">
                  <Button disabled={updateItem.isPending} onClick={() => saveItem(it)}>항목 저장</Button>
                  {/* 배치 취소("취소")·이름 편집("편집 취소")과 라벨이 겹치지 않게 */}
                  <Button variant="ghost" onClick={() => setEditSeq(null)}>항목 편집 취소</Button>
                </div>
                {updateItem.isError && (
                  <p className="text-bad text-sm">{(updateItem.error as ApiError).message}</p>
                )}
              </div>
            )}
            {/* 펼침 패널: 들여쓰기는 컨테이너 한 곳에서만 준다(예전엔 섹션마다
                ITEM_INDENT 를 되풀이했다) — 섹션이 늘어도 정렬이 한 곳에 남는다. */}
            {openSeq === it.seq && (
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
                    <dt className="text-muted">파일 수</dt>
                    <dd className="tabular-nums">{it.files_count ?? "—"}</dd>
                    <dt className="text-muted">완료 시각</dt>
                    <dd className="text-muted">{it.completed_at ?? "—"}</dd>
                    <dt className="text-muted">payload</dt>
                    <dd className="font-mono text-xs break-all">{JSON.stringify(it.payload)}</dd>
                    {/* payload 의 상대경로를 지금의 managed_root 로 해석한 결과.
                        뿌리를 모르면 줄 자체가 없다(거짓 경로 금지 — absOf 주석). */}
                    {absOf(it) !== null && (<>
                      <dt className="text-muted">절대경로</dt>
                      <dd className="font-mono text-xs break-all">{absOf(it)}</dd>
                    </>)}
                    <dt className="text-muted">요청</dt>
                    <dd>{it.request_id
                      ? <Link className="text-accent" to={`/jobs/${it.request_id}`}>요청 상세</Link>
                      : "—"}</dd>
                  </dl>
                </section>
                {/* 항목별 데이터 온도: scan 배치만 — sync 항목엔 dscan 리포트가
                    존재할 수 없어 섹션 자체가 거짓 약속이 된다. */}
                {b?.operation === "scan" && (
                  <ItemScanStats requestId={it.request_id}
                                 succeeded={it.request_state === "Succeeded"} />
                )}
              </div>
            )}
          </div>
        ))}
        {/* 항목 0개: 도구 버튼(추가·CSV)이 전부 사라진 자리에 왜 없는지를 남긴다 —
            물려받을 스토리지가 없어 항목 추가 자체가 불가능하다(빈 배치는 삭제·
            재생성이 동선). */}
        {b && !hasItems && (
          <p className="text-muted text-sm mt-2">항목이 없어 스토리지를 물려받을 수 없습니다 — 항목 추가는 새 배치로 하세요</p>
        )}
      </Card>
    </section>
  );
}
