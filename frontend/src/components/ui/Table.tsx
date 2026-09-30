// boxed(2026-09-30 대시보드 개선, 사용자 보고 "테이블들의 boundary 도 없어 보인다"):
// 테두리 + 머리줄 배경 + 칸 여백을 준다. 기본(boxed 없음)은 기존 화면 그대로다 --
// 카드 안 목록 표들은 카드가 이미 경계라 바꾸지 않는다.
const BOXED = [
  "[&_thead_tr]:bg-panel [&_th]:px-3 [&_th]:py-2 [&_th]:font-medium",
  "[&_td]:px-3 [&_td]:py-1.5 [&_tbody_tr]:border-t [&_tbody_tr]:border-line",
].join(" ");

export function Table({ children, boxed = false }: { children: React.ReactNode; boxed?: boolean }) {
  return (
    <div className={boxed ? "overflow-x-auto rounded-lg border border-line" : "overflow-x-auto"}>
      <table className={`w-full text-sm text-left ${boxed ? BOXED : ""}`}>{children}</table>
    </div>
  );
}
