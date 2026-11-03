import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { HelpTip } from "../../components/ui/HelpTip";
import { StatusPill } from "../../components/ui/StatusPill";
import { Table } from "../../components/ui/Table";
import { ApiError, retryAfterText } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import type { PillVariant } from "../../lib/jobState";
import type { SecurityEvent, SecurityStatus, VerificationLock } from "../../lib/types";
import { useSearchField } from "../jobs/searchField";
import { SECURITY_EVENT_GROUPS, eventLabel, ownValue } from "./eventLabels";
import {
  type SecurityEventFilter, type VerificationPurpose, useGlobalUnlock, useInfiniteSecurityEvents,
  useSecurityStatus, useUnlockVerification, useVerificationLocks,
} from "./useSecurity";

// 관리 → 보안 이벤트(2026-11-03, 설계 v6 §5 「운영자 구제」·선택 4종). 세 카드:
//  1) 보호 장치 상태 -- 로그인·인증번호 상한의 지금 값(dms-api 프로세스 메모리, 재시작하면 비워짐) + 인증번호 전체 잠금 해제.
//  2) 인증번호 잠금(아이디별) -- DB verification_failures 의 창 안 행 + 행마다 잠금 해제.
//  3) 최근 보안 이벤트 -- 로그인 감속·경보·과부하·잠금·해제·완료 메일 이벤트(종류·심각도·검색 필터, 키셋 무한 스크롤).
// 원 IP 는 기록·경보에만 쓴다(막는 데 쓰지 않는다) -- 이벤트의 「출발지」 칸이 그 기록이다. 0 = 「꺼짐」, null = 「없음·모름」을
// 섞지 않는다. 해제 둘은 세션 관리자만(토큰이면 서버 403 accounts_session_required 가 그 자리에 보인다).

const errText = (e: unknown): string =>
  e instanceof ApiError ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요";

const PURPOSE_LABEL: Record<VerificationPurpose, string> = { signup: "계정 생성", password_reset: "비밀번호 변경" };
const purposeLabel = (p: string): string => ownValue<string>(PURPOSE_LABEL, p) ?? p;

const SEVERITY: Record<string, { variant: PillVariant; label: string }> = {
  info: { variant: "neutral", label: "정보" },
  warning: { variant: "action", label: "경고" },
  error: { variant: "bad", label: "오류" },
};

/** 초 → 나눠떨어지는 가장 큰 단위(「1시간」「10분」「45초」). 기본값(3600·600)은 계약 문구(「1시간」「10분」)와 같다. */
export function windowText(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600}시간`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60}분`;
  return `${seconds}초`;
}

// 0 은 「꺼짐」(설정으로 끈 상한) -- 「0회」로 보이면 「한 번도 못 한다」로 읽힌다.
const perWindow = (limit: number, seconds: number) => (limit === 0 ? "꺼짐" : `${limit}회/${seconds}초`);
const times = (n: number) => (n === 0 ? "꺼짐" : `${n}회`);

// 2열(dt | dd) 정의 목록 -- 375 처럼 좁으면 1열(dt 위·dd 아래). 행 div 는 좁을 때 한 덩어리(행 사이 간격 = 그리드 gap)이고
// sm 이상에서 contents 로 풀려 dt·dd 가 두 열 그리드에 직접 선다.
const DL = "grid grid-cols-1 gap-y-3 text-sm sm:grid-cols-[max-content_minmax(0,1fr)] sm:gap-x-6";

function Row({ label, help, children }: { label: string; help?: { head: string; text: string }; children: ReactNode }) {
  return (
    <div className="sm:contents">
      <dt className="flex items-center gap-1 text-ink/70">
        {label}
        {help && <HelpTip label={`${help.head} 설명`} head={help.head} help={help.text} />}
      </dt>
      <dd className="mt-0.5 min-w-0 [overflow-wrap:anywhere] sm:mt-0">{children}</dd>
    </div>
  );
}

// ---------------------------------------------------------------- 카드 1: 보호 장치 상태

type GlobalUnlockDone = { cleared: number; wasLocked: boolean };

