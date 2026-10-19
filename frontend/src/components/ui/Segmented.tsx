// 세그먼트 컨트롤(2026-10-20 공용화). 대시보드 기간 선택(WindowSelect, 2026-09-30)의 모양 그대로 -- 선택은 채움(흰 면 +
// accent 글자)으로 분명히 하고 aria-pressed 로 알린다. 선택지가 몇 개 안 되는 즉시 전환(표시 창·온도 축·기간)에 쓴다 --
// 아래 내용을 통째로 바꾸는 큰 전환은 Tabs 다. 라벨은 한 줄(whitespace-nowrap): 좁은 폭에서 「30건」이 음절 중간에서
// 꺾이면 버튼 높이가 들쭉날쭉해진다(e2e 03 의 aria-pressed 한 줄 검사).
export function Segmented<T extends string | number>({ label, options, value, onChange, className = "" }: {
  label: string;
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label}
         className={`inline-flex shrink-0 rounded-lg border border-line bg-panel p-0.5 ${className}`}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button key={String(o.value)} type="button" aria-pressed={on} onClick={() => onChange(o.value)}
                  className={`whitespace-nowrap rounded-md px-2.5 py-1 text-xs focus-visible:outline-none
                              focus-visible:ring-2 focus-visible:ring-accent ${
                    on ? "bg-surface font-semibold text-accent shadow-soft" : "text-muted hover:text-ink"}`}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
