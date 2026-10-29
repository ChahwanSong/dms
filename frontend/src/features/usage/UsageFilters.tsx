import { Button } from "../../components/ui/Button";
import { HelpTip } from "../../components/ui/HelpTip";
import type { UsageAttrFilter, UsageFacets, UsageScope } from "../../lib/types";
import { TERMS, nsLabel } from "../k8s/k8sTerms";
import { SELECT_FIELD } from "../k8s/parts";
import { SCOPES, cleanAttr, hasAttr, hasPvAxis } from "./useUsage";

// 사용량 필터 줄(2026-10-29 사용자 요청 「사용자·PV 이름·namespace 등으로 필터」). 패널 툴바(경로 검색 · 건수 · CSV) 아래 둘째 줄.
// 순서: 클러스터 → namespace → PV 이름 → StorageClass → 구분 → 권한 사용자 → 요청자 → 「필터 지우기」. 네이티브 select(첫 옵션
// 「X 전체」 -- PV 탐색 화면과 같은 관용·같은 SELECT_FIELD), 라벨은 k8sTerms 에서만(화면 「PV 이름」 = PVC).
//
// 선택지는 서버 facets(옵션마다 대상 수 -- 「… · N개」). 위계: 클러스터를 바꾸거나 지우면 namespace·PV 이름이, namespace 를
// 지우면 PV 이름이 풀리고, 아래 단계의 선택지는 위 단계 선택으로 좁힌다(namespace 는 클러스터마다만, PVC 이름은 namespace 마다만
// 유일하다 -- 값은 늘 짝으로 싣는다). 구분 「PV 밖」·「모름」은 PV 축(클러스터·namespace·PV 이름·StorageClass·권한 사용자)과
// 함께 걸 수 없어(늘 0건) 서로 푼다.
//
// 보일 조건: 선택지가 둘 이상이거나 그 필터가 지금 켜져 있을 때 -- 하나뿐인 선택지는 걸러도 같은 목록이다. PV 안 대상이 0 이고
// PV 축이 꺼져 있으면 PV 축 전부를 숨긴다(filesystem·미등록 탭은 요청자만). 딥링크·탭 이동으로 온 값이 이 스토리지의 선택지에
// 없으면 「{값} (이 스토리지에 없음)」 옵션으로 보인다 -- 그래야 그 칸에서 풀 수 있다. 짝이 덜 찬 값(클러스터 없이 namespace,
// namespace 없이 PV 이름 -- 서버는 빈 축을 「모두」로 본다)은 비어 있는 축을 말하고(「(모든 클러스터)」·「(모든 namespace)」·
// 「(모든 클러스터·namespace)」), 이름은 이 스토리지에 있는데 고른 클러스터·namespace 아래에만 없으면 「(이 클러스터에 없음)」·
// 「(이 namespace 에 없음)」이다(2026-10-29 리뷰 -- 예전엔 늘 「모든 namespace」·「이 스토리지에 없음」이라 사실과 달랐다).
// facets 를 못 받으면(실패·로딩) 줄을 그리지 않고, 필터가 켜져 있으면 「필터 지우기」만 남긴다(UsageAnalysis 가 URL 에서 읽은
// 필터는 그대로 걸린다).
//
// 판정 창(2026-10-29 리뷰): 대상이 창(window_limit)을 넘는 스토리지면 서버는 PV 선택지·수(클러스터·namespace·PV 이름·
// StorageClass·구분·권한 사용자)를 최근 스캔된 창 안에서만 센다(window_truncated -- 목록의 PV 필터도 같은 창이라 옵션 수 = 그
// 옵션을 고른 목록의 대상 수다). 요청자는 스토리지 전체 기준이다. 그 사실을 줄 끝 한 줄로 말하고, 선택지에 없는 PV 값은
// 「(최근 N개 안에 없음)」이라 한다(창 밖에는 있을 수 있다).
//
// 375 폭: select 는 두 개씩 한 줄(basis 50% - gap 절반, min-w-0 -- 긴 옵션 글자가 칸을 밀지 않게), sm 부터 내용 폭(상한 14rem).
// 포커스: 줄(group)은 tabIndex -1 이다 -- 「필터 지우기」나 값 때문에만 보이던 칸이 고르는 순간 사라지면 StoragePanel 이 포커스를
// 여기로 옮긴다(groupRef).

export const FILTERS_HELP = "PV 필터는 각 경로가 지금 어느 PV(=PVC) 아래에 있는지로 거릅니다. 지금 인벤토리에 없는 PV 는 그 경로를 "
  + "PV 주소로 scan 한 기록으로 봅니다. 권한 사용자는 지금 RoleBinding 으로 그 namespace 에 접근하는 사용자이며, 관리자·특권 "
  + "운영자의 전체 접근은 포함하지 않습니다. 요청자는 그 경로를 scan 한 적이 있는 제출 계정입니다(작업 목록의 요청자와 같은 기준). "
  + "어느 필터든 경로 단위라 스캔 횟수·최근 scan·이력은 전체 기록입니다.";

