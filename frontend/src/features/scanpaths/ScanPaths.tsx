import { Fragment, useEffect, useState } from "react";
import {
  useScanPaths, useAddScanPath, useDeleteScanPath, useScanPathStats,
} from "./useScanPaths";
import { useUserStorages } from "../storages/useUserStorages";
import { StoragePicker, field } from "../jobs/formFields";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import type { ScanPath } from "../../lib/types";

function DeleteButton({ sp }: { sp: ScanPath }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteScanPath();
  // 닫힐 때마다 에러를 비운다 — StoragesList.DeleteButton과 동일한 이유로
  // onOpenChange만으로는 부족하다("취소"가 setOpen(false)를 직접 호출).
  useEffect(() => { if (!open) del.reset(); }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="스캔 경로 삭제"
            trigger={<Button variant="ghost">삭제</Button>}>
      <p className="text-sm text-muted mb-3">{sp.storage_name}:{sp.path} 을(를) 삭제할까요?</p>
      {del.isError && <p className="text-bad text-sm mb-2">{(del.error as ApiError).message}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
        <Button onClick={() => del.mutate(sp.id, { onSuccess: () => setOpen(false) })}
                disabled={del.isPending}>삭제 확인</Button>
      </div>
    </Dialog>
  );
}

/** 리포트 생성 시각은 UTC로만 보여준다 — 스토리지·잡은 UTC로 기록되고,
 *  브라우저 로컬시간으로 바꿔 보여주면 운영자·사용자가 다른 시각을 말하게 된다. */
function utcStamp(epoch: number) {
  return `${new Date(epoch * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

function StatsPanel({ sp }: { sp: ScanPath }) {
  const statsQ = useScanPathStats(sp.id, true);
  const title = `${sp.storage_name}:${sp.path} 통계`;

  if (statsQ.isLoading) return <p className="text-muted">통계를 불러오는 중…</p>;

  if (statsQ.isError) {
    const err = statsQ.error as ApiError;
    return (
      <Card className="mt-4 space-y-1">
        <h2 className="font-semibold">{title}</h2>
        <p className="text-bad text-sm">{err.message}</p>
        {err.code === "no_covering_scan" && (
          <p className="text-muted text-sm">관리자가 이 경로를 포함하는 scan을 실행하면 통계가 표시됩니다</p>
        )}
      </Card>
    );
  }

  const stats = statsQ.data!;
  return (
    <Card className="mt-4 space-y-4">
      <div>
        <h2 className="font-semibold">{title}</h2>
        {/* 언제 찍힌 숫자인지 없이 보여주는 건 "디렉터리 전체 기준"을 숨기는 것과
            같은 부정직이다 — 리포트 생성 시각을 항상 같이 보여준다. */}
        <p className="text-muted text-sm">
          {typeof stats.generated_at_epoch === "number"
            ? `scan 리포트 생성: ${utcStamp(stats.generated_at_epoch)}`
            : "scan 리포트 생성 시각을 알 수 없습니다"}
        </p>
      </div>
      {!stats.covered_by.exact && (
        <p className="text-bad text-sm">
          상위 경로 {stats.covered_by.target} 기준 집계입니다 — 이 경로만의 통계가 아닙니다
        </p>
      )}
      <div>
        <h2 className="font-semibold mb-2">요약</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          {/* 서버가 null을 흘리지 않도록 고쳤지만 화면도 스스로 버틴다 — 앱 전역
              ErrorBoundary는 최후의 방어선일 뿐, 이 화면 자체가 방어하지 않으면
              옆의 다른 데이터(경로 목록, 나머지 통계 항목)까지 같이 사라진다. */}
          {Object.entries(stats.summary ?? {}).map(([k, v]) => (
            <Fragment key={k}>
              <dt className="text-muted">{k}</dt>
              <dd>{v}</dd>
            </Fragment>
          ))}
        </dl>
      </div>
      <div>
        <h2 className="font-semibold mb-2">크기 히스토그램</h2>
        <Table>
          <thead><tr className="text-muted"><th className="py-2">구간</th><th>개수</th></tr></thead>
          <tbody>
            {(stats.file_size_histogram ?? []).map((b, i) => (
              <tr key={i} className="border-t border-black/5">
                <td className="py-2">{b.bucket}</td><td>{b.count}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
      {Object.entries(stats.time_histograms ?? {}).map(([key, buckets]) => (
        <div key={key}>
          <h2 className="font-semibold mb-2">{key} 히스토그램</h2>
          <Table>
            <thead><tr className="text-muted"><th className="py-2">구간</th><th>바이트</th></tr></thead>
            <tbody>
              {(buckets ?? []).map((b, i) => (
                <tr key={i} className="border-t border-black/5">
                  <td className="py-2">{b.bucket}</td><td>{b.bytes}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      ))}
    </Card>
  );
}

export function ScanPaths() {
  const q = useScanPaths();
  const storagesQ = useUserStorages();
  const add = useAddScanPath();
  const [storage, setStorage] = useState("");
  const [path, setPath] = useState("");
  // 통계 요청은 이 id가 세워진 뒤에만 나간다 — "통계 보기"를 누르기 전에는 요청이 없다.
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const storages = storagesQ.data ?? [];
  // 패널이 어느 행의 통계인지 제목에 그대로 적으려면 선택된 행 자체가 필요하다.
  const selected = (q.data ?? []).find((sp) => sp.id === selectedId) ?? null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    add.mutate({ storage_name: storage, path }, {
      onSuccess: () => { setStorage(""); setPath(""); },
    });
  }

  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">내 스캔 경로</h1>

      <Card>
        <form className="flex flex-wrap items-end gap-3" onSubmit={handleSubmit}>
          <StoragePicker label="스토리지" value={storage} onChange={setStorage}
                          storages={storages} loading={storagesQ.isLoading} />
          <label className="text-sm">경로
            <input aria-label="경로" className={field} value={path}
                   onChange={(e) => setPath(e.target.value)} />
          </label>
          <Button type="submit" disabled={add.isPending}>등록</Button>
        </form>
        {add.isError && <p className="text-bad text-sm mt-2">{(add.error as ApiError).message}</p>}
      </Card>

      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <Table>
          <thead>
            <tr className="text-muted"><th className="py-2">스토리지</th><th>경로</th><th>등록일</th><th>작업</th></tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((sp) => (
              <tr key={sp.id} className="border-t border-black/5">
                <td className="py-2">{sp.storage_name}</td>
                <td>{sp.path}</td>
                <td className="text-muted">{sp.created_at}</td>
                <td className="flex gap-2 py-2">
                  <Button variant="ghost" onClick={() => setSelectedId(sp.id)}>통계 보기</Button>
                  <DeleteButton sp={sp} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      {selected !== null && <StatsPanel sp={selected} />}
    </section>
  );
}
