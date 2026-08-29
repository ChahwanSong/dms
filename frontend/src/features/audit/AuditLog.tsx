import { Fragment, useState } from "react";
import { useAuditLog } from "./useAudit";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { kstStamp } from "../../lib/datetime";
import type { AuditEntry } from "../../lib/types";

/** 확장 상세(사용자 요청 2026-08-19): before/after 스냅샷에서 **변경된 필드만**
    추린다 -- 전체 JSON 덤프는 소음이고, 무엇이 바뀌었는지가 감사의 본질이다.
    파싱 실패·양쪽 null 은 null(상세 없음) -- 지어내지 않는다. */
export function auditDiff(e: AuditEntry): { field: string; from: string; to: string }[] | null {
  const parse = (s: string | null): Record<string, unknown> | null => {
    if (s === null) return null;
    try {
      const v: unknown = JSON.parse(s);
      return typeof v === "object" && v !== null ? (v as Record<string, unknown>) : null;
    } catch { return null; }
  };
  const before = parse(e.before_state);
  const after = parse(e.after_state);
  if (before === null && after === null) return null;
  // undefined(필드 없음)와 null(명시적 없음)을 구분해 표기 -- null≠모름 규약의 미러.
  const fmt = (v: unknown): string =>
    v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])].sort();
  return keys.map((k) => ({ field: k, from: fmt(before?.[k]), to: fmt(after?.[k]) }))
    .filter((r) => r.from !== r.to);
}

function DetailRow({ e }: { e: AuditEntry }) {
  const diff = auditDiff(e);
  return (
    <tr>
      <td colSpan={6} className="pb-3">
        <div className="bg-panel rounded-card p-3 text-xs space-y-1">
          {diff === null ? (
            <p className="text-muted">기록된 변경 스냅샷이 없습니다</p>
          ) : diff.length === 0 ? (
            <p className="text-muted">값 변경 없음(동일 내용 저장)</p>
          ) : (
            diff.map((d) => (
              <div key={d.field} className="flex gap-3">
                <span className="w-48 shrink-0 text-muted font-mono">{d.field}</span>
                {/* break-all: 경로·JSON 값이 길다 -- 셀 밖으로 밀지 않고 접는다 */}
                <span className="font-mono break-all">
                  {d.from} <span className="text-muted">→</span> {d.to}
                </span>
              </div>
            ))
          )}
        </div>
      </td>
    </tr>
  );
}

export function AuditLog() {
  const q = useAuditLog();
  // 한 번에 하나만 펼친다 -- 여러 diff 가 쌓이면 어느 행의 것인지 흐려진다.
  const [openId, setOpenId] = useState<number | null>(null);
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">감사 로그</h1>
      {/* Card 구획(2026-08-19): 운영 화면들과 같은 서피스 — 회색 페이지 배경 위
          맨 표는 경계가 없어 관리 그룹만 다른 화면처럼 보였다 */}
      <Card>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">클래스</th><th>시각</th><th>작업</th><th>대상</th><th>실행자</th><th>상세</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((e) => (
              <Fragment key={e.id}>
                <tr className="border-t border-black/5">
                  <td className="py-2">{e.mutation_class}</td>
                  <td className="text-muted">{kstStamp(e.at)}</td><td>{e.operation}</td>
                  <td>{e.target_key}</td><td className="text-muted">{e.actor}</td>
                  <td className="py-1">
                    <Button variant="ghost" aria-expanded={openId === e.id}
                            onClick={() => setOpenId((cur) => (cur === e.id ? null : e.id))}>
                      {openId === e.id ? "닫기" : "펼치기"}
                    </Button>
                  </td>
                </tr>
                {openId === e.id && <DetailRow e={e} />}
              </Fragment>
            ))}
          </tbody>
        </Table>
      )}
      </Card>
    </section>
  );
}
