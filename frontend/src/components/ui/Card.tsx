// 보더 구획(슬라이스 31 T3): 회색 페이지 배경(canvas=panel) 위에서 그림자만으로는
// 카드 경계가 흐리다 -- DS 는 보더 중심이라 border-line 을 더한다. Card 는 한 곳이라
// 전 화면이 일괄로 새 구획을 받는다.
export function Card({ className = "", ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`bg-surface rounded-card border border-line shadow-soft p-5 ${className}`} {...p} />;
}
