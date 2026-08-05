import { useState } from "react";
import { useArtifacts, useArtifactFile, useJobLogs } from "./useArtifacts";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

type Tab = { kind: "artifact"; phase: string; name: string } | { kind: "log" };

function tabKey(t: Tab): string {
  return t.kind === "log" ? "log:preflight" : `artifact:${t.phase}/${t.name}`;
}

export function JobViewer({ jobId }: { jobId: string }) {
  const [selected, setSelected] = useState<Tab | null>(null);
  const artifacts = useArtifacts(jobId);

  const selectedArtifact = selected?.kind === "artifact" ? selected : null;
  const artifactFile = useArtifactFile(
    jobId,
    selectedArtifact?.phase ?? "",
    selectedArtifact?.name ?? "",
    selectedArtifact !== null,
  );
  const logs = useJobLogs(jobId, "preflight", selected?.kind === "log");

  if (artifacts.isLoading) return <p className="text-muted text-sm mt-3">불러오는 중…</p>;
  if (artifacts.isError) {
    return <p className="text-bad text-sm mt-3">{(artifacts.error as ApiError).message}</p>;
  }

  const entries = artifacts.data?.entries ?? [];
  const truncatedList = artifacts.data?.truncated ?? false;

  const tabs: Tab[] = [
    ...entries.map((e) => ({ kind: "artifact" as const, phase: e.phase, name: e.name })),
    { kind: "log" as const },
  ];

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        {tabs.map((t) => {
          const label = t.kind === "log" ? "preflight 로그" : `${t.phase}/${t.name}`;
          const isSelected = selected !== null && tabKey(selected) === tabKey(t);
          return (
            <Button
              key={tabKey(t)}
              variant={isSelected ? "primary" : "ghost"}
              onClick={() => setSelected(t)}
            >
              {label}
            </Button>
          );
        })}
        {truncatedList && <span className="text-muted text-xs">일부 항목만 표시됩니다</span>}
      </div>

      <div className="mt-3">
        {selectedArtifact && (
          artifactFile.isLoading ? (
            <p className="text-muted text-sm">불러오는 중…</p>
          ) : artifactFile.isError ? (
            <p className="text-bad text-sm">{(artifactFile.error as ApiError).message}</p>
          ) : artifactFile.data ? (
            <div>
              {artifactFile.data.truncated && (
                <span className="inline-block text-xs text-muted border border-black/10 rounded px-2 py-0.5 mb-2">
                  뒷부분만 표시
                </span>
              )}
              <pre className="overflow-x-auto text-xs whitespace-pre-wrap">{artifactFile.data.content}</pre>
            </div>
          ) : null
        )}

        {selected?.kind === "log" && (
          logs.isLoading ? (
            <p className="text-muted text-sm">불러오는 중…</p>
          ) : logs.isError ? (
            <p className="text-bad text-sm">{(logs.error as ApiError).message}</p>
          ) : logs.data ? (
            <div className="space-y-3">
              {logs.data.entries.map((e) => (
                <div key={e.pod}>
                  <p className="text-xs font-medium">{e.pod}</p>
                  {e.log === null ? (
                    <p className="text-muted text-sm">파드 로그를 더 이상 조회할 수 없습니다</p>
                  ) : (
                    <pre className="overflow-x-auto text-xs whitespace-pre-wrap">{e.log}</pre>
                  )}
                </div>
              ))}
            </div>
          ) : null
        )}
      </div>
    </div>
  );
}
