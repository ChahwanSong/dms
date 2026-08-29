import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { ApiError, reasonText } from "../../lib/api";
import { buildPillVariant, isTerminal } from "../../lib/jobState";
import { formatDuration, spanMs } from "../../lib/duration";
import { kstStamp } from "../../lib/datetime";
import { useBuilds, useDeleteBuilds } from "./useBuilds";
import { BuildTabs } from "./BuildTabs";
import type { Build } from "../../lib/types";

const PAGE_SIZE = 20;

// 진행 중 빌드 체크박스에 다는 사유. 서버(build_not_deletable)와 같은 동선을 가리킨다.
const ACTIVE_HINT = "진행 중인 빌드는 삭제할 수 없습니다 — 종료된 뒤에 삭제하세요";
const IDLE_HINT = "빌드를 선택해 이력에서 삭제할 수 있습니다";
// 체크박스 표적 크기(BatchesList 와 같은 상수) -- td 안 래퍼 금지(L2)라 input 자신에게.
const BOX = "h-5 w-5 cursor-pointer align-middle";

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
// 커밋 표기: 로컬 소스 빌드(git_ref === "local")는 SHA 앞 7자(+-dirty 접미 보존),
// 파싱 전(빌드 중)·실패는 "—". 옛 git clone 시절 행은 브랜치명이 남아 그대로 보인다.
function commitText(b: Build): string {
  if (b.git_ref !== "local") return b.git_ref;
  const sha = b.commit_sha;
  if (typeof sha !== "string" || sha === "" || sha === "unknown") return "—";
  const dirty = sha.endsWith("-dirty");
  const head = (dirty ? sha.slice(0, -"-dirty".length) : sha).slice(0, 7);
  return dirty ? `${head}-dirty` : head;
}

function spentText(b: Build, now: number): string {
  const terminal = isTerminal(b.state);
  const ms = terminal ? spanMs(b.created_at, b.finished_at) : spanMs(b.created_at, now);
  if (ms === null) return "—";
  return `${formatDuration(ms)} ${terminal ? "소요" : "경과"}`;
}

/** 빌드 이력 — 「빌드」의 두 번째 하위 페이지(목록 전용). 폼은 BuildForm.
 *
 *  폭을 제한하지 않는다(전폭): 다른 목록 화면(BatchesList·JobsList·계정 등)이
 *  전부 전폭이고, 8열 표는 좁히면 정보가 줄지 않고 **접히기만** 한다(bfc55fd 에서
 *  3xl 로 눌러 봤을 때 시각·ref·이미지가 2~4줄로 접혀 행 높이가 들쭉날쭉해졌다).
 *  가운데 정렬(mx-auto)도 쓰지 않는다 -- 이 앱의 모든 화면이 왼쪽 기준선이다.
 */
