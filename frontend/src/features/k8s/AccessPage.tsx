import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Segmented } from "../../components/ui/Segmented";
import { Table } from "../../components/ui/Table";
import { useSearchField } from "../jobs/searchField";
import { K8sHeader } from "./K8sTabs";
import { ACCESS_LABEL, bindingParts, subjectText, unrecognizedWhy } from "./k8sTerms";
import { FreshnessLine, Mono, Tag } from "./parts";
import { useK8sAccess } from "./useK8s";
import type { AccessGrant, AccessUser } from "./types";

// 사용자 권한(설계 §7·D5): RoleBinding 이름 `<ID>-clusterrole-admin|edit` + roleRef ClusterRole kubeflow-admin|kubeflow-edit 로
// 판정한 「DMS ID → namespace」 맵. 사용자별·namespace별 두 보기, 근거 바인딩 원문, 경고 둘(subject 불일치 — 이름에서 뽑은 ID 와
// 바인딩 subject 가 다름 / 계정 없음 — 같은 이름의 DMS 계정이 없어 그 사람은 로그인해도 권한을 못 씀), 인식하지 않은 바인딩은
// 접어서 보인다(권한 없음 — 왜 인식하지 않았는지 한 줄). 검색은 서버(?q=)가 걸고 주소에도 남긴다(공유·뒤로가기).

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

type View = "user" | "namespace";

function accountCreateLink(id: string): string {
  return `/admin/accounts?create=${encodeURIComponent(id)}`;
}

function AccessTag({ access }: { access: string }) {
  return <Tag tone={access === "admin" ? "info" : "neutral"} title="읽기·쓰기">{ACCESS_LABEL[access] ?? access}</Tag>;
}

function BindingCell({ g }: { g: AccessGrant }) {
  // 서버(routes_k8s.admin_access)는 binding 을 이름으로, 역할·subjects 를 grant 바로 아래에 싣는다 — 인식된 바인딩은 늘 ClusterRole.
  const b = bindingParts(typeof g.binding === "string"
    ? { name: g.binding, role_kind: "ClusterRole", role_name: g.role_name ?? null, subjects: g.subjects ?? [] }
    : g.binding);
  return (
    <div className="min-w-[14rem]">
      <Mono>{b.name}</Mono>
      {(b.role || b.subjects.length > 0) && (
        <span className="block text-xs text-ink/70 [overflow-wrap:anywhere]">
          {[b.role ? `→ ${b.role}` : null, b.subjects.length > 0 ? `subject ${b.subjects.join(", ")}` : null]
            .filter(Boolean).join(" · ")}
        </span>
      )}
      {g.subject_mismatch === true && (
        <span className="block text-xs text-attn">
          subject 불일치 — 바인딩 subject 의 이름·이메일이 이름에서 뽑은 ID 와 다릅니다. 실제 사람이 맞는지 확인하세요.
        </span>
      )}
    </div>
  );
}

function AccountCell({ u }: { u: AccessUser }) {
  if (u.account_exists) return <Tag tone="ok">계정 있음</Tag>;
  return (
    <div className="min-w-0 space-y-0.5">
      <Tag tone="warn">계정 없음</Tag>
      <Link to={accountCreateLink(u.dms_user)} className="block whitespace-nowrap text-xs text-accent hover:underline">
        계정 만들기
      </Link>
    </div>
  );
}

function ByUser({ users }: { users: AccessUser[] }) {
  return (
    <Table>
      <thead>
        <tr className="whitespace-nowrap text-ink/70">
          <th className="py-2 pr-3">DMS ID</th><th className="pr-3">계정</th><th className="pr-3">클러스터 · namespace</th>
          <th className="pr-3">권한</th><th className="pr-3">근거 바인딩</th><th className="text-right">PV</th>
        </tr>
      </thead>
      <tbody>
        {users.map((u) => {
          const grants = u.namespaces.length > 0 ? u.namespaces : [null];
          const span = grants.length;
          return (
            <Fragment key={u.dms_user}>
              {grants.map((g, i) => (
                <tr key={g ? `${g.cluster}/${g.namespace}/${bindingParts(g.binding).name}` : "none"}
                    className={`align-top ${i === 0 ? "border-t border-line" : ""}`}>
                  {i === 0 && (
                    <>
                      <td rowSpan={span} className="py-2 pr-3">
                        <span className="font-semibold [overflow-wrap:anywhere]">{u.dms_user}</span>
                      </td>
                      <td rowSpan={span} className="py-2 pr-3"><AccountCell u={u} /></td>
                    </>
                  )}
                  {g === null ? (
                    <td colSpan={3} className="py-2 pr-3 text-ink/70">인식된 바인딩 없음</td>
                  ) : (
                    <>
                      <td className="py-2 pr-3">
                        <span className="text-ink/70">{g.cluster}</span> · <span className="font-medium">{g.namespace}</span>
                      </td>
                      <td className="py-2 pr-3"><AccessTag access={g.access} /></td>
                      <td className="max-w-[26rem] py-2 pr-3"><BindingCell g={g} /></td>
                    </>
                  )}
                  {i === 0 && (
                    <td rowSpan={span} className="py-2 text-right">
                      <Link to={`/admin/k8s/volumes?user=${encodeURIComponent(u.dms_user)}`}
                            className="whitespace-nowrap tabular-nums text-accent hover:underline"
                            title="이 사용자가 쓸 수 있는 PV 보기">
                        {u.volume_count === null ? "보기" : `${u.volume_count}개`}
                      </Link>
                    </td>
                  )}
                </tr>
              ))}
            </Fragment>
          );
        })}
      </tbody>
    </Table>
  );
}

