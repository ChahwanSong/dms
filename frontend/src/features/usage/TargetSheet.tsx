import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Dialog, SHEET_ICON_BUTTON } from "../../components/ui/Dialog";
import type { ScanTargetRow, UsageAttrFilter } from "../../lib/types";
import { LIST_LIMIT } from "./useUsage";
import { TargetDetail } from "./TargetDetail";
import type { PvDisplay } from "./TargetTable";
import { storageLabel } from "./usageTabs";
import { usagePvOf } from "./usageStats";

// 경로 상세 시트(2026-10-29 사용자 결정 「경로 상세는 표 행 펼침 대신 오른쪽 시트」). 예전엔 고른 행 바로 아래 colSpan 펼침 행에
// 상세가 열려 표가 밀리고(필터 + 펼침 1440 에서 2,264px) 목록 밖 딥링크는 표 아래 폴백 칸에 따로 그렸다. 이제 ?target= 이 있으면
// 모달 측면 시트(Dialog side="right" -- md 이상 오른쪽 640px, 그 아래 전체 화면)가 표 위에 덮인다. 본문은 TargetDetail 그대로
// (소속 → 최신 scan → 추이 → 온도 → 이력)를 한 번에 스크롤한다 -- 시트 안 탭은 없다.
//
// 머리: 제목 = 경로(등폭, 한 번 누르면 전체 선택 -- 복사 버튼은 두지 않는다: http 포탈이라 clipboard API 가 없다, BuildHistory
// 관용), 아래 흐린 한 줄 「스토리지 · 목록 i/n번째」, 오른쪽 이전·다음·닫기. 이전·다음은 **지금 목록**(검색·필터·정렬·상위 100개)의
// 이웃이고 끝에서 비활성이다. 목록에 없는 경로(옛 딥링크·검색으로 빠진 경로)는 머리 아래 「지금 목록 밖」 한 줄 + 이전·다음 없음 --
// 목록이 아직 자리 잡지 않았으면(첫 응답 전) 말하지 않는다(깜빡임). 목록 밖이면 행이 없어 PV 소속·최신 scan 기준을 모른다(상세가
// 이력의 가장 늦은 scan 으로 대신한다).
//
// URL·포커스 규칙은 페이지(UsageAnalysis)와 패널(StoragePanel)이 정한다 -- 여기는 그리기와 콜백뿐: onMove = 이웃으로(replace),
// onClose = Esc·✕·덮개(세션에서 push 로 열었으면 뒤로, 딥링크면 target 만 지우는 replace), onFilter = 소속 글자(PV 이름·사용자)를
// 누르면 그 필터 + 시트 닫기를 한 번에(replace), onCloseAutoFocus = 닫힌 뒤 포커스를 그 행의 경로 버튼으로.
// ↑↓로 경로를 바꾸면 본문을 맨 위로 되돌리고 TargetDetail 을 새로 그린다(key -- 표시 창·온도 축이 이전 경로에서 묻어 오지 않게).
//
// 이전·다음의 접근성(2026-10-29 리뷰, 실 Chrome): 포커스는 누른 버튼에 그대로 두고(연달아 누를 수 있게) 옮긴 결과는 sr-only
// 상태 줄(role=status)이 「<경로> · 목록 i/n번째」로 알린다 -- 열 때는 제목 포커스가 말하므로 비워 두고 이동 뒤에만 채우며,
// ANNOUNCE_MS 뒤 비운다(2차 리뷰: 남겨 두면 읽기 모드에서 바로 위 제목·부제와 같은 경로·위치를 한 번 더 읽었다 -- 비우는 변화는
// 알리지 않는다). 끝에 닿아 비활성이 되는 버튼은 disabled 가 아니라 aria-disabled(누르면 아무 일 없음)다: disabled 면 브라우저가
// 포커스를 <body> 로 떨어뜨리고 Radix 포커스 가둠도 그것을 되돌리지 않아(relatedTarget 없는 focusout 은 무시) 키보드 위치가 모달
// 밖으로 빠졌다.
export const ANNOUNCE_MS = 2000;   // 낭독기가 받아 읽기에 넉넉하고, 이동 직후 읽기 모드로 훑기 전에 비는 길이

