// boxed(2026-09-30 대시보드 개선, 사용자 보고 "테이블들의 boundary 도 없어 보인다"):
// 테두리 + 머리줄 배경 + 칸 여백을 준다. 기본(boxed 없음)은 기존 화면 그대로다 --
// 카드 안 목록 표들은 카드가 이미 경계라 바꾸지 않는다.
const BOXED = [
  "[&_thead_tr]:bg-panel [&_th]:px-3 [&_th]:py-2 [&_th]:font-medium",
  "[&_td]:px-3 [&_td]:py-1.5 [&_tbody_tr]:border-t [&_tbody_tr]:border-line",
].join(" ");

// wrapRef(2026-10-20): 가로 스크롤 래퍼를 호출측이 잰다 -- 사용량 분석 표가 옆으로 밀렸는지(고정 경로 칸의 그늘).
// framed(2026-10-29): 카드 밖(페이지 바탕) 표의 경계 -- 흰 바탕 + 1px 테두리 + 카드 모서리만 주고 칸 여백·머리줄은 호출측 그대로
// (boxed 는 칸 여백·머리 배경까지 바꾼다). 사용량 분석 패널이 바깥 카드를 없애며 표가 스스로 구획이 됐다.
export function Table({ children, boxed = false, framed = false, wrapRef }: {
  children: React.ReactNode; boxed?: boolean; framed?: boolean; wrapRef?: React.Ref<HTMLDivElement>;
}) {
  const wrap = boxed ? "overflow-x-auto rounded-lg border border-line"
    : framed ? "overflow-x-auto rounded-card border border-line bg-surface" : "overflow-x-auto";
  return (
    <div ref={wrapRef} className={wrap}>
      <table className={`w-full text-sm text-left ${boxed ? BOXED : ""}`}>{children}</table>
    </div>
  );
}