// 전체 오답 합계 되돌리기. 잠겼을 때는 「전체 잠금 해제」, 잠기지 않았는데 오답이 쌓였으면 「오답 합계 초기화」다 -- 잠기지 않은
// 상태에서 「잠금을 풀었다·다시 열어 준다」고 말하면 틀린 말이다(2026-11-03 리뷰). 같은 POST 다. 오답 0 이면 트리거가 없다(show).
// 보내는 동안은 닫히지 않는다(Esc·X·바깥 클릭·「취소」) -- 닫히면 effect 의 unlock.reset() 이 진행 중인 mutation 에서 observer 를
// 떼어 결과 문구(onDone)가 사라진다(ResetPasswordDialog 와 같은 이유 -- isPending 이 다음 렌더에야 참이라 누른 순간 동기로
// 세우는 sending 으로도 막는다: 그 틈의 Esc·「취소」·두 번째 클릭).
// 이 컴포넌트는 오답이 0 이어도 **마운트된 채로** 있고 트리거만 감춘다(2026-11-03 리뷰, 실 Chrome): 예전엔 호출측이 오답 0 이면
// 컴포넌트를 통째로 뺐다. 해제의 무효화는 상태·잠금·이벤트 셋을 다시 읽고 셋이 다 끝나야 mutation 이 성공을 알리는데, 먼저 온
// 상태(오답 0)가 그려지며 이 컴포넌트가 사라져 mutate 콜백(창 닫기·결과 문구)이 영영 불리지 않았다 -- 15초 재조회가 POST 응답보다
// 먼저 와도 같다. 창의 문구(잠김/누적)는 연 순간의 값으로 고정한다(열린 사이 재조회로 제목이 바뀌지 않게).
// 성공해 닫히면 포커스는 트리거가 아니라 호출측의 결과 줄로 간다(focusAfterDone): 오답 0 이면 트리거가 없어 Radix 가 돌려줄
// 자리가 없고 포커스가 <body> 로 떨어졌다. 열린 사이 트리거가 사라진 채 「취소」로 닫혀도 같은 자리로 보낸다.
function GlobalUnlockButton({ show, failures, limit, locked, onDone, focusAfterDone }: {
  show: boolean; failures: number; limit: number; locked: boolean;
  onDone: (r: GlobalUnlockDone) => void; focusAfterDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [openedLocked, setOpenedLocked] = useState(locked);
  const unlock = useGlobalUnlock();
  const sending = useRef(false);
  const succeeded = useRef(false);
  const close = () => { if (!sending.current && !unlock.isPending) setOpen(false); };
  useEffect(() => { if (!open) unlock.reset(); }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps
  const label = (l: boolean) => (l ? "전체 잠금 해제" : "오답 합계 초기화");
  const t = openedLocked
    ? { title: "인증번호 전체 잠금 해제", confirm: "잠금 해제",
        body: `모든 아이디의 인증번호 오답 합계(${failures}회)를 0으로 되돌려 계정 생성·비밀번호 변경을 바로 다시 열어 줍니다. 공격이 계속되고 있으면 곧 다시 잠깁니다 — 아래 이벤트에서 출발지를 먼저 확인하세요.` }
    : { title: "인증번호 오답 합계 초기화", confirm: "초기화",
        body: `모든 아이디의 인증번호 오답 합계(${failures}회)를 0으로 되돌립니다. 지금은 잠겨 있지 않습니다 — 상한 ${limit}회까지 남은 여유를 다시 채울 뿐이고, 계정 생성·비밀번호 변경은 지금도 됩니다.` };
  return (
    <Dialog open={open} title={t.title} busy={unlock.isPending}
            onOpenChange={(o) => { if (o) { setOpenedLocked(locked); setOpen(true); } else close(); }}
            onCloseAutoFocus={(e) => {
              const done = succeeded.current;
              succeeded.current = false;
              if (done || !show) { e.preventDefault(); focusAfterDone(); }
            }}
            trigger={show ? <Button variant="outline" className="shrink-0">{label(locked)}</Button> : undefined}>
      <div className="space-y-3 text-sm">
        <p>{t.body}</p>
        {unlock.isError && <p role="alert" className="text-bad">{errText(unlock.error)}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" disabled={unlock.isPending} onClick={close}>취소</Button>
          <Button type="button" disabled={unlock.isPending}
                  onClick={() => {
                    if (sending.current) return;
                    sending.current = true;
                    unlock.mutate(undefined, {
                      onSuccess: (r) => {
                        succeeded.current = true;
                        setOpen(false);
                        onDone({ cleared: r.cleared_failures, wasLocked: openedLocked });
                      },
                      onSettled: () => { sending.current = false; },
                    });
                  }}>
            {t.confirm}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// 화면이 지은 상태 배지 -- state 를 label 과 같게 둔다. StatusPill 은 label ≠ state 면 state 를 title(마우스 툴팁)로 보이는데,
// 여기 state 는 서버 원문이 아니라 화면용 값이라 'counting'·'locked' 같은 영어가 툴팁에 떴다(2026-11-03 리뷰). 서버 원문을
// 배지로 보이는 자리(이벤트 심각도)는 StatusPill 관례대로 원문을 title 로 남긴다.
function Pill({ variant, label }: { variant: PillVariant; label: string }) {
  return <StatusPill state={label} variant={variant} label={label} />;
}

function StatusRows({ s, onGlobalUnlocked, focusAfterGlobalUnlock }: {
  s: SecurityStatus; onGlobalUnlocked: (r: GlobalUnlockDone) => void; focusAfterGlobalUnlock: () => void;
}) {
  const v = s.verification;
  const l = s.login;
  const a = s.alarms;
  const off = v.global_guess_limit === 0;
  const pill = off ? <Pill variant="neutral" label="꺼짐" />
    : v.global_locked
      ? <Pill variant="bad"
              label={v.global_retry_after_seconds === null ? "잠김"
                : `잠김 — ${retryAfterText(v.global_retry_after_seconds)} 뒤 풀림`} />
      : <Pill variant="ok" label="정상" />;
  return (
    <dl className={DL}>
      <Row label="인증번호 전체 오답">
        <div className="flex flex-wrap items-center gap-2">
          {/* 꺼짐(0)이면 배지 하나로 말한다 -- 값 자리에 「꺼짐」을 또 쓰면 같은 말이 두 번 보인다. */}
          {!off && (
            <span className="tabular-nums">
              {`${v.global_guess_failures} / ${v.global_guess_limit}회 (최근 ${windowText(v.global_guess_window_seconds)})`}
            </span>
          )}
          {pill}
          {/* 늘 마운트(트리거만 show 로) -- GlobalUnlockButton 머리 주석. */}
          <GlobalUnlockButton show={!off && (v.global_locked || v.global_guess_failures > 0)}
                              failures={v.global_guess_failures} limit={v.global_guess_limit}
                              locked={v.global_locked} onDone={onGlobalUnlocked}
                              focusAfterDone={focusAfterGlobalUnlock} />
        </div>
      </Row>
      <Row label="인증번호(아이디별)">
        {`${v.code_digits}자리 · 유효 ${windowText(v.code_ttl_seconds)} · 코드당 ${v.per_code_attempts}회 · `
          + `틀린 횟수 ${v.per_id_failure_limit}회/${windowText(v.per_id_failure_window_seconds)}`}
      </Row>
      <Row label="로그인(아이디별)">
        {`실패 ${perWindow(l.id_failure_limit, l.id_failure_window_seconds)} · `
          + `시도(성공 포함) ${perWindow(l.id_attempt_limit, l.id_attempt_window_seconds)}`}
      </Row>
      <Row label="비밀번호 검증 슬롯">
        {`${l.scrypt_slots}개 (사용 중 ${l.scrypt_in_use} · 대기 ${l.scrypt_waiting}) · 최대 대기 ${l.scrypt_max_wait_seconds}초`}
      </Row>
      <Row label="대기형 전체 스위치"
           help={{ head: "대기형 전체 스위치",
                   text: "여러 아이디로 흩뿌리는 로그인 실패를 전체 속도로 늦춥니다(거절하지 않고 기다리게 함). 켜고 끄려면 dms-config 의 DMS_LOGIN_GLOBAL_PACE_LIMIT 를 바꾸고 dms-api 를 재시작합니다." }}>
        {l.pacing.enabled ? (
          <div className="flex flex-wrap items-center gap-2">
            <Pill variant="action" label="켜짐" />
            <span>{`실패 ${l.pacing.limit}회/${l.pacing.window_seconds}초 속도로 늦춤 · 최대 대기 ${l.pacing.max_wait_seconds}초 · 대기 중 ${l.pacing.waiting}`}</span>
          </div>
        ) : <Pill variant="neutral" label="꺼짐" />}
      </Row>
      <Row label="경보 임계">
        <span className="block">
          {`실패 ${times(a.failures)} · 시도 ${times(a.attempts)} · 출발지별 실패 ${times(a.source_failures)} / ${windowText(a.window_seconds)}`}
        </span>
        <span className="block text-muted">
          {`최근 ${windowText(a.window_seconds)}: 실패 ${a.recent_failures}회 · 시도 ${a.recent_attempts}회`}
        </span>
      </Row>
    </dl>
  );
}

function StatusCard() {
  const q = useSecurityStatus();
  const [globalDone, setGlobalDone] = useState<GlobalUnlockDone | null>(null);
  // 전체 해제 결과 줄은 늘 그려 둔다(비어 있으면 높이·여백 0) -- 해제 뒤 버튼이 사라지면 포커스를 받을 자리이고, 미리 있는
  // 라이브 영역이라 새로 끼워 넣은 role=status 처럼 화면낭독기가 놓치지 않는다(2026-11-03 리뷰, AccountsList 결과 줄 관례).
  const doneRef = useRef<HTMLParagraphElement | null>(null);
  return (
    <Card className="space-y-4">
      <div className="flex items-center gap-1">
        <h2 className="text-lg font-semibold">보호 장치 상태</h2>
        <HelpTip label="보호 장치 상태 설명" head="보호 장치 상태"
                 help="로그인과 계정 셀프서비스(인증번호)를 지키는 상한들의 지금 값입니다. 원 IP 는 기록·경보에만 쓰고 막는 데 쓰지 않습니다." />
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p>
        : q.isError && !q.data ? <p className="text-bad">{errText(q.error)}</p>
        : q.data ? (
          <>
            <StatusRows s={q.data} onGlobalUnlocked={setGlobalDone}
                        focusAfterGlobalUnlock={() => doneRef.current?.focus()} />
            <p ref={doneRef} role="status" tabIndex={-1}
               className={globalDone === null ? "!mt-0 focus:outline-none" : "text-ok text-sm focus:outline-none"}>
              {globalDone === null ? null
                : globalDone.wasLocked ? `전체 잠금을 풀었습니다(오답 ${globalDone.cleared}회 초기화)`
                : `인증번호 오답 합계를 0으로 되돌렸습니다(${globalDone.cleared}회 초기화)`}
            </p>
            {/* 15초 재조회가 실패하면 직전 값을 그대로 두고 알린다(값이 멈춘 걸 「정상」으로 오독하지 않게). */}
            {q.isRefetchError && (
              <p className="text-bad text-sm">{`최신 상태를 다시 읽지 못했습니다(표시는 직전 값): ${errText(q.error)}`}</p>
            )}
            <p className="text-xs text-muted">{`카운터는 dms-api 프로세스 메모리에 있습니다 — ${kstStamp(q.data.started_at)} 시작 이후 값이며 재시작하면 비워집니다.`}</p>
          </>
        ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------- 카드 2: 인증번호 잠금(아이디별)

type UnlockNote = { ok: true; text: string } | { ok: false; text: string };

function LocksCard() {
  const q = useVerificationLocks();
  const unlock = useUnlockVerification();
  const [note, setNote] = useState<UnlockNote | null>(null);
  // 풀린 행은 다음 조회에서 사라진다 -- 누른 버튼이 없어지면 포커스가 <body> 로 떨어지므로 결과 줄로 옮긴다(RootGrantCell 관례).
  // 결과 줄은 표 **위**에 늘 그려 두고(비어 있으면 높이·여백 0) 스크롤을 막지 않는다(2026-11-03 리뷰 -- 표 아래 + preventScroll
  // 이면 잠금 행이 수십 개일 때 결과가 화면 밖이었다. AccountsList 결과 줄과 같은 자리·관례).
  const noteRef = useRef<HTMLParagraphElement | null>(null);
  useEffect(() => { if (note) noteRef.current?.focus(); }, [note]);
  const items = q.data?.items ?? [];
  // 한 번에 한 행만 보낸다(2026-11-03 리뷰): 모든 행이 mutation 하나를 함께 쓰므로, 앞 행을 보내는 동안 다른 행을 누르면
  // TanStack 이 mutate 콜백을 뒤 것으로 바꿔 앞 행의 결과·오류가 영영 보이지 않았고, 앞 행 버튼이 다시 눌려 같은 POST 를 한 번
  // 더 보냈다. 보내는 동안 모든 행 버튼을 잠그고, isPending 이 다음 렌더에야 참인 틈(같은 틱의 두 번째 클릭)은 누른 순간 동기로
  // 세우는 sending 으로 막는다(GlobalUnlockButton·ResetPasswordDialog 와 같은 이유).
  const sending = useRef(false);
  // 잠긴 행은 「잠금 해제」, 아직 잠기지 않은(누적 중) 행은 「기록 지우기」다 -- 같은 POST(행 삭제)지만 누적 중인 행에 「잠금을
  // 풀었다」고 말하면 틀린 말이다(2026-11-03 리뷰). 문구는 누른 순간의 행 상태로 고른다.
  const run = (r: VerificationLock) => {
    if (sending.current) return;
    sending.current = true;
    const who = `${r.username}(${purposeLabel(r.purpose)})`;
    setNote(null);
    unlock.mutate({ username: r.username, purpose: r.purpose }, {
      onSuccess: (res) => setNote({ ok: true, text: res.cleared === 0
        ? (r.locked ? `${who} 는 풀 잠금이 없었습니다(그 사이 풀렸거나 성공했습니다)`
          : `${who} 는 지울 실패 기록이 없었습니다(그 사이 창이 지났거나 성공했습니다)`)
        : r.locked ? `${who} 의 인증 잠금을 풀었습니다`
        : `${who} 의 인증번호 실패 기록(${r.failures}회)을 지웠습니다` }),
      onError: (e) => setNote({ ok: false, text: errText(e) }),
      onSettled: () => { sending.current = false; },
    });
  };
  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-1">
        <h2 className="text-lg font-semibold">인증번호 잠금 (아이디별)</h2>
        <HelpTip label="인증번호 잠금 설명" head="인증번호 잠금 (아이디별)"
                 help="같은 아이디·용도로 인증번호를 24시간 안에 10번 틀리면 그 창이 끝날 때까지 인증번호 발급·확인이 막힙니다(로그인과는 무관). 잠금을 풀면 바로 다시 시도할 수 있습니다. 아직 잠기지 않은 행의 「기록 지우기」는 쌓인 실패 횟수를 0으로 되돌립니다." />
      </div>
      <p ref={noteRef} tabIndex={-1} role={note !== null && !note.ok ? "alert" : "status"}
         className={note === null ? "!mt-0 focus:outline-none"
           : `text-sm focus:outline-none ${note.ok ? "text-ok" : "text-bad"}`}>
        {note?.text}
      </p>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p>
        : q.isError && !q.data ? <p className="text-bad">{errText(q.error)}</p>
        : items.length === 0 ? <p className="text-muted">잠기거나 실패가 쌓인 아이디가 없습니다</p> : (
          <Table>
            <thead>
              <tr className="text-muted whitespace-nowrap">
                <th className="py-2 pr-3">아이디</th><th className="pr-3">용도</th><th className="pr-3">틀린 횟수</th>
                <th className="pr-3">첫 실패</th><th className="pr-3">상태</th><th>작업</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => {
                const key = `${r.username}\u0000${r.purpose}`;
                return (
                  <tr key={key} className="border-t border-black/5">
                    <td className="py-2 pr-3 whitespace-nowrap">{r.username}</td>
                    <td className="pr-3 whitespace-nowrap">{purposeLabel(r.purpose)}</td>
                    <td className="pr-3 whitespace-nowrap tabular-nums">{`${r.failures} / ${r.limit}`}</td>
                    <td className="pr-3 whitespace-nowrap text-muted">{kstStamp(r.window_start)}</td>
                    <td className="pr-3 whitespace-nowrap">
                      {r.locked
                        ? <Pill variant="bad"
                                label={r.retry_after_seconds === null ? "잠김"
                                  : `잠김 — ${retryAfterText(r.retry_after_seconds)} 뒤 풀림`} />
                        : <Pill variant="neutral" label="누적 중" />}
                    </td>
                    <td className="py-1">
                      <Button variant="ghost" className="whitespace-nowrap" disabled={unlock.isPending}
                              aria-label={`${r.username} ${purposeLabel(r.purpose)} ${r.locked ? "잠금 해제" : "실패 기록 지우기"}`}
                              onClick={() => run(r)}>
                        {r.locked ? "잠금 해제" : "기록 지우기"}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      {q.data?.truncated && <p className="text-xs text-muted">{`상위 ${q.data.limit}건만 보입니다`}</p>}
      {q.isRefetchError && q.data && (
        <p className="text-bad text-sm">{`최신 목록을 다시 읽지 못했습니다(표시는 직전 값): ${errText(q.error)}`}</p>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- 카드 3: 최근 보안 이벤트

// payload 키의 화면 이름 -- 모르는 키는 원문 그대로(서버가 먼저 새 키를 실어도 보인다). 키 원문도 함께 보여 검색어로 쓸 수 있다.
const PAYLOAD_KEY_LABELS: Record<string, string> = {
  client_ip: "출발지 IP", peer: "피어(직전 연결)", username: "아이디", actor: "실행자", purpose: "용도",
  last_username: "마지막 아이디", last_purpose: "마지막 용도", limit: "상한", threshold: "임계", count: "횟수",
  window_seconds: "창(초)", wait_seconds: "대기(초)", max_wait_seconds: "최대 대기(초)", retry_after_seconds: "다시 시도까지(초)",
  cause: "원인", kind: "종류", recipient: "받는 사람", reason: "사유", cleared: "지운 행 수", cleared_failures: "초기화한 오답",
};
const CAUSE_LABEL: Record<string, string> = { pacing: "대기형 전체 스위치", slots: "비밀번호 검증 슬롯" };

function payloadValue(key: string, v: unknown): string {
  if ((key === "purpose" || key === "last_purpose") && v === null) return "null (두 용도 모두)";
  if ((key === "purpose" || key === "last_purpose") && typeof v === "string" && ownValue<string>(PURPOSE_LABEL, v))
    return `${v} (${purposeLabel(v)})`;
  const cause = key === "cause" && typeof v === "string" ? ownValue(CAUSE_LABEL, v) : undefined;
  if (cause) return `${v} (${cause})`;
  if (typeof v === "string") return v;
  return v === undefined ? "—" : JSON.stringify(v);
}

const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

function EventDetail({ e }: { e: SecurityEvent }) {
  const entries = e.payload === null ? [] : Object.entries(e.payload);
  return (
    <tr className="max-sm:block">
      <td colSpan={6} className="pb-3 max-sm:block">
        <div className="bg-panel rounded-card p-3 text-xs space-y-1">
          {e.payload === null || entries.length === 0 ? <p className="text-muted">추가 기록이 없습니다</p> : (
            <dl className="space-y-1">
              {entries.map(([k, v]) => (
                <div key={k} className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
                  <dt className="shrink-0 text-muted sm:w-52">
                    {ownValue(PAYLOAD_KEY_LABELS, k) ?? k}
                    {ownValue(PAYLOAD_KEY_LABELS, k) !== undefined && <span className="ml-1 font-mono">{k}</span>}
                  </dt>
                  <dd className="min-w-0 font-mono [overflow-wrap:anywhere]">{payloadValue(k, v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </td>
    </tr>
  );
}

const SELECT = "rounded-lg border border-line bg-surface px-3 py-2 text-sm";

function EventsCard() {
  const [type, setType] = useState("");
  const [severity, setSeverity] = useState("");
  const search = useSearchField();
  const filter: SecurityEventFilter = { type, severity, q: search.applied };
  const filtered = type !== "" || severity !== "" || search.applied !== "";
  const q = useInfiniteSecurityEvents(filter);
  const rows = q.data?.pages.flat() ?? [];
  // 한 번에 하나만 펼친다(감사 로그 관례).
  const [openId, setOpenId] = useState<number | null>(null);

  // 무한 스크롤(감사 로그와 같은 IntersectionObserver 방식) -- 다음 쪽이 실패하면 자동으로 다시 당기지 않는다(버튼으로만).
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = q;
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage && !isFetchNextPageError)
        fetchNextPage();
    }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage, rows.length]);

  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-1">
        <h2 className="text-lg font-semibold">최근 보안 이벤트</h2>
        <HelpTip label="최근 보안 이벤트 설명" head="최근 보안 이벤트"
                 help="로그인 감속·경보·과부하, 인증번호 잠금, 관리자 조치(잠금 해제·비밀번호 재설정), 인증·안내 메일 결과입니다. 경보와 감속은 같은 종류가 창마다 한 번만 남습니다. 서버 메시지는 서버 기록 원문(영문)이고, 검색은 그 메시지와 상세 값(아이디·IP 등)에서 찾습니다. 출발지는 접속한 원 IP(프록시가 덧붙인 X-Forwarded-For 마지막 값, 없으면 직전 연결)이고 기록용입니다 — 이 값으로 막지 않습니다." />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="이벤트 종류" className={`${SELECT} max-w-full`} value={type}
                onChange={(e) => { setType(e.target.value); setOpenId(null); }}>
          <option value="">전체</option>
          {SECURITY_EVENT_GROUPS.map((g) => (
            <optgroup key={g.label} label={g.label}>
              {g.types.map((t) => <option key={t} value={t}>{eventLabel(t)}</option>)}
            </optgroup>
          ))}
        </select>
        <select aria-label="심각도" className={SELECT} value={severity}
                onChange={(e) => { setSeverity(e.target.value); setOpenId(null); }}>
          <option value="">전체</option>
          <option value="info">정보</option>
          <option value="warning">경고</option>
          <option value="error">오류</option>
        </select>
        {/* 검색은 서버 메시지(영문 원문)·payload(아이디·IP 등) 부분 일치다 -- 한국어 종류 이름은 「이벤트 종류」로 고른다. */}
        <input aria-label="이벤트 검색" placeholder="아이디·IP·서버 메시지" maxLength={200}
               className="w-52 max-w-full rounded-lg border border-line px-3 py-2 text-sm" {...search.bind} />
        <Button variant="ghost" disabled={q.isFetching} onClick={() => q.refetch()}>새로고침</Button>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p>
        : q.isError && rows.length === 0 ? <p className="text-bad">{errText(q.error)}</p>
        : rows.length === 0 ? (
          <p className="text-muted">{filtered ? "조건에 맞는 이벤트가 없습니다" : "기록된 보안 이벤트가 없습니다"}</p>
        ) : (
          <>
            {/* 좁은 화면(< sm, 2026-11-03 d17 실증 375폭 「종류」 열이 옆으로 밀림): 같은 DOM 을 CSS 로만 쌓는다 --
                1줄 시각·심각도·펼치기, 다음 줄 종류 → 서버 메시지 → 출발지. 머리줄은 숨기고 출발지는 ::before 로 이름을 단다
                (텍스트 노드를 늘리지 않아 표 의미·테스트 조회가 그대로다). tbody 를 block 으로 바꿔 펼침 행(colSpan)과 섞여도
                열 폭이 갈라지지 않는다. */}
            <Table>
              <thead className="max-sm:hidden">
                <tr className="text-muted whitespace-nowrap">
                  <th className="py-2 pr-3">시각</th><th className="pr-3">종류</th><th className="pr-3">심각도</th>
                  <th className="pr-3">서버 메시지</th><th className="pr-3">출발지</th><th>상세</th>
                </tr>
              </thead>
              <tbody className="max-sm:block">
                {rows.map((e) => {
                  const sev = ownValue(SEVERITY, e.severity) ?? { variant: "neutral" as const, label: e.severity };
                  const ip = str(e.payload?.client_ip);
                  const peer = str(e.payload?.peer);
                  return (
                    <Fragment key={e.id}>
                      <tr className="border-t border-black/5 align-top max-sm:flex max-sm:flex-wrap max-sm:items-center max-sm:gap-x-2 max-sm:gap-y-1 max-sm:py-2">
                        <td className="py-2 pr-3 whitespace-nowrap text-muted max-sm:order-1 max-sm:p-0 max-sm:text-xs">{kstStamp(e.at)}</td>
                        <td className="py-2 pr-3 min-w-[10rem] max-sm:order-4 max-sm:basis-full max-sm:min-w-0 max-sm:p-0">
                          <span className="block">{eventLabel(e.event_type)}</span>
                          <span className="block font-mono text-xs text-muted">{e.event_type}</span>
                        </td>
                        <td className="py-2 pr-3 whitespace-nowrap max-sm:order-2 max-sm:p-0">
                          <StatusPill state={e.severity} variant={sev.variant} label={sev.label} />
                        </td>
                        <td className="py-2 pr-3 min-w-[14rem] break-words [overflow-wrap:anywhere] max-sm:order-5 max-sm:basis-full max-sm:min-w-0 max-sm:p-0">{e.message ?? "—"}</td>
                        <td className="py-2 pr-3 whitespace-nowrap font-mono text-xs max-sm:order-6 max-sm:basis-full max-sm:p-0 max-sm:text-muted max-sm:before:mr-1 max-sm:before:font-sans max-sm:before:content-['출발지']">
                          {ip ?? "—"}
                          {peer !== null && peer !== ip && <span className="block text-xs text-muted max-sm:ml-1 max-sm:inline">{`피어 ${peer}`}</span>}
                        </td>
                        <td className="py-1 max-sm:order-3 max-sm:ml-auto max-sm:p-0">
                          <Button variant="ghost" className="whitespace-nowrap" aria-expanded={openId === e.id}
                                  onClick={() => setOpenId((cur) => (cur === e.id ? null : e.id))}>
                            {openId === e.id ? "닫기" : "펼치기"}
                          </Button>
                        </td>
                      </tr>
                      {openId === e.id && <EventDetail e={e} />}
                    </Fragment>
                  );
                })}
              </tbody>
            </Table>
            <div ref={sentinel} className="pt-1 text-center text-xs text-muted" aria-live="polite">
              {isFetchNextPageError ? (
                <span className="text-bad">
                  {`이전 기록을 더 불러오지 못했습니다: ${errText(q.error)} `}
                  <Button variant="ghost" onClick={() => fetchNextPage()}>다시 시도</Button>
                </span>
              ) : isFetchingNextPage ? "더 불러오는 중…"
                : hasNextPage ? `${rows.length}건 표시 — 스크롤하면 더 불러옵니다`
                : `마지막 기록입니다 — 전체 ${rows.length}건`}
              {q.isRefetchError && (
                <p className="text-bad">{`최신 기록을 다시 읽지 못했습니다(표시는 직전 값): ${errText(q.error)}`}</p>
              )}
            </div>
          </>
        )}
    </Card>
  );
}

export function SecurityPage() {
  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">보안 이벤트</h1>
        <p className="mt-1 text-sm text-muted">로그인·인증번호 보호 장치의 현재 상태와 최근 보안 이벤트입니다. 계정 비밀번호 재설정은 계정 화면에서 합니다.</p>
      </div>
      <StatusCard />
      <LocksCard />
      <EventsCard />
    </section>
  );
}
