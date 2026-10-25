import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Segmented } from "../../components/ui/Segmented";
import { Table } from "../../components/ui/Table";
import { reasonText } from "../../lib/api";
import type { Storage } from "../../lib/types";
import { field } from "../jobs/formFields";
import { useStorages } from "../storages/useStorages";
import { K8sHeader } from "./K8sTabs";
import { KIND_LABEL, PATH_PLACEHOLDERS, countText, storageKindOf } from "./k8sTerms";
import { DefList, DefRow, Mono, Tag } from "./parts";
import { useSaveScMap, useStorageClasses } from "./useK8s";
import type { StorageClassRow, StorageKind } from "./types";

// StorageClass → DMS 스토리지 매핑과 경로 템플릿(설계 D9·D10). 매핑은 **StorageClass 이름 하나에 전역**이다(SC 이름은 전
// 클러스터에서 유일하다는 사용자 전제) -- 같은 이름이 두 클러스터에 보이면 경고만 하고 둘 다에 같은 매핑이 걸린다.
// 예시 경로는 서버가 실제 PVC 하나로 계산한 값(sample)이다 -- 화면은 템플릿을 스스로 풀지 않는다(서버 pv_paths 와 어긋난
// 미리보기는 거짓말이 된다). 저장하면 표가 다시 읽혀 새 예시가 보인다.

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

type StorageWithKind = Storage & { kind?: string | null };

type Filter = "all" | "todo" | "mapped";

/** 손볼 것: 매핑이 없거나, 매핑은 있는데 예시 경로가 풀리지 않는(problem) StorageClass. PVC 가 없어 예시가 없는 것은 손볼 것이 아니다. */
export function needsAttention(r: StorageClassRow): boolean {
  return r.storage_name === null || (r.sample !== null && r.sample.problem !== null)
    || (r.storage_name !== null && r.effective_template === null);
}

function SourceTag({ r }: { r: StorageClassRow }) {
  if (r.template_source === "storageclass") return <Tag tone="info">SC 지정</Tag>;
  if (r.template_source === "preset") return <Tag>백엔드 프리셋</Tag>;
  return null;
}

function SampleCell({ r }: { r: StorageClassRow }) {
  if (r.storage_name === null) return <span className="text-ink/70">매핑하면 계산됩니다</span>;
  if (r.sample === null) return <span className="text-ink/70">PVC 없음 — 예시 없음</span>;
  const who = `${r.sample.namespace}/${r.sample.pvc}`;
  if (r.sample.problem !== null) {
    return <div className="min-w-0"><span className="text-bad">{reasonText(r.sample.problem)}</span>
      <span className="block text-xs text-ink/70">{`예: ${who}`}</span></div>;
  }
  return (
    <div className="min-w-0">
      <Mono>{r.sample.abs_root ?? r.sample.root ?? "—"}</Mono>
      <span className="block text-xs text-ink/70">{`예: ${who}`}</span>
    </div>
  );
}

