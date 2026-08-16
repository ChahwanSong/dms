import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { ApiError, REASON_MESSAGES, reasonText } from "../../lib/api";
import { buildPillVariant, isTerminal } from "../../lib/jobState";
import { formatDuration, spanMs } from "../../lib/duration";
import { useControlState } from "../control/useControlState";
import { useBuilds, useSubmitBuild } from "./useBuilds";
import type { Build } from "../../lib/types";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

// 빌드 이미지 3종과 의존 순서: dms-mpifileutils → dms → dms-agent.
// 기본 체크는 dms만이다 — dms-mpifileutils는 소스에서 컴파일해 매우 오래 걸린다.
const IMAGES = ["dms-mpifileutils", "dms", "dms-agent"] as const;

// repositories/builds.py:BUILD_IMAGES 의 주석이 말하는 의존 관계의 화면 쪽 미러:
// dms-agent 의 Dockerfile 은 앞의 둘을 **같은 태그로** FROM 한다. 함께 빌드하지
// 않으면 그 태그가 레지스트리에 이미 있어야 성공한다 — 없으면 파드가 pull 에서
// 죽고, 사용자는 "왜 agent 만 실패하지"를 로그에서 찾아야 한다.
const AGENT_DEPS = ["dms", "dms-mpifileutils"] as const;

// 최근 ref 빠른 선택 개수. 목록 데이터에서 파생한다 — 신규 API 없이 "방금 쓰던
// 브랜치"를 다시 치지 않게 하는 것이 전부라 3개면 족하다.
const REF_SUGGESTIONS = 3;

const PAGE_SIZE = 20;

// 상태 필터. 진행 중 판정은 jobState 의 종단 집합을 재사용한다(빌드 상태
// Pending/Running/Succeeded/Failed 는 그 집합과 그대로 맞는다 — useBuilds 주석).
const FILTERS: { key: string; label: string; match: (state: string) => boolean }[] = [
  { key: "all", label: "전체", match: () => true },
  { key: "active", label: "진행 중", match: (s) => !isTerminal(s) },
  { key: "succeeded", label: "성공", match: (s) => s === "Succeeded" },
  { key: "failed", label: "실패", match: (s) => s === "Failed" },
];

// 경과(진행 중)·소요(종단) 표기. now 는 호출자가 넘긴다.
//
// null(모름)을 "—"로 접는 자리가 여기다: 종단인데 finished_at 이 없으면(워처가
// 종료 시각을 못 남긴 옛 행) 소요를 **지어내지 않는다** — 지금 시각을 끝으로
// 쓰면 이미 끝난 빌드가 계속 자라는 거짓 숫자가 된다.
function spentText(b: Build, now: number): string {
  const terminal = isTerminal(b.state);
  const ms = terminal ? spanMs(b.created_at, b.finished_at) : spanMs(b.created_at, now);
  if (ms === null) return "—";
  return `${formatDuration(ms)} ${terminal ? "소요" : "경과"}`;
}