export const SCOPE_LABEL: Record<UsageScope, string> = { pv: "PV 안", outside: "PV 밖(직접 경로)", unknown: "모름" };

/** 적용 필터 한 줄 요약(필터 합계 카드). 고정 순서 = 필터 줄 순서. */
export function attrSummary(a: UsageAttrFilter): string {
  return [
    a.cluster && `${TERMS.cluster} ${a.cluster}`,
    a.namespace && `${TERMS.namespace} ${a.namespace}`,
    a.pvc && `${TERMS.pvc} ${a.pvc}`,
    a.storage_class && `${TERMS.storageClass} ${a.storage_class}`,
    a.scope && `구분 ${SCOPE_LABEL[a.scope]}`,
    a.user && `권한 사용자 ${a.user}`,
    a.requester && `요청자 ${a.requester}`,
  ].filter(Boolean).join(" · ");
}

const FIELD = `${SELECT_FIELD} min-w-0 basis-[calc(50%-0.25rem)] sm:basis-auto`;
const n = (x: number) => x.toLocaleString("ko-KR");
const counted = (label: string, c: number) => `${label} · ${n(typeof c === "number" && Number.isFinite(c) ? c : 0)}개`;
const nonEmpty = (x: unknown): x is string => typeof x === "string" && x !== "";
// 짝 값(클러스터·namespace·PVC)의 옵션 값 -- 이름에 어떤 글자가 와도 갈라지지 않게 JSON 배열로.
const key = (...parts: (string | null)[]) => JSON.stringify(parts);
function parse(v: string): (string | null)[] {
  try {
    const x: unknown = JSON.parse(v);
    return Array.isArray(x) ? x.map((p) => (typeof p === "string" && p !== "" ? p : null)) : [];
  } catch {
    return [];
  }
}

interface Opt { value: string; label: string }

function FilterSelect({ label, all, value, options, truncated, onChange }: {
  label: string; all: string; value: string; options: Opt[]; truncated: boolean; onChange: (v: string) => void;
}) {
  // 좁은 칸에서 잘린 선택 글자는 title 로 전부(마우스) -- 보조기기는 select 의 값 글자를 그대로 읽는다.
  const shown = options.find((o) => o.value === value)?.label;
  return (
    <select aria-label={label} className={FIELD} value={value} title={shown}
            onChange={(e) => onChange(e.target.value)}>
      <option value="">{all}</option>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      {/* 서버가 선택지 목록을 상한에서 잘랐다 -- 없는 이름이 「없다」는 뜻이 아님을 목록 안에서 말한다. */}
      {truncated && <option disabled value="__truncated__">목록이 길어 일부만 보입니다</option>}
    </select>
  );
}

