import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { StatusPill } from "../../components/ui/StatusPill";
import { Table } from "../../components/ui/Table";
import { reasonText } from "../../lib/api";
import { kstStampOrDash } from "../../lib/datetime";
import { useMe } from "../auth/useAuth";
import { useSearchField } from "../jobs/searchField";
import { K8sHeader } from "./K8sTabs";
import { ACCESS_LABEL, TERMS, nsLabel, phaseVariant, pvJobLink } from "./k8sTerms";
import { bindingNameFor } from "./pvLocation";
import { DefList, DefRow, FreshnessLine, Mono, Tag } from "./parts";
import { VOLUMES_LIMIT, useK8sVolumes } from "./useK8s";
import type { VolumeRow, VolumesQuery } from "./types";

// PV 탐색(설계 §7): 동기화된 PVC 전체(관리자 응답 — 권한 사용자 포함)를 검색·필터하고 상세에서 실제 경로·권한 사용자를 본다.
// 검색어·권한 사용자 칸은 「전체 작업」 검색칸(useSearchField — 300ms 디바운스·한글 조합 처리)을 그대로 쓰고 필터는 서버가 건다.
// 주소 쿼리(?q=·?user=·?cluster=·?namespace=·?storage_class=)로 들어오면 그 조건으로 시작한다(사용자 권한 화면의 「PV 보기」).

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

const SEARCH_FIELD = "w-64 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm";
const SELECT_FIELD = "max-w-[14rem] rounded-lg border border-line bg-surface px-2 py-1.5 text-sm";

function rowKey(v: VolumeRow): string { return `${v.cluster}/${v.namespace}/${v.pvc}`; }

function PathCell({ v }: { v: VolumeRow }) {
  if (v.abs_root) return <Mono>{v.abs_root}</Mono>;
  if (v.reason) return <span className={v.selectable ? "text-ink/70" : "text-bad"}>{reasonText(v.reason)}</span>;
  return <span className="text-ink/70">—</span>;
}

function UsersCell({ users }: { users: string[] | undefined }) {
  if (users === undefined) return <span className="text-ink/70">—</span>;
  if (users.length === 0) return <span className="text-ink/70">없음</span>;
  const head = users.slice(0, 3);
  return (
    <div className="flex min-w-0 flex-wrap gap-1">
      {head.map((u) => <Tag key={u}>{u}</Tag>)}
      {users.length > head.length && <span className="text-xs text-ink/70">{`외 ${users.length - head.length}명`}</span>}
    </div>
  );
}

/** 이 관리자가 이 PV 로 단일 작업을 낼 수 있나(2026-10-26 -- 권한 밖 PV 의 「이 PV로 단일 작업」이 제출 화면의 막다른 길이던 것).
 *  전체 PV 권한(unrestricted)이면 늘, 아니면 그 namespace 에 자기 맵핑(users)이 있을 때만. 모르면(users·me 없음) 열어 둔다 --
 *  제출 화면·서버가 다시 본다(모름 ≠ 없음). */
export function canUsePv(v: Pick<VolumeRow, "users">, me: { actor?: string; unrestricted?: boolean } | undefined): boolean {
  if (me === undefined || me.unrestricted === true) return true;
  if (v.users === undefined || me.actor === undefined) return true;
  return v.users.includes(me.actor);
}

