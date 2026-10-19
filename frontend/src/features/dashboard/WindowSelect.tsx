import { Segmented } from "../../components/ui/Segmented";

// 설계 §4.2의 기간 선택(1h/6h/24h/7d). 백엔드가 720h로 클램프하므로 프론트는
// 선택지만 제한하면 된다 -- 자유 입력을 받지 않는다.
const WINDOWS = [
  { label: "1h", value: 1 }, { label: "6h", value: 6 },
  { label: "24h", value: 24 }, { label: "7d", value: 168 },
] as const;

// 세그먼트 컨트롤(2026-09-30 대시보드 개선): 선택된 기간을 채움(accent)으로 분명히 --
// 예전 굵은 테두리만으로는 무엇이 선택됐는지 한눈에 안 보였다. aria-pressed 로 상태를 알린다.
// 2026-10-20: 모양은 공용 Segmented 한 벌(사용량 분석의 표시 창·온도 축과 같은 컨트롤).
export function WindowSelect({ value, onChange }: {
  value: number; onChange: (h: number) => void;
}) {
  return <Segmented label="기간" options={WINDOWS} value={value} onChange={onChange} />;
}
