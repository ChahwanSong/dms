// 설계 §4.2의 기간 선택(1h/6h/24h/7d). 백엔드가 720h로 클램프하므로 프론트는
// 선택지만 제한하면 된다 -- 자유 입력을 받지 않는다.
const WINDOWS = [
  { label: "1h", hours: 1 }, { label: "6h", hours: 6 },
  { label: "24h", hours: 24 }, { label: "7d", hours: 168 },
] as const;

// 세그먼트 컨트롤(2026-09-30 대시보드 개선): 선택된 기간을 채움(accent)으로 분명히 --
// 예전 굵은 테두리만으로는 무엇이 선택됐는지 한눈에 안 보였다. aria-pressed 로 상태를 알린다.
export function WindowSelect({ value, onChange }: {
  value: number; onChange: (h: number) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel p-0.5" role="group" aria-label="기간">
      {WINDOWS.map((w) => (
        <button key={w.hours} onClick={() => onChange(w.hours)} aria-pressed={value === w.hours}
                className={`rounded-md px-2.5 py-1 text-xs ${
                  value === w.hours
                    ? "bg-surface font-semibold text-accent shadow-soft"
                    : "text-muted hover:text-ink"}`}>
          {w.label}
        </button>
      ))}
    </div>
  );
}
