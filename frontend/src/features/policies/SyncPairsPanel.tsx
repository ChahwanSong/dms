import { useMemo, useState } from "react";
import { useStorages } from "../storages/useStorages";
import { scopeMeta, scopeOf } from "../storages/storageScope";
import { useAddSyncPair, useRemoveSyncPair, useSyncPairs } from "./useSyncPairs";
import { Card } from "../../components/ui/Card";
import { ApiError } from "../../lib/api";
import type { Storage } from "../../lib/types";

// 사용자 sync 허용 스토리지 쌍 편집기(2026-09-30 사용자 요청: "Sync 가능한 스토리지 쌍들에 대한
// policy -- 기본 전부 불가에 허용 쌍들을 추가"). 행 = 소스, 열 = 목적지인 체크 매트릭스이고 체크가
// 곧 저장이다(한 칸 = 한 쌍이라 "저장" 단계를 두면 여러 칸을 바꾸다 일부만 저장되는 모호함이 생긴다).
// 방향이 있어 A → B 와 B → A 는 다른 칸이고, 대각선(A → A)은 같은 스토리지 안의 sync 다.
//
// 관리자 전용·완전 비활성 스토리지도 행·열에 둔다: 사용 범위를 열기 전에 쌍을 미리 준비할 수 있고,
// 서버가 사용자 조회(/api/user/sync-pairs)에서 그런 쌍을 빼므로 허용해 둬도 사용자에겐 효과가 없다
// -- 화면은 그 칸을 흐리게 칠하고 이유를 적는다(숨기면 "왜 이 쌍이 사용자에게 안 보이나"를 못 푼다).

const keyOf = (src: string, dst: string) => JSON.stringify([src, dst]);
const INERT_NOTE_ID = "sync-pairs-inert-note";

function openToUsers(s: Storage): boolean { return scopeOf(s) === "all"; }

function ScopeTag({ s }: { s: Storage }) {
  const scope = scopeOf(s);
  if (scope === "all") return null;
  const meta = scopeMeta(scope);
  return <span className={`block text-[11px] font-normal ${meta.text}`}>{meta.label}</span>;
}

