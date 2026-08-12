import type { ReactNode } from "react";

// 하단 액션바(슬라이스 31 T3): cancel(좌) / help(중앙 회색) / actions(우) 슬롯.
// 위저드 비종속 -- Wizard(T4)를 import 하지 않고 props 슬롯만으로 동작해야
// 단독 화면(확인 다이얼로그 없는 폼 등)도 같은 바를 쓸 수 있다.
// 슬롯은 ReactNode 그대로 렌더한다: disabled 등 버튼 속성을 여기서 재해석하면
// 호출부 계약(활성화 판정)이 두 곳으로 쪼개진다.
export function BottomActionBar({ cancel, help, actions }: {
  cancel?: ReactNode;
  help?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mt-6 flex items-center justify-between gap-3 border-t border-line pt-4">
      <div className="flex items-center gap-2">{cancel}</div>
      {/* help 가 비어도 칸은 유지 -- 좌/우 정렬이 슬롯 유무에 흔들리지 않게 */}
      <div className="text-sm text-muted">{help}</div>
      <div className="flex items-center gap-2">{actions}</div>
    </div>
  );
}
