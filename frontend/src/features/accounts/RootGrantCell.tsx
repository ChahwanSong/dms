import { useRef, useState } from "react";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import type { ApiError } from "../../lib/api";
import type { Account } from "../../lib/types";
import type { useSetRootGrant } from "./useAccounts";

// 계정 표의 「root 실행」 칸(2026-10-12 사용자 보고: 「일반 사용자명에게 admin 역할을 줬어. 근데 root로 실행 체크 옵션이
// 작업 등록할때 안뜨네」). root 실행 자격은 admin 역할만으로 생기지 않는다 -- 관리자 사이의 경계라(목록 밖 관리자가 아무
// 계정이나 admin 으로 올려 root 를 얻지 못하게) **이미 root 자격이 있는 관리자**만 다른 admin 계정에 줄 수 있다(서버
// routes_accounts.set_root_grant 가 다시 강제). 이 칸이 그 사실을 화면에 보인다: 누가 자격이 있고(출처: 배포 설정·부여),
// 지금 보는 관리자가 줄 수 있는지.
//
// 주기는 확인 창을 거친다(root = 파일 권한 검사 우회 · 배치 생성 · 실행 신원 지정, 그 계정의 비밀번호를 아는 사람은 모두
// root 를 얻는다는 것, 그 계정이 특권 계정 보호를 받는다는 사실을 말한다). 빼기는 권한을 줄이기만 하므로 바로 한다.
//
// 포커스(2026-10-12 리뷰): 주기·빼기가 끝나면 그 행의 동작 버튼이 다른 버튼으로 바뀌어(주기 ↔ 빼기) 누른 자리가 사라진다 --
// 포커스가 <body> 로 떨어지지 않게 그 행의 상태 글자(바뀐 결과, aria-live)로 옮긴다.

/** k8s 모드(dms-k8s 설계 D4, 2026-10-25)의 화면 문구. k8s 모드에서는 **모든 작업이 root** 라 「root 실행 자격」이라는 말이 거짓이
 *  된다 -- 같은 자격(can_run_as_root)이 k8s 모드에서 주는 것은 「k8s 권한 맵핑 면제」(전체 PV · 직접 경로 · 사용자 지정)다. 자격
 *  판정·서버 경로는 그대로이고 라벨만 바뀐다. */
export const K8S_GRANT_LABEL = "전체 PV·직접 경로 권한";

function grantTerms(k8s: boolean) {
  return k8s
    ? { grantAria: (u: string) => `${u} ${K8S_GRANT_LABEL} 주기`,
        revokeAria: (u: string) => `${u} ${K8S_GRANT_LABEL} 빼기`, title: `${K8S_GRANT_LABEL} 주기`,
        lead: (u: string) => `${u} 에게 ${K8S_GRANT_LABEL}을 줍니다.` }
    : { grantAria: (u: string) => `${u} root 실행 자격 주기`,
        revokeAria: (u: string) => `${u} root 실행 자격 빼기`, title: "root 실행 자격 주기",
        lead: (u: string) => `${u} 에게 root 실행 자격을 줍니다.` };
}

export function rootStatusText(a: Account): string {
  if (a.root_source === "config") {
    const base = a.root_eligible ? "있음 · 배포 설정" : "배포 설정 · 지금 효력 없음";
    return a.root_granted ? `${base} + 부여` : base;
  }
  if (a.root_source === "granted") return a.root_eligible ? "있음 · 부여" : "부여됨 · 지금 효력 없음";
  return "없음";
}

function inactiveWhy(a: Account): string | undefined {
  if (a.root_source === undefined || a.root_source === null || a.root_eligible) return undefined;
  if (a.role !== "admin") return "admin 역할이 아니라 효력이 없습니다";
  if (a.disabled === 1) return "비활성 계정이라 효력이 없습니다(다시 켜면 돌아옵니다)";
  return "사이트 설정이 root 실행을 끄고 있습니다(DMS_ALLOW_PRIVILEGED_REQUESTERS)";
}

