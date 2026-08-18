import * as D from "@radix-ui/react-dialog";
export function Dialog({ trigger, title, children, open, onOpenChange }: {
  trigger: React.ReactNode; title: string; children: React.ReactNode;
  open?: boolean; onOpenChange?: (o: boolean) => void;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Trigger asChild>{trigger}</D.Trigger>
      <D.Portal>
        <D.Overlay className="fixed inset-0 bg-black/30" />
        {/* max-h+overflow: 필드가 많은 다이얼로그(정책 9필드)가 낮은 화면에서
            제목·저장 버튼째 뷰포트 밖으로 잘리던 결함 — 넘치면 내부 스크롤로 */}
        <D.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-surface rounded-card shadow-soft p-5 w-full max-w-md max-h-[85vh] overflow-y-auto">
          <D.Title className="text-base font-semibold mb-3">{title}</D.Title>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
