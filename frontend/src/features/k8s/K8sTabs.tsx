import { useLayoutEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";

// k8s 연동의 하위 내비게이션(BuildTabs 선례). 사이드바 항목은 「k8s 연동」 하나(navigation.ts)이고 여섯 화면은 여기 탭으로
// 가른다 -- 사이드바에 여섯을 늘어놓으면 스토리지 그룹이 길어지고 「k8s 연동」이라는 한 가지 일이 여러 일처럼 읽힌다.
// 「클러스터」(2026-11-11 사용자 요청 「개요에서 클러스터를 다른 탭으로」)는 개요 바로 다음이다 -- 설정 순서(호출 노드 → 클러스터
// 등록 → StorageClass 매핑)와 같다. 개요에는 합계·호출 노드와 주기만 남는다.
// end 는 「개요」에만: /admin/k8s 는 나머지의 접두라 end 가 없으면 하위 화면에서도 개요가 함께 켜진다.
// nav 라벨은 이 하나뿐(고유) -- 화면의 다른 nav·구획에는 aria-label 을 달지 않는다(Playwright getByLabel 부분 일치 사고 예방).
export const K8S_OVERVIEW_PATH = "/admin/k8s";
export const K8S_CLUSTERS_PATH = "/admin/k8s/clusters";

/** 클러스터 카드의 앵커 — 개요의 클러스터 이름 링크(실패 사유가 카드의 「편집」을 시킨다)가 클러스터 탭의 그 카드로 바로 간다
 *  (ClustersPage 가 #id 대상 카드를 보이는 곳으로 옮기고 강조·포커스한다). 이름은 주소에 들어가 인코딩한다. */
export const clusterCardId = (name: string): string => `cluster-${name}`;
export const clusterCardHref = (name: string): string => `${K8S_CLUSTERS_PATH}#${encodeURIComponent(clusterCardId(name))}`;

export const K8S_TABS = [
  { to: K8S_OVERVIEW_PATH, label: "개요", end: true },
  { to: K8S_CLUSTERS_PATH, label: "클러스터", end: false },
  { to: "/admin/k8s/storageclasses", label: "StorageClass", end: false },
  { to: "/admin/k8s/volumes", label: "PV 탐색", end: false },
  { to: "/admin/k8s/access", label: "사용자 권한", end: false },
  { to: "/admin/k8s/history", label: "동기화 이력", end: false },
] as const;

const tabCls = ({ isActive }: { isActive: boolean }) =>
  `-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm leading-6 ${
    isActive ? "border-accent font-medium text-accent" : "border-transparent text-ink/70 hover:text-ink"}`;

/** 탭 줄이 넘칠 때 켜진 탭이 보이는 곳에 있게 탭 줄만 가로로 옮긴다. scrollIntoView 는 쓰지 않는다(페이지 세로 스크롤까지
 *  움직인다). offsetLeft 는 nav(relative = offsetParent) 기준이다. 이미 다 보이면 건드리지 않는다. */
function revealActiveTab(nav: HTMLElement | null): void {
  const a = nav?.querySelector<HTMLElement>('[aria-current="page"]');
  if (!nav || !a) return;
  const left = a.offsetLeft;
  const right = left + a.offsetWidth;
  if (left < nav.scrollLeft || right > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = Math.max(0, left - 16);
}

/** 현재 위치는 NavLink 의 aria-current="page" 가 알린다(색만으로 말하지 않는다). 좁은 폭에선 가로로 스크롤하고, 켜진 탭은 보이는
 *  곳으로 옮긴다 -- 탭이 여섯이 되며(2026-11-11 클러스터) 375·390 에서 「사용자 권한」·「동기화 이력」, 768(사이드바 포함)에서
 *  「동기화 이력」이 켜진 채 화면 밖에 있었다(리뷰 실측). 웹폰트가 늦게 들어와 탭 폭이 바뀌면 한 번 더 맞춘다. */
export function K8sTabs() {
  const ref = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  useLayoutEffect(() => {
    revealActiveTab(ref.current);
    let live = true;
    void document.fonts?.ready.then(() => { if (live) revealActiveTab(ref.current); });
    return () => { live = false; };
  }, [pathname]);
  return (
    <nav ref={ref} aria-label="k8s 연동 하위 메뉴" className="relative flex gap-1 overflow-x-auto border-b border-line">
      {K8S_TABS.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end} className={tabCls}>{t.label}</NavLink>
      ))}
    </nav>
  );
}

/** 하위 화면 공용 머리(h1 + 한 줄 설명 + 오른쪽 동작 + 탭). h1 은 화면에 하나 — 개요는 사이드바 라벨(「k8s 연동」), 하위 화면은
 *  탭·브레드크럼 라벨과 같은 문자열이다(빌드 이력 선례 — router.test 가 짝을 고정한다).
 *  글 칸은 flex-1 + 기준 폭 20rem: 넓은 화면에선 동작 버튼이 제목 오른쪽에 남고(스토리지·배치 화면과 같은 자리 — 긴 설명이 칸을
 *  최대 폭까지 밀어 버튼 두 개가 아래 줄로 떨어지던 것, 2026-11-11 클러스터 탭), 좁은 폭(390)에선 버튼이 설명 아래로 접힌다. */
export function K8sHeader({ title, description, actions }: {
  title: string; description: React.ReactNode; actions?: React.ReactNode;
}) {
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-80">
          <h1 className="text-2xl font-bold">{title}</h1>
          <p className="mt-1 max-w-3xl text-sm text-ink/70">{description}</p>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <K8sTabs />
    </>
  );
}
