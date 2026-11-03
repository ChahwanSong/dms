import { useEffect, useId, useRef, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import type { Account, AdminPasswordResetResult } from "../../lib/types";
import { useAdminResetPassword } from "./useAccounts";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

/** 관리자 비밀번호 재설정(2026-11-03, 사용자 결정 -- 인증번호 전체 잠금 중에도 계정을 되살리는 구제 경로).
 *  - 인증번호 없이 바로 바뀌고, 대상 계정의 모든 로그인 세션이 끝난다(서버 session_epoch 회전). 관리자 자신의 세션은 그대로다.
 *  - 자기 계정 행에는 이 버튼을 두지 않는다(서버도 409 cannot_reset_self) -- 탈취한 세션으로 비밀번호를 바꿔 주인을 잠그는
 *    길이 생긴다. 자기 비밀번호는 로그인 화면의 셀프 재설정(인증번호)으로 바꾼다.
 *  - 특권 계정도 버튼은 보인다 -- 판정은 서버(guard_privileged_account)가 하고 403 사유는 창 안에 보인다.
 *  - 비밀번호 규칙은 없다(사용자 결정) -- 두 칸이 같고 비어 있지 않은지만 본다(오타 방지). 비밀번호는 봉인 통로로만 보낸다.
 *  - 보내는 동안은 닫히지 않는다(Esc·X·바깥 클릭·「취소」, 2026-11-03 리뷰 -- RootGrantCell 관례). 닫히면 아래 effect 의
 *    reset.reset() 이 진행 중인 mutation 에서 observer 를 떼어 mutate() 의 onSuccess 가 불리지 않는다 -- 서버는 비밀번호를 바꾸고
 *    세션을 끊었는데 관리자는 「취소했다」고 믿고 성공 안내도 못 받는다(「취소」는 이미 보낸 요청을 되돌리지 못한다).
 *    판정은 reset.isPending 만이 아니라 제출 순간 동기로 세우는 sending 으로도 한다 -- isPending 은 TanStack 알림(setTimeout 0)
 *    뒤 다음 렌더에야 참이라, 「재설정」 직후의 Esc·「취소」·두 번째 Enter 가 그 틈에 들어오면 창이 닫히거나 두 번 보냈다
 *    (실 Chrome 5회 중 4회 재현).
 *  - 성공해 닫히면 포커스는 트리거가 아니라 호출측의 결과 문구로 간다(focusAfterDone) -- 긴 목록에서도 결과가 화면에 들어오고
 *    화면낭독기가 읽는다. */
export function ResetPasswordDialog({ account, onDone, focusAfterDone }: {
  account: Account; onDone: (result: AdminPasswordResetResult) => void; focusAfterDone?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const reset = useAdminResetPassword();
  const mismatchId = useId();
  const username = account.username;
  const succeeded = useRef(false);
  const sending = useRef(false);
  const busy = () => sending.current || reset.isPending;
  const close = () => { if (!busy()) setOpen(false); };
  // 닫을 때마다 입력과 지난 오류를 비운다(DeleteAccountDialog 관례) -- 다시 열었을 때 남의 비밀번호·실패 문구가 남지 않게.
  useEffect(() => {
    if (!open) { setPassword(""); setConfirm(""); reset.reset(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const mismatch = password !== "" && confirm !== "" && password !== confirm;
  const ready = password !== "" && password === confirm && !reset.isPending;
  return (
    <Dialog open={open} title="비밀번호 재설정" busy={reset.isPending}
            onOpenChange={(o) => { if (o) setOpen(true); else close(); }}
            onCloseAutoFocus={(e) => {
              const done = succeeded.current;
              succeeded.current = false;
              if (done && focusAfterDone) { e.preventDefault(); focusAfterDone(); }
            }}
            trigger={<Button variant="ghost" aria-label={`${username} 비밀번호 재설정`}>비밀번호 재설정</Button>}>
      <form className="space-y-3 text-sm"
            onSubmit={(e) => {
              e.preventDefault();
              if (!ready || sending.current) return;
              sending.current = true;
              reset.mutate({ username, password }, {
                onSuccess: (result) => { succeeded.current = true; setOpen(false); onDone(result); },
                onSettled: () => { sending.current = false; },
              });
            }}>
        {/* 아이디 칸 없이 new-password 두 칸뿐인 폼은 브라우저 비밀번호 관리자가 「이 사이트에 저장된 자격(대개 관리자 본인)의
            비밀번호 변경」으로 읽고 대상의 새 비밀번호로 관리자 자격을 덮자고 제안한다(2026-11-03 리뷰). 숨은 username 칸으로
            자격을 대상 아이디에 묶는다(Chrome 권장 「숨은 필드로 암묵 정보」). 보내지 않는 값이다(봉인 요청은 username 을 따로 싣는다). */}
        <input type="text" name="username" autoComplete="username" value={username} readOnly hidden />
        <p><b>{username}</b> 의 비밀번호를 새로 정합니다. 인증번호 없이 바로 바뀌고, 이 계정으로 로그인해 있던 모든 브라우저는 다시 로그인해야 합니다.</p>
        <label className="block">새 비밀번호
          <input aria-label="새 비밀번호" type="password" autoComplete="new-password" className={field}
                 value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <label className="block">새 비밀번호 확인
          <input aria-label="새 비밀번호 확인" type="password" autoComplete="new-password" className={field}
                 aria-invalid={mismatch || undefined} aria-describedby={mismatch ? mismatchId : undefined}
                 value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
        {mismatch && <p id={mismatchId} className="text-bad text-xs">두 비밀번호가 다릅니다</p>}
        <p className="text-xs text-muted">새 비밀번호는 사용자에게 직접 전달하세요. 사용자에게 변경 안내 메일이 갑니다(메일 발송 방식이 stub 이면 보내지 않습니다).</p>
        {reset.isError && (
          <p role="alert" className="text-bad">
            {reset.error instanceof ApiError ? reset.error.message : "재설정 요청에 실패했습니다 — 네트워크 상태를 확인하세요"}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" disabled={reset.isPending} onClick={close}>취소</Button>
          <Button type="submit" disabled={!ready}>{reset.isPending ? "재설정 중…" : "재설정"}</Button>
        </div>
      </form>
    </Dialog>
  );
}
