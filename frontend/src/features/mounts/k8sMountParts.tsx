import { InfoPanel } from "../../components/ui/InfoPanel";
import { KIND_LABEL, storageKindOf } from "../k8s/k8sTerms";
import { Tag } from "../k8s/parts";
import type { StorageKind } from "../k8s/types";
import { MountStatusPage } from "./MountStatusPage";
import type { MountStatusResponse, StorageColumn } from "./types";

// 마운트 현황의 dms-k8s 덧붙임(2026-11-23) -- 공용 코어(MountStatusPage 등)는 dms-ssc 와 같고, k8s 정보는 이 파일만 그린다(ssc 로 옮길
// 때 이 파일과 라우터의 K8sMountStatusPage 만 빼면 된다). 서버 짝은 src/dms/mount_status_k8s.py.
//   - 스토리지 열 머리: 종류(파일시스템/PV/PV·파일시스템 -- KIND_LABEL) + PV 류면 「PV n개」(지금 고를 수 있는 PV: Bound·StorageClass
//     매핑·경로 해석 가능; 한 번도 동기화되지 않은 클러스터가 있으면 「PV 모름」 -- null ≠ 0)·클러스터 수·「StorageClass 매핑 없음」
//     (기존 화면 용어 -- pvLocation·사용량 PV_UNKNOWN_TITLE 과 같은 말).
//   - 노드 꼬리표: k8s 호출 노드 주·보조.
//   - PV 안내: PV 열이 하나라도 있을 때만 -- PV 도 노드 마운트가 전제라는 것(PVC 를 붙이지 않고 스토리지 마운트 경로를 hostPath 로).
// PV 용어는 「PV n개」·「StorageClass」·「클러스터」만 쓴다(k8sTerms 밖에서 짓지 않는다).

export type K8sColumn = StorageColumn & {
  kind?: StorageKind | null;
  pv?: { count: number | null; clusters: string[]; storage_classes: string[] } | null;
};

export const K8S_ROLE_LABELS: Record<string, string> = { k8s_primary: "k8s 호출 주", k8s_secondary: "k8s 호출 보조" };

const isPvKind = (kind: StorageKind) => kind === "pv" || kind === "both";
const NO_SC_TITLE = "이 스토리지를 가리키는 StorageClass 매핑이 없어 PV 를 고를 수 없습니다 — StorageClass 화면에서 매핑하세요.";

/** 스토리지 열 머리의 k8s 줄. kind 키가 없는 응답(dms-ssc 모양)이면 아무것도 그리지 않는다. */
export function K8sStorageMeta({ col }: { col: StorageColumn }) {
  const c = col as K8sColumn;
  if (!("kind" in c)) return null;
  const kind = storageKindOf(c);
  const pv = isPvKind(kind) ? c.pv ?? null : null;
  const title = pv === null ? undefined
    : `선택 가능한 PV ${pv.count ?? "모름"}${pv.count === null ? "" : "개"}(Bound·StorageClass 매핑) · `
      + `클러스터 ${pv.clusters.length > 0 ? pv.clusters.join(", ") : "—"} · `
      + `StorageClass ${pv.storage_classes.length > 0 ? pv.storage_classes.join(", ") : "—"}`;
  return (
    <>
      <Tag tone={isPvKind(kind) ? "info" : "neutral"}>{KIND_LABEL[kind]}</Tag>
      {pv !== null && (
        <Tag title={title}>
          {pv.count === null ? "PV 모름"
            : `PV ${pv.count}개${pv.clusters.length > 1 ? ` · 클러스터 ${pv.clusters.length}` : ""}`}
        </Tag>
      )}
      {pv !== null && pv.storage_classes.length === 0 && (
        <Tag tone="warn" title={NO_SC_TITLE}>StorageClass 매핑 없음</Tag>
      )}
    </>
  );
}

/** PV 열이 있을 때만 띄우는 안내. */
export function PvMountExplainer({ data }: { data: MountStatusResponse }) {
  const hasPv = data.storages.some((col) => "kind" in col && isPvKind(storageKindOf(col)));
  if (!hasPv) return null;
  return (
    <InfoPanel className="text-xs text-muted break-keep">
      PV 스토리지도 노드 마운트가 전제입니다 — DMS 작업 파드는 PVC 를 붙이지 않고 PV 스토리지의 마운트 경로를 hostPath 로 붙여 PV
      디렉터리에 접근합니다. 그래서 PV 가 몇 개든 칸 하나가 그 노드에서 이 스토리지의 PV 작업이 가능한지를 말합니다(PV 별 점검은
      없습니다).
    </InfoPanel>
  );
}

/** 라우터가 쓰는 dms-k8s 판 마운트 현황(dms-ssc 라우터는 MountStatusPage 를 바로 쓴다). */
export function K8sMountStatusPage() {
  return (
    <MountStatusPage renderStorageMeta={(col) => <K8sStorageMeta col={col} />} roleLabels={K8S_ROLE_LABELS}
                     footer={(data) => <PvMountExplainer data={data} />} />
  );
}
