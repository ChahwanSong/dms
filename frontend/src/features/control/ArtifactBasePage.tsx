import { useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import type { ArtifactBaseNodeCheck } from "../../lib/types";
import { useArtifactBase, useSetArtifactBase, useValidateArtifactBase } from "./useArtifactBase";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

function Hop({ ok, pending, reason }: { ok: boolean | null; pending: boolean; reason: string | null }) {
  // null(모름)과 실패를 뭉개지 않는다(설계 §4) -- "확인 대기 중"은 실패가 아니다.
  if (pending) return <span className="text-muted">확인 대기 중</span>;
  return (
    <span>
      {ok ? <span className="text-ok">정상</span> : <span className="text-bad">실패</span>}
      {reason && <span className="ml-2 text-muted">{reason}</span>}
    </span>
  );
}

function NodeRow({ n }: { n: ArtifactBaseNodeCheck }) {
  return (
    <tr>
      <td className="py-1 pr-3">{n.node_name}</td>
      <td className="py-1 pr-3">{n.pending ? "확인 대기 중" : n.exists ? "있음" : "없음"}</td>
      <td className="py-1 pr-3">{n.pending ? "확인 대기 중" : n.writable ? "가능" : "불가"}</td>
    </tr>
  );
}

export function ArtifactBasePage() {
  const q = useArtifactBase();
  const setBase = useSetArtifactBase();
  const validate = useValidateArtifactBase();
  const [uri, setUri] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const save = (force: boolean) => {
    setBase.mutate({ uri, force }, {
      onSuccess: () => setConfirmOpen(false),
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
          </Card>
          <Card>
            <h2 className="font-medium mb-2">3홉 검증</h2>
            <dl className="text-sm space-y-1">
              <div className="flex gap-2">
                <dt className="text-muted w-28">API(즉석)</dt>
                <dd><Hop ok={d.checks.api.ok} pending={false} reason={d.checks.api.reason} /></dd>
              </div>
              <div className="flex gap-2">
                <dt className="text-muted w-28">컨트롤러</dt>
                <dd><Hop ok={d.checks.controller.ok} pending={d.checks.controller.pending}
                         reason={d.checks.controller.reason} /></dd>
              </div>
            </dl>
            {d.checks.nodes.length > 0 && (
              <table className="text-sm mt-3 w-full">
                <thead><tr className="text-left text-muted">
                  <th className="pr-3 font-medium">노드</th>
                  <th className="pr-3 font-medium">디렉터리 존재</th>
                  <th className="pr-3 font-medium">쓰기</th></tr></thead>
                <tbody>{d.checks.nodes.map((n) => <NodeRow key={n.node_name} n={n} />)}</tbody>
              </table>
            )}
            {/* 정직한 한계 표기(설계 §2.4b): W_OK 판정 주체를 화면에 그대로 적는다 */}
            <p className="text-xs text-muted mt-2">쓰기 가능 여부는 에이전트 프로세스(uid) 기준입니다 — 잡 파드 요청자 권한과 다를 수 있습니다</p>
          </Card>
          <Card>
            <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); save(false); }}>
              <label className="block">새 경로 (file:///절대경로)
                <input aria-label="새 경로" className={field} value={uri}
                       onChange={(e) => setUri(e.target.value)} /></label>
              {validate.isSuccess && (
                <p className="text-ok">{`검증 통과: ${validate.data.normalized}`}</p>
              )}
              {validate.isError && (
                <p className="text-bad">{(validate.error as ApiError).message}</p>
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
          <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}
                  title="아티팩트 경로 강제 변경" trigger={<span aria-hidden="true" />}>
            <div className="space-y-3 text-sm">
              {/* 설계 §2.3: 잠금은 실패 잡의 stdout/stderr(디스크의 유일한 진단
                  사본)까지 지키는 장치다 -- 강제 변경의 대가를 그대로 보여주고
                  확인시킨 뒤에만 force=true 를 보낸다. */}
              <p>{`기존 잡 ${d.locked_by_jobs}건이 있습니다. 경로를 바꾸면 이 잡들의 아티팩트·로그 열람이 깨집니다.`}</p>
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
