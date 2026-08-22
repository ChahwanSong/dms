import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteRequests } from "./useJobs";
import type { RequestFilters } from "./useJobs";
import { useMe } from "../auth/useAuth";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { pathSummary } from "../../lib/storagePaths";

const field = "rounded-lg border border-line px-3 py-2 text-sm";

// 필터 select 값(빈 문자열 = 전체). 연산·상태는 도메인 열거의 미러 -- 서버는
// 미지 값에 0건을 주므로 여기 목록이 곧 사용자에게 유효한 값 집합이다.
const OPERATIONS = ["sync", "scan", "rm"];
const STATES = ["Pending", "Previewing", "ConfirmPending", "Planned",
                "Running", "Succeeded", "Failed", "Rejected", "Cancelled", "Conflict"];

export function JobsList() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const [operation, setOperation] = useState("");
  const [state, setState] = useState("");
  const [requester, setRequester] = useState("");
  // 필터 객체는 쿼리 키라 -- 매 렌더 새 객체면 키가 흔들려 재조회가 폭주한다.
  // 값이 실제로 바뀔 때만 새 객체를 만든다(useMemo).
  const filters: RequestFilters = useMemo(
    () => ({ operation: operation || undefined, state: state || undefined,
             requester: requester || undefined }),
    [operation, state, requester]);
  const q = useInfiniteRequests(filters);
  const rows = q.data?.pages.flat() ?? [];

  // 무한 스크롤: 표 끝 감시 노드가 보이면 다음 쪽을 당긴다. IntersectionObserver
  // 는 스크롤 이벤트보다 싸고(오버헤드 최소화 요청), 화면 밖이면 발화하지 않는다.
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && q.hasNextPage && !q.isFetchingNextPage)
        q.fetchNextPage();
    }, { rootMargin: "200px" });   // 바닥 200px 전에 미리 당겨 끊김을 줄인다
    io.observe(el);
    return () => io.disconnect();
  }, [q.hasNextPage, q.isFetchingNextPage, q]);

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">전체 작업</h1>
        <Link to="/jobs/new"><Button>작업 제출</Button></Link>
      </div>

      {/* 필터: 연산·상태는 누구나, 요청자는 운영자만(사용자는 서버가 자기 것으로
          강제하므로 요청자 필터가 무의미하다). */}
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="연산 필터" className={field} value={operation}
                onChange={(e) => setOperation(e.target.value)}>
          <option value="">연산 전체</option>
          {OPERATIONS.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <select aria-label="상태 필터" className={field} value={state}
                onChange={(e) => setState(e.target.value)}>
          <option value="">상태 전체</option>
          {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {isAdmin && (
          <input aria-label="요청자 필터" className={field} placeholder="요청자 아이디"
                 value={requester} onChange={(e) => setRequester(e.target.value)} />
        )}
      </div>

      <Card>
        {q.isError ? <p className="text-bad">{(q.error as ApiError).message}</p> : (
          <>
          {/* 표 헤더는 항상 렌더한다 -- 빈 결과에도 헤더가 남아야 화면 구조가
              유지되고, e2e L2(표 셀 하한)도 지킨다. 로딩·빈 문구는 표 아래로. */}
          <Table>
            <thead>
              <tr className="text-muted whitespace-nowrap">
                <th className="py-2">요청</th><th>요청자</th><th>작업</th>
                <th>대상</th><th>우선순위</th><th>상태</th><th>생성</th><th>갱신</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.request_id} className="border-t border-black/5 align-top">
                  <td className="py-2">
                    {/* 전체 request_id 를 DOM 에 둔다(textContent) -- e2e 가 전체 id 로
                        행을 찾고, 표시는 CSS truncate 로만 줄인다(잘라 렌더하면 e2e
                        의 hasText 전체-id 매칭이 깨진다). */}
                    <Link className="text-accent font-mono text-xs block max-w-[10rem] truncate"
                          title={r.request_id} to={`/jobs/${r.request_id}`}>
                      {r.request_id}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap">{r.requester_id}</td>
                  <td>{r.operation}</td>
                  {/* 대상 요약: sync 는 src→dst, scan/rm 은 storage:target (배치 항목
                      요약과 같은 규칙 -- pathSummary 공용). break-all 로 길어도 접는다. */}
                  <td className="text-muted break-all">{pathSummary(r.operation, r.payload)}</td>
                  <td className="text-muted">{r.priority}</td>
                  <td><StatusPill state={r.state} /></td>
                  <td className="text-muted whitespace-nowrap">{r.created_at}</td>
                  <td className="text-muted whitespace-nowrap">{r.updated_at}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          {q.isLoading ? <p className="text-muted pt-3">불러오는 중…</p>
           : rows.length === 0 ? <p className="text-muted pt-3">조건에 맞는 작업이 없습니다</p> : (
            /* 무한 스크롤 감시 노드 + 상태 문구(데이터 있을 때만). */
            <div ref={sentinel} className="pt-3 text-center text-xs text-muted">
              {q.isFetchingNextPage ? "더 불러오는 중…"
               : q.hasNextPage ? "스크롤하면 더 불러옵니다" : "마지막입니다"}
            </div>
          )}
          </>
        )}
      </Card>
    </section>
  );
}
