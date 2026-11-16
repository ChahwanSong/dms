import { useEffect, useMemo, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { Segmented } from "../../components/ui/Segmented";
import { field } from "../jobs/formFields";
import { ApiError } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import { batchStatusLabel } from "../../lib/jobState";
import {
  DEFAULT_SCHEDULE_FORM, INTERVAL_HOURS, SCHEDULE_KINDS, WEEKDAYS, formFromSchedule, isDestructiveBatch,
  scheduleFormErrors, scheduleSummary, schedulableOp, specFromForm,
  type ScheduleForm, type ScheduleKind,
} from "../../lib/batchSchedule";
import { storageText } from "../../lib/batchStorage";
import type { Batch, BatchSchedule } from "../../lib/types";
import { useBatches } from "./useBatches";
import {
  useBatchForSchedule, useCreateBatchSchedule, useSchedulePreview, useUpdateBatchSchedule,
} from "./useBatchSchedules";

// 스케줄 추가·수정 겸용 창(2026-11-16). 회차 = 그 배치의 「전체 재실행」 — sync·rm 은 회차마다 기존 확인 게이트(미리보기 →
// 배치 확인, 자동 실행 배치는 오케스트레이터가 같은 경로로)를 그대로 거친다. 서버가 kind/spec 검증·다음 실행 계산·특권
// 게이트의 진실이고, 이 창은 저장 전 즉답과 서버 미리보기(:preview)를 보여 줄 뿐이다.
// 열림은 호출측(목록 화면)이 쥔다: ?batch= 미리 채우기가 창을 코드로 열어야 해서다.

// 대상 배치를 창이 쓰는 한 모양으로(추가 = 배치 목록 행, 수정 = 스케줄 행의 배치 요약).
interface Target {
  // itemCount null = 모름(스케줄 행의 배치 요약이 변조된 값을 null 로 접는다) — 0(항목 없음)과 다르다.
  id: string; name: string | null; operation: string; status: string; itemCount: number | null;
  autoConfirm: boolean; destructive: boolean; storage: string;
}
function targetFromBatch(b: Batch): Target {
  return {
    id: b.batch_id, name: b.name ?? null, operation: b.operation, status: b.status, itemCount: b.item_count,
    autoConfirm: b.auto_confirm === true, destructive: isDestructiveBatch(b.operation, b.options),
    // 배치 목록의 스토리지 필드는 서버가 실을 때만 있다(옛 서버면 「—」).
    storage: storageText(b.operation, b),
  };
}
function targetFromSchedule(s: BatchSchedule): Target | null {
  const b = s.batch;
  if (b === null) return null;
  return {
    id: s.batch_id, name: b.name ?? null, operation: b.operation, status: b.status, itemCount: b.item_count,
    autoConfirm: b.auto_confirm === true,
    // 서버 판정(행의 최상위 destructive — 지금 배치의 옵션까지 본 값)이 있으면 그것을, 없으면 요약의 연산·옵션으로.
    destructive: typeof s.destructive === "boolean" ? s.destructive : isDestructiveBatch(b.operation, b.options),
    storage: storageText(b.operation, b),
  };
}

const optionLabel = (b: Batch, scheduled: boolean) => {
  const storage = storageText(b.operation, b);
  const parts = [b.name ?? b.batch_id.slice(0, 12), b.operation, ...(storage === "—" ? [] : [storage]),
                 batchStatusLabel(b.status)];
  const why = !schedulableOp(b.operation) ? " (반복 실행할 수 없는 연산)" : scheduled ? " (스케줄 있음)" : "";
  return parts.join(" · ") + why;
};

// 폼 → 서버 요청의 비교용 정규형(키 순서 무관) — 수정 창의 「변경 없음」 판정. 저장된 spec 도 같은 함수를 거쳐 비교한다.
const canon = (f: ScheduleForm) => {
  const r = specFromForm(f);
  return r === null ? null : JSON.stringify([r.kind, Object.keys(r.spec).sort().map((k) => [k, r.spec[k]])]);
};

export function BatchScheduleDialog({ open, onOpenChange, trigger, schedule, scheduledBatchIds, initialBatchId }: {
  open: boolean; onOpenChange: (o: boolean) => void; trigger: React.ReactNode;
  /** 있으면 수정 창(배치는 바꿀 수 없다). */
  schedule?: BatchSchedule;
  /** 이미 스케줄이 있는 배치(배치당 하나 — 추가 창의 선택지를 잠근다). */
  scheduledBatchIds: ReadonlySet<string>;
  /** 추가 창의 미리 고른 배치(?batch=). */
  initialBatchId?: string | null;
}) {
  const isEdit = schedule !== undefined;
  const [batchId, setBatchId] = useState("");
  const [form, setForm] = useState<ScheduleForm>(DEFAULT_SCHEDULE_FORM);
  const [enabled, setEnabled] = useState(true);
  // 「되돌릴 수 없음」 동의 — 열 때마다·배치를 바꿀 때마다 풀린다(BatchExecutionSettingsDialog 의 rm 동의와 같은 규칙).
  const [ack, setAck] = useState(false);
  const create = useCreateBatchSchedule();
  const update = useUpdateBatchSchedule(schedule?.schedule_id ?? "");
  const m = isEdit ? update : create;

  // 열 때마다 서버 상태로 다시 채운다(닫았다 열면 지난 미저장 입력이 아니라 저장된 값이 보여야 한다).
  useEffect(() => {
    if (!open) { create.reset(); update.reset(); return; }
    setBatchId(schedule?.batch_id ?? initialBatchId ?? "");
    setForm(schedule ? formFromSchedule(schedule.kind, schedule.spec) : { ...DEFAULT_SCHEDULE_FORM });
    setEnabled(true);
    setAck(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 배치 선택지 = 배치 목록과 같은 조회(["batches"] — 캐시를 나눠 쓰고 배치 mutation 의 무효화도 닿는다). 추가 창이 열렸을
  // 때만 읽는다(닫힌 창·수정 창은 조회도 폴링도 없다).
  const batchesQ = useBatches({ enabled: open && !isEdit });
  const listed = useMemo(() => (batchesQ.data ?? []).find((b) => b.batch_id === batchId), [batchesQ.data, batchId]);
  // 최근 100건 창 밖의 ?batch= 는 단건 조회로 보충한다(목록이 도착한 뒤에도 없을 때만).
  const extraQ = useBatchForSchedule(batchId === "" ? null : batchId,
                                     open && !isEdit && batchesQ.data !== undefined && listed === undefined);
  const picked: Batch | undefined = listed ?? extraQ.data;
  const target: Target | null = isEdit ? targetFromSchedule(schedule) : picked ? targetFromBatch(picked) : null;

  const errors = scheduleFormErrors(form);
  const req = specFromForm(form);
  // 미리보기는 입력이 멈춘 뒤에(300ms) — 시각을 한 글자씩 고칠 때마다 서버를 부르지 않는다.
  const [debounced, setDebounced] = useState<typeof req>(null);
  const reqKey = req === null ? null : JSON.stringify(req);
  useEffect(() => {
    if (!open) { setDebounced(null); return; }
    const t = setTimeout(() => setDebounced(reqKey === null ? null : JSON.parse(reqKey)), 300);
    return () => clearTimeout(t);
  }, [open, reqKey]);
  const preview = useSchedulePreview(open ? debounced : null);

  const targetError = target === null ? null
    : !schedulableOp(target.operation) ? `이 배치의 연산(${target.operation})은 반복 실행할 수 없습니다 — scan·sync·rm 만 됩니다`
    : !isEdit && scheduledBatchIds.has(target.id) ? "이 배치에는 이미 스케줄이 있습니다 — 목록에서 수정하세요"
    : null;
  const ackMissing = target !== null && target.destructive && !ack;
  const unchanged = isEdit && canon(form) !== null && canon(form) === canon(formFromSchedule(schedule.kind, schedule.spec));
  const blocked = req === null || target === null || targetError !== null || ackMissing || m.isPending || unchanged;

  const submit = () => {
    if (blocked || req === null || target === null) return;
    const ackBody = target.destructive && ack ? { acknowledge_irreversible: true as const } : {};
    const done = { onSuccess: () => onOpenChange(false) };
    if (isEdit) update.mutate({ kind: req.kind, spec: req.spec, ...ackBody }, done);
    else create.mutate({ batch_id: target.id, kind: req.kind, spec: req.spec, enabled, ...ackBody }, done);
  };

  const setKind = (kind: ScheduleKind) => setForm((f) => ({ ...f, kind }));
  const toggleDay = (d: number) => setForm((f) => ({
    ...f, weekdays: f.weekdays.includes(d) ? f.weekdays.filter((x) => x !== d) : [...f.weekdays, d] }));
  const err = (e: string | null) => e && <span className="mt-1 block text-sm text-bad">{e}</span>;
  const confirmOp = target !== null && (target.operation === "sync" || target.operation === "rm");

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={isEdit ? "스케줄 수정" : "스케줄 추가"} trigger={trigger} size="lg">
      <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div>
          <label className="block">배치
            {/* 수정 창은 배치를 바꾸지 않는다(배치당 하나 — 다른 배치는 그 배치의 새 스케줄이다). */}
            <select aria-label="배치" className={`${field} min-w-0`} value={batchId} disabled={isEdit}
                    onChange={(e) => { setBatchId(e.target.value); setAck(false); }}>
              {isEdit ? (
                <option value={schedule.batch_id}>
                  {schedule.batch ? `${schedule.batch.name ?? schedule.batch_id.slice(0, 12)} · ${schedule.batch.operation}`
                    : `기록 없음 ${schedule.batch_id.slice(0, 12)}`}
                </option>
              ) : (<>
                <option value="">{batchesQ.isLoading ? "불러오는 중…" : "배치를 고르세요"}</option>
                {/* 목록 창 밖에서 보충한 배치(?batch=)는 맨 위에 */}
                {listed === undefined && extraQ.data && (
                  <option value={extraQ.data.batch_id}
                          disabled={!schedulableOp(extraQ.data.operation) || scheduledBatchIds.has(extraQ.data.batch_id)}>
                    {optionLabel(extraQ.data, scheduledBatchIds.has(extraQ.data.batch_id))}
                  </option>
                )}
                {(batchesQ.data ?? []).map((b) => (
                  <option key={b.batch_id} value={b.batch_id}
                          disabled={!schedulableOp(b.operation) || scheduledBatchIds.has(b.batch_id)}>
                    {optionLabel(b, scheduledBatchIds.has(b.batch_id))}
                  </option>
                ))}
              </>)}
            </select>
          </label>
          {batchesQ.isError && err(`배치 목록을 읽지 못했습니다: ${(batchesQ.error as ApiError).message}`)}
          {/* 미리 고른 배치(?batch=)를 보충 조회도 못 찾았다(지워진 배치의 낡은 링크 등) — 선택칸이 「배치를 고르세요」로만 보이고
              저장이 잠긴 이유를 말한다. */}
          {!isEdit && listed === undefined && extraQ.isError &&
            err(`고른 배치를 불러오지 못했습니다: ${(extraQ.error as ApiError).message}`)}
          {isEdit && target === null && err("배치 기록이 없습니다 — 이 스케줄은 실행되지 않습니다(삭제하세요)")}
          {err(targetError)}
          {target !== null && (
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-muted">
              <dt>작업</dt>
              <dd className="text-ink">{target.operation}{confirmOp && (target.autoConfirm ? " · 미리보기 후 자동 실행" : " · 사람이 확인")}</dd>
              <dt>대상 스토리지</dt><dd className="break-all text-ink">{target.storage}</dd>
              <dt>지금 상태</dt><dd className="text-ink">{batchStatusLabel(target.status)}</dd>
              <dt>항목</dt>
              <dd className="text-ink">{target.itemCount === null ? "—" : `${target.itemCount.toLocaleString("ko-KR")}개`}</dd>
            </dl>
          )}
        </div>

        <fieldset className="space-y-3 rounded-lg border border-line px-3 pb-3 pt-1">
          <legend className="px-1 font-semibold">주기 (한국시간)</legend>
          <Segmented label="주기" options={SCHEDULE_KINDS} value={form.kind} onChange={setKind} />
          <label className="block">실행 시각(KST)
            {/* type=time 의 value 는 로캘과 무관하게 늘 "HH:MM" 이다. step=60 = 분 단위(초 칸 없음). */}
            <input type="time" step={60} aria-label="실행 시각(KST)" className={field} value={form.time}
                   onChange={(e) => setForm((f) => ({ ...f, time: e.target.value }))} />
            {form.kind === "interval" && (
              <span className="mt-1 block text-xs text-muted">이 시각을 기준으로 N시간 간격 — 날마다 같은 시각들에 실행합니다.</span>
            )}
            {err(errors.time)}
          </label>
          {form.kind === "weekly" && (
            <fieldset>
              <legend className="mb-1">요일</legend>
              {/* 좁은 화면(375)에서 줄바꿈되도록 flex-wrap */}
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {WEEKDAYS.map((d) => (
                  <label key={d.value} className="flex items-center gap-1">
                    <input type="checkbox" className="h-4 w-4" checked={form.weekdays.includes(d.value)}
                           onChange={() => toggleDay(d.value)} />{d.label}
                  </label>
                ))}
              </div>
              {err(errors.weekdays)}
            </fieldset>
          )}
          {form.kind === "monthly" && (
            <label className="block">날짜
              <select aria-label="날짜" className={field} value={form.day}
                      onChange={(e) => setForm((f) => ({ ...f, day: Number(e.target.value) }))}>
                {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{`${d}일`}</option>)}
              </select>
              {form.day >= 29 && (
                <span className="mt-1 block text-xs text-muted">29~31일은 그 날이 없는 달에는 그 달 말일에 실행합니다.</span>
              )}
              {err(errors.day)}
            </label>
          )}
          {form.kind === "interval" && (
            <label className="block">간격
              <select aria-label="간격" className={field} value={form.everyHours}
                      onChange={(e) => setForm((f) => ({ ...f, everyHours: Number(e.target.value) }))}>
                {INTERVAL_HOURS.map((h) => <option key={h} value={h}>{`${h}시간마다`}</option>)}
              </select>
              {err(errors.everyHours)}
            </label>
          )}
          {req !== null && <p className="font-medium">{scheduleSummary(req.kind, req.spec)}</p>}
          {/* 다음 5회 — 서버 계산(말일 당기기 규칙 등을 화면이 흉내 내지 않는다). */}
          {req !== null && (
            <div aria-label="다음 실행 미리보기">
              <p className="text-xs text-muted">다음 5회(KST)</p>
              {preview.isError ? (
                <p className="text-xs text-bad">{`미리보기를 계산하지 못했습니다: ${(preview.error as ApiError).message}`}</p>
              ) : preview.data ? (
                <ul className="text-xs tabular-nums">
                  {preview.data.next_runs.map((t) => <li key={t}>{kstStamp(t)}</li>)}
                </ul>
              ) : <p className="text-xs text-muted">계산 중…</p>}
            </div>
          )}
        </fieldset>

        {target !== null && targetError === null && (
          <div className="space-y-1 text-xs text-muted">
            {/* 회차의 크기·확인 흐름을 저장 전에 말한다 — 자식 요청 수는 배치 항목 수 그대로다(0 은 정상값: 건너뜀,
                null 은 모름: 수를 지어내지 않는다). */}
            {target.itemCount !== null && (
              <p>{target.itemCount === 0
                ? "항목이 없는 배치라 회차가 건너뛰어집니다(항목을 추가하면 실행됩니다)."
                : `회차마다 자식 요청 ${target.itemCount.toLocaleString("ko-KR")}개가 새로 생깁니다.`}</p>
            )}
            {confirmOp && (
              <p>{target.autoConfirm
                ? "이 배치는 「미리보기 후 자동 실행」이 켜져 있어 회차마다 미리보기가 끝나면 사람 확인 없이 실행됩니다."
                : "회차마다 사람이 확인해야 하며, 확인하지 않으면 미리보기 만료 시간(사이트 설정, 기본 24시간)이 지나 실패로 집계되고, 확인 대기 중의 다음 회차는 건너뜁니다."}</p>
            )}
          </div>
        )}

        {target !== null && target.destructive && (
          <div role="note" aria-label="반복 삭제 경고"
               className="rounded-lg border border-bad/30 bg-badbg px-3 py-2 text-sm text-bad">
            <p className="font-medium">{target.operation === "rm"
              ? `회차마다 이 배치의 대상 전체를 root 권한으로 다시 삭제합니다${target.autoConfirm ? "(자동 실행 — 사람 확인 없이)" : ""} — 되돌릴 수 없습니다`
              : "회차마다 delete 옵션으로 목적지에서 소스에 없는 항목을 root 권한으로 삭제합니다 — 되돌릴 수 없습니다"}</p>
            <p className="mt-1 text-xs">동의한 뒤 배치의 대상·옵션이 바뀌면 스케줄이 자동으로 일시정지되고, 다시 켤 때 다시 동의해야 합니다.</p>
            <label className="mt-2 flex items-start gap-2 font-medium">
              <input type="checkbox" className="mt-0.5" aria-required="true" checked={ack}
                     onChange={(e) => setAck(e.target.checked)} />
              <span>반복 실행이 되돌릴 수 없는 삭제를 만든다는 것을 이해했습니다(필수)</span>
            </label>
          </div>
        )}

        {!isEdit && (
          <label className="flex items-center gap-2">
            <input type="checkbox" className="h-4 w-4" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            사용(끄면 저장만 하고 실행하지 않습니다)
          </label>
        )}

        {m.isError && <p role="alert" className="text-sm text-bad">{(m.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" type="button" onClick={() => onOpenChange(false)}>닫기</Button>
          <Button type="submit" disabled={blocked}>{unchanged ? "변경 없음" : isEdit ? "저장" : "추가"}</Button>
        </div>
      </form>
    </Dialog>
  );
}
