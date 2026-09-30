import { useId, useState } from "react";
import { AlertTriangle } from "lucide-react";

// 경고성 툴팁(2026-09-30, 아티팩트 경로 변경 주의점). 의존성을 늘리지 않는다 -- radix
// tooltip 을 새로 들이면 빌드 lock 이 바뀐다(런타임 airgap 이라 번들만 되면 되지만 이 정도는
// 상태 하나로 충분하다).
// 열림: 마우스 올림(CSS group-hover) · 키보드 포커스(onFocus) · 클릭(터치). 닫힘: Esc · 포커스
// 이탈 · 마우스가 트리거와 내용 밖으로 나감(WCAG 1.4.13 -- 닫을 수 있고, 머무를 수 있다).
// 내용 상자는 트리거 바로 아래 투명 패딩(pt-2)으로 이어 붙여 포인터가 틈에 빠져 사라지지 않게 한다.
// 접근성: 트리거 button 이 aria-describedby 로 role="tooltip" 내용을 가리킨다 -- 스크린리더는
// 포커스만으로 내용을 읽는다. 내용은 늘 DOM 에 있고 CSS 로만 숨긴다(검색·테스트 가능).
export function WarnTooltip({ label, children, align = "left" }: {
  label: string; children: React.ReactNode; align?: "left" | "right";
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span className="group relative inline-flex"
          onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
      <button type="button" aria-describedby={id} aria-expanded={open}
              onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
              onClick={() => setOpen(true)}
              className="inline-flex items-center gap-1 rounded-full border border-bad/30 bg-badbg px-2 py-0.5 text-xs font-semibold text-bad">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        {label}
      </button>
      <span role="tooltip" id={id}
            className={`absolute ${align === "right" ? "right-0" : "left-0"} top-full z-30 pt-2 w-[22rem] max-w-[calc(100vw-2rem)] ${
              open ? "block" : "hidden group-hover:block"}`}>
        <span className="block rounded-card border border-bad/30 bg-surface p-3 text-xs leading-relaxed text-ink shadow-lg">
          {children}
        </span>
      </span>
    </span>
  );
}
