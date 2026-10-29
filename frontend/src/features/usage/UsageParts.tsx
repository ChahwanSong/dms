import { Link } from "react-router-dom";
import { ExternalLink, HardDrive, Info } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { HelpTip } from "../../components/ui/HelpTip";
import { Highlight, containsToken } from "../../components/ui/Highlight";
import type { ApiError } from "../../lib/api";
import type { UsageAttrFilter, UsagePv, UsagePvVolume } from "../../lib/types";
import type { useStorageTabs } from "./useActiveStorage";
import { pvInside } from "./usageStats";
import { TERMS, nsLabel } from "../k8s/k8sTerms";
import { Tag } from "../k8s/parts";

// 사용량 분석의 작은 공용 부품(2026-10-21 TargetDetail 에서 이동 -- 스토리지 합계가 같은 문법으로 쓴다: 펼친 상세의 「최신 scan」과
// 패널의 「이 스토리지 합계」가 같은 소제목·수치 모양이라 한 번 익히면 두 곳에서 읽힌다). 무거운 TargetDetail 을 import 하지 않게
// 따로 둔다.

// 파일 크기 분포 설명(2026-10-21 -- 경로 상세와 스토리지 합계 공용). 「4–64 KiB」 읽는 법은 여기서 한 번만 말한다(줄마다 쓰면
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
// 상세 시트 머리는 「PV 이름 — namespace (StorageClass)」(서버 target_label·pvLocationText 와 같은 순서), 표는 같은 값을 「PV 이름」
// 열 두 줄로(PvName -- 2026-10-29 사용자 요청 「테이블에 경로 이름뿐 아니라 pv 이름도 표시」로 경로 칸 둘째 줄에서 열이 됐다). 라벨은
// k8sTerms 에서만(화면 「PV 이름」 = PVC, 「볼륨」 = PV 객체). 소속은 서버가 **지금 인벤토리** 기준으로 정한다(없으면 PV 주소 scan
// 기록) -- PVC 를 지우거나 매핑을 바꾸면 바뀐다.
export const PV_RECORDED_TITLE = "지금 인벤토리에 없는 PV — 이 경로를 PV 주소로 scan 한 기록 기준";
export const PV_OUTSIDE_TITLE = "지금 어떤 PV 루트에도 속하지 않는 경로(직접 경로 scan·PV 상위 디렉터리)";
export const PV_UNKNOWN_TITLE = "PV 소속을 판정할 수 없음(미등록 스토리지·StorageClass 매핑 없음·동기화 전 클러스터)";
/** 「datasets — team-a (tb-nfs)」 + 클러스터가 여럿인 탭이면 「 · dmsk8s」(namespace 는 클러스터마다만 유일하다). */
export function pvVolumeText(v: UsagePvVolume, multiCluster: boolean): string {
  return `${v.pvc} — ${nsLabel(v.namespace, v.storage_class)}${multiCluster ? ` · ${v.cluster}` : ""}`;
}
const volumeTitle = (v: UsagePvVolume) => `${TERMS.cluster} ${v.cluster} · ${TERMS.volume} ${v.volume ?? "—"}`;
// 표 칸의 title: 정체 전부를 줄마다 전체 표기로(칸은 말줄임·「외 n개」라 다 보이지 않는다 -- 클러스터는 늘 적는다) + PV 루트.
const pvTitle = (pv: UsagePv) => [...pv.volumes.map((v) => `${pvVolumeText(v, true)} · ${TERMS.volume} ${v.volume ?? "—"}`),
                                  `PV 루트 ${pv.root ?? "—"}`].join("\n");

// 루트 충돌(한 루트를 PVC 여럿이 가짐)일 때 칸에 세울 정체: 검색 토큰에 가장 많이 맞는 PV 이름(같으면 서버 순서) -- 둘째 정체의
// PV 이름으로 걸린 행이 강조 없는 첫째 이름만 보이면 「왜 걸렸는지」가 칸에서 사라진다. 검색이 없으면 첫째.
function shownVolume(vs: UsagePvVolume[], tokens: readonly string[]): UsagePvVolume {
  let best = vs[0], bestScore = 0;
  for (const v of vs) {
    const score = tokens.filter((t) => containsToken(v.pvc, t)).length;
    if (score > bestScore) { best = v; bestScore = score; }
  }
  return best;
}