export function UsageFilters({ attr, facets, onChange, groupRef }: {
  attr: UsageAttrFilter; facets: UsageFacets | undefined; onChange: (a: UsageAttrFilter) => void;
  groupRef?: React.Ref<HTMLDivElement>;
}) {
  const active = hasAttr(attr);
  const clear = active && (
    <Button type="button" variant="ghost" className="whitespace-nowrap py-1.5" onClick={() => onChange({})}>
      필터 지우기
    </Button>
  );
  if (facets === undefined) {
    return active ? <div className="mt-2 flex flex-wrap items-center gap-2">{clear}</div> : null;
  }

  // PV 축을 걸면 「PV 밖」·「모름」 구분은 푼다(함께 걸면 늘 0건 -- 서버 결합 규칙).
  const commit = (next: UsageAttrFilter) => {
    const c = cleanAttr(next);
    if (hasPvAxis(c) && (c.scope === "outside" || c.scope === "unknown")) delete c.scope;
    onChange(c);
  };
  const cut = facets.lists_truncated ?? {};
  const multi = facets.clusters.length >= 2;
  const pvKnown = facets.scope.inventory + facets.scope.recorded > 0;
  const showPv = pvKnown || hasPvAxis(attr);
  const visible = (opts: Opt[], on: boolean) => on || opts.length >= 2;
  // PV 선택지가 최근 스캔된 창 안의 것뿐인가(머리 주석 「판정 창」). 요청자 선택지는 스토리지 전체라 해당 없다.
  const windowed = facets.window_truncated === true && typeof facets.window_limit === "number";
  const missing = (v: string) => `${v} (이 스토리지에 없음)`;
  const pvMissing = (v: string) => (windowed ? `${v} (최근 ${n(facets.window_limit)}개 안에 없음)` : missing(v));

  // 클러스터
  const clusterOpts: Opt[] = facets.clusters.filter((c) => nonEmpty(c.cluster))
    .map((c) => ({ value: c.cluster, label: counted(c.cluster, c.target_count) }));
  if (attr.cluster && !clusterOpts.some((o) => o.value === attr.cluster)) {
    clusterOpts.push({ value: attr.cluster, label: pvMissing(attr.cluster) });
  }

  // namespace -- (클러스터, namespace) 짝. 클러스터를 골랐으면 그 클러스터의 것만.
  const nsOpts: Opt[] = facets.namespaces
    .filter((x) => nonEmpty(x.cluster) && nonEmpty(x.namespace) && (!attr.cluster || x.cluster === attr.cluster))
    .map((x) => ({ value: key(x.cluster, x.namespace),
                   label: counted(multi ? `${x.namespace} · ${x.cluster}` : x.namespace, x.target_count) }));
  const nsValue = attr.namespace ? key(attr.cluster ?? null, attr.namespace) : "";
  if (nsValue !== "" && !nsOpts.some((o) => o.value === nsValue)) {
    // 클러스터 없이 namespace 만 온 딥링크(서버: 모든 클러스터의 그 이름) -- 있으면 그렇게 말한다. 클러스터를 골랐는데 그
    // 이름이 다른 클러스터에만 있으면 「이 클러스터에 없음」, 이 스토리지 어디에도 없으면 없다고.
    const anywhere = facets.namespaces.some((x) => x.namespace === attr.namespace);
    nsOpts.push({ value: nsValue,
                  label: !anywhere ? pvMissing(attr.namespace!)
                    : !attr.cluster ? `${attr.namespace} (모든 ${TERMS.cluster})`
                    : `${attr.namespace} (이 ${TERMS.cluster}에 없음)` });
  }

  // PV 이름 -- (클러스터, namespace, PVC) 셋. 위 단계 선택으로 좁힌다.
  const pvcOpts: Opt[] = facets.pvcs
    .filter((x) => nonEmpty(x.cluster) && nonEmpty(x.namespace) && nonEmpty(x.pvc)
      && (!attr.cluster || x.cluster === attr.cluster) && (!attr.namespace || x.namespace === attr.namespace))
    .map((x) => ({ value: key(x.cluster, x.namespace, x.pvc),
                   label: counted(`${x.pvc} — ${nsLabel(x.namespace, x.storage_class)}${multi ? ` · ${x.cluster}` : ""}`,
                                  x.target_count) }));
  const pvcValue = attr.pvc ? key(attr.cluster ?? null, attr.namespace ?? null, attr.pvc) : "";
  if (pvcValue !== "" && !pvcOpts.some((o) => o.value === pvcValue)) {
    const same = facets.pvcs.filter((x) => x.pvc === attr.pvc);
    // 짝이 덜 찬 값: 고른 축은 맞고 빈 축만 「모두」 -- 비어 있는 축을 말한다.
    const loose = (!attr.cluster || !attr.namespace) && same.some((x) =>
      (!attr.cluster || x.cluster === attr.cluster) && (!attr.namespace || x.namespace === attr.namespace));
    const all = [!attr.cluster && TERMS.cluster, !attr.namespace && TERMS.namespace].filter(Boolean).join("·");
    // 이름은 있는데 고른 짝 아래에만 없다: 고른 클러스터(없으면 아무 클러스터)에 그 이름이 있으면 namespace 가, 아니면 클러스터가 다르다.
    const inCluster = same.some((x) => !attr.cluster || x.cluster === attr.cluster);
    pvcOpts.push({ value: pvcValue,
                   label: same.length === 0 ? pvMissing(attr.pvc!)
                     : loose ? `${attr.pvc} (모든 ${all})`
                     : attr.namespace && inCluster ? `${attr.pvc} (이 ${TERMS.namespace} 에 없음)`
                     : `${attr.pvc} (이 ${TERMS.cluster}에 없음)` });
  }

  const flat = <T,>(rows: T[], name: (r: T) => unknown, count: (r: T) => number, current: string | undefined,
                    gone: (v: string) => string): Opt[] => {
    const out: Opt[] = rows.flatMap((r) => {
      const v = name(r);
      return nonEmpty(v) ? [{ value: v, label: counted(v, count(r)) }] : [];
    });
    if (current && !out.some((o) => o.value === current)) out.push({ value: current, label: gone(current) });
    return out;
  };
  const scOpts = flat(facets.storage_classes, (r) => r.storage_class, (r) => r.target_count, attr.storage_class,
                      pvMissing);
  const userOpts = flat(facets.users, (r) => r.dms_user, (r) => r.target_count, attr.user, pvMissing);
  const requesterOpts = flat(facets.requesters, (r) => r.requester_id, (r) => r.target_count, attr.requester, missing);

  // 구분: 대상이 있는 범주만(지금 고른 것은 0 이어도 남긴다 -- 풀 수 있게).
  const scopeCount: Record<UsageScope, number> = {
    pv: facets.scope.inventory + facets.scope.recorded, outside: facets.scope.outside, unknown: facets.scope.unknown };
  const scopeOpts: Opt[] = SCOPES.filter((s) => scopeCount[s] > 0 || attr.scope === s)
    .map((s) => ({ value: s, label: counted(SCOPE_LABEL[s], scopeCount[s]) }));
  const showScope = attr.scope !== undefined || SCOPES.filter((s) => scopeCount[s] > 0).length >= 2;
  const showRequester = visible(requesterOpts, attr.requester !== undefined);

  const controls = [
    showPv && visible(clusterOpts, attr.cluster !== undefined) && (
      <FilterSelect key="cluster" label={`${TERMS.cluster} 필터`} all={`${TERMS.cluster} 전체`} value={attr.cluster ?? ""}
                    options={clusterOpts} truncated={cut.clusters === true}
                    onChange={(v) => commit({ ...attr, cluster: v, namespace: undefined, pvc: undefined })} />
    ),
    showPv && visible(nsOpts, attr.namespace !== undefined) && (
      <FilterSelect key="namespace" label={`${TERMS.namespace} 필터`} all={`${TERMS.namespace} 전체`} value={nsValue}
                    options={nsOpts} truncated={cut.namespaces === true}
                    onChange={(v) => {
                      if (v === "") { commit({ ...attr, namespace: undefined, pvc: undefined }); return; }
                      const [c, ns] = parse(v);
                      commit({ ...attr, cluster: c ?? undefined, namespace: ns ?? undefined, pvc: undefined });
                    }} />
    ),
    showPv && visible(pvcOpts, attr.pvc !== undefined) && (
      <FilterSelect key="pvc" label={`${TERMS.pvc} 필터`} all={`${TERMS.pvc} 전체`} value={pvcValue}
                    options={pvcOpts} truncated={cut.pvcs === true}
                    onChange={(v) => {
                      if (v === "") { commit({ ...attr, pvc: undefined }); return; }
                      const [c, ns, p] = parse(v);
                      commit({ ...attr, cluster: c ?? undefined, namespace: ns ?? undefined, pvc: p ?? undefined });
                    }} />
    ),
    showPv && visible(scOpts, attr.storage_class !== undefined) && (
      <FilterSelect key="sc" label={`${TERMS.storageClass} 필터`} all={`${TERMS.storageClass} 전체`}
                    value={attr.storage_class ?? ""} options={scOpts} truncated={cut.storage_classes === true}
                    onChange={(v) => commit({ ...attr, storage_class: v })} />
    ),
    showScope && (
      <FilterSelect key="scope" label="PV 구분 필터" all="구분 전체" value={attr.scope ?? ""} options={scopeOpts}
                    truncated={false}
                    onChange={(v) => (v === "outside" || v === "unknown"
                      // 「PV 밖」·「모름」은 PV 축을 푼다(요청자는 남긴다)
                      ? commit({ scope: v, requester: attr.requester })
                      : commit({ ...attr, scope: v === "pv" ? "pv" : undefined }))} />
    ),
    showPv && visible(userOpts, attr.user !== undefined) && (
      <FilterSelect key="user" label="권한 사용자 필터" all="권한 사용자 전체" value={attr.user ?? ""} options={userOpts}
                    truncated={cut.users === true} onChange={(v) => commit({ ...attr, user: v })} />
    ),
    showRequester && (
      <FilterSelect key="requester" label="요청자 필터" all="요청자 전체" value={attr.requester ?? ""}
                    options={requesterOpts} truncated={cut.requesters === true}
                    onChange={(v) => commit({ ...attr, requester: v })} />
    ),
  ].filter(Boolean);

  if (controls.length === 0 && !active) return null;
  // PV 칸(또는 구분)이 보일 때만 창 한 줄 -- 요청자만 남은 탭에는 해당이 없다.
  const pvShown = controls.length > (showRequester ? 1 : 0);
  return (
    <div ref={groupRef} tabIndex={-1} role="group" aria-label="사용량 필터"
         className="mt-2 flex flex-wrap items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-accent">
      {controls}
      {controls.length > 0 && <HelpTip label="필터 설명" head="필터" help={FILTERS_HELP} />}
      {clear}
      {windowed && pvShown && (
        <p className="basis-full text-xs text-muted break-keep">
          {`대상이 ${n(facets.window_limit)}개를 넘는 스토리지라 PV 선택지와 수는 최근 스캔된 ${n(facets.window_limit)}개 기준입니다`
            + "(요청자는 전체 기준)"}
        </p>
      )}
    </div>
  );
}
