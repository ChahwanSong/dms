import { useEffect, useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { ApiError } from "../../lib/api";
import { useNodes } from "../nodes/useNodes";
import { useJobMetrics } from "../dashboard/useMetrics";
import { kpiFromStates } from "../dashboard/Dashboard";
import { useControlState, useControlHistory, useSetControlState } from "./useControlState";
import { kstStampOrDash, kstStamp } from "../../lib/datetime";
import type { ControlState } from "../../lib/types";
import type { ControlHistoryEntry } from "./useControlState";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

// 이력 diff 대상 필드와 한국어 라벨. before/after 전체 스냅샷에서 이 다섯만 비교한다
// -- changed_by/changed_at 등 메타까지 diff 에 넣으면 매 행이 "변경 시각이 바뀜"이 된다.
const DIFF_FIELDS: { key: keyof ControlState; label: string;
                     fmt: (v: unknown) => string }[] = [
  { key: "maintenance", label: "유지보수", fmt: (v) => (v ? "ON" : "OFF") },
  { key: "drain", label: "드레인", fmt: (v) => (v ? "ON" : "OFF") },
  { key: "reason", label: "사유", fmt: (v) => (v ? `'${v}'` : "—") },
  { key: "build_node_name", label: "빌드 노드", fmt: (v) => String(v ?? "—") },
  { key: "build_source_path", label: "소스 경로", fmt: (v) => String(v ?? "—") },
];

// 한 이력 행의 변경 내용 요약: "유지보수 OFF→ON · 사유 —→'점검'". 변한 게 없으면
// (동일 저장 재클릭) "변경 없음" -- 지어내지 않는다.
export function diffText(e: ControlHistoryEntry): string {
  if (!e.before) return "초기 설정";
  const parts: string[] = [];
  for (const f of DIFF_FIELDS) {
    const b = e.before?.[f.key]; const a = e.after?.[f.key];
    // 0/null/undefined 정규화: maintenance 0 과 null 은 화면상 같은 OFF 다.
    if (f.fmt(b) !== f.fmt(a)) parts.push(`${f.label} ${f.fmt(b)}→${f.fmt(a)}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "변경 없음";
}

// 상대 시각: "3분 전". now 는 호출자가 넘긴다(렌더마다 자라는 숫자 방지 --
// BuildHistory 의 dataUpdatedAt 관례). export: ArtifactBasePage(같은 폴더)가
// 검증 시각·노드 보고 시각에 같은 문구를 쓴다(슬라이스 38).
export function relTime(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const ms = now - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return "";
  const m = Math.floor(ms / 60000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}시간 전` : `${Math.floor(h / 24)}일 전`;
}

export function ControlStatePage() {
  const q = useControlState();
  const nodesQ = useNodes();
  const historyQ = useControlHistory();
  // 영향 요약 재료: 대시보드와 같은 잡 집계(24h 창의 by_state 에서 실행/대기).
  const jobsQ = useJobMetrics(24);
  const setControlState = useSetControlState();
  const [maintenance, setMaintenance] = useState(false);
  const [drain, setDrain] = useState(false);
  const [reason, setReason] = useState("");
  const [buildNodeName, setBuildNodeName] = useState("");
  const [buildSourcePath, setBuildSourcePath] = useState("");

  useEffect(() => {
    if (!q.data) return;
    setMaintenance(q.data.maintenance === 1);
    setDrain(q.data.drain === 1);
    setReason(q.data.reason ?? "");
    setBuildNodeName(q.data.build_node_name ?? "");
    setBuildSourcePath(q.data.build_source_path ?? "");
  }, [q.data]);

  const submit = () => {
    setControlState.mutate({
      maintenance, drain,
      reason: reason.trim() === "" ? null : reason,
      build_node_name: buildNodeName.trim() === "" ? null : buildNodeName,
      build_source_path: buildSourcePath.trim() === "" ? null : buildSourcePath.trim(),
    });
  };

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">컨트롤 상태</h1>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <>
          {q.data?.maintenance === 1 && (
            <p className="text-bad font-medium">유지보수 중 — 새 작업 제출이 차단됩니다. 이미 접수된 배치의 작업은 계속 진행되므로, 클러스터 작업을 완전히 멈추려면 드레인도 함께 켜세요</p>
          )}
          {q.data?.drain === 1 && (
            <p className="text-bad font-medium">드레인 중 — 진행 중인 작업이 더 전진하지 않습니다</p>
          )}
          <Card>
            <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
              {/* 토글마다 "무엇을 막고 무엇은 계속되는지"를 결정하는 자리(체크박스
                  옆)에서 한 줄로 말한다 -- 별도 도움말 화면으로 밀면 켜기 전에
                  안 읽는다. 캡션은 label 밖(체크박스 클릭 표적과 분리)이다. */}
              <div>
                <label className="flex items-center gap-2 font-medium">
                  <input type="checkbox" aria-label="유지보수" checked={maintenance}
                         onChange={(e) => setMaintenance(e.target.checked)} /> 유지보수
                  <span className="text-muted text-xs font-normal">입구 차단</span>
                </label>
                <p className="text-muted text-xs mt-1 ml-6">
                  신규 제출(잡·배치·빌드·릴리스)을 503 으로 막습니다 — 이미 접수돼
                  진행 중인 작업은 완주합니다. 관리자도 예외 없음.
                </p>
              </div>
              <div>
                <label className="flex items-center gap-2 font-medium">
                  <input type="checkbox" aria-label="드레인" checked={drain}
                         onChange={(e) => setDrain(e.target.checked)} /> 드레인
                  <span className="text-muted text-xs font-normal">진행 정지</span>
                </label>
                <p className="text-muted text-xs mt-1 ml-6">
                  잡 진행(스테퍼)을 지금 자리에서 멈춥니다 — 떠 있는 파드는 죽이지
                  않고, 끄면 멈춘 자리부터 재개됩니다.
                </p>
              </div>
              <p className="text-muted text-xs border-t border-line pt-3">
                완전 정지 = 둘 다 켜기 — 유지보수가 입구를, 드레인이 진행을 막습니다.
                저장 즉시 적용됩니다(재시작 없음).
              </p>
              <label className="block">사유
                <input aria-label="사유" className={field} value={reason}
                       onChange={(e) => setReason(e.target.value)} /></label>
              <label className="block">빌드 노드
                {/* I1: 자유 입력 금지 -- 오타가 nodeSelector로 새면 빌드 파드가
                    영원히 Pending이다. agent_nodes에 실제로 보고된 노드 중에서만
                    고르게 한다(select). */}
                <select aria-label="빌드 노드" className={field} value={buildNodeName}
                        onChange={(e) => setBuildNodeName(e.target.value)}>
                  <option value="">지정 안 함</option>
                  {(nodesQ.data ?? []).map((n) => (
                    <option key={n.node_name} value={n.node_name}>{n.node_name}</option>
                  ))}
                </select>
              </label>
              <label className="block">빌드 소스 경로
                {/* 노드와 달리 목록 대조가 불가하다(서버는 빌드 노드의 파일시스템을
                    못 본다) -- 모양(절대 경로)만 저장 시 검증하고, 실재는 빌드
                    프리플라이트가 노드 위에서 검사한다. */}
                <input aria-label="빌드 소스 경로" className={field} value={buildSourcePath}
                       placeholder="/home/mason/dms-dev/dms"
                       onChange={(e) => setBuildSourcePath(e.target.value)} />
                <span className="block text-muted text-xs mt-1">
                  빌드 노드에서 DMS 저장소가 있는 절대 경로 — 빌드는 이 경로의 로컬
                  소스(미커밋 변경 포함)로 진행됩니다
                </span>
              </label>
              {setControlState.isError && (
                <p className="text-bad">{(setControlState.error as ApiError).message}</p>
              )}
              <div className="flex justify-end pt-2">
                <Button type="submit" disabled={setControlState.isPending}>저장</Button>
              </div>
            </form>
          </Card>
          <Card className="space-y-3">
            <h2 className="font-medium">현재 상태</h2>
            {/* 위 폼은 "편집 중 값"이라 저장 전엔 실제와 갈릴 수 있다 -- 이 카드가
                서버의 **적용값**을 말한다(폼과 분리된 진실 표시). */}
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StatusPill state={q.data?.maintenance === 1 ? "유지보수 ON" : "유지보수 OFF"}
                          variant={q.data?.maintenance === 1 ? "bad" : "ok"} />
              <StatusPill state={q.data?.drain === 1 ? "드레인 ON" : "드레인 OFF"}
                          variant={q.data?.drain === 1 ? "bad" : "ok"} />
              {q.data?.reason && (
                <span className="text-muted">사유: <span className="text-ink">{q.data.reason}</span></span>
              )}
            </div>
            {/* 영향 요약: 드레인을 켜면 무엇이 멈추는지(켠 상태면 무엇이 동결 중인지)
                의 재료. null(집계 실패)은 표시하지 않는다 -- 0 과 모름을 섞지 않는다. */}
            {Array.isArray(jobsQ.data?.by_state) && (() => {
              const kpi = kpiFromStates(jobsQ.data.by_state);
              return (
                <p className="text-sm text-muted">
                  진행 중 작업: 실행 중 <span className="text-ink font-medium tabular-nums">{kpi.running}</span>
                  {" · "}대기 <span className="text-ink font-medium tabular-nums">{kpi.pending}</span>
                  {q.data?.drain === 1 && <span className="text-bad"> — 드레인으로 동결 중</span>}
                </p>
              );
            })()}
            {/* 항목당 한 줄 -- 소스 경로가 길어 한 줄에 붙이면 좁은 폭에서 임의
                위치로 접힌다(사용자 지적). */}
            <div className="text-sm text-muted space-y-1 border-t border-line pt-3">
              <p>빌드 노드: <span className="text-ink font-medium">{q.data?.build_node_name ?? "—"}</span></p>
              <p>소스 경로: <span className="text-ink font-mono">{q.data?.build_source_path ?? "—"}</span></p>
              <p>마지막 변경: <span className="text-ink font-medium">{q.data?.changed_by ?? "—"}</span>
                 {" · "}{kstStampOrDash(q.data?.changed_at)}
                 {q.data?.changed_at && (
                   <span> ({relTime(q.data.changed_at, q.dataUpdatedAt)})</span>
                 )}</p>
            </div>
          </Card>
          <Card>
            <h2 className="font-medium mb-3">변경 이력</h2>
            {historyQ.isError ? (
              <p className="text-bad text-sm">{(historyQ.error as ApiError).message}</p>
            ) : (historyQ.data ?? []).length === 0 ? (
              <p className="text-muted text-sm">이력 없음</p>
            ) : (
              <Table>
                <thead>
                  <tr className="text-muted whitespace-nowrap">
                    <th className="py-2">시각</th><th>변경자</th><th>변경 내용</th>
                  </tr>
                </thead>
                <tbody>
                  {(historyQ.data ?? []).map((e, i) => (
                    <tr key={`${e.at}-${i}`} className="border-t border-black/5">
                      <td className="py-2 text-muted whitespace-nowrap">{kstStamp(e.at)}</td>
                      <td className="whitespace-nowrap">{e.actor ?? "—"}</td>
                      {/* diff 가 길 수 있다(여러 필드 동시 변경) -- 행 높이를 지키러
                          자르지 않는다: 이 표는 폴링이 없고 행이 최대 10개라 접힘의
                          비용이 낮고, 무엇이 바뀌었는지가 이 화면의 존재 이유다. */}
                      <td className="text-muted">{diffText(e)}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </>
      )}
    </section>
  );
}
