import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { isBatchTerminal, useBatches, useDeleteBatches } from "./useBatches";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { batchPillVariant } from "../../lib/jobState";

// 활성 배치 체크박스에 다는 사유. 서버 문구("먼저 취소하세요")와 같은 동선을 가리킨다
// — 활성은 Previewing 도 포함이라 "실행 중"보다 "진행 중"이 정직하다.
const ACTIVE_HINT = "진행 중인 배치는 삭제할 수 없습니다 — 먼저 취소하세요";
// 미선택 액션 바의 문구. 빈 바를 두면 "왜 여기 회색 띠가 있나"가 되므로, 예약된
// 자리가 무엇을 위한 자리인지 말하게 한다(자리 예약 + 어포던스 안내 겸용).
const IDLE_HINT = "배치를 선택해 삭제할 수 있습니다";
// 체크박스 표적 크기. 브라우저 기본 체크박스는 13px 라 표에서 조준이 어렵다는
// 사용자 보고가 있었다 — 20px(h-5 w-5)로 키운다. 헤더 전체선택과 행이 같은 크기여야
// 세로로 한 줄에 정렬돼 보인다. **td 안 래퍼 금지**(e2e L2, 아래 표 주석 참고)라
// 크기는 input 자신에게 준다.
const BOX = "h-5 w-5 cursor-pointer align-middle";

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
  const none = selected.length === 0;
  // 예약된 바의 한 줄은 셋 중 하나다(우선순위: 선택 > 직전 결과 > 안내). 선택이
  // 결과보다 앞서는 이유: 결과는 selected 가 비었을 때만 남고(완료 시 초기화),
  // 새로 고르기 시작하면 disarm() 이 del.reset() 으로 결과를 지운다 — 즉 실제로는
  // 배타적이고, 이 순서는 그 사실을 코드로 다시 못박는 것뿐이다.
  const barText = !none ? `${selected.length}개 선택됨`
    : r ? (r.failed.length > 0 ? `${r.ok.length}개 삭제됨 · ${r.failed.length}개 실패`
                               : `${r.ok.length}개 삭제됨`)
    : IDLE_HINT;
  const barTone = !none ? "" : r ? (r.failed.length > 0 ? "text-bad" : "text-ok") : "text-muted";

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">배치 작업</h1>
        <Link to="/admin/batches/new"><Button>배치 생성</Button></Link>
      </div>
      {/* 액션 바는 **늘 렌더된다**(자리 예약). 조건부로 나타나게 두면 체크 한 번에
          바 높이만큼 아래 표 전체가 밀려 내려가, 방금 조준한 행이 커서 밑에서
          도망간다(사용자 보고). 자리를 비워 두는 대신 안내 문구를 넣어 그 자리가
          무엇을 위한 것인지 말하게 했다.
          높이를 min-h 상수로 박지 않고 **버튼을 늘 렌더**해 고정한 이유: 상수는
          Button 패딩이 바뀌는 순간 조용히 어긋나지만, 같은 버튼이 늘 있으면 높이는
          구조상 같다. 미선택 시 버튼은 disabled — 사라지는 대신 왜 못 누르는지를
          안내 문구와 함께 남긴다.
          액션 바가 표 **밖**인 것은 그대로다 — td 안에 버튼·flex 를 넣으면 e2e
          L2(td 의 computed display=table-cell 불변식)가 문다. 표 안에 남는 건 단독
          <input type="checkbox"> 뿐이다(래퍼 div 없음). */}
      <div role="toolbar" aria-label="배치 일괄 작업"
           className="flex flex-wrap items-center gap-2 rounded-lg border border-line
                      bg-surface px-3 py-2 text-sm">
        {/* 한 줄 안에서 안내 → 선택 수 → 삭제 결과가 교대한다. role="status" 를
            **늘 있는** 이 노드에 두는 게 핵심이다: 라이브 리전은 내용이 바뀌기
            전부터 DOM 에 있어야 읽히므로, 조건부 렌더였던 이전 결과 블록은 사실
            안정적으로 announce 되지 못했다. */}
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
      {/* 실패 사유는 바 **아래**다. 부분 실패를 뭉개지 않는 게 이 블록의 존재 이유고
          (어느 배치가 왜 실패했는지), 줄 수가 실패 건수만큼 달라져 예약 높이 안에
          넣을 수 없다. 이 증가는 사용자가 삭제를 **명시적으로 실행한** 뒤 한 번만
          일어나고 — 그때는 행도 함께 사라져 표가 어차피 다시 그려진다 — 신고된
          결함(체크만 해도 밀림)의 경로에는 없다. */}
      {r && r.failed.length > 0 && (
        <div className="text-sm">
          {r.failed.map((f) => (
            <p key={f.id} className="text-muted">{f.id.slice(0, 12)}: {f.message}</p>
          ))}
        </div>
      )}
      {/* Card 구획(2026-08-19): 대시보드·릴리스 등과 같은 서피스 — 목록 화면 일관화 */}
      <Card>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted">
            {/* 전체 선택은 **선택 가능한 행만** 토글한다 — 활성 배치까지 켜 두면
                지울 수 없는 것을 골라 둔 셈이 되어 매번 409 를 만든다. */}
            {/* w-12 = 체크박스 20px + px-3 좌우 24px. 셀 패딩은 표적을 넓히지
                않는다(패딩을 눌러도 토글되지 않는다) — 옆 열 링크와의 오조준을
                줄이는 여백이다. 표적 자체를 셀만큼 넓히려면 <label> 로 감싸야
                하는데, td 자식 구조를 바꾸는 변경이라 L2 불변식 근처에서 얻는 것
                대비 위험이 크다. 크기는 input 에, 여백은 셀에 준다. */}
            <th className="px-3 py-2 w-12"><input ref={allRef} type="checkbox" className={BOX}
                aria-label="전체 선택"
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
                <td className="px-3 py-2"><input type="checkbox" className={BOX}
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
      </Card>
    </section>
  );
}
