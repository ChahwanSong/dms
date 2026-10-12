"""특권 목록 = root 실행 자격을 가진 이름의 집합(2026-10-12).

원래는 env `DMS_PRIVILEGED_REQUESTERS`(기동 때 한 번 읽는 콤마 목록)뿐이었다. 사용자 보고(2026-10-12): 「관리 -> 계정 에서
일반 사용자명에게 admin 역할을 줬어. 근데 root로 실행 체크 옵션이 작업 등록할때 안뜨네」 -- 역할만으로는 자격이 생기지
않는다(목록은 **관리자 사이의 경계**: 목록 밖 관리자가 아무 계정이나 admin 으로 올려 root 를 얻지 못하게, 2026-10-07).
그래서 경계는 그대로 두고 **재배포 없이** 넓힐 길을 더했다: 특권 세션 관리자(이미 root 자격이 있는 세션 관리자)가 계정
화면에서 활성 admin 계정에 root 실행 자격을 준다(accounts.root_granted, routes_accounts.set_root_grant).

자격 이름 집합 = env 목록 ∪ 부여된 계정(role admin · 활성 · root_granted = 1). 강등·비활성화·삭제된 계정은 빠진다(강등은
부여도 지운다 -- 다시 올려도 저절로 되살아나지 않는다; 삭제 뒤 같은 이름으로 다시 만든 계정도 부여가 없다 -- 행과 함께
사라진다). 허용 설정(DMS_ALLOW_PRIVILEGED_REQUESTERS=false)은 여전히 모두를 끈다 -- 판정 함수들이 따로 본다.

집합이 둘이다 -- **효력**(privileged_names: env ∪ 부여된 활성 admin)과 **보호**(protected_names: env ∪ 부여 플래그가 선
모든 계정). 효력을 읽는 곳(전부 privileged_names 를 거친다 -- 한 곳이라도 settings.privileged_requesters 를 직접 보면 그 경로만
부여를 모른다): api.auth.can_run_as_root(제출 게이트·/api/auth/me·자격 부여 라우트의 호출자 판정), routes_requests 의 실행 신원
지정, routes_batches._require_batch_privilege, planner(실행 신원 지정·root 계획), batch_orchestrator(자동 확인). 보호를 읽는 곳:
routes_accounts.guard_privileged_account, routes_auth._refuse_privileged_reset -- 비활성화된 부여 계정도 특권 관리자만 다시 켤
수 있어야 한다(효력 집합으로 지키면 목록 밖 관리자가 다시 켜는 것만으로 자격을 되살렸다).
"""


def privileged_names(settings, repos) -> frozenset:
    """env 목록 ∪ 계정 화면에서 부여한 root 실행 자격(활성 admin 계정만). repos 에 accounts 가 없으면(단위 시험의
    좁은 스텁) env 목록만 -- 운영 Repositories 는 늘 accounts 를 가진다."""
    env = frozenset(settings.privileged_requesters)
    accounts = getattr(repos, "accounts", None)
    if accounts is None:
        return env
    return env | accounts.root_granted_names()


def protected_names(settings, repos) -> frozenset:
    """**특권 관리자만 관리하는** 계정 이름 = env 목록 ∪ 부여 플래그가 서 있는 모든 계정(역할·활성 무관 -- 효력과 다르다).
    guard_privileged_account(생성·역할·비활성화·삭제)와 _refuse_privileged_reset(셀프 재설정)이 쓴다. 효력 집합
    (privileged_names)을 쓰면 비활성화된 부여 계정이 보호에서 빠져, 목록 밖 관리자가 그 계정을 다시 켜는 것만으로 root
    자격을 되살렸다(부여는 비활성화로 지워지지 않는다 -- 보안 리뷰 2026-10-12). 강등은 부여를 지우므로 user 역할 + 플래그는
    정상 경로로 생기지 않는다(DB 변조여도 보호 쪽으로 기운다 -- fail-closed)."""
    env = frozenset(settings.privileged_requesters)
    accounts = getattr(repos, "accounts", None)
    if accounts is None:
        return env
    return env | accounts.root_grant_flagged_names()
