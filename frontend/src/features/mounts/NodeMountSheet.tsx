import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Dialog } from "../../components/ui/Dialog";
import { agoText, kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import { MountCellChip, NodeTags, PodTag, SharedCellChip } from "./MountCell";
import {
  cellAriaLabel, cellHelp, cellLabel, cellMounted, coverText, podFailedHelp, podFailedOn, podFailedRoles, podTag, podsOn, probeModeText,
  sharedDisplay, sharedHelp, usageDetail, writableText, yesNo,
} from "./mountTerms";
import type { MountRow, MountStatusResponse, StorageColumn } from "./types";

// 노드 하나의 마운트 상세(측면 시트, 2026-11-23). 표의 칸 툴팁과 같은 내용을 키보드로도 볼 수 있게 하는 진입점이다(칸은 탭 정지가
// 아니다 -- MountMatrix). 열고 닫기는 부르는 쪽(MountStatusPage)이 하고, 닫으면 그 노드 버튼으로 포커스를 돌려준다(onCloseAutoFocus).
// renderStorageTip = 열 머리 툴팁의 사이트 덧붙임(dms-k8s 의 종류·선택 가능한 PV·클러스터·StorageClass) -- 열 머리 툴팁은 마우스
// 전용이라 같은 내용을 스토리지 줄마다 싣는다(2026-11-25 리뷰: 키보드·터치로 PV 수·「모름」을 볼 길이 없었다).

export function NodeMountSheet({ row, data, roleLabels, renderStorageTip, onClose, onCloseAutoFocus }: {
  row: MountRow | null;
  data: MountStatusResponse;
  roleLabels?: Record<string, string>;
  renderStorageTip?: (col: StorageColumn) => ReactNode;
  onClose: () => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const nowMs = useNow(15_000, row !== null);
  const shared = data.shared_dir;
  const pods = row !== null ? podsOn(data, row.node_name) : [];
  const podRoles = [...new Set(pods.map((p) => p.role))];
  return (
    <Dialog side="right" open={row !== null} onOpenChange={(o) => { if (!o) onClose(); }}
            title={row?.node_name ?? ""} titleClassName="font-mono" onCloseAutoFocus={onCloseAutoFocus}
            subtitle={row !== null ? <NodeTags row={row} roleLabels={roleLabels} pods={pods} /> : undefined}>
      {row !== null && (
        <div className="space-y-4 text-sm">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5">
            <dt className="text-muted">마지막 보고</dt>
            <dd className={row.fresh ? "" : "text-bad"}>
              {`${agoText(row.reported_at, nowMs)} (${kstStamp(row.reported_at)})${row.fresh ? "" : " · 리포트 지연"}`}
            </dd>
            <dt className="text-muted">점검 방식</dt>
            <dd>{probeModeText(row.probe_mode)}</dd>
            {shared !== null && row.shared_dir !== null && (
              <>
                <dt className="text-muted">DMS 공용 디렉터리</dt>
                <dd className="space-y-1">
                  <SharedCellChip cell={row.shared_dir} className="!items-start" podFailed={podFailedOn(data, row.node_name)}
                                  aria-label={cellAriaLabel(row.node_name, "DMS 공용",
                                    sharedDisplay(row.shared_dir, podFailedOn(data, row.node_name)).meta.label)} />
                  {podFailedOn(data, row.node_name) && sharedDisplay(row.shared_dir, true).overridden && (
                    <p className="text-xs text-bad">{podFailedHelp(podFailedRoles(data, row.node_name))}</p>
                  )}
                  <p className="font-mono text-xs break-all">{shared.path}</p>
                  <p className="text-xs text-muted">
                    {`존재 ${yesNo(row.shared_dir.exists)} · `
                      + `쓰기 ${writableText(row.shared_dir.writable, row.shared_dir.exists === true)} · `
                      + `덮는 마운트 ${coverText(row.shared_dir)}`}
                  </p>
                  <p className="text-xs">{sharedHelp(row.shared_dir, shared.majority, agoText(row.reported_at, nowMs))}</p>
                </dd>
              </>
            )}
            {podRoles.length > 0 && (
              // 꼬리표의 title 문장을 키보드로도 읽게(행 머리 꼬리표는 탭 정지가 아니다) -- 같은 podTag 문장.
              <>
                <dt className="text-muted">이 노드의 DMS 파드</dt>
                <dd>
                  <ul aria-label={`${row.node_name} DMS 파드`} className="space-y-1.5">
                    {podRoles.map((role) => {
                      const view = podTag(row, role, pods);
                      return (
                        <li key={role} className="space-y-0.5">
                          <PodTag view={view} />
                          {view.lines.map((line, i) => (
                            <p key={i} className="text-xs [overflow-wrap:anywhere]">{line}</p>
                          ))}
                        </li>
                      );
                    })}
                  </ul>
                </dd>
              </>
            )}
          </dl>
          {data.storages.length === 0 ? (
            <p className="text-muted">점검할(활성) 스토리지가 없습니다.</p>
          ) : (
            // 스토리지별 목록은 표가 아니라 세로 쌓임이다(2026-11-23 d38 실증): 640px 시트(모바일은 더 좁다)에 6열 표를 넣으면
            // 경로 열이 한 글자 폭까지 줄어 「/c ep hf s」처럼 끊기고 사유 열은 가로로 밀어야 보였다. 줄 하나 = 스토리지 하나.
            <ul aria-label={`${row.node_name} 스토리지별 마운트`} className="space-y-2">
              {data.storages.map((col) => {
                const cell = row.cells[col.storage_name];
                if (cell === undefined) return null;
                const usage = usageDetail(cell.disk);
                return (
                  <li key={col.storage_name} className="rounded-lg border border-line px-3 py-2.5 space-y-1">
                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                      <span className="font-mono text-xs font-medium break-all">{col.storage_name}</span>
                      <MountCellChip cell={cell} className="!items-end"
                                     aria-label={cellAriaLabel(row.node_name, col.storage_name, cellLabel(cell))} />
                    </div>
                    <p className="font-mono text-xs text-muted break-all">{col.mount_path}</p>
                    {renderStorageTip !== undefined && <div className="text-xs">{renderStorageTip(col)}</div>}
                    <p className="text-xs tabular-nums">
                      {`쓰기 ${cell.status === null ? "—" : writableText(cell.writable, cellMounted(cell))}`}
                      {usage !== null && ` · ${usage}`}
                    </p>
                    {cell.state !== "ready" && (
                      <p className="text-xs">
                        {cellHelp(cell, { mountPath: col.mount_path, ago: agoText(row.reported_at, nowMs) })}
                        {cell.reason !== null && <span className="mt-0.5 block font-mono text-muted">{cell.reason}</span>}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <Link to="/admin/nodes" className="inline-block text-accent hover:underline">
            노드 화면에서 보기 — 배치 제외·도구 상태
          </Link>
        </div>
      )}
    </Dialog>
  );
}
