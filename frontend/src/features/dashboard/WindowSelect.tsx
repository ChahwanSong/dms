// 설계 §4.2의 기간 선택(1h/6h/24h/7d). 백엔드가 720h로 클램프하므로 프론트는
// 선택지만 제한하면 된다 -- 자유 입력을 받지 않는다.
const WINDOWS = [
  { label: "1h", hours: 1 }, { label: "6h", hours: 6 },
  { label: "24h", hours: 24 }, { label: "7d", hours: 168 },
] as const;

export function WindowSelect({ value, onChange }: {
  value: number; onChange: (h: number) => void;
}) {
  return (
    <div className="flex gap-1">
      {WINDOWS.map((w) => (
        <button key={w.hours} onClick={() => onChange(w.hours)}
                className={`rounded px-2 py-1 text-xs border ${
                  value === w.hours
                    ? "font-semibold border-black/30"
                    : "text-muted border-black/10"}`}>
          {w.label}
        </button>
      ))}
    </div>
  );
}
