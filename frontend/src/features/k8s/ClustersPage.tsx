import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { InfoCard } from "../../components/ui/InfoCard";
import { StatusPill } from "../../components/ui/StatusPill";
import { reasonText } from "../../lib/api";
import { useNow } from "../../lib/useNow";
import { ClusterDialog } from "./ClusterDialog";
import { K8S_OVERVIEW_PATH, K8sHeader, clusterCardId } from "./K8sTabs";
import {
  ROLE_LABEL, TLS_MODE_LABEL, TOKEN_LABEL, TRIGGER_LABEL, clusterStatusCounts, clusterStatusMeta, countText, daysUntil,
  durationText, intervalText, plusSeconds, runStatusMeta, untilText, warningText,
} from "./k8sTerms";
import { Ago, Mono, SignupGuardNote, Tag } from "./parts";
import {
  overviewPollMs, serverOffsetMs, useDeleteCluster, useK8sOverview, useSyncAllClusters, useSyncCluster,
} from "./useK8s";
import type { ClusterSummary, K8sSettings } from "./types";

// k8s 연동 「클러스터」 탭(2026-11-11 사용자 요청 「개요에서 클러스터를 다른 탭으로」 — 설계 §7 의 클러스터 카드를 개요에서 옮겼다):
// 등록·편집·삭제·지금 동기화와 클러스터 카드(상태·n분 전·개수·마지막 run·경고·같은 클러스터 경고). 데이터는 개요와 같은 응답
// (useK8sOverview — 같은 키·같은 폴링: 평소 10초, 동기화 중 3초)이라 탭을 오가도 다시 기다리지 않는다. 호출 노드·주기는 개요 탭의
// 일이라 여기서는 고치지 않고 링크로 보낸다 — 클러스터가 「호출 노드 없음」이면 맨 위에 그 링크를 띄운다.
// 상태 문구는 셋을 가른다: null(모름 — 아직 동기화 전·목록 권한 없음) = 「—」·「동기화 전」, 0 = 「0」, 실패 = 사유 문장.
// 가입 신원 경고(SignupGuardNote)는 개요와 함께 여기도 그린다 — 경고가 막으려는 행동(stub 메일 상태에서 클러스터 등록)이 이 화면의
// 버튼이다(2026-11-11 리뷰). 주소의 #cluster-<이름>(개요의 클러스터 이름 링크)은 그 카드로 옮겨 강조·포커스한다.

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

const linkCls = "text-accent hover:underline";

function Until({ iso, offsetMs }: { iso: string | null; offsetMs: number }) {
  const now = useNow(15_000, true);
  return <span>{untilText(iso, now + offsetMs)}</span>;
}

