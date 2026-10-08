import { useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { WarnTooltip } from "../../components/ui/WarnTooltip";
import { ApiError, reasonText } from "../../lib/api";
import type { ArtifactBaseInfo, ArtifactBaseNodeCheck } from "../../lib/types";
import { relTime } from "./ControlStatePage";
import { kstStamp } from "../../lib/datetime";
import { useArtifactBase, useArtifactBaseHistory, useSetArtifactBase,
         useValidateArtifactBase } from "./useArtifactBase";
import type { ArtifactBaseHistoryEntry } from "./useArtifactBase";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

/** 3홉 요약 배지: 실패가 하나라도 있으면 "문제 N건" -- 대기(모름)와 뭉개지
    않는다(설계 §4). 실패 없이 대기만 있으면 "확인 대기 N건", 다 통과면
    "모두 정상". */
export function hopSummary(checks: ArtifactBaseInfo["checks"]):
    { label: string; variant: "ok" | "bad" | "busy" } {
  let bad = 0, pending = 0;
  if (!checks.api.ok) bad += 1;
  if (checks.controller.pending) pending += 1;
  else if (!checks.controller.ok) bad += 1;
  for (const n of checks.nodes) {
    if (n.pending) pending += 1;
    else if (!n.exists || !n.writable) bad += 1;
  }
  if (bad > 0) return { label: `문제 ${bad}건`, variant: "bad" };
  if (pending > 0) return { label: `확인 대기 ${pending}건`, variant: "busy" };
  return { label: "모두 정상", variant: "ok" };
}

/** 이력 한 행의 "무엇→무엇". before 의 uri 가 null 이면 당시엔 env 기본이
    유효했다 -- 값을 지어내지 않고 그 사실을 밝힌다. */
export function histText(e: ArtifactBaseHistoryEntry): string {
  const b = e.before?.artifact_base_uri ?? "(env 기본)";
  return `${b} → ${e.after?.artifact_base_uri ?? "—"}`;
}

function Hop({ ok, pending, reason }: { ok: boolean | null; pending: boolean; reason: string | null }) {
  // null(모름)과 실패를 뭉개지 않는다(설계 §4) -- "확인 대기 중"은 실패가 아니다.
  if (pending) return <span className="text-muted">확인 대기 중</span>;
  return (
    <span>
      {ok ? <span className="text-ok">정상</span> : <span className="text-bad">실패</span>}
      {reason && <span className="ml-2 text-muted">{reasonText(reason)}</span>}
    </span>
  );
}

function NodeRow({ n, now }: { n: ArtifactBaseNodeCheck; now: number }) {
  return (
    <tr>
      <td className="py-1 pr-6">{n.node_name}</td>
      <td className="py-1 pr-6">{n.pending ? "확인 대기 중" : n.exists ? "있음" : "없음"}</td>
      <td className="py-1 pr-6">{n.pending ? "확인 대기 중" : n.writable ? "가능" : "불가"}</td>
      {/* 보고 시각이 있어야 "확인 대기 중"이 왜 대기인지 보인다 -- 오래된 보고
          (fresh=false)는 에이전트가 죽었다는 신호라 실패색으로 구분한다 */}
      <td className="py-1 text-muted whitespace-nowrap">
        {relTime(n.reported_at, now) || "—"}
        {!n.fresh && <span className="text-bad"> · 오래됨</span>}
      </td>
    </tr>
  );
}

export function ArtifactBasePage() {
  const q = useArtifactBase();
  const historyQ = useArtifactBaseHistory();
  const setBase = useSetArtifactBase();
  const validate = useValidateArtifactBase();
  const [uri, setUri] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const save = (force: boolean) => {
    setBase.mutate({ uri, force }, {
      // 성공 시 입력을 비운다: 남은 옛 입력으로 저장을 또 누르는 실수 방지 +
      // isSuccess 문구("저장됨")의 대상이 명확해진다.
      onSuccess: () => { setConfirmOpen(false); setUri(""); },
      onError: (e) => {
        // 잠금(409)만 다이얼로그로 승격 -- 그 외 오류는 폼 아래 문구로 남는다.
        if ((e as ApiError).code === "artifact_base_locked") setConfirmOpen(true);
      },
    });
  };
  const d = q.data;
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">아티팩트 경로</h1>
      <p className="text-sm text-muted">
        잡의 stdout·stderr 와 스캔 요약이 저장되는 위치입니다. DB 설정이 env
        기본값보다 우선하며, 저장 즉시 재시작 없이 반영됩니다.
      </p>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : d && (
        <>
          <Card>
            <h2 className="font-medium mb-2">현재 값</h2>
            <p className="text-sm">
              <span className="font-mono">{d.effective}</span>
              <span className={`ml-2 rounded px-2 py-0.5 text-xs ${d.source === "db" ? "bg-accent text-white" : "bg-black/10"}`}>
                {d.source === "db" ? "DB 설정" : "env 기본"}
              </span>
            </p>
            <p className="text-sm text-muted mt-1">env: <span className="font-mono">{d.env_value}</span></p>
            <p className="text-sm text-muted">DB: <span className="font-mono">{d.db_value ?? "—"}</span></p>
            {/* 잠금 사전 고지(설계 §2.3): 저장을 눌러 409 를 맞기 전에 안다 */}
            <p className="text-sm mt-2">
              {d.locked_by_jobs > 0 ? (
                <>
                  <span className="font-medium">잡 {d.locked_by_jobs}건</span>
                  {/* 서버 locked_by_jobs = 잡 + 삭제한 작업의 결과 파일 정리 대기(request_purges) — 정리 대기 중 base 가
                      바뀌면 그 파일은 옛 경로에 남는다(routes_artifact_base._job_count). */}
                  <span className="text-muted">이 이 경로를 참조합니다(삭제한 작업의 결과 파일 정리 대기 포함) — 변경하면 그 잡들의 아티팩트·로그 열람이 깨지고 정리 대기 중인 결과 파일은 옛 경로에 남으므로 강제 확인을 거칩니다</span>
                </>
              ) : (
                <span className="text-muted">참조하는 잡이 없어 잠금 없이 변경할 수 있습니다</span>
              )}
            </p>
          </Card>
          <Card>
            <div className="flex items-center gap-2 mb-1">
              <h2 className="font-medium">3홉 검증</h2>
              <StatusPill state={hopSummary(d.checks).label}
                          variant={hopSummary(d.checks).variant} />
            </div>
            <p className="text-xs text-muted mb-3">
              같은 경로를 세 관점에서 확인합니다 — API 파드(조회 시 즉석, 쓰기 왕복) ·
              컨트롤러(30초 주기, 쓰기 왕복) · 노드 에이전트(60초 주기, 디렉터리 존재·쓰기
              권한). API·컨트롤러는 root(uid 0, 권한 없음)로 돌므로 root:root 경로가 정상이며,
              허용 접두(DMS_ARTIFACT_BASE_ALLOWED_PREFIXES) 밖 경로는 저장되지 않습니다.
            </p>
            <dl className="text-sm space-y-1">
              <div className="flex gap-2">
                <dt className="text-muted w-28">API(즉석)</dt>
                <dd><Hop ok={d.checks.api.ok} pending={false} reason={d.checks.api.reason} /></dd>
              </div>
              <div className="flex gap-2">
                <dt className="text-muted w-28">컨트롤러</dt>
                <dd>
                  <Hop ok={d.checks.controller.ok} pending={d.checks.controller.pending}
                       reason={d.checks.controller.reason} />
                  {!d.checks.controller.pending && d.checks.controller.checked_at && (
                    <span className="ml-2 text-muted text-xs">
                      ({relTime(d.checks.controller.checked_at, q.dataUpdatedAt)})
                    </span>
                  )}
                </dd>
              </div>
            </dl>
            {d.checks.nodes.length > 0 && (
              <table className="text-sm mt-3">
                <thead><tr className="text-left text-muted">
                  <th className="pr-6 font-medium">노드</th>
                  <th className="pr-6 font-medium">디렉터리 존재</th>
                  <th className="pr-6 font-medium">쓰기</th>
                  <th className="font-medium">보고</th></tr></thead>
                <tbody>{d.checks.nodes.map((n) =>
                  <NodeRow key={n.node_name} n={n} now={q.dataUpdatedAt} />)}</tbody>
              </table>
            )}
            {/* 정직한 한계 표기(설계 §2.4b): W_OK 판정 주체를 화면에 그대로 적는다 */}
            <p className="text-xs text-muted mt-2">쓰기 가능 여부는 에이전트 프로세스(uid) 기준입니다 — 잡 파드 요청자 권한과 다를 수 있습니다</p>
          </Card>
          <Card>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <h2 className="font-medium">경로 변경</h2>
              {/* 경로 변경 주의점(2026-09-30 사용자 요청: "툴팁으로 경고성으로"). 각 항목은
                  실제 동작 근거가 있다: 열람은 **현재** base 기준(api/artifacts) · stepper 는
                  단계마다 base 를 새로 읽는다 · 잡 파드는 base 를 hostPath 로 받는다 · 비 root
                  잡의 도구는 요청자 uid 로 base 를 통과한다(preflight artifact_base_not_traversable)
                  · 3홉의 소유자·o+w·g+w·POSIX ACL·o+x 검사와 허용 접두(artifact_base.roundtrip_artifact_base,
                  routes_artifact_base). g+w·ACL 금지는 잡이 LDAP 보조 그룹을 달고 돌기 때문(2026-10-07 D6),
                  o+x 필수는 launcher 가 보조 그룹 없이 base 를 지나기 때문(D12) -- 그룹으로 여는 750/710 도 거부. */}
              <WarnTooltip label="변경 전 주의">
                <p className="font-semibold text-bad mb-1">아티팩트 경로를 바꾸기 전에 확인하세요</p>
                <ul className="list-disc space-y-1 pl-4">
                  <li>기존 잡의 로그(stdout·stderr)·스캔 리포트는 <strong>옮겨지지 않습니다</strong> — 열람은 새 경로를 보므로 이전 잡의 기록이 보이지 않게 됩니다(참조 잡이 있으면 강제 확인).</li>
                  <li>진행 중인 잡이 없을 때 바꾸세요 — 실행 중인 잡은 이후 단계의 기록을 새 경로에 써서 기록이 두 곳으로 흩어집니다.</li>
                  <li>새 경로는 <strong>모든 노드에 같은 경로로 마운트된 공유 스토리지</strong>여야 합니다(잡 파드가 노드의 그 경로를 그대로 씁니다) — 저장 후 3홉 검증에서 노드별 존재·쓰기를 확인하세요.</li>
                  <li>디렉토리는 root 소유·world-writable 금지, 그리고 <strong>그룹 쓰기(g+w)·POSIX ACL 쓰기 항목·기본 ACL 금지</strong> — 작업이 실행 계정의 LDAP 보조 그룹으로도 돌기 때문에 그룹 쓰기는 그 그룹 사용자에게 경로 쓰기를 엽니다(artifact_base_group_writable).</li>
                  <li><strong>다른 사용자 실행(x) 권한 필수(711 또는 755)</strong> — 없으면 3홉 검증과 저장이 artifact_base_not_traversable 로 거부합니다(예전 안내가 허용하던 750/710 도 이제 거부).</li>
                  <li>허용 접두(DMS_ARTIFACT_BASE_ALLOWED_PREFIXES) 밖 경로는 저장되지 않습니다.</li>
                </ul>
              </WarnTooltip>
            </div>
            <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); save(false); }}>
              <label className="block">새 경로 (file:///절대경로)
                <input aria-label="새 경로" className={field} value={uri}
                       onChange={(e) => {
                         setUri(e.target.value);
                         // 입력이 바뀌면 옛 입력의 검증·저장 결과 문구는 거짓이
                         // 된다 -- 그 자리에서 지운다(stale 문구 방지).
                         validate.reset(); setBase.reset();
                       }} /></label>
              {validate.isSuccess && (
                <p className="text-ok">{`검증 통과: ${validate.data.normalized}`}</p>
              )}
              {validate.isError && (
                <p className="text-bad">{(validate.error as ApiError).message}</p>
              )}
              {setBase.isSuccess && (
                <p className="text-ok">저장됨 — 컨트롤러·노드 검증이 곧 새 경로로 갱신됩니다</p>
              )}
              {setBase.isError && (setBase.error as ApiError).code !== "artifact_base_locked" && (
                <p className="text-bad">{(setBase.error as ApiError).message}</p>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" disabled={validate.isPending || uri === ""}
                        onClick={() => validate.mutate({ uri })}>검증</Button>
                <Button type="submit" disabled={setBase.isPending || uri === ""}>저장</Button>
              </div>
            </form>
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
                      <td className="text-muted">
                        <span className="font-mono">{histText(e)}</span>
                        {/* 강제 통과는 affected_jobs 건의 열람을 깬 결정(설계
                            §2.3) -- 이력에서도 실패색으로 도드라져야 한다 */}
                        {e.after?.forced && (
                          <span className="ml-2 whitespace-nowrap rounded bg-badbg px-1.5 py-0.5 text-xs text-bad">
                            강제 · 잡 {e.after.affected_jobs ?? 0}건 영향
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
          <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}
                  title="아티팩트 경로 강제 변경" trigger={<span aria-hidden="true" />}>
            <div className="space-y-3 text-sm">
              {/* 설계 §2.3: 잠금은 실패 잡의 stdout/stderr(디스크의 유일한 진단
                  사본)까지 지키는 장치다 -- 강제 변경의 대가를 그대로 보여주고
                  확인시킨 뒤에만 force=true 를 보낸다. */}
              <p>{`기존 잡 ${d.locked_by_jobs}건이 있습니다(삭제한 작업의 결과 파일 정리 대기 포함). 경로를 바꾸면 이 잡들의 아티팩트·로그 열람이 깨집니다. 정리 대기 중인 결과 파일은 지워지지 않고 옛 경로에 남습니다.`}</p>
              <div className="flex justify-end gap-2">
                <Button type="button" onClick={() => setConfirmOpen(false)}>취소</Button>
                <Button type="button" disabled={setBase.isPending}
                        onClick={() => save(true)}>강제 변경</Button>
              </div>
            </div>
          </Dialog>
        </>
      )}
    </section>
  );
}