function MapDialog({ r, storages, trigger }: { r: StorageClassRow; storages: StorageWithKind[]; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [storage, setStorage] = useState(r.storage_name ?? "");
  const [tpl, setTpl] = useState(r.path_template ?? "");
  const save = useSaveScMap();
  // 열 때만 맞춘다 -- 다른 저장의 무효화로 표가 다시 읽혀 r 이 바뀌어도 편집 중인 칸을 덮지 않는다.
  useEffect(() => {
    if (!open) { save.reset(); return; }
    setStorage(r.storage_name ?? ""); setTpl(r.path_template ?? "");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const capable = storages.filter((s) => storageKindOf(s) !== "filesystem");
  const fsOnly = storages.filter((s) => storageKindOf(s) === "filesystem");
  // 지금 매핑된 스토리지가 종류가 바뀌어(또는 지워져) 목록에 없으면 그 값을 잃지 않게 한 옵션으로 남긴다.
  const orphan = r.storage_name !== null && !capable.some((s) => s.storage_name === r.storage_name);
  const dirty = storage !== (r.storage_name ?? "") || tpl.trim() !== (r.path_template ?? "");
  const unmapping = storage === "" && r.storage_name !== null;
  const params = Object.entries(r.parameters ?? {});

  const submit = () => {
    save.mutate({ storageClass: r.name,
                  body: { storage_name: storage === "" ? null : storage,
                          path_template: storage === "" || tpl.trim() === "" ? null : tpl.trim() } },
                { onSuccess: () => setOpen(false) });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen} size="lg" busy={save.isPending}
            title={`StorageClass ${r.name} 매핑`} trigger={trigger}>
      <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <DefList>
          <DefRow label="클러스터">{[r.cluster, ...r.duplicate_in].join(", ")}</DefRow>
          <DefRow label="provisioner"><Mono>{r.provisioner ?? "모름"}</Mono></DefRow>
          <DefRow label="parameters">
            {params.length === 0 ? <span className="text-ink/70">없음</span> : (
              <span className="flex flex-wrap gap-1">
                {params.map(([k, v]) => (
                  <code key={k} className="rounded bg-panel px-1.5 py-0.5 text-xs [overflow-wrap:anywhere]">{`${k}=${v}`}</code>
                ))}
              </span>
            )}
          </DefRow>
          <DefRow label="PVC">{countText(r.pvc_count)}</DefRow>
        </DefList>
        {r.duplicate_in.length > 0 && (
          <p className="rounded-lg border border-attn/40 bg-attnbg/50 px-3 py-2 text-attn">
            {`같은 이름의 StorageClass 가 다른 클러스터(${r.duplicate_in.join(", ")})에도 있습니다 — 매핑은 이름 하나에 걸리므로 모두에 적용됩니다. 실제로 다른 저장소라면 한쪽 이름을 바꿔야 합니다.`}
          </p>
        )}

        <label className="block">대상 스토리지
          <select aria-label="대상 스토리지" className={field} value={storage} onChange={(e) => setStorage(e.target.value)}>
            <option value="">{r.storage_name === null ? "선택하세요(매핑 안 함)" : "매핑 해제"}</option>
            {capable.map((s) => (
              <option key={s.storage_name} value={s.storage_name}>
                {`${s.storage_name} · ${KIND_LABEL[storageKindOf(s)]} · ${s.backend_type} · ${s.managed_root}`}
              </option>
            ))}
            {orphan && <option value={r.storage_name!}>{`${r.storage_name} (지금 매핑 — PV 를 담을 수 없는 종류)`}</option>}
            {fsOnly.length > 0 && (
              <optgroup label="파일시스템 전용 — 고를 수 없음">
                {fsOnly.map((s) => <option key={s.storage_name} value={s.storage_name} disabled>{s.storage_name}</option>)}
              </optgroup>
            )}
          </select>
          <span className="mt-1 block text-xs text-ink/70">
            종류가 PV·둘 다 인 스토리지만 고를 수 있습니다. 없다면 <Link className="text-accent" to="/admin/storages">스토리지</Link>
            {" "}화면에서 종류를 바꾸세요. 경로는 그 스토리지의 관리 디렉토리 아래입니다.
          </span>
        </label>
        {capable.length === 0 && (
          <p className="text-attn">PV 를 담을 수 있는(종류 PV·둘 다) 스토리지가 아직 없습니다.</p>
        )}

        <label className="block">경로 템플릿(덮어쓰기)
          <input aria-label="경로 템플릿(덮어쓰기)" className={`${field} font-mono text-[13px]`} value={tpl}
                 disabled={storage === ""} spellCheck={false} autoComplete="off"
                 onChange={(e) => setTpl(e.target.value)}
                 placeholder={r.template_source === "preset" && r.effective_template
                   ? `비우면 백엔드 프리셋 — 지금 ${r.effective_template}` : "비우면 스토리지 백엔드의 프리셋"} />
          <span className="mt-1 block text-xs text-ink/70">
            관리 디렉토리 기준 상대 경로입니다(앞에 / 없이). 예: GPFS {"{volume}/{volume}-data"} · OneFS {"data/csi/{volume}"} ·
            NFS(csi-driver-nfs) {"{subdir}"}. 프리셋이 없는 백엔드는 여기서 지정해야 합니다.
          </span>
        </label>
        <details className="rounded-lg border border-line px-3 py-2">
          <summary className="cursor-pointer text-ink/80">쓸 수 있는 자리표시자</summary>
          <dl className="mt-2 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
            {PATH_PLACEHOLDERS.map((p) => (
              <div key={p.token} className="contents">
                <dt><code className="font-mono">{p.token}</code></dt><dd className="text-ink/80">{p.help}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-ink/70">값에 .·.. 성분이 생기거나 값이 없으면 그 PVC 는 「경로를 정할 수 없음」이 됩니다.</p>
        </details>

        <div className="rounded-lg bg-panel px-3 py-2">
          <p className="text-xs text-ink/70">지금 예시(서버 계산 — 저장하면 새로 계산합니다)</p>
          <div className="mt-1"><SampleCell r={r} /></div>
        </div>
        {unmapping && (
          <p className="text-attn">
            매핑을 해제하면 이 StorageClass 의 PVC 는 「StorageClass 매핑 없음」이 되어 PV 작업에 쓸 수 없습니다(진행 중인 작업은
            재확인에서 멈춥니다).
          </p>
        )}
        {save.isError && <p role="alert" className="text-bad">{errText(save.error)}</p>}
        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button type="button" variant="ghost" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={!dirty || save.isPending}>{save.isPending ? "저장 중…" : "저장"}</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function StorageClassesPage() {
  const q = useStorageClasses();
  const storagesQ = useStorages();
  const [filter, setFilter] = useState<Filter>("all");
  const rows = useMemo(() => (Array.isArray(q.data) ? q.data : []), [q.data]);
  const storages = (storagesQ.data ?? []) as StorageWithKind[];
  const kindByName = new Map<string, StorageKind>(storages.map((s) => [s.storage_name, storageKindOf(s)]));
  const todo = rows.filter(needsAttention).length;
  const shown = rows.filter((r) => filter === "all" ? true : filter === "todo" ? needsAttention(r) : !needsAttention(r));

  return (
    <section className="space-y-4">
      <K8sHeader title="StorageClass"
                 description="StorageClass 를 DMS 스토리지에 연결하고 PV 경로 규칙을 정합니다. 매핑은 StorageClass 이름 하나에 걸리며, 경로는 스토리지의 관리 디렉토리 아래 템플릿(SC 지정 > 백엔드 프리셋)으로 만듭니다." />
      {q.isError && !q.data ? <p role="alert" className="text-sm text-bad">{`StorageClass 목록을 불러오지 못했습니다 — ${errText(q.error)}`}</p> : (
        <Card>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <Segmented label="매핑 상태 필터" value={filter} onChange={setFilter}
                       options={[{ value: "all", label: `전체 ${rows.length}` },
                                 { value: "todo", label: `손볼 것 ${todo}` },
                                 { value: "mapped", label: `정상 ${rows.length - todo}` }]} />
            {q.isError && <span className="text-xs text-bad">{`다시 읽지 못했습니다(표시는 직전 값): ${errText(q.error)}`}</span>}
          </div>
          <Table>
            <thead>
              <tr className="whitespace-nowrap text-ink/70">
                <th className="py-2 pr-3">StorageClass</th><th className="pr-3">클러스터</th><th className="pr-3 text-right">PVC</th>
                <th className="pr-3">스토리지</th><th className="pr-3">경로 템플릿</th><th className="pr-3">예시 경로</th><th>작업</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const kind = r.storage_kind ?? (r.storage_name ? kindByName.get(r.storage_name) ?? null : null);
                return (
                  <tr key={`${r.cluster}/${r.name}`} className="border-t border-line align-top">
                    <td className="py-2 pr-3">
                      <div className="min-w-[9rem]">
                        <span className="font-medium [overflow-wrap:anywhere]">{r.name}</span>
                        <Mono className="block text-ink/70">{r.provisioner ?? "provisioner 모름"}</Mono>
                      </div>
                    </td>
                    <td className="py-2 pr-3">
                      <span className="whitespace-nowrap">{r.cluster}</span>
                      {r.duplicate_in.length > 0 && (
                        <span className="block text-xs text-attn">{`다른 클러스터에도: ${r.duplicate_in.join(", ")}`}</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums">{countText(r.pvc_count)}</td>
                    <td className="py-2 pr-3">
                      {r.storage_name === null ? <Tag tone="warn">매핑 없음</Tag> : (
                        <div className="min-w-0">
                          <span className="font-medium">{r.storage_name}</span>{" "}
                          {kind && <Tag tone={kind === "filesystem" ? "bad" : "neutral"}>{KIND_LABEL[kind]}</Tag>}
                          {kind === "filesystem" && <span className="block text-xs text-bad">PV 를 담을 수 없는 종류입니다</span>}
                        </div>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {r.effective_template === null
                        ? <span className={r.storage_name ? "text-bad" : "text-ink/70"}>{r.storage_name ? "규칙 없음 — 템플릿 지정 필요" : "—"}</span>
                        : <div className="min-w-0"><Mono>{r.effective_template}</Mono> <SourceTag r={r} /></div>}
                    </td>
                    <td className="max-w-[22rem] py-2 pr-3"><div className="min-w-[14rem]"><SampleCell r={r} /></div></td>
                    <td className="py-2">
                      <MapDialog r={r} storages={storages}
                                 trigger={<Button variant="outline" className="whitespace-nowrap px-2.5 py-1 text-xs">
                                   {r.storage_name === null ? "매핑" : "편집"}</Button>} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          {q.isLoading ? <p className="pt-3 text-ink/70">불러오는 중…</p>
            : rows.length === 0 ? (
              <p className="pt-3 text-ink/70">
                아직 받은 StorageClass 가 없습니다 — 클러스터가 동기화되면 여기 나타납니다(리더 계정에 storageclasses list 권한이
                없으면 PVC 에 적힌 이름만 보입니다).
              </p>
            ) : shown.length === 0 ? <p className="pt-3 text-ink/70">이 조건의 StorageClass 가 없습니다</p> : null}
          {storagesQ.isError && (
            <p className="pt-2 text-xs text-bad">{`스토리지 목록을 불러오지 못해 매핑 선택지가 비어 있습니다 — ${errText(storagesQ.error)}`}</p>
          )}
        </Card>
      )}
    </section>
  );
}
