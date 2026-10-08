import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowDownToLine, CircleAlert, CircleDashed, Download, File, FileCode, FileDown, FileJson, FileText,
  History, Info, ScrollText, Server, WrapText, X, type LucideIcon,
} from "lucide-react";
import { useArtifactFile, useJobLogs } from "./useArtifacts";
import { ApiError } from "../../lib/api";
import { downloadText } from "../../lib/csvExport";
import { Skeleton } from "../../components/ui/Skeleton";
import type { JobLogs } from "../../lib/types";
import type { OutputItem } from "./jobStages";
import { humanBytes } from "./format";
import { BADGE, FOCUS_RING, ICON_BTN, SMALL_BTN } from "./ui";

// 단계 행 아래에 열리는 출력 뷰어(로그 1개 또는 아티팩트 파일 1개). 옛 JobViewer 본문을 대체한다.
//
// 지키는 계약(옛 JobViewer.test 를 JobStages.test 로 그대로 옮겼다):
//   - 본문은 이 뷰어가 열릴 때만 조회한다(칩 클릭 전 조회 0건). 선택한 phase 만 조회한다.
//   - log === null(얻을 수 없음)과 ""(빈 로그, launcher 의 정상값)를 truthy 검사로 뭉개지 않는다.
//   - 박제 사본(archived)은 "지금 파드에서 읽은 것" 이 아니라고 말한다(캡션) -- 라이브로 오인하지 않게.
//   - 다운로드 크기는 목록 entries 의 실측값(본문 응답의 size 아님), 모르면 "—", 0 은 "0 B".
// clipboard API 는 쓰지 않는다(http 비보안 컨텍스트) -- 대신 「로그 저장」(Blob)과 줄 선택 복사.

export const LINE_CAP = 2000;   // 서버 상한(아티팩트 256KB 꼬리·박제 로그 파드당 16KB)을 넘는 렌더 비용 방어

function iconFor(item: OutputItem): LucideIcon {
  if (item.kind === "log") return ScrollText;
  const n = item.name;
  if (n.endsWith(".log")) return FileText;
  if (n.endsWith(".json")) return FileJson;
  if (n.endsWith(".sh")) return FileCode;
  if (n === "mpi-hostfile") return Server;
  return File;
}
export { iconFor as outputIcon };

// 줄바꿈 기본값은 켬(좁은 화면에서 가로 스크롤 대신 접힌다). 사람마다 기억한다 -- 저장소 접근이 막힌 브라우저
// (사생활 보호 모드·차단된 사이트 데이터)에서도 화면은 기본값으로 떠야 해서 읽기·쓰기 모두 try/catch.
const WRAP_KEY = "dms.logWrap";
function readWrap(): boolean {
  try {
    const v = localStorage.getItem(WRAP_KEY);
    return v === null ? true : v === "1";
  } catch {
    return true;
  }
}
function writeWrap(v: boolean) {
  try { localStorage.setItem(WRAP_KEY, v ? "1" : "0"); } catch { /* 기본값으로 계속 */ }
}

export function splitLines(text: string): string[] {
  if (text === "") return [];
  const lines = text.split(/\r?\n/);
  if (lines[lines.length - 1] === "") lines.pop();   // 끝 줄바꿈이 만든 빈 줄은 줄이 아니다
  return lines;
}

// 바닥 붙기: 바닥 24px 안에 있으면 새 내용이 와도 바닥을 유지하고, 위로 올려 읽는 중이면 끌어내리지 않는다.
// 로그·*.log 는 꼬리에 오류가 있어 처음부터 바닥에서 연다. 나머지(json·sh·hostfile)는 맨 위에서 시작한다.
function useTailScroll(tail: boolean, content: unknown) {
  const ref = useRef<HTMLElement | null>(null);
  const atBottom = useRef(tail);
  const [away, setAway] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && tail && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [content, tail]);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    atBottom.current = near;
    setAway(!near);
  };
  const toBottom = () => {
    const el = ref.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    atBottom.current = true;
    setAway(false);
  };
  return { ref, onScroll, away, toBottom };
}

function ToBottomButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
            className={`absolute bottom-2 right-3 inline-flex items-center gap-1 rounded-full border border-line bg-surface/95 px-2.5 py-1 text-xs text-ink shadow-soft hover:bg-panel ${FOCUS_RING}`}>
      <ArrowDownToLine className="h-3.5 w-3.5" aria-hidden />맨 아래로
    </button>
  );
}