/** 표 「PV 이름」 열의 칸(2026-10-29). PV 안이면 윗줄 = PV 이름(본문 글자, 검색 토큰 강조) + 「외 n개」 + 「기록」 배지, 아랫줄 = 작게
 *  「namespace (StorageClass)」(+ 클러스터가 여럿인 탭이면 「 · 클러스터」). PV 밖은 흐린 「PV 밖」, 판정 불가는 기울인 「모름」, 소속을
 *  받지 못한 행(pv null -- 옛·부분 응답)은 「—」. 두 줄 다 칸 상한에서 말줄임(전체는 title·상세 시트) -- 배지·「외 n개」는 줄지 않는다. */
export function PvName({ pv, multiCluster, tokens = [] }: {
  pv: UsagePv | null; multiCluster: boolean; tokens?: readonly string[];
}) {
  if (pv === null) return <span className="text-muted">—</span>;
  if (pv.source === "outside") return <span className="whitespace-nowrap text-muted" title={PV_OUTSIDE_TITLE}>PV 밖</span>;
  if (pv.source === "unknown") return <span className="italic text-muted" title={PV_UNKNOWN_TITLE}>모름</span>;
  const v = shownVolume(pv.volumes, tokens);
  const rest = pv.volumes.length - 1;
  return (
    <div className="max-w-[10rem] sm:max-w-[14rem]" title={pvTitle(pv)}>
      <div className="flex min-w-0 items-center gap-1 whitespace-nowrap">
        <span className="min-w-0 truncate text-ink"><Highlight text={v.pvc} tokens={tokens} /></span>
        {rest > 0 && <span className="shrink-0 text-xs text-muted">{`외 ${rest}개`}</span>}
        {pv.source === "recorded" && <span className="shrink-0"><Tag title={PV_RECORDED_TITLE}>기록</Tag></span>}
      </div>
      <div className="truncate text-xs text-muted">
        {`${nsLabel(v.namespace, v.storage_class)}${multiCluster ? ` · ${v.cluster}` : ""}`}</div>
    </div>
  );
}

// 상세 시트의 소속 글자 버튼(2026-10-29) -- 누르면 그 값으로 목록을 거르고 시트를 닫는다(호출측 onFilter). 글자 링크 모양이지만
// 이동이 아니라 동작이라 버튼이다.
const PICK = "rounded text-left text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent "
  + "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
export const PV_PICK_HINT = "누르면 이 PV 로 경로 목록을 거릅니다(상세가 닫힙니다)";
export const USER_PICK_HINT = "누르면 이 권한 사용자로 경로 목록을 거릅니다(상세가 닫힙니다)";

/** PV 탐색 화면(VolumesPage)이 읽는 쿼리 -- 클러스터·namespace 는 정확히, PV 이름은 검색칸(q). */
export function volumesHref(v: UsagePvVolume): string {
  const p = new URLSearchParams({ cluster: v.cluster, namespace: v.namespace, q: v.pvc });
  return `/admin/k8s/volumes?${p.toString()}`;
}

/** 상세 머리: PV 소속(정체 전부) + 권한 사용자(지금 RoleBinding -- 면제 운영자의 전체 접근은 없다). onFilter 가 있으면(상세
 *  시트, 2026-10-29) PV 이름·사용자 이름이 그 필터를 거는 버튼이고, 지금 인벤토리의 PV 옆에 「PV 탐색에서 보기」 링크가 붙는다
 *  (기록뿐인 PV 는 PV 탐색에 없다). PV 를 고르면 그 정체(클러스터·namespace·PV 이름 -- 이름은 namespace 마다만 유일)를 함께 싣는다. */
