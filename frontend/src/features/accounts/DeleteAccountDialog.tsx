import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import type { Account } from "../../lib/types";
import { useDeleteAccount } from "./useAccounts";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

export function DeleteAccountDialog({ account }: { account: Account }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const del = useDeleteAccount();
  // 다이얼로그를 닫을 때마다 재입력값과 이전 에러를 비운다(DenyDialog 관례) --
  // 다시 열었을 때 지난 실패 문구나 이미 채워진 확인란이 남아 있으면 안 된다.
  useEffect(() => { if (!open) { setTyped(""); del.reset(); } }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="계정 삭제"
            trigger={<Button variant="ghost">삭제</Button>}>
      <div className="space-y-3 text-sm">
        <p>이 작업은 되돌릴 수 없습니다. 삭제하려면 <b>{account.username}</b> 를 그대로 입력하세요.</p>
        {/* 재입력 일치 전엔 확인 버튼을 잠근다(오조작 방지). 프론트 가드는 보안 경계가
            아니다 -- 서버가 자기/마지막관리자/비종단요청을 다시 강제한다(설계 §3). */}
        <input aria-label="삭제 확인 사용자명 재입력" className={field}
               value={typed} onChange={(e) => setTyped(e.target.value)} />
        {del.isError && <p className="text-bad">{(del.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="button" disabled={typed !== account.username || del.isPending}
                  onClick={() => del.mutate(account.username,
                                            { onSuccess: () => setOpen(false) })}>
            계정 삭제
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