function VolumeDetail({ v }: { v: VolumeRow }) {
  const [open, setOpen] = useState(false);
  const me = useMe();
  const usable = canUsePv(v, me.data);
  return (
    <Dialog open={open} onOpenChange={setOpen} size="lg" title={`${TERMS.pvc} ${v.pvc}`}
            trigger={<Button variant="ghost" className="whitespace-nowrap px-2.5 py-1 text-xs">상세</Button>}>
      <div className="space-y-4 text-sm">
        <DefList>
          <DefRow label={TERMS.cluster}>{v.cluster}</DefRow>
          <DefRow label="namespace (StorageClass)">{nsLabel(v.namespace, v.storage_class)}</DefRow>
          <DefRow label={TERMS.pvc}><span className="font-medium">{v.pvc}</span></DefRow>
          <DefRow label={`${TERMS.volume}(경로용)`}>{v.volume ? <Mono>{v.volume}</Mono> : <span className="text-ink/70">아직 없음(바인딩 전)</span>}</DefRow>
          <DefRow label="상태">
            <StatusPill state={v.phase ?? "모름"} variant={phaseVariant(v.phase)} />
            <span className="ml-2 text-ink/80">{[v.capacity, (v.access_modes ?? []).join(", ")].filter(Boolean).join(" · ") || ""}</span>
          </DefRow>
          <DefRow label="만든 시각">{kstStampOrDash(v.created_at)}</DefRow>
          <DefRow label="스토리지">{v.storage_name ?? <span className="text-ink/70">매핑 없음</span>}</DefRow>
          <DefRow label="관리 디렉토리 기준">{v.root !== null ? <Mono>{v.root === "" ? "(관리 디렉토리 자체)" : v.root}</Mono> : <span className="text-ink/70">—</span>}</DefRow>
          <DefRow label="실제 경로">
            {v.abs_root ? <Mono className="font-semibold">{v.abs_root}</Mono>
              : <span className="text-bad">{v.reason ? reasonText(v.reason) : "경로를 정할 수 없습니다"}</span>}
          </DefRow>
        </DefList>

        <div>
          <h3 className="mb-1.5 text-sm font-semibold">권한 사용자</h3>
          {v.users === undefined ? <p className="text-ink/70">이 응답에는 권한 사용자 정보가 없습니다</p>
            : v.users.length === 0 ? (
              <p className="text-ink/70">
                {`이 namespace 에 <ID>-clusterrole-admin|edit·namespaceAdmin(소유자) 바인딩이 없습니다 — 일반 사용자는 이 PV 를 고를 수 없습니다(전체 PV 권한 관리자만).`}
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {v.users.map((u) => (
                  <Link key={u} to={`/admin/k8s/access?q=${encodeURIComponent(u)}`}
                        className="rounded-md bg-panel px-2 py-0.5 text-xs text-accent hover:underline">{u}</Link>
                ))}
              </div>
            )}
          <p className="mt-1.5 text-xs text-ink/70">namespace 의 admin·edit 바인딩은 그 namespace 의 모든 PV 에 읽기·쓰기입니다.</p>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3">
          {!v.selectable && <span className="mr-auto text-xs text-bad">{v.reason ? reasonText(v.reason) : "지금은 작업 대상으로 고를 수 없습니다"}</span>}
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          {v.selectable && !usable && (
            <span className="mr-auto text-xs text-attn">
              {`이 PV 에 대한 권한이 없습니다 — ${v.namespace} 에 ${bindingNameFor(me.data?.actor ?? "<ID>")} 또는 namespaceAdmin(소유자)이 필요합니다`}
            </span>
          )}
          {v.selectable && usable ? (
            <Link to={pvJobLink(v.cluster, v.namespace, v.pvc)}
                  className="inline-flex items-center justify-center whitespace-nowrap rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accenthover">
              이 PV로 단일 작업
            </Link>
          ) : (
            <Button disabled title={v.selectable ? "이 PV 에 대한 권한이 없습니다" : "고를 수 없는 PV 입니다"}>이 PV로 단일 작업</Button>
          )}
        </div>
      </div>
    </Dialog>
  );
}

