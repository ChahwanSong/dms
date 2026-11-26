import type { ReactNode } from "react";

// 제출 화면 공용 조각(단일 작업 SubmitJob·배치 생성 BatchCreate). 파일 이름은 이력이다 -- 레이아웃 부품은 더 없다.
//
// 이력: 2026-10-06(사용자 요청 "데스크탑에서 모바일처럼 폭이 좁다 — 심플하고 직관적이고 한눈에 보이게") 4스텝 위저드(max-w-xl/2xl
// 카드)를 걷고 **한 장의 시트(번호 구획 FormSection) + 오른쪽 제출 요약(SummaryPanel, xl:sticky)**(SubmitLayout)으로 갔다 -- 그때의
// 실패 넷: 1440 화면의 오른쪽 절반이 빔, 한 스텝이 드롭다운 하나뿐, 전체를 보려면 다음·이전을 오감, 확인 스텝이 JSON 한 줄.
// 2026-11-26(사용자 결정 「단계별 등록 — 초안 1 상단 스테퍼」) 다시 단계로 나눴다: 골격은 components/form/Wizard(스테퍼 + 단계 카드 +
// 하단 고정 바), 단계 본문 부품은 components/form/wizardParts. 그 넷은 이렇게 피한다 -- 본문 열 전체 폭(화면이 max-w·mx-auto 를 두지
// 않는다), 단계마다 의미 있는 묶음(배치 스토리지는 카드 묶음, 고를 것이 없는 단계는 건너뜀), 스테퍼 아래 값, 사람이 읽는 검토 카드
// (아래 SummaryList·OptionTokens 를 그대로 쓴다). 그래서 SubmitLayout·SummaryPanel·FormSection 은 지웠고(2026-11-26), 아래 다섯은
// 단계 본문·검토 카드가 그대로 다시 쓴다 -- 계약 주석은 바꾸지 않았다. 옛 규칙 중 남는 것: 제출 화면의 카드·바·시트 어디에도
// <aside> 를 쓰지 않는다(e2e 레이아웃 불변식 L3 가 첫 aside = 사이드바 폭 240 으로 잰다), minmax(0,1fr) 없이 긴 무공백 경로를 1fr 열에
// 넣지 않는다(문서 가로 넘침 L1).

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
