import { useId, useState } from "react";
import type { ChangeEventHandler, ReactNode } from "react";
import { Check, RefreshCw, ScanSearch, Trash2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";

// 제출 위저드(components/form/Wizard)의 단계 본문 부품(2026-11-26 단계별 등록). 단일 작업·배치 생성이 같이 쓴다. 값은 들지 않는다 --
// 모두 props(값·onChange)이고, 지역 상태는 「자세히」 펼침 같은 **표시 상태**뿐이다(단계에 다시 들어오면 접힘 -- 남길 값이 아니다).
// 모든 버튼은 type="button"(단일 화면은 <form> 안 -- Wizard.tsx 머리 주석). 접근 이름·역할은 옛 컨트롤과 같게 둔다(체크박스는 그대로
// checkbox, 라디오 이름은 연산 이름) -- 겉모양만 카드다.

/**
 * 「한 줄 요약 + 자세히」. <p><span>{summary}</span> <button aria-expanded aria-controls>자세히/접기</button></p> + 전문.
 * - 전문은 <p> 로 그린다(FieldRow 「설명은 <p>」 규약 -- 캡션을 tagName P 로 고정한 시험이 있다). 배열이면 문단마다 <p> 를 든 <div>.
 *   접혀도 전문은 DOM 에 남는다(hidden 속성) -- getByText 로는 찾히므로 「보인다」 단언은 toBeVisible 로 쓴다.
 * - summary 는 늘 보인다(「요약은 늘 보이고 켜면 전문」). 따로 <span> 이라 getByText(요약) 정확 일치가 버튼 글자에 섞이지 않는다.
 * - forceOpen(켠 옵션)이면 전문을 펼친 채 고정하고 버튼을 숨긴다.
 * - full 이 없으면 요약 한 줄만(버튼 없음).
 */
export function MoreText({ summary, full, forceOpen = false, className = "" }: {
  summary: ReactNode;
  full?: ReactNode;          // 문단 하나(문자열·인라인 요소) 또는 문단 배열
  forceOpen?: boolean;
  className?: string;
}) {
  const id = `${useId()}-more`;
  const [open, setOpen] = useState(false);
  const hasFull = full !== undefined && full !== null && full !== false && full !== "";
  const shown = hasFull && (forceOpen || open);
  const fullClass = `mt-1.5 rounded-r-lg border-l-2 bg-panel/70 px-3 py-2 text-xs leading-relaxed text-ink
                     ${forceOpen ? "border-accent" : "border-line"}`;
  return (
    <div className={`min-w-0 ${className}`}>
      <p className="text-xs text-muted">
        <span>{summary}</span>
        {hasFull && !forceOpen && <>{" "}
          <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}
                  className="whitespace-nowrap font-semibold text-accent underline underline-offset-2 hover:text-accenthover">
            {open ? "접기" : "자세히"}
          </button>
        </>}
      </p>
      {hasFull && (Array.isArray(full) ? (
        <div id={id} hidden={!shown} className={`${fullClass} space-y-1.5`}>
          {full.map((para, i) => <p key={i}>{para}</p>)}
        </div>
      ) : (
        <p id={id} hidden={!shown} className={fullClass}>{full}</p>
      ))}
    </div>
  );
}

// 토글 모양 체크박스(겉모양만 스위치 -- 역할은 checkbox 그대로: role="switch" 로 바꾸면 보조기기 낭독이 달라진다).
const SWITCH = "relative h-5 w-9 shrink-0 cursor-pointer appearance-none rounded-full bg-muted/40 transition-colors "
  + "before:absolute before:left-0.5 before:top-0.5 before:h-4 before:w-4 before:rounded-full before:bg-surface before:shadow-soft "
  + "before:transition-transform before:content-[''] checked:before:translate-x-4 "
  + "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 "
  + "disabled:cursor-not-allowed disabled:opacity-50";

/**
 * 옵션 토글 카드: <label><input type="checkbox" aria-label={label}>제목</label> + MoreText(켜면 전문을 강제로 펼친다 -- 목업
 * expandOnOn; d192 「direct·quiet 설명은 툴팁이 아니라 칸 옆에」는 요약이 핵심 사실을 담고 전문은 자세히·켜면 보이는 것으로 지킨다)
 * + 오류 줄(있으면 체크박스에 aria-invalid -- blockedNudge 가 그리로 포커스한다). 체크박스의 역할·접근 이름(label)은 옛 칸과 같다.
 */