interface NsGroup {
  cluster: string; namespace: string;
  entries: { user: AccessUser; grant: AccessGrant }[];
}

/** 사용자별 응답을 namespace 별로 뒤집는다(클러스터·namespace 정렬, 그 안은 ID 정렬). */
export function pivotByNamespace(users: AccessUser[]): NsGroup[] {
  const map = new Map<string, NsGroup>();
  for (const u of users) {
    for (const g of u.namespaces) {
      const key = `${g.cluster}\u0000${g.namespace}`;
      let grp = map.get(key);
      if (!grp) { grp = { cluster: g.cluster, namespace: g.namespace, entries: [] }; map.set(key, grp); }
      grp.entries.push({ user: u, grant: g });
    }
  }
  const groups = [...map.values()];
  groups.sort((a, b) => a.cluster.localeCompare(b.cluster) || a.namespace.localeCompare(b.namespace));
  for (const g of groups) g.entries.sort((a, b) => a.user.dms_user.localeCompare(b.user.dms_user));
  return groups;
}

function ByNamespace({ users }: { users: AccessUser[] }) {
  const groups = useMemo(() => pivotByNamespace(users), [users]);
  return (
    <Table>
      <thead>
        <tr className="whitespace-nowrap text-ink/70">
          <th className="py-2 pr-3">클러스터 · namespace</th><th className="pr-3">DMS ID</th><th className="pr-3">권한</th>
          <th className="pr-3">근거 바인딩</th><th>계정</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((grp) => (
          <Fragment key={`${grp.cluster}/${grp.namespace}`}>
            {grp.entries.map(({ user, grant }, i) => (
              <tr key={`${user.dms_user}/${bindingParts(grant.binding).name}`}
                  className={`align-top ${i === 0 ? "border-t border-line" : ""}`}>
                {i === 0 && (
                  <td rowSpan={grp.entries.length} className="py-2 pr-3">
                    <span className="text-ink/70">{grp.cluster}</span> · <span className="font-semibold">{grp.namespace}</span>
                    <span className="block text-xs text-ink/70">{`사용자 ${new Set(grp.entries.map((e) => e.user.dms_user)).size}명`}</span>
                  </td>
                )}
                <td className="py-2 pr-3 font-medium">{user.dms_user}</td>
                <td className="py-2 pr-3"><AccessTag access={grant.access} /></td>
                <td className="max-w-[26rem] py-2 pr-3"><BindingCell g={grant} /></td>
                <td className="py-2"><AccountCell u={user} /></td>
              </tr>
            ))}
          </Fragment>
        ))}
      </tbody>
    </Table>
  );
}

