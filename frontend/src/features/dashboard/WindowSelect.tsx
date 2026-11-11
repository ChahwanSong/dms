import { Segmented } from "../../components/ui/Segmented";

// 설계 §4.2의 기간 선택(1h/6h/24h/7d). 자유 입력을 받지 않는다. 백엔드는 창을 보존 기간(DMS_AGENT_REPORT_RETENTION_DAYS
// × 24h, 기본 30일 = 720h)으로 클램프한다 -- 기본값이면 7d 가 늘 들어가지만 보존일을 7 미만으로 줄인 사이트에선 「7d」를
// 눌러도 더 짧은 창이 온다. 응답의 window_hours 가 고른 값보다 작으면 WindowClampNote 가 그 사실을 한 줄로 말한다(2026-11-11).
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

// 서버가 접은 창(served = 응답 window_hours)이 고른 창보다 짧을 때만 한 줄. served 가 수가 아니면(구 서버·로딩) 아무것도
// 그리지 않는다 -- 모르는 클램프를 지어내지 않는다. 기간 전환 중(앞 기간 값을 보여 주는 동안)은 호출부가 served 를 넘기지 않는다.
export function WindowClampNote({ selected, served }: { selected: number; served: unknown }) {
  if (typeof served !== "number" || !Number.isFinite(served) || served >= selected) return null;
  return (
    <p className="mt-2 text-xs text-attn break-keep">
      {`보존 기간(DMS_AGENT_REPORT_RETENTION_DAYS) 상한 때문에 최근 ${served}시간만 보여 줍니다.`}
    </p>
  );
}
