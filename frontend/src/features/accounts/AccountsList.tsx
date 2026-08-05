import { useAccounts, useSetRole, useSetDisabled } from "./useAccounts";
import { useMe } from "../auth/useAuth";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
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
            <tr className="text-muted">
              <th className="py-2">사용자명</th><th>역할</th><th>이메일</th><th>상태</th><th>등록일</th><th>작업</th>
            </tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((a) => {
              const isSelf = me.data?.actor === a.username;
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
                  <td>{a.disabled === 1 ? "비활성" : "활성"}</td>
                  <td className="text-muted">{a.created_at}</td>
                  <td className="flex items-center gap-2 py-2">
                    <Button variant="ghost" disabled={isSelf} onClick={() => toggle(a)}>
                      {a.disabled === 1 ? "활성화" : "비활성화"}
                    </Button>
                    {isSelf && <span className="text-muted text-xs">자기 계정은 변경할 수 없습니다</span>}
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