export function AccessPage() {
  const [params, setParams] = useSearchParams();
  const search = useSearchField(params.get("q") ?? "");
  const [view, setView] = useState<View>(params.get("view") === "namespace" ? "namespace" : "user");
  const q = useK8sAccess(search.applied);
  const data = q.data;
  const users = useMemo(() => (Array.isArray(data?.users) ? data!.users : []), [data]);
  const unrecognized = Array.isArray(data?.unrecognized) ? data!.unrecognized : [];
  const clusters = Array.isArray(data?.clusters) ? data!.clusters : [];

  // 적용된 검색어·보기를 주소에 남긴다(replace — 타이핑마다 뒤로가기 기록을 쌓지 않게).
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (search.applied === "") next.delete("q"); else next.set("q", search.applied);
    if (view === "user") next.delete("view"); else next.set("view", view);
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [search.applied, view]); // eslint-disable-line react-hooks/exhaustive-deps

  const noAccount = users.filter((u) => !u.account_exists).length;
  const mismatch = users.reduce((n, u) => n + u.namespaces.filter((g) => g.subject_mismatch === true).length, 0);
  const noneSynced = clusters.length > 0 && clusters.every((c) => c.synced_at === null);

  return (
    <section className="space-y-4">
      <K8sHeader title="사용자 권한"
                 description="RoleBinding <ID>-clusterrole-admin|edit(역할 ClusterRole kubeflow-admin|kubeflow-edit) 또는 namespace 소유자 namespaceAdmin(이메일 로컬파트, 계정 메일 도메인과 같을 때)이 있는 namespace 의 모든 PV 에 그 ID 가 읽기·쓰기 권한을 가집니다. ID 는 이름 앞부분의 - 를 . 으로 바꾼 값이고 DMS 계정 아이디와 정확히 같아야 합니다." />
      <FreshnessLine clusters={clusters} />
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="보기 기준" value={view} onChange={setView}
                   options={[{ value: "user", label: "사용자별" }, { value: "namespace", label: "namespace별" }]} />
        <input type="search" aria-label="권한 검색" placeholder="DMS ID·namespace·바인딩 이름(일부)"
               className="w-72 max-w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm" {...search.bind} />
        <span className="ml-auto flex flex-wrap gap-x-3 text-xs text-ink/70 tabular-nums">
          <span>{`권한 사용자 ${users.length}명`}</span>
          {noAccount > 0 && <span className="text-attn">{`계정 없음 ${noAccount}`}</span>}
          {mismatch > 0 && <span className="text-attn">{`subject 불일치 ${mismatch}`}</span>}
          {q.isFetching && !q.isLoading && <span>새로 읽는 중…</span>}
        </span>
      </div>

      {q.isError && !data ? <p role="alert" className="text-sm text-bad">{`권한 목록을 불러오지 못했습니다 — ${errText(q.error)}`}</p> : (
        <>
          <Card>
            {q.isError && <p className="mb-3 text-xs text-bad">{`다시 읽지 못했습니다(표시는 직전 값): ${errText(q.error)}`}</p>}
            {view === "user" ? <ByUser users={users} /> : <ByNamespace users={users} />}
            {q.isLoading ? <p className="pt-3 text-ink/70">불러오는 중…</p>
              : users.length > 0 ? null
              : clusters.length === 0 ? <p className="pt-3 text-ink/70">등록된 클러스터가 없습니다 — 「개요」에서 클러스터를 등록하세요.</p>
              : noneSynced ? <p className="pt-3 text-ink/70">아직 동기화에 성공한 클러스터가 없습니다 — 권한은 모름입니다(권한이 없다는 뜻이 아닙니다).</p>
              : search.applied !== "" ? <p className="pt-3 text-ink/70">검색어에 맞는 권한이 없습니다</p>
              : <p className="pt-3 text-ink/70">인식된 바인딩이 없습니다 — 아래 「인식하지 않은 바인딩」에서 이름·역할을 확인하세요.</p>}
          </Card>

          <details className="rounded-card border border-line bg-surface px-5 py-3">
            <summary className="cursor-pointer text-sm font-medium">
              {`인식하지 않은 바인딩 ${unrecognized.length}건`}
              <span className="ml-2 font-normal text-ink/70">권한으로 치지 않음 — 표시만</span>
            </summary>
            {unrecognized.length === 0 ? <p className="mt-2 text-sm text-ink/70">없습니다</p> : (
              <div className="mt-3">
                <Table>
                  <thead>
                    <tr className="whitespace-nowrap text-ink/70">
                      <th className="py-2 pr-3">클러스터 · namespace</th><th className="pr-3">이름</th><th className="pr-3">역할</th>
                      <th className="pr-3">subject</th><th>인식하지 않은 이유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {unrecognized.map((b) => (
                      <tr key={`${b.cluster}/${b.namespace}/${b.name}`} className="border-t border-line align-top">
                        <td className="py-2 pr-3"><span className="text-ink/70">{b.cluster}</span> · {b.namespace}</td>
                        <td className="py-2 pr-3"><Mono>{b.name}</Mono></td>
                        <td className="py-2 pr-3"><Mono>{b.role_name ? `${b.role_kind ?? "?"}/${b.role_name}` : "—"}</Mono></td>
                        <td className="max-w-[18rem] py-2 pr-3 text-xs [overflow-wrap:anywhere]">
                          {(b.subjects ?? []).map(subjectText).join(", ") || "—"}
                        </td>
                        <td className="py-2 text-xs text-ink/80">{unrecognizedWhy(b)}</td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            )}
          </details>
        </>
      )}
    </section>
  );
}
