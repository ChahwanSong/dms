import { CircleX } from "lucide-react";
import { Button, type ButtonProps } from "./Button";

/** 실행 중인 잡·요청·배치를 멈추는 「취소」(2026-10-12 사용자 요청 -- 회색 ghost 라 다른 버튼에 묻혔다). 빨강 아웃라인 +
 *  아이콘. 아이콘은 aria-hidden 이라 접근 이름은 글자 그대로다(「취소」·「작업 취소」 정확 일치 단언·e2e 무수정).
 *  대화상자 닫기·「편집 취소」 같은 되돌리기는 이걸 쓰지 않는다 -- 멈추는 동작만 빨갛게 해야 신호가 산다. */
export function CancelButton({ children, className = "", ...p }: Omit<ButtonProps, "variant">) {
  return (
    <Button variant="danger" className={`gap-1.5 ${className}`} {...p}>
      <CircleX aria-hidden="true" className="h-4 w-4 shrink-0" />
      {children}
    </Button>
  );
}