export function SyncPairsPanel() {
  const storagesQ = useStorages();
  const pairsQ = useSyncPairs();
  const add = useAddSyncPair();
  const remove = useRemoveSyncPair();
  // 요청 중인 칸 → 원하는 값. 응답(과 재조회)이 올 때까지 그 값을 그리고 칸을 잠근다.
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const storages = useMemo(() => [...(storagesQ.data ?? [])]
    .sort((a, b) => a.storage_name.localeCompare(b.storage_name)), [storagesQ.data]);
  const byName = useMemo(() => new Map(storages.map((s) => [s.storage_name, s])), [storages]);
  const pairs = useMemo(() => [...(pairsQ.data ?? [])].sort((a, b) =>
    a.source_storage.localeCompare(b.source_storage)
    || a.destination_storage.localeCompare(b.destination_storage)), [pairsQ.data]);
  const allowed = useMemo(() => new Set(pairs.map((p) => keyOf(p.source_storage, p.destination_storage))),
                          [pairs]);
  const effective = (src: string, dst: string) => {
    const s = byName.get(src), d = byName.get(dst);
    return s !== undefined && d !== undefined && openToUsers(s) && openToUsers(d);
  };
  const effectiveCount = pairs.filter((p) => effective(p.source_storage, p.destination_storage)).length;

  async function toggle(src: string, dst: string, next: boolean) {
    const k = keyOf(src, dst);
    setError(null);
    setPending((p) => ({ ...p, [k]: next }));
    try {
      await (next ? add : remove).mutateAsync({ source_storage: src, destination_storage: dst });
    } catch (e) {
      setError(`${src} → ${dst} ${next ? "허용" : "해제"} 실패: ${(e as ApiError).message}`);
    } finally {
      setPending((p) => { const rest = { ...p }; delete rest[k]; return rest; });
    }
  }

  const loading = storagesQ.isLoading || pairsQ.isLoading;
  // 데이터가 아예 없을 때의 실패만 매트릭스를 대신한다 -- 토글 뒤 재조회가 잠깐 실패해도 이미 받은
  // 목록은 그대로 두고(react-query 는 실패한 재조회에서도 이전 data 를 유지한다) 경고만 얹는다.
  const loadError = ((storagesQ.data === undefined ? storagesQ.error : null)
    ?? (pairsQ.data === undefined ? pairsQ.error : null)) as ApiError | null;
  const refreshError = (storagesQ.error ?? pairsQ.error) as ApiError | null;

  return (
    <section aria-labelledby="sync-pairs-title" className="space-y-3">
      <div>
        <h2 id="sync-pairs-title" className="text-lg font-semibold">사용자 Sync 허용 스토리지 쌍</h2>
        <p className="mt-1 text-sm text-muted">
          사용자는 여기서 허용한 <strong>소스 → 목적지</strong> 조합으로만 sync 할 수 있습니다. 기본은 전부
          불가입니다. 방향이 있어 A → B 와 B → A 는 따로 허용하고, 같은 스토리지 안의 sync(A → A)도 하나의
          쌍입니다. 관리자와 배치 작업은 이 목록과 관계없이 모든 조합을 쓸 수 있습니다. 체크하면 바로 저장되고,
          사용자 단일 작업 화면에는 허용된 조합만 선택지로 보입니다.
        </p>
      </div>
      <Card className="space-y-4">
        {loading ? <p className="text-muted">불러오는 중…</p> : loadError ? (
          <p className="text-bad">{loadError.message}</p>
        ) : storages.length === 0 ? (
          <p className="text-muted">등록된 스토리지가 없습니다.</p>
        ) : (
          <>
            <p className="text-sm" aria-live="polite">
              허용된 쌍 <strong className="tabular-nums">{pairs.length}</strong>개
              {effectiveCount !== pairs.length && (
                <span className="text-muted"> · 사용자에게 적용 중 {effectiveCount}개</span>
              )}
            </p>
            {effectiveCount === 0 && (
              <p className="rounded-lg bg-badbg px-3 py-2 text-sm text-bad">
                사용자에게 적용되는 허용 쌍이 없습니다 — 지금은 사용자가 sync 를 제출할 수 없습니다.
              </p>
            )}
            {error && <p role="alert" className="text-sm text-bad">{error}</p>}
            {!error && refreshError && (
              <p className="text-sm text-bad">{`최신 목록을 다시 읽지 못했습니다(표시는 직전 값): ${refreshError.message}`}</p>
            )}
            <div className="overflow-x-auto">
              <table className="border-collapse text-sm">
                <caption className="sr-only">행은 소스 스토리지, 열은 목적지 스토리지입니다</caption>
                <thead>
                  <tr>
                    <th scope="col" className="border border-line bg-panel px-3 py-2 text-left text-xs font-medium text-muted">
                      소스 ↓ · 목적지 →
                    </th>
                    {storages.map((d) => (
                      <th key={d.storage_name} scope="col"
                          className="border border-line bg-panel px-3 py-2 text-center font-medium whitespace-nowrap">
                        {d.storage_name}<ScopeTag s={d} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {storages.map((s) => (
                    <tr key={s.storage_name}>
                      <th scope="row" className="border border-line bg-panel px-3 py-2 text-left font-medium whitespace-nowrap">
                        {s.storage_name}<ScopeTag s={s} />
                      </th>
                      {storages.map((d) => {
                        const k = keyOf(s.storage_name, d.storage_name);
                        const busy = k in pending;
                        const checked = busy ? pending[k] : allowed.has(k);
                        const inert = !effective(s.storage_name, d.storage_name);
                        return (
                          <td key={d.storage_name}
                              className={`border border-line px-3 py-2 text-center ${
                                inert ? "bg-panel" : checked ? "bg-okbg" : ""}`}>
                            <input type="checkbox" className="h-4 w-4 cursor-pointer align-middle accent-accent"
                                   aria-label={`${s.storage_name} → ${d.storage_name} 허용`}
                                   aria-describedby={inert ? INERT_NOTE_ID : undefined}
                                   checked={checked} disabled={busy}
                                   onChange={(e) => toggle(s.storage_name, d.storage_name, e.target.checked)} />
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p id={INERT_NOTE_ID} className="text-xs text-muted">
              회색 칸: 관리자 전용·완전 비활성 스토리지가 낀 조합 — 허용해 두어도 사용자에게는 적용되지
              않습니다(스토리지 사용 범위를 「전체 사용」으로 바꾸면 그때부터 적용).
            </p>
            {pairs.length > 0 && (
              <div>
                <h3 className="text-sm font-medium">허용된 쌍</h3>
                <ul aria-label="허용된 쌍 목록" className="mt-2 flex flex-wrap gap-2">
                  {pairs.map((p) => {
                    const on = effective(p.source_storage, p.destination_storage);
                    return (
                      <li key={keyOf(p.source_storage, p.destination_storage)}
                          className={`rounded-full border px-2.5 py-1 text-xs ${
                            on ? "border-ok/30 bg-okbg text-ok" : "border-line bg-panel text-muted"}`}>
                        {p.source_storage} → {p.destination_storage}{on ? "" : " (사용자 미적용)"}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </>
        )}
      </Card>
    </section>
  );
}
