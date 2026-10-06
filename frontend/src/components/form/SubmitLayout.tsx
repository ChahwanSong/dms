import type { ReactNode } from "react";

// 제출 화면 공용 레이아웃(2026-10-06 사용자 요청: "데스크탑에서 모바일처럼 폭이 좁다 — 심플하고
// 직관적이고 한눈에 보이게"). 단일 작업·배치 생성이 4스텝 위저드(max-w-xl/2xl 카드)였을 때
// 1440 화면의 오른쪽 절반이 비고, 한 스텝이 드롭다운 하나뿐이라 전체를 보려면 다음·이전을
// 오가야 했다. 지금은 **한 장의 시트(번호 구획) + 오른쪽 제출 요약**이다.
//
// - xl(1280) 이상: [시트 | 22rem 요약] 2열. 요약은 화면에 붙어 따라오고 제출 버튼을 든다 --
//   어디까지 스크롤했든 "무엇이 제출되는가"와 "왜 아직 못 내는가"가 늘 보인다.
// - 그 아래: 1열(시트 → 요약). lg(1024)에서 2열이면 시트가 ~430px 라 sync 경로 입력이 다시 좁아진다.
// - DOM 은 한 벌이다. 데스크탑 패널/모바일 하단 바를 따로 그리면(hidden xl:block) jsdom 과 스크린
//   리더에는 제출 버튼·경고가 두 번 있다 -- 배치만 grid 로 바꾼다.
// - 폭 상한 max-w-6xl, mx-auto 없음: 왼쪽 기준선(가운데 정렬은 사용자 지적 이력 -- BuildForm 주석).
// - minmax(0,1fr): 1fr 의 자동 최소폭이 긴 무공백 경로에 밀리면 문서가 가로로 넘친다(e2e L1).
// - 요약은 <aside> 가 아니다: e2e 레이아웃 불변식이 첫 aside = 사이드바(폭 240)로 잰다.
export function SubmitLayout({ sheet, summary }: { sheet: ReactNode; summary: ReactNode }) {
  return (
    <div className="grid max-w-6xl gap-5 xl:grid-cols-[minmax(0,1fr)_22rem] xl:items-start">
      <div className="min-w-0 rounded-card border border-line bg-surface shadow-soft">{sheet}</div>
      {summary}
    </div>
  );
}

// 시트 안의 번호 붙은 구획. 번호는 장식이 아니라 실제 순서다(종류 → 대상 → 옵션 → 실행).
// aria-label·aria-labelledby 를 달지 않는다: 섹션 이름이 필드 라벨("연산"·"스토리지")을 품으면
// Playwright getByLabel(부분 일치)이 섹션까지 잡아 strict 위반이 된다(사이드바 "스토리지" 그룹
// 버튼에서 실제로 났던 사고 -- AppShell.test). 제목은 h2(셸 규칙: h1 은 화면 제목 하나).
export function FormSection({ step, title, caption, children }: {
  step: number; title: string; caption?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="border-t border-line px-5 py-5 first:border-t-0 sm:px-6">
      <div className="mb-4 flex gap-3">
        <span aria-hidden
              className="mt-px grid h-6 w-6 shrink-0 place-items-center rounded-full bg-infobg text-xs font-bold text-accent">
          {step}
        </span>
        <div className="min-w-0">
          <h2 className="text-base font-bold leading-6">{title}</h2>
          {caption !== undefined && <div className="mt-0.5 text-xs text-muted">{caption}</div>}
        </div>
      </div>
      {/* 본문을 제목 글자 기준선에 맞춘다(번호 24px + 간격 12px) -- 번호가 왼쪽 레일이 된다 */}
      <div className="min-w-0 space-y-4 sm:pl-9">{children}</div>
    </section>
  );
}

// 입력(왼쪽)과 그 설명(오른쪽)을 한 줄에. 위저드 시절엔 설명이 입력 아래로 쌓여 세로로만 길었다 --
// 넓은 화면에선 "무엇을 정하나 | 그게 무슨 뜻인가"를 나란히 읽는다. 좁으면(sm 미만) 설명이 아래로 접힌다.
// 설명은 그 필드 묶음(라벨 + 입력)의 **맨 위**에 맞춘다. 입력 상자에 맞추던 고정 오프셋(sm:pt-6)은 라벨이
// 한 줄이라는 가정이라, 반폭 열에서 긴 라벨(예: broken_limit 범위 설명)이 두 줄로 접히면 설명이 라벨
// 둘째 줄 옆으로 어긋났다(리뷰 2026-10-06). align 은 모바일(설명이 아래로 접힐 때)의 들여쓰기만 정한다.
// 설명은 <p> 로 그린다(테스트가 캡션을 tagName P 로 고정한 곳이 있다) -- 블록 요소를 넘기지 말 것.
export function FieldRow({ control, help, align = "field" }: {
  control: ReactNode; help?: ReactNode; align?: "field" | "check";
}) {
  return (
    <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
      <div className="min-w-0">{control}</div>
      {help !== undefined && help !== null && help !== false && (
        <p className={`min-w-0 text-xs text-muted sm:pt-0.5 ${align === "check" ? "ml-6 sm:ml-0" : ""}`}>
          {help}
        </p>
      )}
    </div>
  );
}

