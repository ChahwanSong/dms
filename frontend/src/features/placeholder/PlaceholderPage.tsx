// NAS·Monitoring 자리 화면(슬라이스 31 T2). h1 은 화면 소유다(셸엔 h1 금지 --
// e2e visit() 이 heading level:1 을 단언한다). h1 클래스는 기존 23개 h1 과 같은
// `text-lg font-semibold` 로 맞춘다 -- T5 의 기계적 클래스 스왑(grep)에 함께 잡힌다.
export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">{title}</h1>
      {/* InfoPanel 풍 안내(T3 전이라 공용 컴포넌트 없이 같은 질감만 낸다) */}
      <div className="rounded-card border border-line bg-infobg p-4 text-sm text-ink">
        준비 중입니다 — 이 메뉴는 이후 반복에서 채워집니다.
      </div>
    </div>
  );
}
