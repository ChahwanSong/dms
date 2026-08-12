import type { ReactNode } from "react";
import { Stepper } from "../ui/Stepper";
import { Button } from "../ui/Button";
import { BottomActionBar } from "./BottomActionBar";

export interface WizardStep { id: string; label: string }

// 재사용 위저드 프레임(슬라이스 31 T4). 프레임은 도메인을 모른다 --
// Stepper+콘텐츠+BottomActionBar 조립과 스텝 전이만 소유해야 배치 생성 등
// 다른 다단계 화면이 나중에 그대로 얹힌다. current 는 호출자 소유(제어형):
// 스텝별 검증(canNext)·제출 가드가 호출자의 파생 상태에서 나오기 때문이다.
export function Wizard({
  steps, current, onNavigate, canNext = true, onCancel, help,
  submitLabel, submitDisabled = false, onSubmit, children,
}: {
  steps: WizardStep[];
  current: number;
  onNavigate: (i: number) => void;
  canNext?: boolean;              // 스텝 국소 검증(기본 true) -- "다음"만 잠근다
  onCancel: () => void;
  help?: ReactNode;
  submitLabel: string;
  submitDisabled?: boolean;
  onSubmit: () => void;
  children: ReactNode;            // 현재 스텝 콘텐츠(스위칭은 호출자 몫)
}) {
  const last = current === steps.length - 1;
  return (
    <div>
      {/* 스테퍼 클릭은 뒤로만 -- 앞 점프를 허용하면 canNext(스텝 국소 검증)가
          "다음" 버튼에만 걸려 있어 검증 우회 통로가 된다 */}
      <Stepper steps={steps} current={current}
               onNavigate={(i) => { if (i < current) onNavigate(i); }} />
      <div className="mt-4">{children}</div>
      {/* Enter 제출 유출 금지: form 소유는 호출자 쪽이고 프레임 버튼은 전부
          type="button" -- 제출 버튼만 명시 onClick 이라 초반 스텝에서 Enter 가
          제출로 새지 않는다 */}
      <BottomActionBar
        cancel={<Button type="button" variant="ghost" onClick={onCancel}>취소</Button>}
        help={help}
        actions={
          <>
            {current > 0 && (
              <Button type="button" variant="outline"
                      onClick={() => onNavigate(current - 1)}>이전</Button>
            )}
            {last ? (
              <Button type="button" disabled={submitDisabled} onClick={onSubmit}>
                {submitLabel}
              </Button>
            ) : (
              <Button type="button" disabled={!canNext}
                      onClick={() => onNavigate(current + 1)}>다음</Button>
            )}
          </>
        }
      />
    </div>
  );
}