function GrantDialog({ a, grant, onGranted, k8s }: {
  a: Account; grant: ReturnType<typeof useSetRootGrant>; onGranted: () => void; k8s: boolean;
}) {
  const [open, setOpen] = useState(false);
  const mine = grant.variables?.username === a.username;
  const busy = grant.isPending && mine;
  const t = grantTerms(k8s);
  return (
    <Dialog open={open} title={t.title} busy={busy}
            onOpenChange={(o) => { if (!o && busy) return; if (o && grant.isError) grant.reset(); setOpen(o); }}
            trigger={<Button variant="outline" aria-label={t.grantAria(a.username)}>자격 주기</Button>}>
      <div className="space-y-3 text-sm text-ink/70 break-keep">
        <p className="font-medium text-ink [overflow-wrap:anywhere]">{t.lead(a.username)}</p>
        {k8s ? (
          <>
            <p>
              이 관리자는 단일 작업에서 k8s 권한(namespace 의 rolebinding)과 관계없이 모든 클러스터의 PV 와 직접 경로(등록된
              파일시스템 스토리지)를 대상으로 고르고, 다른 사용자를 권한 기준으로 지정하고, 배치 작업을 만들 수 있게 됩니다. k8s
              모드에서는 모든 작업이 root 로 실행됩니다.
            </p>
            <p className="font-medium text-ink">
              이 계정의 비밀번호를 아는 사람은 누구나 모든 PV 와 직접 경로를 읽고 쓰고 지울 수 있게 됩니다 — 비밀번호를 계정
              주인만 아는지 먼저 확인하세요(다른 관리자가 만들어 알려 준 비밀번호라면 주지 마세요).
            </p>
          </>
        ) : (
          <>
            <p>
              이 관리자는 작업 등록에서 「root 권한으로 실행」(파일 권한 검사를 건너뜀)을 쓰고, 배치 작업을 만들고, 다른 사용자를
              실행 신원으로 지정할 수 있게 됩니다.
            </p>
            <p className="font-medium text-ink">
              이 계정의 비밀번호를 아는 사람은 누구나 root 로 실행할 수 있게 됩니다 — 비밀번호를 계정 주인만 아는지 먼저
              확인하세요(다른 관리자가 만들어 알려 준 비밀번호라면 주지 마세요).
            </p>
          </>
        )}
        <p>
          이 계정은 이제 root 자격이 있는 관리자만 역할 변경·비활성화·삭제할 수 있고, 메일 인증번호로 비밀번호를 재설정할 수
          없습니다. 역할을 user 로 내리면 자격도 함께 빠집니다.
        </p>
        {grant.isError && mine && <p role="alert" className="text-bad">{(grant.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" disabled={busy} onClick={() => setOpen(false)}>닫기</Button>
          <Button disabled={busy} onClick={() => grant.mutate({ username: a.username, granted: true },
                                                              { onSuccess: () => { setOpen(false); onGranted(); } })}>
            {busy ? "주는 중…" : "자격 주기"}</Button>
        </div>
      </div>
    </Dialog>
  );
}

export function RootGrantCell({ a, canGrant, isSelf, grant, k8s = false }: {
  a: Account;
  /** 지금 보는 관리자가 root 자격이 있나(me.can_run_as_root) -- 없으면 상태만 보인다(서버도 403). */
  canGrant: boolean;
  isSelf: boolean;
  grant: ReturnType<typeof useSetRootGrant>;
  /** k8s 모드(me.identity_mode === "k8s") -- 라벨·확인 창 문구만 「전체 PV·직접 경로 권한」으로 바뀐다. */
  k8s?: boolean;
}) {
  const t = grantTerms(k8s);
  const status = rootStatusText(a);
  const why = inactiveWhy(a);
  const statusRef = useRef<HTMLSpanElement>(null);
  // 동작 버튼이 바뀐 뒤(재조회로 행이 다시 그려진 뒤) 포커스를 상태 글자로 -- Radix 의 닫힘 복귀(사라진 트리거 → <body>)보다
  // 뒤에 오게 한 틱 미룬다.
  const focusStatus = () => setTimeout(() => statusRef.current?.focus(), 0);
  let action: React.ReactNode = null;
  // 저장된 부여가 있으면 뺄 수 있다(env 이름과 겹쳐도 -- env 에서 빠지면 부여로 효력이 남는다, 리뷰).
  if (a.root_granted === true && canGrant && !isSelf) {
    action = (
      <Button variant="ghost" aria-label={t.revokeAria(a.username)}
              disabled={grant.isPending}
              onClick={() => grant.mutate({ username: a.username, granted: false }, { onSuccess: focusStatus })}>
        자격 빼기</Button>
    );
  } else if (a.root_source === null && a.role === "admin" && canGrant && !isSelf) {
    action = <GrantDialog a={a} grant={grant} onGranted={focusStatus} k8s={k8s} />;
  }
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <span ref={statusRef} tabIndex={-1} aria-live="polite"
            className={`outline-none focus-visible:ring-2 focus-visible:ring-accent ${a.root_eligible ? "text-ink" : "text-muted"}`}
            title={why ?? (a.root_source === "config"
              ? "배포 설정(DMS_PRIVILEGED_REQUESTERS)에 있는 계정 — 화면에서 바꿀 수 없습니다" : undefined)}>
        {status}</span>
      {action}
    </div>
  );
}
