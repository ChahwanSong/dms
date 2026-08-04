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
        <D.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-surface rounded-card shadow-soft p-5 w-full max-w-md">
          <D.Title className="text-base font-semibold mb-3">{title}</D.Title>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