export function OptionToggle({
  label, title, checked, onChange, summary, full, error, disabled = false, danger = false, expandOnOn = true,
}: {
  label: string;                 // 체크박스 접근 이름(옛 칸과 같게 -- "delete"·"재귀 삭제(필수)"·"미리보기 후 자동 실행")
  title?: ReactNode;             // 보이는 제목(기본 = label)
  checked: boolean;
  onChange: ChangeEventHandler<HTMLInputElement>;
  summary?: ReactNode;
  full?: ReactNode;
  error?: string | null;
  disabled?: boolean;
  danger?: boolean;              // 켜면 빨강(배치 rm 자동 실행 -- 위험 = 빨강 의미 체계)
  expandOnOn?: boolean;
}) {
  const tone = error ? "border-bad"
    : checked ? (danger ? "border-bad/45 bg-badbg/60" : "border-accent/45 bg-infobg/60")
    : "border-line bg-surface";
  const hasHelp = (summary !== undefined && summary !== null) || (full !== undefined && full !== null);
  return (
    <div className={`min-w-0 rounded-xl border px-4 py-3.5 transition-colors ${tone} ${disabled ? "opacity-70" : ""}`}>
      <label className={`flex items-center gap-3 ${disabled ? "cursor-not-allowed" : "cursor-pointer"}`}>
        <input type="checkbox" aria-label={label} checked={checked} onChange={onChange} disabled={disabled}
               aria-invalid={error ? true : undefined}
               className={`${SWITCH} ${danger ? "checked:bg-bad" : "checked:bg-accent"}`} />
        <span className="min-w-0 text-sm font-bold text-ink">{title ?? label}</span>
      </label>
      {hasHelp && (
        <MoreText className="mt-1.5 sm:pl-12" summary={summary ?? null} full={full}
                  forceOpen={checked && expandOnOn && full !== undefined && full !== null} />
      )}
      {error && <p className="mt-1.5 text-sm text-bad sm:pl-12">{error}</p>}
    </div>
  );
}

export interface OperationCardOption<V extends string> {
  value: V;                      // 라디오 접근 이름 = 연산 이름 그대로(sync·scan·rm -- 시험·e2e 접점)
  subtitle: string;              // 짧은 부제(features/jobs/optionRules OPERATION_LABEL·OPERATION_LABEL_BATCH)
  description: ReactNode;        // 설명 한 줄(화면의 OPERATION_HELP 등) -- 라디오의 aria-describedby
  chips?: readonly string[];     // 보조 칩(선택)
  danger?: boolean;              // rm: 「되돌릴 수 없음」 칩 + 고르면 빨간 테두리
  icon?: LucideIcon;
}

const OPERATION_ICONS: Record<string, LucideIcon> = { sync: RefreshCw, scan: ScanSearch, rm: Trash2 };

/**
 * 연산 고르기 카드형 라디오 그룹. <div role="radiogroup" aria-label={label}>(기본 「연산」 -- 옛 select 의 접근 이름) + 카드별 <label>
 * 안의 <input type="radio" name aria-label={value} aria-describedby={설명}>. 라디오는 시각적으로 숨기되(sr-only) pointer-events 는 막지
 * 않는다 -- 카드 전체가 누르는 영역이다. 같은 name 이라 화살표 이동은 브라우저 기본 동작. 포커스 링은 카드에(has-[:focus-visible]).
 * name 은 화면마다 따로 준다(한 화면에 라디오 묶음이 여럿이면 같은 name 끼리 선택이 풀린다).
 */