export function TargetSheet({ storage, target, rows, listSettled, pvDisplay, onMove, onClose, onFilter, onCloseAutoFocus }: {
  storage: string; target: string | null; rows: ScanTargetRow[]; listSettled: boolean; pvDisplay: PvDisplay;
  onMove: (target: string) => void; onClose: () => void; onFilter: (a: UsageAttrFilter) => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const idx = target === null ? -1 : rows.findIndex((r) => r.target === target);
  const row = idx >= 0 ? rows[idx] : null;
  const outside = target !== null && listSettled && row === null;
  const prev = idx > 0 ? rows[idx - 1].target : null;
  const next = idx >= 0 && idx < rows.length - 1 ? rows[idx + 1].target : null;
  const topRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const body = topRef.current?.closest<HTMLElement>("[data-sheet-body]");
    if (body) body.scrollTop = 0;
  }, [target]);
  // 이전·다음으로 옮겨 간 경로(상태 줄은 지금 경로가 그것일 때만 말한다). 닫히면 잊는다 -- 다시 열 때 옛 문장을 싣고 나오지 않게.
  const [movedTo, setMovedTo] = useState<string | null>(null);
  useEffect(() => { if (target === null) setMovedTo(null); }, [target]);
  const move = (t: string | null) => { if (t !== null) { setMovedTo(t); onMove(t); } };

  const at = (i: number) => `목록 ${(i + 1).toLocaleString("ko-KR")}/${rows.length.toLocaleString("ko-KR")}번째`;
  const position = row !== null ? ` · ${at(idx)}` : "";
  const announce = row !== null && movedTo === target ? `${target} · ${at(idx)}` : "";
  useEffect(() => {
    if (announce === "") return;
    const t = setTimeout(() => setMovedTo(null), ANNOUNCE_MS);
    return () => clearTimeout(t);
  }, [announce]);
  return (
    <Dialog side="right" open={target !== null} onOpenChange={(o) => { if (!o) onClose(); }}
            onCloseAutoFocus={onCloseAutoFocus} title={target ?? ""}
            titleClassName="select-all font-mono max-md:line-clamp-2"
            subtitle={<span className={storage === "" ? "italic" : ""}>{`${storageLabel(storage)}${position}`}</span>}
            actions={<>
              <button type="button" aria-label="이전 경로" title={prev ?? undefined} aria-disabled={prev === null || undefined}
                      className={SHEET_ICON_BUTTON} onClick={() => move(prev)}>
                <ChevronUp aria-hidden className="h-4 w-4" />
              </button>
              <button type="button" aria-label="다음 경로" title={next ?? undefined} aria-disabled={next === null || undefined}
                      className={SHEET_ICON_BUTTON} onClick={() => move(next)}>
                <ChevronDown aria-hidden className="h-4 w-4" />
              </button>
            </>}
            banner={outside && (
              <p className="border-b border-line bg-panel px-4 py-2 text-xs text-muted break-keep md:px-5">
                {`지금 목록(검색·필터·상위 ${LIST_LIMIT}개) 밖의 경로입니다 — 이전/다음 없음`}</p>
            )}>
      <p role="status" className="sr-only">{announce}</p>
      <div ref={topRef} />
      {target !== null && (
        <TargetDetail key={target} storage={storage} target={target}
                      latest={row === null ? undefined : row.latest ?? null}
                      pv={row !== null && pvDisplay.pvLine ? usagePvOf(row.pv) : null}
                      multiCluster={pvDisplay.multiCluster} requesterFiltered={pvDisplay.requesterFiltered}
                      onFilter={onFilter} />
      )}
    </Dialog>
  );
}
