import { Check } from "lucide-react";

// 위저드 진행 표시기(슬라이스 31 T3). li/button 렌더만 쓴다 -- a 로 만들면
// 스텝이 라우트 URL 을 갖게 되고(URL 계약 침범), h1 은 화면이 소유한다(전제 #4).
// aside 밖이지만 규율을 통일해 두면 셸 불변식을 외울 게 하나로 줄어든다.
export function Stepper({ steps, current, onNavigate }: {
  steps: { id: string; label: string }[];
  current: number;
  onNavigate?: (index: number) => void;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-2 text-sm">
      {steps.map((s, i) => {
        const active = i === current;
        const done = i < current;
        return (
          <li key={s.id} className="flex items-center gap-2">
            {/* 구분자는 장식 -- 스크린리더에 "보다 큼"으로 읽히면 소음이라 숨긴다 */}
            {i > 0 && <span aria-hidden className="text-muted">&gt;</span>}
            <button type="button" aria-current={active ? "step" : undefined}
                    onClick={() => onNavigate?.(i)}
                    className={`inline-flex items-center gap-2 font-medium ${active ? "text-ink" : "text-muted"}`}>
              <span aria-hidden
                    className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs ${
                      active ? "border-accent bg-accent text-white"
                      : done ? "border-accent bg-surface text-accent"
                      : "border-line bg-surface text-muted"}`}>
                {done ? <Check size={14} /> : i + 1}
              </span>
              {s.label}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
