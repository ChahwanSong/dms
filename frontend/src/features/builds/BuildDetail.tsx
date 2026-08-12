import { useParams } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { ApiError, reasonText } from "../../lib/api";
import { buildPillVariant, isTerminal } from "../../lib/jobState";
import { useBuild, useBuildLog } from "./useBuilds";

export function BuildDetail() {
  const { buildId = "" } = useParams();
  const q = useBuild(buildId);
  const b = q.data;
  const isActive = b !== undefined && !isTerminal(b.state);
  const logQ = useBuildLog(buildId, isActive);

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">빌드 {buildId.slice(0, 8)}</h1>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <>
          <Card className="space-y-2 text-sm">
            <div className="flex items-center gap-3">
              <StatusPill state={b?.state ?? "—"} variant={b ? buildPillVariant(b.state) : undefined} />
              <span className="text-muted">태그 {b?.tag ?? "—"}</span>
            </div>
            {/* 슬라이스 21 §3: Pending 은 적합성 확인(프리플라이트 프로브, 최대
                DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS=180s)을 포함한다 -- 별도 상태
                기계는 만들지 않는다. 실패는 어차피 고유 사유 코드로 드러난다. */}
            {b?.state === "Pending" && (
              <p className="text-muted">적합성 확인(프리플라이트) 포함 — 최대 약 3분</p>
            )}
            <p>저장소: <span className="text-ink">{b?.repo_url ?? "—"}</span></p>
            <p>git ref: <span className="text-ink">{b?.git_ref ?? "—"}</span></p>
            <p>commit: <span className="text-ink">{b?.commit_sha ? b.commit_sha.slice(0, 8) : "—"}</span></p>
            <p>이미지: <span className="text-ink">{b && (b.images ?? []).length > 0 ? (b.images ?? []).join(", ") : "—"}</span></p>
            <p>노드: <span className="text-ink">{b?.node_name ?? "—"}</span></p>
            <p>사유: <span className="text-ink">
              {b?.reason_code ? reasonText(b.reason_code) : "—"}
            </span></p>
            <p>생성 시각: <span className="text-ink">{b?.created_at ?? "—"}</span></p>
            <p>종료 시각: <span className="text-ink">{b?.finished_at ?? "—"}</span></p>
          </Card>
          <Card>
            <h2 className="font-medium mb-2">로그</h2>
            {logQ.isLoading ? (
              <p className="text-muted">불러오는 중…</p>
            ) : logQ.isError ? (
              <p className="text-bad">{(logQ.error as ApiError).message}</p>
            ) : logQ.data?.log ? (
              <pre className="overflow-x-auto text-xs whitespace-pre-wrap">{logQ.data.log}</pre>
            ) : (
              <p className="text-muted">로그가 아직 없습니다</p>
            )}
          </Card>
        </>
      )}
    </section>
  );
}
