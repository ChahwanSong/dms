// 로딩 자리표시 막대. 실제 내용과 비슷한 높이로 자리를 먼저 잡아 데이터 도착 때 화면이 밀리지 않게 한다(CLS).
// 장식이라 aria-hidden 이고, "불러오는 중" 의미는 호출측의 sr-only 문구가 따로 전한다. 맥동은 motion-safe 에만.
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`rounded bg-line/60 motion-safe:animate-pulse ${className}`} />;
}
