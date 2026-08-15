import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { isBatchTerminal, useBatches, useDeleteBatches } from "./useBatches";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { batchPillVariant } from "../../lib/jobState";

// 활성 배치 체크박스에 다는 사유. 서버 문구("먼저 취소하세요")와 같은 동선을 가리킨다
// — 활성은 Previewing 도 포함이라 "실행 중"보다 "진행 중"이 정직하다.
const ACTIVE_HINT = "진행 중인 배치는 삭제할 수 없습니다 — 먼저 취소하세요";

export function BatchesList() {
  const q = useBatches();
  const del = useDeleteBatches();
  const [selected, setSelected] = useState<string[]>([]);
  // 2단 확인(BatchDetail 의 항목 삭제 armedSeq 관례): 1단 클릭이 무장하고 2단이
  // 쏜다. 일괄 삭제는 단건보다 비가역 폭이 넓어 오클릭 방어가 더 필요하다.
  const [armed, setArmed] = useState(false);

  // q.data 를 그대로 dep 에 쓴다(`?? []` 를 dep 으로 쓰면 매 렌더 새 배열이라 아래
  // 정리 effect 가 무한 루프가 된다). react-query 의 구조적 공유 덕에 내용이 같은
  // 리페치는 같은 참조를 돌려준다.
  const deletableIds = useMemo(
    () => (q.data ?? []).filter((b) => isBatchTerminal(b.status)).map((b) => b.batch_id),
    [q.data]);
  const liveIds = useMemo(() => new Set((q.data ?? []).map((b) => b.batch_id)), [q.data]);
  // 유령 선택 정리: 4s 폴링 리페치로 목록에서 사라진 배치(다른 세션이 지웠거나
  // 100건 창 밖으로 밀렸거나)를 선택에서 뺀다 — 안 그러면 화면에 없는 id 에
  // DELETE 를 쏘고 404 를 "실패"로 보고하게 된다. 부분집합이라 길이 비교로
  // 동일성을 판정할 수 있고, 같으면 같은 참조를 돌려 재렌더 루프를 끊는다.
  useEffect(() => {
    setSelected((prev) => {
      const next = prev.filter((id) => liveIds.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [liveIds]);

  const allChecked = deletableIds.length > 0 && selected.length === deletableIds.length;
  const someChecked = selected.length > 0 && !allChecked;
  // indeterminate 는 속성이 아니라 DOM 프로퍼티라 ref 로만 설정된다.
  const allRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (allRef.current) allRef.current.indeterminate = someChecked; },
            [someChecked]);

  // 선택이 바뀌면 무장을 풀고 직전 결과 문구도 지운다 — "2개 삭제 확인"이 무장된 채
  // 선택만 3개로 늘면 사용자가 확인한 것과 다른 것을 지우게 된다. 진행 중에는
  // reset 하지 않는다(비행 중인 mutation 의 결과를 삼킨다).
  const disarm = () => { setArmed(false); if (!del.isPending) del.reset(); };
  const toggleRow = (id: string) => {
    disarm();
    setSelected((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };
  const toggleAll = () => { disarm(); setSelected(allChecked ? [] : deletableIds); };
  const r = del.data;

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">배치 작업</h1>
        <Link to="/admin/batches/new"><Button>배치 생성</Button></Link>
      </div>
      {/* 일괄 삭제 결과는 액션 바 **밖**이다: 완료 시 선택이 비어 바가 사라지므로
          바 안에 두면 결과가 같이 증발한다. 부분 실패를 뭉개지 않는 게 이 블록의
          존재 이유 — 성공 수·실패 수·실패 사유를 각각 말한다. */}
      {r && (
        <div className="text-sm" role="status">
          <p className={r.failed.length > 0 ? "text-bad" : "text-ok"}>
            {r.failed.length > 0
              ? `${r.ok.length}개 삭제됨 · ${r.failed.length}개 실패`
              : `${r.ok.length}개 삭제됨`}
          </p>
          {r.failed.map((f) => (
            <p key={f.id} className="text-muted">{f.id.slice(0, 12)}: {f.message}</p>
          ))}
        </div>
      )}
      {/* 액션 바는 표 **밖**이다 — td 안에 버튼·flex 를 넣으면 e2e L2(td 의
          computed display=table-cell 불변식)가 문다. 표 안에 남는 건 단독
          <input type="checkbox"> 뿐이다(래퍼 div 없음). */}
      {selected.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line
                        bg-surface px-3 py-2 text-sm">
          <span className="mr-auto">{selected.length}개 선택됨</span>
          {armed
            ? <Button disabled={del.isPending}
                      onClick={() => del.mutate(selected,
                        { onSettled: () => { setArmed(false); setSelected([]); } })}>
                {selected.length}개 삭제 확인
              </Button>
            : <Button variant="outline" disabled={del.isPending}
                      onClick={() => setArmed(true)}>선택 삭제</Button>}
          <Button variant="ghost" disabled={del.isPending}
                  onClick={() => { setArmed(false); setSelected([]); }}>선택 해제</Button>
        </div>
      )}
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted">
            {/* 전체 선택은 **선택 가능한 행만** 토글한다 — 활성 배치까지 켜 두면
                지울 수 없는 것을 골라 둔 셈이 되어 매번 409 를 만든다. */}
            <th className="py-2 w-8"><input ref={allRef} type="checkbox" aria-label="전체 선택"
                checked={allChecked} disabled={deletableIds.length === 0 || del.isPending}
                onChange={toggleAll} /></th>
            <th className="py-2">배치</th><th>이름</th><th>작업</th><th>상태</th><th>진행</th>
            {/* 「최근 수행일」이 아니라 「최근 갱신」인 이유: 이 값(updated_at)은 실행
                뿐 아니라 이름·메모 수정, 항목 편집, 취소까지 **모든 전이**가 민다 —
                "수행"이라 부르면 마지막 실행 시각이라는 오독을 만든다. */}
            <th>최근 갱신</th>
          </tr></thead>
          <tbody>
            {(q.data ?? []).map((b) => (
              <tr key={b.batch_id} className="border-t border-black/5">
                {/* 종단 배치만 선택 가능(표시 게이트 — 진짜 차단은 서버 409).
                    disabled 로 끝내지 않고 title 로 이유와 동선을 남긴다. */}
                <td className="py-2"><input type="checkbox"
                     aria-label={`배치 ${b.batch_id.slice(0,12)} 선택`}
                     checked={selected.includes(b.batch_id)}
                     disabled={!isBatchTerminal(b.status) || del.isPending}
                     title={isBatchTerminal(b.status) ? undefined : ACTIVE_HINT}
                     onChange={() => toggleRow(b.batch_id)} /></td>
                <td className="py-2"><Link className="text-accent" to={`/admin/batches/${b.batch_id}`}>{b.batch_id.slice(0,12)}</Link></td>
                {/* null/부재 = 이름 없음 — 빈칸 대신 "—" 로 명시(모름과 구분할 값이
                    없는 단순 부재라 대시가 정직하다) */}
                <td>{b.name ?? "—"}</td>
                {/* 배치 상태 전용 색(batchPillVariant) — 상세 헤더와 동일 계약.
                    공유 pillVariant 로는 배치 상태가 전부 neutral 로 죽는다. */}
                <td>{b.operation}</td><td><StatusPill state={b.status} variant={batchPillVariant(b.status)} /></td>
                <td className="text-muted">{b.succeeded_count}/{b.failed_count}/{b.item_count}</td>
                {/* 시각은 저장소 관례대로 ISO 원문 그대로(AccountsList·JobsList·
                    RecentRequestsSection 과 같은 표기). 값 없음은 "—". */}
                <td className="text-muted whitespace-nowrap">{b.updated_at ?? "—"}</td>
              </tr>))}
          </tbody>
        </Table>)}
    </section>
  );
}