export function BuildsPage() {
  const q = useBuilds();
  const controlQ = useControlState();
  const submitBuild = useSubmitBuild();
  const [gitRef, setGitRef] = useState("main");
  const [images, setImages] = useState<string[]>(["dms"]);
  const [filter, setFilter] = useState<string>("all");
  const [page, setPage] = useState(1);

  const buildNodeName = controlQ.data?.build_node_name ?? null;
  const builds = useMemo(() => (Array.isArray(q.data) ? q.data : []), [q.data]);
  const canSubmit = buildNodeName !== null && images.length > 0;

  // "지금"은 Date.now() 가 아니라 **마지막 성공 조회 시각**이다. 목록은 진행 중
  // 항목이 있을 때만 5초로 폴링하는데(useBuilds), 같은 데이터가 돌아오면 참조가
  // 유지돼 재렌더가 안 일어난다 — dataUpdatedAt 을 읽으면 매 폴링마다 값이 바뀌어
  // 경과 시간이 따라 올라간다. 별도 타이머(setInterval)를 두지 않는 이유이자,
  // 표기가 "마지막 갱신 기준"이라는 뜻이기도 하다(최대 5초 뒤처짐).
  const now = q.dataUpdatedAt;

  // dms-agent 를 고르면서 그 FROM 대상을 이번 빌드에 안 넣은 것들.
  const missingDeps = images.includes("dms-agent")
    ? AGENT_DEPS.filter((d) => !images.includes(d))
    : [];

  // 최근 빌드가 실제로 쓴 ref (중복 제거, 최신 순). 목록 응답에서만 파생한다.
  const recentRefs = useMemo(() => {
    const out: string[] = [];
    for (const b of builds) {
      const ref = b.git_ref;
      if (typeof ref !== "string" || ref === "" || out.includes(ref)) continue;
      out.push(ref);
      if (out.length >= REF_SUGGESTIONS) break;
    }
    return out;
  }, [builds]);

  const match = (FILTERS.find((f) => f.key === filter) ?? FILTERS[0]).match;
  const filtered = useMemo(() => builds.filter((b) => match(b.state)), [builds, match]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  // 필터로 페이지 수가 줄면 마지막 페이지로 클램프(RecentRequestsSection 관례).
  const current = Math.min(page, pages);
  const visible = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  const toggleImage = (name: string) => {
    setImages((prev) => (prev.includes(name) ? prev.filter((i) => i !== name) : [...prev, name]));
  };

  const submit = () => {
    submitBuild.mutate({ git_ref: gitRef, images });
  };

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">빌드</h1>
      <Card>
        <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          {/* 빌드 노드 미설정은 제출 시 422(build_node_not_set)로 끝난다 — 그 사실을
              폼 맨 위에서 미리 말하고, 고칠 수 있는 화면으로 바로 보낸다. */}
          {buildNodeName === null ? (
            <p className="text-bad font-medium">
              {REASON_MESSAGES.build_node_not_set}{" "}
              <Link className="text-accent underline" to="/admin/control">컨트롤 상태로 이동</Link>
            </p>
          ) : (
            <p className="text-muted">{`빌드 노드 ${buildNodeName} 에서 빌드합니다`}</p>
          )}
          <label className="block">git ref
            <input aria-label="git ref" className={field} value={gitRef} placeholder="main"
                   onChange={(e) => setGitRef(e.target.value)} /></label>
          <p className="text-muted text-xs">
            GitHub 에 push 된 브랜치·태그 이름만 (커밋 SHA 불가 — 빌드 파드가 --branch 로 clone 합니다)
          </p>
          {recentRefs.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-muted text-xs">최근</span>
              {recentRefs.map((r) => (
                <Button key={r} type="button" variant="ghost" className="px-2 py-1 text-xs"
                        onClick={() => setGitRef(r)}>{r}</Button>
              ))}
            </div>
          )}
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
            <p className="text-muted text-xs mt-1">
              dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM 합니다 — 함께 빌드하거나,
              그 태그가 레지스트리에 이미 있어야 합니다.
            </p>
            {/* 경고일 뿐 제출은 막지 않는다: 그 태그가 이미 레지스트리에 있으면
                agent 단독 빌드가 정상 경로다. 막으면 정당한 사용을 못 하게 된다. */}
            {missingDeps.length > 0 && (
              <p className="text-busy text-xs mt-1">
                {`dms-agent 가 FROM 하는 ${missingDeps.join("·")} 가 이번 빌드에 없습니다 — ` +
                 `같은 태그가 레지스트리에 없으면 pull 에서 실패합니다.`}
              </p>
            )}
          </div>
          <p className="text-muted text-xs">
            제출하면 빌드 노드에서 적합성 프리플라이트(egress·레지스트리·디스크)가 먼저 돕니다 —
            실패하면 수 초~수십 초 안에 사유와 함께 끝납니다.
          </p>
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
        <Card>
          <div className="flex items-center justify-between gap-4 mb-3 text-sm">
            <select aria-label="상태 필터" className="rounded-lg border border-black/10 px-3 py-2"
                    value={filter}
                    onChange={(e) => { setFilter(e.target.value); setPage(1); }}>
              {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            </select>
            <span className="text-muted tabular-nums">{`${filtered.length}건`}</span>
          </div>
          <Table>
            <thead>
              <tr className="text-muted">
                <th className="py-2">시각</th><th>ref</th><th>commit</th><th>이미지</th>
                <th>노드</th><th>태그</th><th>상태</th><th>사유</th><th>경과</th><th>작업</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((b) => (
                <tr key={b.build_id} className="border-t border-black/5">
                  <td className="py-2">{b.created_at}</td>
                  <td>{b.git_ref}</td>
                  <td className="text-muted">{b.commit_sha ? b.commit_sha.slice(0, 8) : "—"}</td>
                  <td>{(b.images ?? []).join(", ")}</td>
                  <td>{b.node_name ?? "—"}</td>
                  {/* 태그는 배포 때 손으로 옮겨야 하는 값이다. 런타임 airgap·비보안
                      컨텍스트라 clipboard API 를 못 쓰므로(규약), 클릭 한 번에 전체가
                      선택되는 select-all 등폭 텍스트로 복사 부담을 줄인다. */}
                  <td><span className="font-mono select-all">{b.tag ?? "—"}</span></td>
                  <td>
                    <StatusPill state={b.state} variant={buildPillVariant(b.state)} />
                    {/* 슬라이스 21 §3: 빌드의 Pending 은 "대기"가 아니라 적합성
                        확인(프리플라이트)을 포함한다 — 상태 문자열만으로는 지금
                        무엇을 하는 중인지 알 수 없어 한 줄 덧붙인다. */}
                    {b.state === "Pending" && (
                      <span className="block text-muted text-xs mt-0.5">적합성 확인 중</span>
                    )}
                  </td>
                  {/* 실패 사유를 상세로 들어가야만 볼 수 있으면 목록의 "Failed" 는
                      아무것도 말하지 않는 것과 같다. 원시 코드는 노출하지 않는다. */}
                  <td className={`max-w-xs ${b.reason_code ? "text-bad" : "text-muted"}`}>
                    {b.reason_code ? reasonText(b.reason_code) : "—"}
                  </td>
                  <td className="text-muted tabular-nums">{spentText(b, now)}</td>
                  <td className="py-2">
                    <Link className="text-accent" to={`/admin/builds/${b.build_id}`}>상세</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
          {/* 페이지네이션은 표 밖(카드 안)이다 — td 안에 버튼·flex 를 넣으면 e2e L2
              (display=table-cell 불변식)가 문다(RecentRequestsSection 관례). */}
          <div className="flex items-center justify-end gap-3 mt-3 text-sm">
            <Button variant="ghost" disabled={current <= 1}
                    onClick={() => setPage(current - 1)}>이전</Button>
            <span className="text-muted tabular-nums">{`${current} / ${pages} 페이지`}</span>
            <Button variant="ghost" disabled={current >= pages}
                    onClick={() => setPage(current + 1)}>다음</Button>
          </div>
        </Card>
      )}
    </section>
  );
}
