import { HelpTip } from "../../components/ui/HelpTip";
import type { UsagePv, UsagePvVolume } from "../../lib/types";
import { pvInside } from "./usageStats";
import { TERMS, nsLabel } from "../k8s/k8sTerms";
import { Tag } from "../k8s/parts";

// 사용량 분석의 작은 공용 부품(2026-10-21 TargetDetail 에서 이동 -- 스토리지 합계가 같은 문법으로 쓴다: 펼친 상세의 「최신 scan」과
// 패널의 「이 스토리지 합계」가 같은 소제목·수치 모양이라 한 번 익히면 두 곳에서 읽힌다). 무거운 TargetDetail 을 import 하지 않게
// 따로 둔다.

// 파일 크기 분포 설명(2026-10-21 -- 펼친 상세와 스토리지 합계 공용). 「4–64 KiB」 읽는 법은 여기서 한 번만 말한다(줄마다 쓰면
// 목록이 길어진다).
export const SIZE_HELP = "최근 성공 scan 에서 센 일반 파일의 크기별 개수입니다(디렉터리·심링크 제외). 구간은 1024 단위이고 "
  + "「4–64 KiB」는 4 KiB 초과 64 KiB 이하입니다. 막대 길이는 가장 많은 구간 대비, 오른쪽 %는 전체 파일 대비 비중입니다.";

// 소제목 줄: 왼쪽 제목(+ 설명 HelpTip), 오른쪽 컨트롤·보조 글자. 좁은 폭에선 오른쪽이 다음 줄로 내려간다(flex-wrap). id 는 제목에
// -- 목록·구역이 aria-labelledby 로 이름을 받는다.
export function SectionHead({ id, title, help, children }: {
  id?: string; title: string; help?: string; children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
      <h3 className="flex items-center gap-0.5 text-sm font-medium text-ink">
        <span id={id}>{title}</span>
        {help && <HelpTip label={`${title} 설명`} head={title} help={help} />}
      </h3>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

// 한 줄 수치(라벨 위, 값 아래). 값은 dt 의 다음 형제 dd -- 테스트가 라벨로 값을 찾는다. lowerBound = 모름을 뺀 합(하한)이라
// 값 뒤에 흐린 「이상」(숫자 옆에서 말한다 -- 각주까지 내려가 읽지 않아도). title = 정확한 값(바이트 등).
export function Stat({ label, value, lowerBound = false, title }: {
  label: string; value: string; lowerBound?: boolean; title?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      {/* 값은 한 줄(「81,241,347개」가 「개」에서 꺾이지 않게), 「이상」은 한 덩어리로 -- 좁으면 통째로 다음 줄(flex-wrap, 첫 줄
          기준선 정렬). 칸보다 넓은 값은 칸 안에서 말줄임한다(min-w-0 truncate -- 옆 칸 숫자를 덮지 않게, 2026-10-21 리뷰: 768px
          에서 「1,234,567,890개」가 옆 칸에 겹쳤다) -- 정확한 값은 title. 사이 공백 글자는 낭독·textContent 용이다(flex 안에선
          그려지지 않고 간격은 gap). */}
      <dd className="mt-0.5 flex flex-wrap items-baseline gap-x-1 text-lg font-medium tabular-nums text-ink" title={title}>
        <span className="min-w-0 max-w-full truncate">{value}</span>
        {lowerBound && <>{" "}<span className="whitespace-nowrap text-xs font-normal text-muted">이상</span></>}
      </dd>
    </div>
  );
}

// ---- PV 소속 표기(2026-10-29 사용자 요청 「사용자·PV 이름·namespace 등으로 필터」) ----
// 표 경로 칸의 둘째 줄과 펼친 상세 머리가 같은 문법이다: 「PV 이름 — namespace (StorageClass)」(서버 target_label·pvLocationText
// 와 같은 순서). 라벨은 k8sTerms 에서만(화면 「PV 이름」 = PVC, 「볼륨」 = PV 객체). 소속은 서버가 **지금 인벤토리** 기준으로 정한다
// (없으면 PV 주소 scan 기록) -- PVC 를 지우거나 매핑을 바꾸면 바뀐다.
export const PV_RECORDED_TITLE = "지금 인벤토리에 없는 PV — 이 경로를 PV 주소로 scan 한 기록 기준";
export const PV_OUTSIDE_TITLE = "지금 어떤 PV 루트에도 속하지 않는 경로(직접 경로 scan·PV 상위 디렉터리)";
export const PV_UNKNOWN_TITLE = "PV 소속을 판정할 수 없음(미등록 스토리지·StorageClass 매핑 없음·동기화 전 클러스터)";
/** 「datasets — team-a (tb-nfs)」 + 클러스터가 여럿인 탭이면 「 · dmsk8s」(namespace 는 클러스터마다만 유일하다). */
export function pvVolumeText(v: UsagePvVolume, multiCluster: boolean): string {
  return `${v.pvc} — ${nsLabel(v.namespace, v.storage_class)}${multiCluster ? ` · ${v.cluster}` : ""}`;
}
const volumeTitle = (v: UsagePvVolume) => `${TERMS.cluster} ${v.cluster} · ${TERMS.volume} ${v.volume ?? "—"}`;
const pvTitle = (pv: UsagePv) => [...pv.volumes.map(volumeTitle), `PV 루트 ${pv.root ?? "—"}`].join("\n");

/** 표 경로 칸의 둘째 줄. 루트 충돌(한 루트를 PVC 여럿이 가짐)이면 첫째 + 「외 n개」, 전부는 상세에. */
export function PvLine({ pv, multiCluster }: { pv: UsagePv; multiCluster: boolean }) {
  if (pv.source === "outside") return <span title={PV_OUTSIDE_TITLE}>PV 밖</span>;
  if (pv.source === "unknown") return <span className="italic" title={PV_UNKNOWN_TITLE}>PV 모름</span>;
  const [first, ...rest] = pv.volumes;
  return (
    <>
      <span title={pvTitle(pv)}>{pvVolumeText(first, multiCluster)}{rest.length > 0 && ` 외 ${rest.length}개`}</span>
      {pv.source === "recorded" && <>{" "}<Tag title={PV_RECORDED_TITLE}>기록</Tag></>}
    </>
  );
}

/** 펼친 상세 머리: PV 소속(정체 전부) + 권한 사용자(지금 RoleBinding -- 면제 운영자의 전체 접근은 없다). */
export function PvHead({ pv, multiCluster }: { pv: UsagePv; multiCluster: boolean }) {
  return (
    <dl aria-label="PV 소속" className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
      <dt className="text-muted">PV 소속</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere]">
        {pv.source === "outside" ? <span title={PV_OUTSIDE_TITLE}>PV 밖 — 지금 어떤 PV 루트에도 속하지 않는 경로</span>
          : pv.source === "unknown" ? <span className="italic" title={PV_UNKNOWN_TITLE}>PV 모름</span>
          : (<>
            {pv.volumes.map((v) => (
              <span key={`${v.cluster}/${v.namespace}/${v.pvc}`} className="block" title={volumeTitle(v)}>
                {pvVolumeText(v, multiCluster)}</span>))}
            {pv.source === "recorded" && <Tag title={PV_RECORDED_TITLE}>기록</Tag>}
          </>)}
      </dd>
      {pvInside(pv) && (<>
        <dt className="text-muted">권한 사용자</dt>
        <dd className="min-w-0 [overflow-wrap:anywhere]">
          {pv.users.length > 0 ? pv.users.join(", ") : "없음 — 관리자·특권 운영자만 접근"}</dd>
      </>)}
    </dl>
  );
}
