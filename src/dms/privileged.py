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
지정, routes_batches._require_batch_privilege, planner(실행 신원 지정·root 계획), privileged_admin_problem(아래 -- 사람 없이
도는 두 자동 경로의 공용 자격 술어: batch_orchestrator 자동 확인의 켠 사람, batch_scheduler 회차의 스케줄을 켠 사람(armed_by)과
배치 요청자 -- 2026-11-16), 스케줄 라우트(routes_batch_schedules -- 생성·수정·켜기의 요청자 판정). 보호를 읽는 곳:
routes_accounts.guard_privileged_account, routes_auth._refuse_privileged_reset -- 비활성화된 부여 계정도 특권 관리자만 다시 켤
수 있어야 한다(효력 집합으로 지키면 목록 밖 관리자가 다시 켜는 것만으로 자격을 되살렸다).
"""
from .identity import privilege_eligible


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


def privileged_admin_problem(settings, repos, username) -> "str | None":
    """사람 없이 그 사람의 이름으로 root 실행을 시작하는 자동 경로가 **그 순간** 다시 보는 자격(2026-11-16 -- 자동 확인의
    술어를 뽑아 배치 스케줄러와 같이 쓴다). None 이면 자격 있음, 아니면 문제 종류:
      "privileged_disabled"  사이트 전체 특권(root) 실행이 꺼졌다(DMS_ALLOW_PRIVILEGED_REQUESTERS=false) -- 그 사람 탓이 아니다
      "not_privileged"       이름이 효력 집합(privileged_names) 밖이다(None·빈 이름 포함 -- 모름은 자격이 아니다)
      "not_admin"            계정이 없거나 비활성이거나 admin 이 아니다(env 목록 이름이어도 -- 활성 관리자만)
    세션 조건은 켜는 라우트(_require_batch_privilege)가 그 순간 봤다 -- 여기서는 이미 켠 사람이라 session_authenticated=True.
    사유 코드는 호출자가 각자 `reason_code=` 키워드 리터럴로 고른다(포탈 문구가 경로마다 다르다 -- 사유 코드 추출기는 반환값을
    읽지 못한다). 두 곳이 같은 함수를 쓰는 이유: 자격 규칙(효력 집합·활성·역할)이 바뀌는 순간 한쪽만 옛 규칙으로 root 를
    시작하지 않게."""
    if not settings.allow_privileged_requesters:
        return "privileged_disabled"
    if not privilege_eligible(requester_id=username, allow_privileged=settings.allow_privileged_requesters,
                              privileged_requesters=privileged_names(settings, repos),
                              session_authenticated=True):
        return "not_privileged"
    account = repos.accounts.get(username)
    if account is None or account["disabled"] or account["role"] != "admin":
        return "not_admin"
    return None