export function PvHead({ pv, multiCluster, onFilter }: {
  pv: UsagePv; multiCluster: boolean; onFilter?: (a: UsageAttrFilter) => void;
}) {
  return (
    <dl aria-label="PV 소속" className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
      <dt className="text-muted">PV 소속</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere]">
        {pv.source === "outside" ? <span title={PV_OUTSIDE_TITLE}>PV 밖 — 지금 어떤 PV 루트에도 속하지 않는 경로</span>
          : pv.source === "unknown" ? <span className="italic" title={PV_UNKNOWN_TITLE}>PV 모름</span>
          : (<>
            {pv.volumes.map((v) => (
              <span key={`${v.cluster}/${v.namespace}/${v.pvc}`} className="block">
                {onFilter === undefined ? <span title={volumeTitle(v)}>{pvVolumeText(v, multiCluster)}</span> : (
                  <button type="button" className={PICK} title={`${volumeTitle(v)}\n${PV_PICK_HINT}`}
                          onClick={() => onFilter({ cluster: v.cluster, namespace: v.namespace, pvc: v.pvc })}>
                    {pvVolumeText(v, multiCluster)}</button>
                )}
                {onFilter !== undefined && pv.source === "inventory" && (<>{" "}
                  <Link to={volumesHref(v)}
                        className="ml-1 inline-flex items-center gap-1 whitespace-nowrap rounded text-xs text-accent
                                   hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                    <ExternalLink aria-hidden className="h-3 w-3" />PV 탐색에서 보기
                    {pv.volumes.length > 1 && <span className="sr-only">{` (${v.pvc})`}</span>}
                  </Link>
                </>)}
              </span>))}
            {pv.source === "recorded" && <Tag title={PV_RECORDED_TITLE}>기록</Tag>}
          </>)}
      </dd>
      {pvInside(pv) && (<>
        <dt className="text-muted">권한 사용자</dt>
        <dd className="min-w-0 [overflow-wrap:anywhere]">
          {pv.users.length === 0 ? "없음 — 관리자·특권 운영자만 접근"
            : onFilter === undefined ? pv.users.join(", ")
            : pv.users.map((u, i) => (
              <span key={u}>{i > 0 && ", "}
                <button type="button" className={PICK} title={USER_PICK_HINT}
                        onClick={() => onFilter({ user: u })}>{u}</button>
              </span>))}
        </dd>
      </>)}
    </dl>
  );
}

// ---- 화면 공용(2026-10-29 하위 메뉴 -- 경로별·스토리지별이 같은 원천 셋을 쓴다) ----

/** 스토리지 원천(요약·대체 원천·미등록 목록)의 알림 줄 -- 각 화면의 알림 자리(탭 바로 아래)에 그대로 놓는다. 요약을 못 받아도
 *  집합은 대체 원천(scan 기록 이름)으로 그려진다 -- 그 동안엔 흐린 한 줄로 「건수 모름」만 말한다(배포 중 옛 API 는 이 경로에 SPA
 *  index.html 을 200 으로 줘서, 원문 오류가 「Unexpected token '<'」 같은 파서 문장이었다). 대체 원천까지 실패하면 그때 오류다.
 *  받아 둔 요약이 있는데 재조회만 실패했으면 그 사실만. */
export function sourceNotices(src: ReturnType<typeof useStorageTabs>): React.ReactNode[] {
  const { summaryMissing, summaryQ, scanQ, unregQ } = src;
  return [
    summaryMissing && !scanQ.isError && (
      <p key="src-count" className="flex items-center gap-1.5 text-sm text-muted">
        <Info aria-hidden className="h-4 w-4 shrink-0" />스토리지별 대상 수를 불러오지 못해 건수 없이 표시합니다</p>
    ),
    summaryMissing && scanQ.isError && (
      <p key="src-scan" className="text-sm text-bad">
        {`스토리지별 요약을 불러오지 못했습니다 — ${(scanQ.error as ApiError).message}`}</p>
    ),
    summaryQ.isError && !summaryMissing && (
      <p key="src-refresh" className="text-sm text-bad">
        {`스토리지별 요약을 새로 고치지 못했습니다 — ${(summaryQ.error as ApiError).message}`}</p>
    ),
    unregQ.isError && (
      <p key="src-unreg" className="text-sm text-bad">
        {`미등록 스토리지 목록을 ${unregQ.data === undefined ? "불러오지" : "새로 고치지"} 못했습니다 — `
          + (unregQ.error as ApiError).message}</p>
    ),
  ].filter((x): x is JSX.Element => x !== false);
}

/** 등록된 스토리지도 기록도 없다(경로별·스토리지별 공용 빈 상태). */
export function NoStorages() {
  return (
    <Card>
      <div className="flex flex-col items-center gap-2 py-8 text-center">
        <span aria-hidden className="grid h-10 w-10 place-items-center rounded-full bg-panel text-muted">
          <HardDrive className="h-5 w-5" /></span>
        <p className="text-sm font-medium text-ink">등록된 스토리지가 없습니다</p>
        <p className="text-xs text-muted">스토리지를 등록하고 scan 을 실행하면 여기에 스토리지별 사용량이 쌓입니다.</p>
        <Link to="/admin/storages" className="mt-1 text-sm text-accent hover:underline">스토리지 화면으로</Link>
      </div>
    </Card>
  );
}