export function OperationCards<V extends string>({ name, value, options, onChange, label = "연산" }: {
  name: string;
  value: V;
  options: readonly OperationCardOption<V>[];
  onChange: (v: V) => void;
  label?: string;
}) {
  const id = useId();
  return (
    <div role="radiogroup" aria-label={label} className="grid grid-cols-[repeat(auto-fit,minmax(14rem,1fr))] gap-4">
      {options.map((o) => {
        const on = o.value === value;
        const Icon = o.icon ?? OPERATION_ICONS[o.value] ?? RefreshCw;
        const descId = `${id}-${o.value}-desc`;
        const ring = on ? (o.danger ? "border-bad ring-4 ring-badbg" : "border-accent ring-4 ring-infobg")
          : "border-line hover:border-muted/60 hover:shadow-station";
        const chips = [...(o.danger ? ["되돌릴 수 없음"] : []), ...(o.chips ?? [])];
        return (
          <label key={o.value}
                 className={`relative flex min-w-0 cursor-pointer flex-col gap-2.5 rounded-xl border-[1.5px] bg-surface p-5 transition
                             has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2
                             has-[:focus-visible]:outline-accent ${ring}`}>
            <input type="radio" name={name} value={o.value} checked={on} aria-label={o.value} aria-describedby={descId}
                   onChange={() => onChange(o.value)} className="sr-only" />
            <span className="flex items-start justify-between">
              <span aria-hidden className={`grid h-10 w-10 place-items-center rounded-xl ${
                o.danger ? "bg-badbg text-bad" : o.value === "scan" ? "bg-stage-running text-stage-running-ink" : "bg-infobg text-accent"}`}>
                <Icon className="h-5 w-5" />
              </span>
              <span aria-hidden className={`grid h-5 w-5 place-items-center rounded-full border-2 ${
                on ? (o.danger ? "border-bad bg-bad text-white" : "border-accent bg-accent text-white") : "border-line text-transparent"}`}>
                <Check className="h-3 w-3" strokeWidth={3} />
              </span>
            </span>
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-lg font-bold text-ink">{o.value}</span>
              <span className="text-sm text-muted">{o.subtitle}</span>
            </span>
            <span id={descId} className="text-sm text-ink">{o.description}</span>
            {chips.length > 0 && (
              <span className="mt-auto flex flex-wrap gap-1.5">
                {chips.map((c) => (
                  <span key={c} className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs ${
                    c === "되돌릴 수 없음" ? "bg-badbg text-bad" : "border border-line bg-panel text-ink"}`}>{c}</span>
                ))}
              </span>
            )}
          </label>
        );
      })}
    </div>
  );
}

/**
 * 검토 카드: h3 + 「수정」 버튼 + 본문(SummaryList/SummaryRow 를 그대로 쓴다). 「수정」의 접근 이름은 내용에서 만든다(「수정: 제목」 --
 * sr-only). aria-label 을 쓰지 않는 이유: 「스토리지 수정」 같은 이름이면 getByLabel("스토리지") 부분 일치가 이 버튼까지 잡는다. 카드
 * 자체에도 aria-label 을 달지 않는다(단계 카드와 같은 이유). tone: info(목적지 조건 -- 연파랑)·bad(삭제 규칙 -- 빨강).
 */
export function ReviewCard({ title, onEdit, actions, tone = "default", children }: {
  title: string;
  onEdit?: () => void;
  actions?: ReactNode;           // 머리 오른쪽에 더 둘 버튼(「옵션·실행 제어로 가기」 등 -- type="button" 은 호출측 몫)
  tone?: "default" | "info" | "bad";
  children: ReactNode;
}) {
  const box = tone === "info" ? "border-transparent bg-infobg"
    : tone === "bad" ? "border-bad/30 bg-badbg text-bad" : "border-line bg-surface";
  return (
    <section className={`min-w-0 rounded-xl border px-4 py-4 sm:px-5 ${box}`}>
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
        <h3 className={`text-sm font-bold ${tone === "bad" ? "text-bad" : "text-ink"}`}>{title}</h3>
        {(onEdit !== undefined || actions !== undefined) && (
          <div className="flex flex-wrap items-center gap-3">
            {actions}
            {onEdit !== undefined && (
              <button type="button" onClick={onEdit}
                      className="text-xs font-semibold text-accent hover:text-accenthover hover:underline hover:underline-offset-2">
                {/* 보이는 글자는 「수정」, 이름은 sr-only 한 덩어리(조각으로 나누면 이름 계산기마다 경계에 공백을 넣거나 안 넣는다) */}
                <span aria-hidden>수정</span><span className="sr-only">{`수정: ${title}`}</span>
              </button>
            )}
          </div>
        )}
      </div>
      <div className="min-w-0 text-sm">{children}</div>
    </section>
  );
}

/** 단계 안 소제목(h3 + 선택 caption) -- 옛 FormSection(번호 h2)의 단계 내부판. caption 은 MoreText 를 받을 수 있게 <div> 다. */
export function SubSection({ title, caption, children, className = "" }: {
  title: string; caption?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`min-w-0 ${className}`}>
      <h3 className="text-[15px] font-bold text-ink">{title}</h3>
      {caption !== undefined && caption !== null && <div className="mt-0.5 text-xs text-muted">{caption}</div>}
      <div className="mt-3 min-w-0 space-y-4">{children}</div>
    </section>
  );
}