// 오른쪽 제출 요약. 본문(dl)만 스크롤되고 버튼 줄(footer)은 패널 바닥에 남는다 -- 요약이 화면보다
// 길어져도(관리자 sync 의 목적지 조건 문장) 제출 버튼이 화면 밖에 고정되지 않는다.
// sticky 가 살려면 조상에 overflow 가 없어야 하고(AppShell 체인은 없음), 이 패널은 grid 셀이라
// 부모의 xl:items-start 로 늘어나지 않게 한다.
export function SummaryPanel({ title, children, footer }: {
  title: string; children: ReactNode; footer: ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-card border border-line bg-surface shadow-soft xl:sticky xl:top-5
                    xl:flex xl:max-h-[calc(100vh-2.5rem)] xl:flex-col">
      <h2 className="border-b border-line px-5 py-3 text-base font-bold">{title}</h2>
      <div className="min-h-0 px-5 py-4 xl:overflow-y-auto">{children}</div>
      <div className="shrink-0 space-y-3 rounded-b-card border-t border-line bg-panel/60 px-5 py-4">
        {footer}
      </div>
    </div>
  );
}

// 요약 목록. dl 하나가 2열 grid 이고 라벨 열은 가장 긴 라벨 폭(max-content)이다 -- 행마다 고정 폭
// (예전 w-24/w-28)이면 22rem 패널에서 값 열이 좁아져 짧은 값까지 여러 줄로 접혔다.
export function SummaryList({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">{children}</dl>;
}

// 요약 한 줄(dt·dd). 행 래퍼 div 는 계약이다 -- 테스트가 getByText(dt).closest("div") 로 행을 잡는다.
// display:contents 라 래퍼는 상자를 만들지 않고 dt·dd 가 바깥 grid 의 칸에 그대로 놓인다.
// dd 의 [overflow-wrap:anywhere]: 경로·storage:path·옵션 값은 공백이 없어 22rem 패널을 뚫는다.
export function SummaryRow({ label, children, className = "" }: {
  label: string; children: ReactNode; className?: string;
}) {
  return (
    <div className="contents">
      <dt className="text-muted">{label}</dt>
      <dd className={`min-w-0 [overflow-wrap:anywhere] ${className}`}>{children}</dd>
    </div>
  );
}

// 옵션 객체를 값 토큰으로(예: delete · batch_files=1000000). 예전 확인 스텝은 JSON.stringify 한 줄이라
// 패널 밖으로 넘쳤고 읽기도 어려웠다. 입력은 제출 바디와 **같은 함수**가 만든 객체여야 한다
// (화면 따로 바디 따로면 "요약과 다른 것이 제출되는" 화면 거짓말 -- 호출측 계약).
export function OptionTokens({ options }: { options: Record<string, unknown> | undefined }) {
  const entries = Object.entries(options ?? {});
  if (entries.length === 0) return <span className="text-muted">없음</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {entries.map(([k, v]) => (
        <code key={k} className="rounded bg-panel px-1.5 py-0.5 text-xs text-ink">
          {v === true ? k : `${k}=${String(v)}`}
        </code>
      ))}
    </span>
  );
}

// 제출이 잠긴 이유(버튼 바로 위). 오류 문구는 각 필드 옆에 이미 있으므로 여기서는 **다른 문장**으로
// 짧게 요약한다 -- 같은 문장을 두 번 그리면 화면이 시끄럽고 getByText 단언이 다중 매칭으로 깨진다.
// role 을 달지 않는다: 입력할 때마다 바뀌는 목록을 live region 으로 읽히면 소음이고, 경보(role=alert)는
// 허용 조합 해제 하나뿐이어야 한다.
export function SubmitHints({ id, items }: { id: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul id={id} className="list-disc space-y-0.5 pl-4 text-xs text-muted">
      {items.map((t) => <li key={t}>{t}</li>)}
    </ul>
  );
}
