import { Virtuoso } from "react-virtuoso";
import type { ReactNode } from "react";

// The live footer node is passed through Virtuoso `context` (not via a fresh
// `components` object each render) so the components identity stays stable and
// Virtuoso never remounts. The footer changes content as `context` changes.
type FooterCtx = { footer: ReactNode };

function ListFooter({ context }: { context: FooterCtx }) {
  return <>{context.footer}</>;
}
const FOOTER_COMPONENTS = { Footer: ListFooter };

// Generic virtual-scroll table used by the batch-detail request lists. Renders a
// fixed column-header row above a bounded-height react-virtuoso scroll region:
// only on-screen rows mount, and `endReached` drives infinite (append) loading,
// so a batch with thousands of requests stays smooth.
//
// Each row is a plain DIV on a CSS grid (NOT a <tr>) so an expanded row can grow
// its own height inside the same virtual item — Virtuoso re-measures it
// automatically (ResizeObserver) on expand/collapse. `computeItemKey` MUST return
// a stable id so selection/expand stay correct as rows recycle. Header and rows
// must share the SAME grid-template (passed via the row class names) to align.
export default function VirtualRows<T>({
  items,
  itemContent,
  computeItemKey,
  endReached,
  header,
  footer,
  empty,
  className,
}: {
  items: T[];
  itemContent: (index: number, item: T) => ReactNode;
  computeItemKey: (index: number, item: T) => string | number;
  endReached?: () => void;
  header: ReactNode;
  footer?: ReactNode;
  empty?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`vtable${className ? ` ${className}` : ""}`}>
      <div className="vhead-wrap">{header}</div>
      {items.length === 0 ? (
        <div className="vempty">{empty}</div>
      ) : (
        <div className="vscroll">
          <Virtuoso<T, FooterCtx>
            data={items}
            computeItemKey={(index, item) => computeItemKey(index, item)}
            itemContent={(index, item) => itemContent(index, item)}
            endReached={endReached}
            increaseViewportBy={400}
            context={{ footer: footer ?? null }}
            components={FOOTER_COMPONENTS}
          />
        </div>
      )}
    </div>
  );
}