export function VolumesPage() {
  const [params] = useSearchParams();
  const search = useSearchField(params.get("q") ?? "");
  const userSearch = useSearchField(params.get("user") ?? "");
  const [cluster, setCluster] = useState(params.get("cluster") ?? "");
  const [namespace, setNamespace] = useState(params.get("namespace") ?? "");
  const [storageClass, setStorageClass] = useState(params.get("storage_class") ?? "");
  const query: VolumesQuery = useMemo(() => ({
    cluster, namespace, storage_class: storageClass, q: search.applied, user: userSearch.applied,
  }), [cluster, namespace, storageClass, search.applied, userSearch.applied]);
  const q = useK8sVolumes(query);
  const data = q.data;
  const rows = useMemo(() => (Array.isArray(data?.volumes) ? data!.volumes : []), [data]);
  const clusters = Array.isArray(data?.clusters) ? data!.clusters : [];

  // namespace·StorageClass 선택지는 지금까지 본 행에서 모은다(필터를 걸어도 선택지가 사라지지 않게 누적).
  const [seenNs, setSeenNs] = useState<string[]>([]);
  const [seenSc, setSeenSc] = useState<string[]>([]);
  useEffect(() => {
    const merge = (prev: string[], add: (string | null)[]) => {
      const next = new Set(prev);
      for (const a of add) if (a) next.add(a);
      return next.size === prev.length ? prev : [...next].sort();
    };
    setSeenNs((p) => merge(p, rows.map((r) => r.namespace).concat(namespace)));
    setSeenSc((p) => merge(p, rows.map((r) => r.storage_class).concat(storageClass)));
  }, [rows, namespace, storageClass]);

  const filtered = cluster !== "" || namespace !== "" || storageClass !== "" || search.applied !== "" || userSearch.applied !== "";
  const noneSynced = clusters.length > 0 && clusters.every((c) => c.synced_at === null);

  return (
    <section className="space-y-4">
      <K8sHeader title="PV 탐색"
                 description="동기화된 PV(PVC)를 찾아 실제 경로와 권한 사용자를 확인합니다. 화면의 「PV 이름」은 PVC 이름, 「볼륨」은 경로를 만드는 PV 이름(VOLUME 열)입니다." />
      <FreshnessLine clusters={clusters} />
      <div className="flex flex-wrap items-center gap-2">
        <input type="search" aria-label="PV 검색" className={SEARCH_FIELD} placeholder="PV 이름·볼륨·namespace(일부)"
               {...search.bind} />
        <select aria-label="클러스터 필터" className={SELECT_FIELD} value={cluster} onChange={(e) => setCluster(e.target.value)}>
          <option value="">클러스터 전체</option>
          {clusters.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
        <select aria-label="namespace 필터" className={SELECT_FIELD} value={namespace} onChange={(e) => setNamespace(e.target.value)}>
          <option value="">namespace 전체</option>
          {seenNs.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <select aria-label="StorageClass 필터" className={SELECT_FIELD} value={storageClass}
                onChange={(e) => setStorageClass(e.target.value)}>
          <option value="">StorageClass 전체</option>
          {seenSc.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <input type="search" aria-label="권한 사용자 필터" className="w-40 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm"
               placeholder="권한 사용자 ID" {...userSearch.bind} />
        <span className="ml-auto text-xs text-ink/70 tabular-nums">
          {q.isFetching && !q.isLoading ? "새로 읽는 중…" : `${rows.length}건`}
        </span>
      </div>

      {q.isError && !data ? <p role="alert" className="text-sm text-bad">{`PV 목록을 불러오지 못했습니다 — ${errText(q.error)}`}</p> : (
        <Card>
          {data?.truncated && (
            <p className="mb-3 rounded-lg bg-attnbg/60 px-3 py-2 text-sm text-attn">
              {`결과가 많아 처음 ${VOLUMES_LIMIT}건만 보입니다 — 검색어나 필터로 좁히세요.`}
            </p>
          )}
          {q.isError && <p className="mb-3 text-xs text-bad">{`다시 읽지 못했습니다(표시는 직전 값): ${errText(q.error)}`}</p>}
          <Table>
            <thead>
              <tr className="whitespace-nowrap text-ink/70">
                <th className="py-2 pr-3">{TERMS.pvc} · {TERMS.volume}</th><th className="pr-3">namespace (StorageClass)</th>
                <th className="pr-3">{TERMS.cluster}</th><th className="pr-3">상태</th><th className="pr-3">실제 경로</th>
                <th className="pr-3">권한 사용자</th><th>작업</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((v) => (
                <tr key={rowKey(v)} className={`border-t border-line align-top ${v.selectable ? "" : "bg-panel/50"}`}>
                  <td className="py-2 pr-3">
                    <div className="min-w-[10rem]">
                      <span className="font-medium [overflow-wrap:anywhere]">{v.pvc}</span>
                      {v.volume ? <Mono className="block text-ink/70">{v.volume}</Mono>
                        : <span className="block text-xs text-ink/70">볼륨 없음(바인딩 전)</span>}
                    </div>
                  </td>
                  <td className="py-2 pr-3"><span className="[overflow-wrap:anywhere]">{nsLabel(v.namespace, v.storage_class)}</span></td>
                  <td className="whitespace-nowrap py-2 pr-3">{v.cluster}</td>
                  <td className="py-2 pr-3">
                    <div className="whitespace-nowrap">
                      <StatusPill state={v.phase ?? "모름"} variant={phaseVariant(v.phase)} />
                      {v.capacity && <span className="ml-1.5 text-xs text-ink/80 tabular-nums">{v.capacity}</span>}
                    </div>
                  </td>
                  {/* 최소 폭: 좁은 화면(375)에서 이 칸이 글자 몇 개 폭으로 짓눌려 행이 화면 몇 개 높이로 늘었다(실 Chrome
                      확인) -- 넘치는 만큼은 표의 가로 스크롤(Table)에 맡긴다. */}
                  <td className="max-w-[24rem] py-2 pr-3"><div className="min-w-[15rem]"><PathCell v={v} /></div></td>
                  <td className="py-2 pr-3"><UsersCell users={v.users} /></td>
                  <td className="py-2"><VolumeDetail v={v} /></td>
                </tr>
              ))}
            </tbody>
          </Table>
          {q.isLoading ? <p className="pt-3 text-ink/70">불러오는 중…</p>
            : rows.length > 0 ? null
            : clusters.length === 0 ? <p className="pt-3 text-ink/70">등록된 클러스터가 없습니다 — 「개요」에서 클러스터를 등록하세요.</p>
            : noneSynced ? <p className="pt-3 text-ink/70">아직 동기화에 성공한 클러스터가 없습니다 — PV 목록은 모름입니다(없다는 뜻이 아닙니다).</p>
            : filtered ? <p className="pt-3 text-ink/70">조건에 맞는 PV 가 없습니다</p>
            : <p className="pt-3 text-ink/70">동기화된 PVC 가 0개입니다</p>}
          <p className="mt-3 text-xs text-ink/70">
            {`권한: namespace 의 <ID>-clusterrole-admin|edit 바인딩 또는 소유자 namespaceAdmin(이메일 로컬파트 = 아이디)(${Object.keys(ACCESS_LABEL).join("·")})이면 그 namespace 의 모든 PV 에 읽기·쓰기. Bound 가 아닌 PV·StorageClass 매핑이 없는 PV 는 작업 대상으로 고를 수 없습니다.`}
          </p>
        </Card>
      )}
    </section>
  );
}
