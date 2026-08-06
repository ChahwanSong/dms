import { useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Table } from "../../components/ui/Table";
import { ApiError, REASON_MESSAGES } from "../../lib/api";
import { useControlState } from "../control/useControlState";
import { useBuilds, useSubmitBuild } from "./useBuilds";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

// 빌드 이미지 3종과 의존 순서: dms-mpifileutils → dms → dms-agent.
// 기본 체크는 dms만이다 — dms-mpifileutils는 소스에서 컴파일해 매우 오래 걸린다.
const IMAGES = ["dms-mpifileutils", "dms", "dms-agent"] as const;

export function BuildsPage() {
  const q = useBuilds();
  const controlQ = useControlState();
  const submitBuild = useSubmitBuild();
  const [gitRef, setGitRef] = useState("main");
  const [images, setImages] = useState<string[]>(["dms"]);

  const buildNodeName = controlQ.data?.build_node_name ?? null;
  const builds = Array.isArray(q.data) ? q.data : [];
  const canSubmit = buildNodeName !== null && images.length > 0;

  const toggleImage = (name: string) => {
    setImages((prev) => (prev.includes(name) ? prev.filter((i) => i !== name) : [...prev, name]));
  };

  const submit = () => {
    submitBuild.mutate({ git_ref: gitRef, images });
  };

  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">빌드</h1>
      {buildNodeName === null && (
        <p className="text-bad font-medium">{REASON_MESSAGES.build_node_not_set}</p>
      )}
      <Card>
        <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <label className="block">git ref
            <input aria-label="git ref" className={field} value={gitRef}
                   onChange={(e) => setGitRef(e.target.value)} /></label>
          <div>
            <span className="block mb-1">이미지</span>
            <div className="space-y-1">
              {IMAGES.map((name) => (
                <label key={name} className="flex items-center gap-2">
                  <input type="checkbox" aria-label={name} checked={images.includes(name)}
                         onChange={() => toggleImage(name)} /> {name}
                </label>
              ))}
            </div>
          </div>
          {submitBuild.isError && (
            <p className="text-bad">{(submitBuild.error as ApiError).message}</p>
          )}
          <div className="flex justify-end pt-2">
            <Button type="submit" disabled={!canSubmit || submitBuild.isPending}>빌드 시작</Button>
          </div>
        </form>
      </Card>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <Table>
          <thead>
            <tr className="text-muted">
              <th className="py-2">시각</th><th>ref</th><th>commit</th><th>이미지</th>
              <th>노드</th><th>태그</th><th>상태</th>
            </tr>
          </thead>
          <tbody>
            {builds.map((b) => (
              <tr key={b.build_id} className="border-t border-black/5">
                <td className="py-2">{b.created_at}</td>
                <td>{b.git_ref}</td>
                <td className="text-muted">{b.commit_sha ? b.commit_sha.slice(0, 8) : "—"}</td>
                <td>{b.images.join(", ")}</td>
                <td>{b.node_name ?? "—"}</td>
                <td>{b.tag ?? "—"}</td>
                <td>{b.state}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