function DeleteClusterButton({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteCluster();
  useEffect(() => { if (!open) del.reset(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open={open} onOpenChange={setOpen} title="클러스터 삭제" busy={del.isPending}
            trigger={<Button variant="ghost" className="whitespace-nowrap">삭제</Button>}>
      <div className="space-y-3 text-sm">
        <p>{`${name} 클러스터 등록을 지울까요?`}</p>
        <p className="text-ink/70">
          저장된 토큰과 이 클러스터에서 받은 PV·권한 스냅숏도 정리됩니다. 이 클러스터의 PV 주소로 낸 작업은 진행 중이면
          재확인에서 「PV 를 찾을 수 없음」으로 멈춥니다. 대상 클러스터의 리더 계정은 지우지 않습니다.
        </p>
        {del.isError && <p role="alert" className="text-bad">{errText(del.error)}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
          <Button variant="danger" disabled={del.isPending}
                  onClick={() => del.mutate(name, { onSuccess: () => setOpen(false) })}>삭제 확인</Button>
        </div>
      </div>
    </Dialog>
  );
}

/** 카드 안 수치 한 칸. null = 모름(「—」), 0 = 0. */
function Count({ label, value, sub }: { label: string; value: number | null | undefined; sub?: string | null }) {
  return (
    <div className="min-w-0 rounded-lg bg-panel px-3 py-2">
      <div className="text-[11px] text-ink/70">{label}</div>
      <div className="text-base font-semibold tabular-nums">{countText(value)}</div>
      {sub && <div className="text-[11px] text-ink/70">{sub}</div>}
    </div>
  );
}

/** 주소 해시(#cluster-<이름>, 인코딩됨) → 카드 id. 깨진 인코딩은 대상 없음. */
function hashTarget(hash: string): string | null {
  if (hash.length <= 1) return null;
  try { return decodeURIComponent(hash.slice(1)); } catch { return null; }
}

/** 클러스터 카드 하나. 이름은 h2 — 이 화면의 h1(「클러스터」) 바로 아래 단계다(제목 단계를 건너뛰지 않는다). targeted = 주소 해시가
 *  가리키는 카드(개요의 실패 문구에서 온 길) — 테두리 강조, 이름 h2 가 포커스 대상(tabIndex -1 — 탭 순서에는 넣지 않는다; 화면
 *  읽기 프로그램이 「dmsk8s-dns, 제목 2」로 어느 카드에 왔는지 읽는다). */
function ClusterCard({ c, offsetMs, targeted }: { c: ClusterSummary; offsetMs: number; targeted: boolean }) {
  const sync = useSyncCluster();
  const meta = clusterStatusMeta(c.status);
  const counts = c.counts;
  const run = c.last_run;
  const failing = c.status === "failing" || c.status === "token_unreadable"
    || (c.last_reason !== null && c.consecutive_failures > 0);
  const canSync = c.enabled && c.token === "set" && c.status !== "syncing";
  const syncHint = !c.enabled ? "동기화가 꺼진 클러스터입니다"
    : c.token !== "set" ? "토큰이 있어야 동기화합니다" : c.status === "syncing" ? "이미 동기화 중입니다" : undefined;
  const nextDue = c.enabled ? plusSeconds(c.last_attempt_at, c.interval_seconds) : null;
  const expDays = daysUntil(c.token_expires_at, Date.now() + offsetMs);
  const warnings = run?.warnings ?? [];

  return (
    <Card id={clusterCardId(c.name)}
          className={`flex min-w-0 scroll-mt-4 flex-col gap-3 p-4 ${targeted ? "ring-2 ring-accent" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 tabIndex={targeted ? -1 : undefined} className="text-base font-semibold [overflow-wrap:anywhere] focus:outline-none">
            {c.name}
          </h2>
          <Mono className="text-ink/80">{c.api_server}</Mono>
        </div>
        <StatusPill state={c.status} label={meta.label} variant={meta.variant} help={meta.help} />
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Tag tone={c.tls_mode === "insecure" ? "bad" : "neutral"}>{`TLS ${TLS_MODE_LABEL[c.tls_mode] ?? c.tls_mode}`}</Tag>
        {c.tls_server_name && <Tag>{`서버 이름 ${c.tls_server_name}`}</Tag>}
        <Tag tone={c.token === "set" ? (expDays !== null && expDays < 14 ? "warn" : "neutral") : "bad"}>
          {`토큰 ${TOKEN_LABEL[c.token] ?? c.token}${expDays !== null && c.token === "set"
            ? expDays < 0 ? " · 만료됨" : ` · ${Math.floor(expDays)}일 뒤 만료` : ""}`}
        </Tag>
        {c.server_version && <Tag>{`k8s ${c.server_version}`}</Tag>}
        {!c.enabled && <Tag>동기화 꺼짐</Tag>}
      </div>

      <dl className="grid grid-cols-3 gap-2 text-sm">
        <div className="min-w-0">
          <dt className="text-xs text-ink/70">마지막 성공</dt>
          <dd className={c.last_success_at ? "font-medium" : "text-ink/70"}>
            <Ago iso={c.last_success_at} empty="아직 없음" offsetMs={offsetMs} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-ink/70">다음 예정</dt>
          <dd className="font-medium">{!c.enabled ? <span className="text-ink/70">꺼짐</span>
            : c.token !== "set" ? <span className="text-ink/70">토큰 필요</span>
            : <Until iso={nextDue} offsetMs={offsetMs} />}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-ink/70">연속 실패</dt>
          <dd className={`font-medium tabular-nums ${c.consecutive_failures > 0 ? "text-bad" : ""}`}>
            {`${c.consecutive_failures}회`}
          </dd>
        </div>
      </dl>

      {counts === null ? (
        <p className="rounded-lg bg-panel px-3 py-2 text-sm text-ink/70">
          아직 받은 목록이 없습니다 — 개수는 모름(첫 동기화가 성공하면 채워집니다).
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Count label="namespace" value={counts.namespaces} />
          <Count label="PV 이름(PVC)" value={counts.pvcs}
                 sub={counts.pvcs_bound === null ? null : `Bound ${countText(counts.pvcs_bound)}`} />
          <Count label="권한 사용자" value={counts.users} />
          <Count label="RoleBinding" value={counts.bindings}
                 sub={counts.recognized_bindings === null ? null : `인식 ${countText(counts.recognized_bindings)}`} />
          <Count label="StorageClass" value={counts.storage_classes}
                 sub={counts.storage_classes === null ? "목록 권한 없음·모름" : null} />
          <Count label="볼륨(PV)" value={counts.pvs} sub={counts.pvs === null ? "목록 권한 없음·모름" : null} />
        </div>
      )}

      {run && (
        <p className="text-xs text-ink/80">
          <span className="text-ink/70">마지막 실행 </span>
          {`${run.node_name ?? "노드 모름"}${run.role ? `(${ROLE_LABEL[run.role] ?? run.role})` : ""} · `}
          {`${TRIGGER_LABEL[run.trigger] ?? run.trigger} · ${durationText(run.duration_ms)} · `}
          <span className={runStatusMeta(run.status).variant === "bad" ? "text-bad" : ""}>{runStatusMeta(run.status).label}</span>
          {run.reason && run.status !== "applied" ? ` · ${reasonText(run.reason)}` : ""}
        </p>
      )}

      {failing && c.last_reason && (
        <div className="rounded-lg border border-bad/30 bg-badbg/40 px-3 py-2 text-sm">
          <p className="font-medium text-bad">{reasonText(c.last_reason)}</p>
          {c.last_error && <Mono className="mt-1 block text-ink/80">{c.last_error}</Mono>}
          <p className="mt-1 text-xs text-ink/70">
            {c.last_success_at ? "권한·PV 판정은 마지막 성공 스냅숏 기준으로 계속됩니다."
              : "한 번도 성공하지 않아 이 클러스터의 PV 권한은 「모름」입니다 — PV 작업을 받지 않습니다."}
          </p>
        </div>
      )}

      {warnings.length > 0 && (
        <ul className="space-y-0.5 text-xs text-attn">
          {warnings.map((w, i) => <li key={i}>{`경고: ${warningText(w)}`}</li>)}
        </ul>
      )}

      {c.duplicate_of && (
        <p className="rounded-lg border border-attn/40 bg-attnbg/50 px-3 py-2 text-xs text-attn">
          {`같은 클러스터로 보입니다(kube-system uid 일치): ${c.duplicate_of} — 같은 PVC 가 두 번 보여 사용자가 헷갈릴 수 있습니다.`}
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <Button variant="outline" className="whitespace-nowrap" disabled={!canSync || sync.isPending} title={syncHint}
                onClick={() => sync.mutate(c.name)}>
          {sync.isPending ? "요청 중…" : "지금 동기화"}
        </Button>
        <ClusterDialog mode="edit" cluster={c}
                       trigger={<Button variant="ghost" className="whitespace-nowrap">편집</Button>} />
        <Link to={`/admin/k8s/history?cluster=${encodeURIComponent(c.name)}`}
              className="whitespace-nowrap px-1 text-sm text-accent hover:underline">이력</Link>
        <span className="ml-auto"><DeleteClusterButton name={c.name} /></span>
      </div>
      {(sync.isError || sync.isSuccess) && (
        <p role="status" className={`text-xs ${sync.isError ? "text-bad" : "text-ink/70"}`}>
          {sync.isError ? errText(sync.error) : "요청했습니다 — 호출 노드가 다음 확인(수 초 안)에 가져갑니다"}
        </p>
      )}
    </Card>
  );
}

/** 빈 상태 — 등록까지의 순서. 호출 노드는 개요 탭의 일이라 그리로 보내고, 지금 지정 상태를 함께 적는다(이 탭에서 바로 안다). */
function EmptyClusters({ settings }: { settings: K8sSettings }) {
  const { primary_node: p, secondary_node: s } = settings;
  return (
    <InfoCard className="space-y-3">
      <h2 className="font-medium">등록된 클러스터가 없습니다</h2>
      <ol className="list-decimal space-y-1 pl-5 text-ink/80">
        <li>대상 클러스터에 읽기 전용 계정(ServiceAccount·ClusterRole·토큰 Secret)을 만듭니다 — 등록 창에 YAML 이 있습니다.</li>
        <li>
          <Link to={K8S_OVERVIEW_PATH} className={linkCls}>개요 탭</Link>에서 호출 노드(주·보조)를 지정하고, 대상 API 서버 방화벽에
          그 노드 IP 를 허용합니다.
          {p || s
            ? <span className="text-ink/70">{` 지금: 주 ${p ?? "없음"} · 보조 ${s ?? "없음"}`}</span>
            : <span className="text-attn"> 아직 지정되지 않았습니다.</span>}
        </li>
        <li>클러스터를 등록하면 다음 주기(또는 「지금 동기화」)에 PVC·RoleBinding 을 받아 옵니다.</li>
        <li>
          <Link to="/admin/k8s/storageclasses" className={linkCls}>StorageClass 탭</Link>에서 StorageClass 를 DMS 스토리지에
          연결하면 PV 경로가 정해집니다.
        </li>
      </ol>
      <ClusterDialog mode="create" trigger={<Button>클러스터 등록</Button>} />
    </InfoCard>
  );
}

/** 「클러스터 3개 — 정상 2 · 실패 1 · 5분마다 동기화 · 화면은 10초마다 새로 읽습니다」. 실패색 상태(bad)는 빨간 글자로 — 색만이
 *  아니라 라벨 글자로도 읽힌다. */
function SummaryLine({ clusters, intervalSeconds, pollMs }: {
  clusters: ClusterSummary[]; intervalSeconds: number; pollMs: number;
}) {
  const parts = clusterStatusCounts(clusters);
  return (
    <p className="flex flex-wrap items-baseline gap-x-1 gap-y-0.5 text-xs text-ink/70">
      <span className="font-medium text-ink/80">{`클러스터 ${clusters.length}개`}</span>
      <span aria-hidden="true">—</span>
      {parts.map((s, i) => (
        <Fragment key={s.status}>
          {i > 0 && <span aria-hidden="true">·</span>}
          <span className={s.variant === "bad" ? "text-bad" : s.variant === "action" ? "text-attn" : undefined}>
            {`${s.label} ${s.count}`}
          </span>
        </Fragment>
      ))}
      <span aria-hidden="true">·</span>
      <span>{`${intervalText(intervalSeconds)}마다 동기화 · 화면은 ${pollMs / 1000}초마다 새로 읽습니다`}</span>
    </p>
  );
}

export function ClustersPage() {
  const q = useK8sOverview();
  const syncAll = useSyncAllClusters();
  const ov = q.data;
  const offsetMs = serverOffsetMs(ov?.now, q.dataUpdatedAt);
  const clusters = Array.isArray(ov?.clusters) ? ov!.clusters : [];
  const syncable = clusters.filter((c) => c.enabled && c.token === "set" && c.status !== "syncing").map((c) => c.name);
  const noNode = clusters.some((c) => c.status === "no_node");

  // #cluster-<이름> 대상 카드로 한 번 옮긴다(같은 링크를 다시 누르면 location.key 가 바뀌어 다시) — 카드가 그려진 다음 프레임에,
  // 폴링으로 다시 그려질 때는 옮기지 않는다. 스크롤은 움직임 줄이기 설정을 따른다(JobStages 선례).
  const location = useLocation();
  const target = hashTarget(location.hash);
  const revealed = useRef<string | null>(null);
  const hasTargetCard = target !== null && clusters.some((c) => clusterCardId(c.name) === target);
  useEffect(() => {
    if (!hasTargetCard || target === null) return;
    const token = `${location.key}|${target}`;
    if (revealed.current === token) return;
    const raf = requestAnimationFrame(() => {
      const el = document.getElementById(target);
      if (!el) return;
      revealed.current = token;
      const reduced = typeof window.matchMedia === "function"
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollIntoView?.({ block: "start", behavior: reduced ? "auto" : "smooth" });
      (el.querySelector<HTMLElement>("h2") ?? el).focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(raf);
  }, [hasTargetCard, target, location.key]);

  return (
    <section className="space-y-4">
      <K8sHeader
        title="클러스터"
        description={<>
          동기화할 k8s 클러스터를 등록하고 연결 상태와 받은 목록을 봅니다. 호출 노드와 주기는{" "}
          <Link to={K8S_OVERVIEW_PATH} className={linkCls}>개요 탭</Link>에서 정합니다. 등록·편집·삭제는 포탈에 로그인한
          관리자만 할 수 있습니다(API 토큰으로는 조회와 「지금 동기화」만).
        </>}
        actions={<>
          {clusters.length > 0 && (
            <Button variant="outline" className="whitespace-nowrap" disabled={syncable.length === 0 || syncAll.isPending}
                    title={syncable.length === 0 ? "지금 동기화할 수 있는(켜짐·토큰 있음·동기화 중 아님) 클러스터가 없습니다" : undefined}
                    onClick={() => syncAll.mutate(syncable)}>
              {syncAll.isPending ? "요청 중…" : "모두 지금 동기화"}
            </Button>
          )}
          <ClusterDialog mode="create" trigger={<Button className="whitespace-nowrap">클러스터 등록</Button>} />
        </>} />

      <SignupGuardNote guard={ov?.signup_guard} />

      {q.isError && (
        <p role="alert" className="text-sm text-bad">
          {ov ? `클러스터 목록을 다시 불러오지 못했습니다 — 아래는 마지막으로 받은 값입니다(${errText(q.error)})`
            : `클러스터 목록을 불러오지 못했습니다 — ${errText(q.error)}`}
        </p>
      )}
      {syncAll.data && (
        <p role="status" className={`text-sm ${syncAll.data.failed.length > 0 ? "text-bad" : "text-ink/70"}`}>
          {syncAll.data.failed.length > 0
            ? `${syncAll.data.requested}개 요청 · 실패 ${syncAll.data.failed.join(", ")}`
            : `${syncAll.data.requested}개 클러스터 동기화를 요청했습니다`}
        </p>
      )}

      {q.isLoading ? <p className="text-ink/70">불러오는 중…</p> : ov && (
        clusters.length === 0 ? <EmptyClusters settings={ov.settings} /> : (
          <>
            {noNode && (
              <p className="rounded-lg border border-bad/30 bg-badbg/40 px-4 py-3 text-sm text-bad">
                호출 노드가 지정되지 않았거나 쓸 수 없어 동기화가 돌지 않는 클러스터가 있습니다 —{" "}
                <Link to={K8S_OVERVIEW_PATH} className="font-medium underline">개요 탭</Link>의 「호출 노드와 주기」에서 주·보조
                호출 노드를 지정하세요.
              </p>
            )}
            <SummaryLine clusters={clusters} intervalSeconds={ov.settings.interval_seconds} pollMs={overviewPollMs(ov)} />
            <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {clusters.map((c) => (
                <ClusterCard key={c.name} c={c} offsetMs={offsetMs} targeted={clusterCardId(c.name) === target} />
              ))}
            </div>
          </>
        )
      )}
    </section>
  );
}