// 본문: 한 줄 = 블록 하나(복사해도 줄바꿈이 남는다), 줄번호는 CSS counter(pseudo-element 라 복사에 섞이지 않고 DOM
// 이 가볍다). 줄이 하나면 텍스트 노드 하나라 text 쿼리가 그대로 잡힌다. role="log" 는 쓰지 않는다 -- 암묵적 live
// region 이라 폴링할 때마다 화면 낭독기가 읽는다.
export function CodeBlock({ text, label, wrap, bounded, startHidden = 0, preRef, onScroll }: {
  text: string; label: string; wrap: boolean; bounded: boolean; startHidden?: number;
  preRef?: (el: HTMLPreElement | null) => void; onScroll?: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const lines = splitLines(text);
  const hidden = showAll ? 0 : Math.max(0, lines.length - LINE_CAP);
  const shown = hidden > 0 ? lines.slice(hidden) : lines;
  const first = startHidden + hidden;
  return (
    <>
      {hidden > 0 && (
        <div className="flex flex-wrap items-center gap-x-1 px-3 py-1 text-xs text-ink/70">
          {`앞 ${hidden.toLocaleString("ko-KR")}줄 숨김 · `}
          <button type="button" className={`text-accent underline ${FOCUS_RING}`} onClick={() => setShowAll(true)}>모두 표시</button>
        </div>
      )}
      <pre lang="en" tabIndex={0} aria-label={`${label} 본문`} ref={preRef} onScroll={onScroll}
           style={{ counterReset: `line ${first}` }}
           className={`${bounded ? "max-h-[60vh] overflow-auto sm:max-h-[28rem]" : "overflow-x-auto"} py-2 font-mono text-xs leading-5 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent`}>
        {shown.map((l, i) => (
          <span key={i}
                className={`flex [counter-increment:line] before:w-12 before:shrink-0 before:select-none before:pr-3 before:text-right before:tabular-nums before:text-ink/40 before:content-[counter(line)] ${wrap ? "" : "w-max min-w-full"}`}>
            <span className={wrap ? "min-w-0 flex-1 whitespace-pre-wrap pr-3 [overflow-wrap:anywhere]" : "whitespace-pre pr-3"}>
              {l === "" ? " " : l}
            </span>
          </span>
        ))}
      </pre>
    </>
  );
}

function LoadingBody() {
  return (
    <div className="space-y-2 px-3 py-3">
      <Skeleton className="h-3 w-11/12" />
      <Skeleton className="h-3 w-3/4" />
      <Skeleton className="h-3 w-5/6" />
      <Skeleton className="h-3 w-2/3" />
      <p className="sr-only" role="status">내용을 불러오는 중…</p>
    </div>
  );
}

function ErrorBody({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  // 409(log_not_available 등)는 "이 단계엔 그런 출력이 없다" 는 안내지 고장이 아니다 -- 정보 톤으로 말한다.
  const info = error instanceof ApiError && error.status === 409;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex flex-wrap items-start gap-2 px-3 py-3 text-sm">
      {info ? <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink/70" aria-hidden />
        : <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-bad" aria-hidden />}
      <p className={`min-w-0 flex-1 break-keep ${info ? "text-ink/70" : "text-bad"}`}>{msg}</p>
      <button type="button" className={SMALL_BTN} onClick={onRetry}>다시 시도</button>
    </div>
  );
}

type LogEntry = JobLogs["entries"][number];

function PodHeader({ entry, sticky }: { entry: LogEntry; sticky: boolean }) {
  return (
    <div className={`${sticky ? "sticky top-0 z-10" : ""} flex flex-wrap items-center gap-2 border-b border-line bg-panel/95 px-3 py-1 text-xs`}>
      <span className="font-mono font-medium [overflow-wrap:anywhere]">{entry.pod}</span>
      {entry.pod.includes("-launcher-") && <span className={BADGE}>launcher</span>}
      {entry.pod.startsWith("submit:") && <span className={BADGE}>제출 실패 원문</span>}
      {entry.truncated === true && <span className={BADGE}>뒷부분만 표시</span>}
    </div>
  );
}

function PodBody({ entry, label, wrap, bounded, preRef, onScroll }: {
  entry: LogEntry; label: string; wrap: boolean; bounded: boolean;
  preRef?: (el: HTMLPreElement | null) => void; onScroll?: () => void;
}) {
  // log === null 비교만 쓴다: ""(빈 로그)는 정상값이라 truthy 검사로 묶으면 "비어 있음" 이 "로그 없음" 으로 둔갑한다.
  // waiting_reason 은 null 을 대체하지 않고 "왜 없는지" 를 병기할 뿐이다(별 채널).
  if (entry.log === null) {
    return (
      <div className="flex items-start gap-2 px-3 py-2">
        <CircleDashed className="mt-0.5 h-4 w-4 shrink-0 text-ink/60" aria-hidden />
        <p className="text-sm text-ink/70">
          {entry.waiting_reason ? `파드 로그 없음 — ${entry.waiting_reason}` : "파드 로그를 더 이상 조회할 수 없습니다"}
        </p>
      </div>
    );
  }
  return (
    <>
      <CodeBlock text={entry.log} label={`${label} ${entry.pod}`} wrap={wrap} bounded={bounded}
                 preRef={preRef} onScroll={onScroll} />
      {entry.log === "" && (
        <p className="px-3 pb-2 text-xs text-ink/70">빈 로그 — 이 파드는 아무것도 출력하지 않았습니다</p>
      )}
    </>
  );
}

function LogBody({ data, label, wrap, live }: { data: JobLogs; label: string; wrap: boolean; live: boolean }) {
  const entries = Array.isArray(data.entries) ? data.entries : [];
  const tail = useTailScroll(true, data);
  if (entries.length === 0) {
    return <p className="px-3 py-3 text-sm text-ink/70">이 단계에는 표시할 파드 로그가 없습니다</p>;
  }
  // 파드 1개 = 머리 + 자기 스크롤 본문. 여러 개 = 바깥 스크롤 영역 하나에 섹션을 쌓고 머리를 sticky 로(파드 탭을
  // 쓰지 않는다 -- 브라우저 Ctrl+F 가 모든 파드를 한 번에 찾는다). 서버 순서 그대로라 launcher 가 앞에 온다.
  if (entries.length === 1) {
    const e = entries[0];
    return (
      <div className="relative">
        <PodHeader entry={e} sticky={false} />
        <PodBody entry={e} label={label} wrap={wrap} bounded
                 preRef={(el) => { tail.ref.current = el; }} onScroll={tail.onScroll} />
        {live && tail.away && <ToBottomButton onClick={tail.toBottom} />}
      </div>
    );
  }
  return (
    <div className="relative">
      <div ref={(el) => { tail.ref.current = el; }} onScroll={tail.onScroll} tabIndex={0}
           role="group" aria-label={`${label} 파드 ${entries.length}개`}
           className="max-h-[60vh] overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent sm:max-h-[28rem]">
        {entries.map((e, i) => (
          <div key={`${e.pod}-${i}`} data-pod={e.pod}>
            <PodHeader entry={e} sticky />
            <PodBody entry={e} label={label} wrap={wrap} bounded={false} />
          </div>
        ))}
      </div>
      {live && tail.away && <ToBottomButton onClick={tail.toBottom} />}
    </div>
  );
}

function prettyJson(name: string, content: string): string {
  if (!name.endsWith(".json")) return content;
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;   // 256KB 꼬리로 잘려 파싱이 안 되면 원문 그대로(다운로드는 늘 원문)
  }
}

function ArtifactBody({ name, content, label, wrap }: { name: string; content: string; label: string; wrap: boolean }) {
  const text = prettyJson(name, content);
  const tail = useTailScroll(name.endsWith(".log"), text);
  return (
    <>
      <CodeBlock text={text} label={label} wrap={wrap} bounded
                 preRef={(el) => { tail.ref.current = el; }} onScroll={tail.onScroll} />
      {content === "" && <p className="px-3 pb-2 text-xs text-ink/70">빈 파일입니다 (0 B)</p>}
    </>
  );
}

// 「로그 저장」 본문: 파드마다 머리줄을 붙여 이어 붙인다(받은 화면 그대로 -- 서버엔 로그 다운로드 라우트가 없다).
export function logSaveText(data: JobLogs): string {
  const entries = Array.isArray(data.entries) ? data.entries : [];
  return entries.map((e) => {
    const body = e.log === null ? `(로그 없음 — ${e.waiting_reason ?? "조회 불가"})` : e.log;
    return `===== ${e.pod} =====\n${body}${body.endsWith("\n") || body === "" ? "" : "\n"}`;
  }).join("");
}

export function OutputViewer({ jobId, item, entrySize, live, viewerId, onClose }: {
  jobId: string; item: OutputItem; entrySize: number | null; live: boolean; viewerId: string;
  onClose: () => void;
}) {
  const [wrap, setWrapState] = useState(readWrap);
  const setWrap = (v: boolean) => { setWrapState(v); writeWrap(v); };
  const isLog = item.kind === "log";
  const logs = useJobLogs(jobId, isLog ? item.phase : "", isLog, { live: isLog && live });
  const file = useArtifactFile(jobId, item.phase, item.kind === "artifact" ? item.name : "", item.kind === "artifact");
  const q = isLog ? logs : file;
  const title = isLog ? `${item.phase} 로그` : `${item.phase}/${item.name}`;
  const Icon = iconFor(item);
  const archived = isLog && logs.data?.source === "archived";
  const truncatedFile = item.kind === "artifact" && file.data?.truncated === true;

  let body: ReactNode = null;
  if (q.isLoading) body = <LoadingBody />;
  else if (q.isError) body = <ErrorBody error={q.error} onRetry={() => { void q.refetch(); }} />;
  else if (isLog && logs.data) body = <LogBody data={logs.data} label={title} wrap={wrap} live={live} />;
  else if (item.kind === "artifact" && file.data) {
    body = <ArtifactBody name={item.name} content={typeof file.data.content === "string" ? file.data.content : ""}
                         label={title} wrap={wrap} />;
  }

  return (
    // relative + bg-surface: 단계 행의 세로 레일(li::before, absolute)이 뷰어 위로 비쳐 줄번호 옆을 가로지르지 않게
    // 덮는다(이 뷰어는 job_id 의 조상이 아니라 bg-surface 를 써도 카드 탐색 계약과 무관하다).
    <div id={viewerId} role="region" aria-label={`${title} 내용`} tabIndex={-1} aria-busy={q.isLoading}
         className="relative col-span-2 mt-2 min-w-0 scroll-mt-4 overflow-hidden rounded-lg border border-line bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-line bg-panel/60 px-3 py-2">
        <Icon className="h-4 w-4 shrink-0 text-ink/70" aria-hidden />
        <span className="min-w-0 font-mono text-xs text-ink [overflow-wrap:anywhere]">{title}</span>
        {archived && <span className={BADGE}><History className="h-3 w-3" aria-hidden />저장된 사본</span>}
        {live && isLog && (
          <span className={BADGE}>
            <span className="h-1.5 w-1.5 rounded-full bg-busy motion-safe:animate-pulse" aria-hidden />실시간
          </span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1">
          <button type="button" className={ICON_BTN} aria-pressed={wrap} aria-label="줄바꿈" title="줄바꿈"
                  onClick={() => setWrap(!wrap)}>
            <WrapText className="h-4 w-4" aria-hidden />
          </button>
          {item.kind === "artifact" ? (
            // a href 네비게이션에도 세션 쿠키가 실리므로 fetch+blob 우회가 필요 없다(그쪽은 상한 크기까지 메모리
            // 버퍼링이라 더 나쁘다). 뷰 라우트(256KB 꼬리 JSON)가 아니라 /download 스트림 라우트다.
            <a href={`/api/user/jobs/${jobId}/artifacts/${encodeURIComponent(item.phase)}/${encodeURIComponent(item.name)}/download`}
               download
               className={`inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs text-accent hover:bg-infobg sm:h-7 ${FOCUS_RING}`}>
              <Download className="h-3.5 w-3.5" aria-hidden />다운로드 ({humanBytes(entrySize)})
            </a>
          ) : (
            <button type="button" className={SMALL_BTN} disabled={!logs.data}
                    onClick={() => { if (logs.data) downloadText(`${jobId.slice(0, 8)}-${item.phase}.log`, logSaveText(logs.data)); }}>
              <FileDown className="h-3.5 w-3.5" aria-hidden />로그 저장
            </button>
          )}
          <button type="button" className={ICON_BTN} aria-label="출력 닫기" title="출력 닫기" onClick={onClose}>
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
      </div>
      {archived && (
        // 박제 사본은 "지금 파드에서 읽은 것" 이 아니다 -- 어느 시점의 무엇인지 말해 주지 않으면 라이브로 오인한다.
        <div className="border-b border-line px-3 py-1.5 text-xs text-ink/70">잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB</div>
      )}
      {truncatedFile && (
        // 256KB 꼬리와 전체 파일의 관계를 화면이 말해야 한다 -- 배지만으로는 "전체를 얻을 수단이 있다" 가 안 전해진다.
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-1.5 text-xs text-ink/70">
          <span className={BADGE}>뒷부분만 표시</span>
          <span>전체는 다운로드로 받으세요</span>
        </div>
      )}
      {body}
    </div>
  );
}
