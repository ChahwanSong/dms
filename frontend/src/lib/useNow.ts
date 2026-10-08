import { useEffect, useState } from "react";

// 화면이 "지금" 을 주기적으로 다시 읽어야 할 때(경과 시간 「12초째」, 컨펌 유효기간 「23시간 41분 남음」)의 틱.
// enabled=false 면 interval 을 **하나도** 만들지 않는다 -- 종단 요청 상세는 모든 폴링과 함께 시계 틱도 멈춰야
// 한다(아무것도 안 바뀌는 화면이 1초마다 다시 그려질 이유가 없다). 틱은 이 훅을 부른 컴포넌트만 다시 그리므로
// 호출측은 경과 표시 같은 작은 잎 컴포넌트에서만 부른다.
export function useNow(intervalMs: number, enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);
  return now;
}
