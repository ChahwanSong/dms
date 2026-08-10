import { useAccounts, useSetRole, useSetDisabled } from "./useAccounts";
import { useMe } from "../auth/useAuth";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { DeleteAccountDialog } from "./DeleteAccountDialog";
import { ApiError } from "../../lib/api";
import type { Account } from "../../lib/types";

export function AccountsList() {
  const q = useAccounts();
  const me = useMe();
  const setRole = useSetRole();
  const setDisabled = useSetDisabled();

  const mutationError = setRole.isError
    ? (setRole.error as ApiError).message
    : setDisabled.isError
    ? (setDisabled.error as ApiError).message
    : null;

  const toggle = (a: Account) => setDisabled.mutate({ username: a.username, disabled: a.disabled !== 1 });

  const rows = q.data ?? [];
  // 활성 관리자(role=admin AND disabled=0). 하나뿐이면 그 행의 삭제를 막는다 --
  // 서버가 last_active_admin(409)으로 다시 강제하지만, 화면에서 미리 사유를 낸다.
  // 목록이 낡으면 어긋날 수 있는 클라이언트 계산이라 힌트일 뿐이다.
  const activeAdmins = rows.filter((a) => a.role === "admin" && a.disabled === 0);

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">계정</h1>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <Table>
          <thead>
            <tr className="text-muted whitespace-nowrap">
              <th className="py-2">사용자명</th><th>역할</th><th>이메일</th><th>상태</th><th>등록일</th><th>작업</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const isSelf = me.data?.actor === a.username;
              const isLastActiveAdmin = a.role === "admin" && a.disabled === 0
                && activeAdmins.length === 1;
              return (
                <tr key={a.username} className="border-t border-black/5">
                  <td className="py-2">{a.username}</td>
                  <td>
                    <select
                      aria-label={`${a.username} 역할`}
                      value={a.role}
                      disabled={isSelf}
                      onChange={(e) => setRole.mutate({ username: a.username, role: e.target.value })}
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="text-muted">{a.email ?? "—"}</td>
                  <td className="whitespace-nowrap">{a.disabled === 1 ? "비활성" : "활성"}</td>
                  <td className="text-muted whitespace-nowrap">{a.created_at}</td>
                  {/* td 를 flex 컨테이너로 만들지 않는다: 셀 안에 div 를 두고 그 div 만
                      flex 로 둔다. td 자체가 flex 면 표 레이아웃 계산에서 빠져 나와
                      다른 열과 폭을 나눠 갖지 못하고, 사유 문구가 길어지는 자기 계정
                      행에서 버튼 글자까지 세로로 뭉개진다(라이브에서 관측).
                      whitespace-nowrap 은 Table 의 overflow-x-auto 와 짝이다 -- 표가
                      컨테이너보다 넓어지면 줄바꿈으로 뭉개는 대신 가로 스크롤한다. */}
                  <td className="py-2">
                    <div className="flex items-center gap-2 whitespace-nowrap">
                      <Button variant="ghost" disabled={isSelf} onClick={() => toggle(a)}>
                        {a.disabled === 1 ? "활성화" : "비활성화"}
                      </Button>
                      {isSelf && <span className="text-muted text-xs">자기 계정은 변경할 수 없습니다</span>}
                      {/* 삭제 자리에는 삭제 고유의 사유를 낸다 -- 왼쪽 문구는 역할/토글
                          가드용이고 여기는 삭제 버튼이 있어야 할 자리다(설계 §3). */}
                      {isSelf ? (
                        <span className="text-muted text-xs">자기 계정은 삭제할 수 없습니다</span>
                      ) : isLastActiveAdmin ? (
                        <span className="text-muted text-xs">마지막 관리자는 삭제할 수 없습니다</span>
                      ) : (
                        <DeleteAccountDialog account={a} />
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
      {mutationError && <p className="text-bad text-sm mt-2">{mutationError}</p>}
    </section>
  );
}
