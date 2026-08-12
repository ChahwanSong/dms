// 연파랑 안내 카드(DS 서피스, 슬라이스 31 T3). InfoPanel(회색)과 색만 다르다 --
// 파랑=적극 안내(권장 경로), 회색=중립 참고. role 없음(InfoPanel 과 같은 이유).
export function InfoCard({ className = "", ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`bg-infobg rounded-card p-4 text-sm ${className}`} {...p} />;
}
