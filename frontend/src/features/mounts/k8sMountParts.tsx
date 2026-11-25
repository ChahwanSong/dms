import { CircleQuestionMark, TriangleAlert } from "lucide-react";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { KIND_LABEL, storageKindOf } from "../k8s/k8sTerms";
import { Tag } from "../k8s/parts";
import type { StorageKind } from "../k8s/types";
import { MountStatusPage } from "./MountStatusPage";
import type { MountStatusResponse, StorageColumn } from "./types";

// 마운트 현황의 dms-k8s 덧붙임(2026-11-23) -- 공용 코어(MountStatusPage 등)는 dms-ssc 와 같고, k8s 정보는 이 파일만 그린다(ssc 로 옮길
// 때 이 파일과 라우터의 K8sMountStatusPage 만 빼면 된다). 서버 짝은 src/dms/mount_status_k8s.py.
//   - 스토리지 열 머리(renderStorageMeta): 종류 꼬리표 하나(파일시스템/PV/PV·파일시스템 -- KIND_LABEL). PV 류인데 이 스토리지를
//     가리키는 StorageClass 매핑이 없으면 그 꼬리표가 주의색 + ⚠ + 낭독용 「StorageClass 매핑 없음」(색만으로 구분하지 않는다).
//     2026-11-25 압축: 머리는 고정 폭(6.5rem)이라 한 줄 꼬리표 하나만 싣는다. 그래도 PV 수·클러스터를 hover 에만 두지 않는다(리뷰):
//     꼬리표 안 sr-only 로 「선택 가능한 PV n개 · 클러스터 n」(모름은 「PV 모름」 -- null ≠ 0)을 낭독하고, 모름은 보이는 표지(범례의
//     「모름」과 같은 물음표 아이콘)로 0·정상과 가른다. 매핑 없음이면 ⚠ 가 우선(PV 를 고를 수 없으니 수는 부차).
//   - 스토리지 열 머리 툴팁(renderStorageTip): 종류 · 선택 가능한 PV 수(Bound·StorageClass 매핑·경로 해석 가능; 한 번도 동기화되지
//     않은 클러스터가 있으면 「모름」 -- null ≠ 0) · 클러스터 · StorageClass(기존 화면 용어 -- pvLocation·사용량 PV_UNKNOWN_TITLE 과
//     같은 말). 같은 슬롯을 노드 시트의 스토리지 줄에도 넘긴다 -- 열 머리 툴팁은 마우스 전용이라 키보드·터치 경로(노드 버튼 → 시트).
//   - 노드 꼬리표: k8s 호출 노드 주·보조.
//   - PV 안내: PV 열이 하나라도 있을 때만 -- PV 도 노드 마운트가 전제라는 것(PVC 를 붙이지 않고 스토리지 마운트 경로를 hostPath 로).
// PV 용어는 「선택 가능한 PV」·「StorageClass」·「클러스터」만 쓴다(k8sTerms 밖에서 짓지 않는다).

export type K8sColumn = StorageColumn & {
  kind?: StorageKind | null;
  pv?: { count: number | null; clusters: string[]; storage_classes: string[] } | null;
};

export const K8S_ROLE_LABELS: Record<string, string> = { k8s_primary: "k8s 호출 주", k8s_secondary: "k8s 호출 보조" };

const isPvKind = (kind: StorageKind) => kind === "pv" || kind === "both";
const NO_SC_TITLE = "이 스토리지를 가리키는 StorageClass 매핑이 없어 PV 를 고를 수 없습니다 — StorageClass 화면에서 매핑하세요.";

/** PV 류 열의 pv 덧붙임(없으면 null) + 매핑 없음 여부. */
function pvOf(c: K8sColumn): { kind: StorageKind; pv: NonNullable<K8sColumn["pv"]> | null; noSc: boolean } {
  const kind = storageKindOf(c);
  const pv = isPvKind(kind) ? c.pv ?? null : null;
  return { kind, pv, noSc: pv !== null && pv.storage_classes.length === 0 };
}

