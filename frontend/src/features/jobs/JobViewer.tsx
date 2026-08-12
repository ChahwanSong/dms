import { useState } from "react";
import { useArtifacts, useArtifactFile, useJobLogs } from "./useArtifacts";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

type Tab =
  | { kind: "artifact"; phase: string; name: string }
  | { kind: "log"; phase: string };

function tabKey(t: Tab): string {
  return t.kind === "log" ? `log:${t.phase}` : `artifact:${t.phase}/${t.name}`;
}

// JobStatsSection.tsx(:25-36)와 같은 로직의 국소 사본 관례 -- "이 표시 하나를 위해
// 공용 모듈을 만드는 것은 이르다". 아티팩트에는 작은 파일이 흔해 KiB 단위를 더한다.
// 0 바이트는 정상값("0 B") -- "—" 는 null(크기 모름) 전용이다.
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2], ["KiB", 1024],
];
function humanBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes)) return "—";
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${bytes} B`;
}

export function JobViewer({
  jobId,
  phaseRefs,
}: {
  jobId: string;
  phaseRefs?: Record<string, string> | null;
}) {
  const [selected, setSelected] = useState<Tab | null>(null);
  const artifacts = useArtifacts(jobId);

  const selectedArtifact = selected?.kind === "artifact" ? selected : null;
  const artifactFile = useArtifactFile(
    jobId,
    selectedArtifact?.phase ?? "",
    selectedArtifact?.name ?? "",
    selectedArtifact !== null,
  );
  // 로그 탭은 하나만 선택되므로 훅도 하나면 된다(선택된 phase로 조회).
  const selectedLog = selected?.kind === "log" ? selected : null;
  const logs = useJobLogs(jobId, selectedLog?.phase ?? "", selectedLog !== null);

  if (artifacts.isLoading) return <p className="text-muted text-sm mt-3">불러오는 중…</p>;
  if (artifacts.isError) {
    return <p className="text-bad text-sm mt-3">{(artifacts.error as ApiError).message}</p>;
  }

  const entries = artifacts.data?.entries ?? [];
  const truncatedList = artifacts.data?.truncated ?? false;

  // 다운로드 라벨의 크기는 목록 entries 의 실측값이다(뷰 본문 응답의 size 가 아니다).
  // 일치 항목이 없으면 null(모름)로 두어 "—" 를 그린다 -- 0 과 null 을 섞지 않는다.
  const selectedEntry = selectedArtifact
    ? entries.find(
        (e) => e.phase === selectedArtifact.phase && e.name === selectedArtifact.name,
      )
    : undefined;

  // 로그 탭은 실제로 ref가 있는 phase에만 만든다. 예전에는 "preflight"를 하드코딩해서,
  // confirm 후 재검증(exec_preflight)이 실패한 잡을 진단할 때 *초기* preflight의 성공
  // 로그를 보여줬다 — 정확히 이 슬라이스가 존재하는 상황에서 사람을 오도한다.
  const logPhases = Object.entries(phaseRefs ?? {})
    .filter(([, ref]) => Boolean(ref))
    .map(([phase]) => phase);

  const tabs: Tab[] = [
    ...entries.map((e) => ({ kind: "artifact" as const, phase: e.phase, name: e.name })),
    ...logPhases.map((phase) => ({ kind: "log" as const, phase })),
  ];

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        {tabs.map((t) => {
          const label = t.kind === "log" ? `${t.phase} 로그` : `${t.phase}/${t.name}`;
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
              {/* a href 네비게이션에도 세션 쿠키가 실리므로 fetch+blob 우회가 필요 없다
                  (그쪽은 상한 크기까지 메모리 버퍼링이라 더 나쁘다). 오류 응답이면
                  브라우저가 JSON 오류 본문으로 이동할 수 있다 -- 설계가 수용한
                  트레이드오프다(설계 §4). */}
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <a
                  className="text-xs text-accent"
                  href={`/api/user/jobs/${jobId}/artifacts/${encodeURIComponent(selectedArtifact.phase)}/${encodeURIComponent(selectedArtifact.name)}/download`}
                  download
                >
                  다운로드 ({humanBytes(selectedEntry?.size ?? null)})
                </a>
                {artifactFile.data.truncated && (
                  <>
                    <span className="inline-block text-xs text-muted border border-black/10 rounded px-2 py-0.5">
                      뒷부분만 표시
                    </span>
                    <span className="text-xs text-muted">전체는 다운로드로 받으세요</span>
                  </>
                )}
              </div>
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
              {/* 박제 사본은 "지금 파드에서 읽은 것"이 아니다 — 어느 시점의 무엇인지
                  말해주지 않으면 사용자는 라이브로 오인한다. */}
              {logs.data.source === "archived" && (
                <span className="inline-block text-xs text-muted border border-black/10 rounded px-2 py-0.5">
                  잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB
                </span>
              )}
              {logs.data.entries.map((e) => (
                <div key={e.pod}>
                  <p className="text-xs font-medium">{e.pod}</p>
                  {e.truncated && (
                    <span className="inline-block text-xs text-muted border border-black/10 rounded px-2 py-0.5 mb-2">
                      뒷부분만 표시
                    </span>
                  )}
                  {/* log === null 비교만 쓴다: ""(빈 로그)는 정상값이라 truthy 검사로
                      묶으면 "비어 있음"이 "로그 없음"으로 둔갑한다. waiting_reason 은
                      null 을 대체하지 않고 "왜 없는지"를 병기할 뿐이다. */}
                  {e.log === null ? (
                    <p className="text-muted text-sm">
                      {e.waiting_reason
                        ? `파드 로그 없음 — ${e.waiting_reason}`
                        : "파드 로그를 더 이상 조회할 수 없습니다"}
                    </p>
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
