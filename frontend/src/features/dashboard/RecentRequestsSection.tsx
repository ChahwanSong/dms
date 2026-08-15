import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useRecentRequests } from "../jobs/useJobs";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import { REQUEST_TERMINAL_STATES } from "../../lib/jobState";
import { absSummary } from "../../lib/storagePaths";
import { useStorageRoots } from "../storages/useUserStorages";
import type { RequestRow } from "../../lib/types";

// 최근 작업 카드(2026-08-13 조정): 요청자·작업내용·요청시간·완료시간 + 검색 +
// 클라이언트 페이지네이션. 서버가 200건으로 캡하므로(useRecentRequests) 전량을
// 받아 클라이언트에서 자르는 것이 정직하다 -- 페이지 이동마다 재조회하면 폴링과
// 겹쳐 순서가 바뀐 목록 위를 걷게 된다.
const PAGE_SIZE = 20;

// payload 필드 결손 방어: ?? 로만 접는다 -- truthy 검사는 ""(빈 경로, 이론상
// 정상값)를 "모름"으로 뭉갠다. String()은 비문자열 오염(숫자 등)도 표기로 살린다.
const part = (v: unknown) => String(v ?? "—");

// 작업내용 요약: sync 는 출발→도착, scan/rm 은 storage:target. operation 자체도
// 함께 표기한다(요약만으로는 rm 과 scan 이 같은 모양이라 구분이 안 된다).
function summarize(r: RequestRow): string {
  const p = r.payload ?? {};
  const body = r.operation === "sync"
    ? `${part(p.source_storage)}:${part(p.source)} → ${part(p.destination_storage)}:${part(p.destination)}`
    : `${part(p.storage)}:${part(p.target)}`;
  return `${r.operation} · ${body}`;
}

export function RecentRequestsSection() {
  const q = useRecentRequests();
  // 스토리지 뿌리 맵(관리자 응답에만 managed_root — 비관리자는 빈 맵). 절대경로는
  // 표 본문이 아니라 **title** 로만 붙인다: 작업 열 본문에 이어 붙이면 한 줄이 두
  // 배로 길어져 표가 가로로 밀린다. 검색 대상도 본문 그대로 둔다(보이는 것만 찾는다).
  const roots = useStorageRoots();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  // 방어적 정규화 -- 배열 아닌 페이로드 하나가 화면을 죽이면 안 된다(Dashboard 관례)
  const rows = useMemo(
    () => (Array.isArray(q.data) ? q.data : []), [q.data]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle === "") return rows;
    // 화면에 보이는 문자열 전부가 검색 대상이다 -- 보이는 걸 못 찾으면 검색이 아니다.
    return rows.filter((r) =>
      `${r.request_id} ${r.requester_id} ${summarize(r)} ${r.state}`
        .toLowerCase().includes(needle));
  }, [rows, query]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  // 필터로 페이지 수가 줄면 마지막 페이지로 클램프 -- 빈 페이지에 갇히지 않는다.
  const current = Math.min(page, pages);
  const visible = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  return (
    <Card>
      <div className="flex items-center justify-between gap-4 mb-3">
        <h2 className="font-medium">최근 작업</h2>
        <input className={`${field} mt-0 max-w-xs`} value={query}
               placeholder="요청자·ID·작업·상태 검색"
               onChange={(e) => {
                 setQuery(e.target.value);
                 setPage(1);  // 검색이 바뀌면 1페이지로 -- 옛 페이지 번호는 무의미
               }} />
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">요청</th><th>요청자</th>
            <th>작업</th><th>상태</th><th>요청시간</th><th>완료시간</th></tr></thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.request_id} className="border-t border-black/5">
                <td className="py-2"><Link className="text-accent"
                     to={`/jobs/${r.request_id}`}>{r.request_id}</Link></td>
                <td>{r.requester_id}</td>
                <td title={absSummary(r.operation, r.payload, roots) ?? undefined}>
                  {summarize(r)}</td>
                <td><StatusPill state={r.state} /></td>
                <td className="text-muted">{r.created_at}</td>
                {/* updated_at 은 "마지막 전이 시각"일 뿐 -- 종단 상태에서만 완료시간
                    으로 정직하다. 비종단의 updated_at 을 보여주면 진행 중인 요청에
                    거짓 완료시간이 찍힌다. 요청 종단엔 Conflict 가 있어 잡 지향
                    TERMINAL_STATES 가 아니라 요청 전용 셋을 쓴다(jobState.ts). */}
                <td className="text-muted">
                  {REQUEST_TERMINAL_STATES.has(r.state) ? r.updated_at : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {/* 페이지네이션은 표 밖(카드 안)이다 -- td 안에 버튼·flex 를 넣으면 e2e L2
          (display=table-cell 불변식)가 문다. */}
      <div className="flex items-center justify-end gap-3 mt-3 text-sm">
        <Button variant="ghost" disabled={current <= 1}
                onClick={() => setPage(current - 1)}>이전</Button>
        <span className="text-muted tabular-nums">{`${current} / ${pages} 페이지`}</span>
        <Button variant="ghost" disabled={current >= pages}
                onClick={() => setPage(current + 1)}>다음</Button>
      </div>
    </Card>
  );
}
