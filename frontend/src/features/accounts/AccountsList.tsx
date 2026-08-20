import { useEffect, useState } from "react";
import { useAccounts, useCreateAccount, useSetRole, useSetDisabled } from "./useAccounts";
import { useMe } from "../auth/useAuth";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { DeleteAccountDialog } from "./DeleteAccountDialog";
import { ApiError } from "../../lib/api";
import type { Account } from "../../lib/types";

const dlgField = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

// 운영자 계정 생성(2026-08-20, 사용자 결정): 인증번호 없이 즉시 생성(관리자
// 권한이 곧 승인) -- 셀프서비스 생성(로그인 화면)과 달리 역할도 고를 수 있다.
function CreateAccountDialog() {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const create = useCreateAccount();
  useEffect(() => {
    if (!open) { create.reset(); return; }
    setUsername(""); setPassword(""); setRole("user");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="계정 생성"
            trigger={<Button>계정 생성</Button>}>
      <form className="space-y-3 text-sm"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate({ username: username.trim(), password, role },
                            { onSuccess: () => setOpen(false) });
            }}>
        <label className="block">회사 아이디
          <input aria-label="회사 아이디" className={dlgField} value={username}
                 placeholder="예: cocoa.song"
                 onChange={(e) => setUsername(e.target.value)} /></label>
        <p className="text-xs text-muted">이메일은 아이디@samsung.com 으로 자동 저장됩니다.</p>
        <label className="block">비밀번호
          <input aria-label="비밀번호" type="password" className={dlgField} value={password}
                 onChange={(e) => setPassword(e.target.value)} /></label>
        <label className="block">역할
          <select aria-label="역할" className={dlgField} value={role}
                  onChange={(e) => setRole(e.target.value)}>
            <option value="user">user</option><option value="admin">admin</option>
          </select></label>
        {create.isError && <p className="text-bad">{(create.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit"
                  disabled={create.isPending || username.trim() === "" || password === ""}>생성</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function AccountsList() {
  const q = useAccounts();
  const me = useMe();
  const setRole = useSetRole();
  const setDisabled = useSetDisabled();
  // 아이디 검색(사용자 요청 2026-08-19): 서버가 전체 목록을 주므로(페이징 없음)
  // 클라이언트 필터가 맞다 — 즉답이고 API 무변경. 대소문자 무시 부분 일치.
  const [search, setSearch] = useState("");

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
  // **전체** rows 로 계산한다: 검색으로 좁힌 화면 기준이면 "마지막 관리자"
  // 판정이 검색어에 따라 달라진다.
  const activeAdmins = rows.filter((a) => a.role === "admin" && a.disabled === 0);
  const needle = search.trim().toLowerCase();
  const visible = needle === "" ? rows
    : rows.filter((a) => a.username.toLowerCase().includes(needle));

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">계정</h1>
        <div className="flex items-center gap-2">
          <input aria-label="아이디 검색" placeholder="아이디 검색"
                 className="w-56 rounded-lg border border-black/10 px-3 py-2 text-sm"
                 value={search} onChange={(e) => setSearch(e.target.value)} />
          <CreateAccountDialog />
        </div>
      </div>
      {/* Card 구획(2026-08-19): 운영 화면들과 같은 서피스 — 관리 그룹 일관화 */}
      <Card>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : visible.length === 0 ? (
        // 검색 무일치와 "계정 없음"을 구분한다 — 문구가 다르면 원인이 보인다.
        <p className="text-muted">
          {needle !== "" ? `'${search.trim()}' 와 일치하는 계정이 없습니다` : "등록된 계정이 없습니다"}
        </p>
      ) : (
        <Table>
          <thead>
            <tr className="text-muted whitespace-nowrap">
              <th className="py-2">사용자명</th><th>역할</th><th>이메일</th><th>상태</th><th>등록일</th><th>작업</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((a) => {
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
      </Card>
    </section>
  );
}
