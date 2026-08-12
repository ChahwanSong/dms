// 회색 안내 패널(DS 서피스, 슬라이스 31 T3). role 을 붙이지 않는다 --
// 장식 서피스이지 alert/region 이 아니라서, role 을 주면 스크린리더가
// 모든 안내 문구를 랜드마크·경보로 읽어 소음이 된다.
export function InfoPanel({ className = "", ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`bg-panel rounded-card p-4 text-sm ${className}`} {...p} />;
}