/** 열 머리 꼬리표의 낭독용 덧붙임 「 — 선택 가능한 PV 5개 · 클러스터 2」(모름은 「선택 가능한 PV 모름」, 매핑 없음은 맨 앞). */
export function pvSpoken(pv: NonNullable<K8sColumn["pv"]>, noSc: boolean): string {
  const parts: string[] = [];
  if (noSc) parts.push("StorageClass 매핑 없음");
  parts.push(pv.count === null ? "선택 가능한 PV 모름" : `선택 가능한 PV ${pv.count}개`);
  if (pv.clusters.length > 0) parts.push(`클러스터 ${pv.clusters.length}`);
  return ` — ${parts.join(" · ")}`;
}

/** 스토리지 열 머리의 k8s 꼬리표(한 줄 하나). kind 키가 없는 응답(dms-ssc 모양)이면 아무것도 그리지 않는다. 상세 문장은 머리
 *  툴팁·노드 시트(K8sStorageTip) -- title 을 달지 않는다(내장 툴팁이 머리 툴팁과 겹친다). 「⚠ PV·파일시스템」·「PV·파일시스템 ?」 가
 *  가장 넓은 꼬리표(약 94.5px)라 머리 상자(96px) 안이다 -- 둘이 겹치면 ⚠ 만. */
export function K8sStorageMeta({ col }: { col: StorageColumn }) {
  const c = col as K8sColumn;
  if (!("kind" in c)) return null;
  const { kind, pv, noSc } = pvOf(c);
  return (
    <Tag tone={noSc ? "warn" : isPvKind(kind) ? "info" : "neutral"}>
      {noSc && <TriangleAlert className="mr-0.5 h-3 w-3 shrink-0" aria-hidden />}
      {KIND_LABEL[kind]}
      {pv !== null && pv.count === null && !noSc && (
        <CircleQuestionMark className="ml-0.5 h-3 w-3 shrink-0" aria-hidden />
      )}
      {pv !== null && <span className="sr-only">{pvSpoken(pv, noSc)}</span>}
    </Tag>
  );
}

/** 스토리지 열 머리 툴팁·노드 시트 스토리지 줄의 k8s 덧붙임. kind 키가 없는 응답이면 그리지 않는다. */
export function K8sStorageTip({ col }: { col: StorageColumn }) {
  const c = col as K8sColumn;
  if (!("kind" in c)) return null;
  const { kind, pv, noSc } = pvOf(c);
  const dt = "whitespace-nowrap text-muted";
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5">
      <dt className={dt}>종류</dt><dd>{KIND_LABEL[kind]}</dd>
      {pv !== null && (
        <>
          <dt className={dt}>선택 가능한 PV</dt>
          <dd className="tabular-nums">
            {pv.count === null ? "모름 — 한 번도 동기화되지 않은 클러스터가 있습니다" : `${pv.count}개(Bound·StorageClass 매핑)`}
          </dd>
          <dt className={dt}>클러스터</dt>
          <dd className="[overflow-wrap:anywhere]">{pv.clusters.length > 0 ? pv.clusters.join(", ") : "—"}</dd>
          <dt className={dt}>StorageClass</dt>
          <dd className="[overflow-wrap:anywhere]">
            {noSc ? <><span className="font-medium text-attn">매핑 없음</span>{` — ${NO_SC_TITLE}`}</> : pv.storage_classes.join(", ")}
          </dd>
        </>
      )}
    </dl>
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

// 슬롯은 모듈 수준 상수 -- 렌더마다 새 함수를 넘기면 표 머리(MatrixHead memo)가 폴링·마우스 이동마다 다시 그려진다.
const META = (col: StorageColumn) => <K8sStorageMeta col={col} />;
const TIP = (col: StorageColumn) => <K8sStorageTip col={col} />;
const FOOTER = (data: MountStatusResponse) => <PvMountExplainer data={data} />;

/** 라우터가 쓰는 dms-k8s 판 마운트 현황(dms-ssc 라우터는 MountStatusPage 를 바로 쓴다). */
export function K8sMountStatusPage() {
  return <MountStatusPage renderStorageMeta={META} renderStorageTip={TIP} roleLabels={K8S_ROLE_LABELS} footer={FOOTER} />;
}