export function BuildHistory() {
  const q = useBuilds();
  const del = useDeleteBuilds();
  const [filter, setFilter] = useState<string>("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  // 2단 확인(BatchesList 관례): 1단 클릭이 무장, 2단이 쏜다.
  const [armed, setArmed] = useState(false);

  const builds = useMemo(() => (Array.isArray(q.data) ? q.data : []), [q.data]);

  // 삭제 가능(종단) 빌드 -- 필터·페이지와 무관하게 목록 전체 기준(전체 선택도 그 기준).
  const deletableIds = useMemo(
    () => builds.filter((b) => isTerminal(b.state)).map((b) => b.build_id),
    [builds]);
  const liveIds = useMemo(() => new Set(builds.map((b) => b.build_id)), [builds]);
  // 유령 선택 정리: 폴링/삭제로 사라진 빌드를 선택에서 뺀다(없는 id 에 DELETE →
  // 404 오보고 방지). 부분집합이라 길이로 동일성 판정, 같으면 같은 참조로 루프 차단.
  useEffect(() => {
    setSelected((prev) => {
      const next = prev.filter((id) => liveIds.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [liveIds]);

  const allChecked = deletableIds.length > 0 && selected.length === deletableIds.length;
  const someChecked = selected.length > 0 && !allChecked;
  const allRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (allRef.current) allRef.current.indeterminate = someChecked; },
            [someChecked]);

  const disarm = () => { setArmed(false); if (!del.isPending) del.reset(); };
  const toggleRow = (id: string) => {
    disarm();
    setSelected((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };
  const toggleAll = () => { disarm(); setSelected(allChecked ? [] : deletableIds); };
  const dr = del.data;
  const none = selected.length === 0;
  const barText = !none ? `${selected.length}개 선택됨`
    : dr ? (dr.failed.length > 0 ? `${dr.ok.length}개 삭제됨 · ${dr.failed.length}개 실패`
                                 : `${dr.ok.length}개 삭제됨`)
    : IDLE_HINT;
  const barTone = !none ? "" : dr ? (dr.failed.length > 0 ? "text-bad" : "text-ok") : "text-muted";

  // "지금"은 Date.now() 가 아니라 **마지막 성공 조회 시각**이다. 목록은 진행 중
  // 항목이 있을 때만 5초로 폴링하는데(useBuilds), 같은 데이터가 돌아오면 참조가
  // 유지돼 재렌더가 안 일어난다 — dataUpdatedAt 을 읽으면 매 폴링마다 값이 바뀌어
  // 경과 시간이 따라 올라간다. 별도 타이머(setInterval)를 두지 않는 이유이자,
  // 표기가 "마지막 갱신 기준"이라는 뜻이기도 하다(최대 5초 뒤처짐).
  const now = q.dataUpdatedAt;

  const match = (FILTERS.find((f) => f.key === filter) ?? FILTERS[0]).match;
  const filtered = useMemo(() => builds.filter((b) => match(b.state)), [builds, match]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  // 필터로 페이지 수가 줄면 마지막 페이지로 클램프(구 RecentRequestsSection 관례 -- 카드는 제거됐지만 규칙은 유지).
  const current = Math.min(page, pages);
  const visible = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-bold">빌드 이력</h1>
        <p className="text-muted mt-1">최근 빌드의 상태와 결과를 확인합니다</p>
      </header>
      <BuildTabs />

      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <Card>
          <div className="flex items-center justify-between gap-4 mb-3 text-sm">
            {/* 필터는 버튼 그룹이다(BatchDetail·JobViewer 관례) -- select 는 눌러
                열어야 선택지가 보인다. aria-pressed 로 "지금 무엇이 켜졌는지"를
                시각 스타일 밖에서도 말한다. */}
            <div role="group" aria-label="상태 필터" className="flex flex-wrap gap-2">
              {FILTERS.map((f) => (
                <Button key={f.key} type="button" aria-pressed={filter === f.key}
                        variant={filter === f.key ? "outline" : "ghost"}
                        className="px-2.5 py-1 text-xs"
                        onClick={() => { setFilter(f.key); setPage(1); }}>{f.label}</Button>
              ))}
            </div>
            <span className="text-muted tabular-nums shrink-0">{`${filtered.length}건`}</span>
          </div>
          {/* 일괄 삭제 툴바 — 늘 렌더(자리 예약)라 체크해도 표가 아래로 밀리지
              않는다(BatchesList 관례). 표 밖이라 L2 사정권 밖이다. */}
          <div role="toolbar" aria-label="빌드 이력 일괄 작업"
               className="flex flex-wrap items-center gap-2 rounded-lg border border-line
                          bg-surface px-3 py-2 text-sm mb-3">
            <span role="status" className={`mr-auto ${barTone}`}>{barText}</span>
            {armed
              ? <Button disabled={del.isPending || none}
                        onClick={() => del.mutate(selected,
                          { onSettled: () => { setArmed(false); setSelected([]); } })}>
                  {selected.length}개 삭제 확인
                </Button>
              : <Button variant="outline" disabled={del.isPending || none}
                        onClick={() => setArmed(true)}>선택 삭제</Button>}
            <Button variant="ghost" disabled={del.isPending || none}
                    onClick={() => { setArmed(false); setSelected([]); }}>선택 해제</Button>
          </div>
          {dr && dr.failed.length > 0 && (
            <div className="text-sm mb-3">
              {dr.failed.map((f) => (
                <p key={f.id} className="text-muted">{f.id.slice(0, 12)}: {f.message}</p>
              ))}
            </div>
          )}
          <Table>
            <thead>
              {/* commit·노드는 상세로 밀었다: commit 은 실패 시 대개 —이고, 노드는
                  빌드하기 화면의 확인 박스에 이미 있어 매 행 반복하면 밀도만 올린다. */}
              <tr className="text-muted whitespace-nowrap">
                {/* 전체 선택은 종단 빌드만 토글한다. td 안 래퍼 금지(L2)라 input 단독. */}
                <th className="px-3 py-2 w-12"><input ref={allRef} type="checkbox" className={BOX}
                    aria-label="전체 선택"
                    checked={allChecked} disabled={deletableIds.length === 0 || del.isPending}
                    onChange={toggleAll} /></th>
                <th className="py-2">시각</th><th>커밋</th><th>이미지</th><th>상태</th>
                <th>사유</th><th>경과</th><th>태그</th><th>작업</th>
              </tr>
            </thead>
            {/* whitespace-nowrap: 셀이 접히면 행 높이가 내용 길이 따라 들쭉날쭉해진다
                (긴 ref·이미지 3종). 넘치면 표 래퍼(overflow-x-auto)가 가로로 스크롤
                한다 -- 행 높이를 일정하게 두는 쪽을 택한다. */}
            <tbody>
              {visible.map((b) => (
                <tr key={b.build_id} className="border-t border-black/5 whitespace-nowrap">
                  {/* 종단 빌드만 선택 가능(표시 게이트 — 진짜 차단은 서버 409). */}
                  <td className="px-3 py-2"><input type="checkbox" className={BOX}
                       aria-label={`빌드 ${b.build_id.slice(0,12)} 선택`}
                       checked={selected.includes(b.build_id)}
                       disabled={!isTerminal(b.state) || del.isPending}
                       title={isTerminal(b.state) ? undefined : ACTIVE_HINT}
                       onChange={() => toggleRow(b.build_id)} /></td>
                  <td className="py-2">{kstStamp(b.created_at)}</td>
                  {/* 로컬 소스 빌드는 브랜치가 없다 -- 무엇을 빌드했는지는 커밋이
                      말한다(빌드 중엔 아직 파싱 전이라 —). -dirty 접미는 미커밋
                      변경 포함 빌드 표시라 자르지 않고 보존한다. 옛 git 시절 행은
                      브랜치명을 그대로 보여 준다(전체 경로·SHA 는 상세에). */}
                  <td>{commitText(b)}</td>
                  {/* 이미지 3종을 다 고르면 이 셀 하나가 220px 를 먹어 뒤쪽 태그·
                      작업 열을 화면 밖으로 밀어낸다 -- 사유와 같은 방식으로 자르고
                      전문은 title 에 둔다(상세에는 전체가 그대로 있다). */}
                  <td className="max-w-[10rem]">
                    <span className="block truncate" title={(b.images ?? []).join(", ")}>
                      {(b.images ?? []).join(", ")}
                    </span>
                  </td>
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
                      아무것도 말하지 않는 것과 같다. 다만 **한 줄로 자른다** — 사유
                      길이에 따라 행 높이가 들쭉날쭉해지던 것이 직전 판의 문제였다.
                      전문은 title(호버)과 상세에 있다. truncate 는 td 가 아니라 안쪽
                      span 에 건다: td 의 display 를 바꾸면 e2e L2 가 문다. */}
                  <td className={`max-w-[9rem] ${b.reason_code ? "text-bad" : "text-muted"}`}>
                    <span className="block truncate"
                          title={b.reason_code ? reasonText(b.reason_code) : undefined}>
                      {b.reason_code ? reasonText(b.reason_code) : "—"}
                    </span>
                  </td>
                  <td className="text-muted tabular-nums whitespace-nowrap">{spentText(b, now)}</td>
                  {/* 태그는 배포 때 손으로 옮겨야 하는 값이다. 런타임 airgap·비보안
                      컨텍스트라 clipboard API 를 못 쓰므로(규약), 클릭 한 번에 전체가
                      선택되는 select-all 등폭 텍스트로 복사 부담을 줄인다. */}
                  <td><span className="font-mono select-all">{b.tag ?? "—"}</span></td>
                  {/* 행 전체를 링크로 만들지 않는다: stretched-link 는 td 에
                      position 을 얹어야 하고, 표 안 텍스트(태그 select-all) 선택을
                      가로챈다 — L2 를 지키면서 얻는 것보다 잃는 게 크다. */}
                  <td className="py-2">
                    <Link className="text-accent" to={`/admin/builds/${b.build_id}`}>상세</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
          {/* 페이지네이션은 표 밖(카드 안)이다 — td 안에 버튼·flex 를 넣으면 e2e L2
              (display=table-cell 불변식)가 문다(구 RecentRequestsSection 관례 -- 카드는 제거됐지만 규칙은 유지). */}
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
