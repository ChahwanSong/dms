# DMS Phase 3c — Live 어댑터 (LDAP + Volcano) + dms-job-runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 3b의 stub 어댑터 자리에 **실제 구현**을 넣는다 — ldap3 기반 IdentityResolver, kubernetes 클라이언트 기반 VolcanoExecutionAdapter(네이티브 Volcano Job 제출/폴링/아티팩트 읽기/종료), 잡 이미지 안에서 도는 dms-job-runner(hostfile 대기·SSH 배리어·identity 물질화·mpirun·summary). 모든 로직은 I/O 주입으로 단위 테스트하고, 실 k8s/LDAP/MPI 검증은 별도 실증 단계.

**Architecture:** 스펙 §5의 "실행 신원(LDAP 실시간 조회)"과 "Volcano 실행"을 구현. 어댑터는 Phase 3b의 `ExecutionAdapter`/`IdentityResolver` 프로토콜을 그대로 만족한다(교체 가능). 순수 로직(매니페스트 빌더, LDAP 검색결과 파싱, mpirun 명령 조립, 요약 지문)은 주입된 클라이언트·subprocess·파일읽기 뒤에 있어 단위 테스트로 완결된다. 실제 k8s API 호출·SSH·mpirun은 얇은 I/O 래퍼로, 실증 단계에서 검증한다.

**Tech Stack:** Phase 1·2·3a·3b 코드 위에 Python 3.11+, `ldap3`(신규 optional dep), `kubernetes`(신규 optional dep). 테스트는 여전히 서비스 없이 SQLite + fake 클라이언트.

## Global Constraints

- 스펙이 진실: `docs/superpowers/specs/2026-08-02-dms-clean-slate-design.md` §5. legacy(`legacy/`)는 매니페스트/명령 shape 참고만(읽기 전용, import·복사 금지).
- **어댑터는 Phase 3b 프로토콜을 만족한다**: `ExecutionAdapter`(submit/poll/read_summary/terminate), `IdentityResolver`(resolve). stub과 교체 가능해야 하고 stepper/planner는 변경 최소.
- **모든 외부 I/O는 주입**: k8s 접근은 주입된 클라이언트 객체 뒤, LDAP은 주입된 connection factory 뒤, subprocess/파일은 주입된 함수 뒤. 테스트는 fake로. **실 k8s/LDAP/MPI 접근 코드는 얇게** 유지.
- **kubectl 서브프로세스 금지** — kubernetes Python 클라이언트만 (스펙 §3).
- **fail-closed**: LDAP 반쪽 설정 → resolver None(요청별 ldap_not_configured). 매니페스트 제출 실패 → ExecutionError. summary 없음/빈 dict → 지문 없음(Phase 3b가 empty_preview 처리).
- 사유 코드 snake_case. 시각 `utc_now_iso`. 전체 테스트는 서비스 없이 SQLite + fake, 0 warnings(filterwarnings=error).
- 커밋: conventional commit + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, 태스크마다 커밋.

## Phase 1·2·3a·3b가 제공하는 인터페이스 (전제 — 변경 금지)

- `dms.execution`: `ExecStatus`(Pending/Running/Succeeded/Failed/TimedOut), `JobSpec`(frozen: job_id/phase/operation/tool/dryrun/identity/paths/options/candidates/process_count/queue/priority_class/artifact_base), `ExecutionError(reason_code, detail)`, `ExecutionAdapter`(Protocol), `StubExecutionAdapter`.
- `dms.identity`: `ResolvedIdentity`(username/uid/gid/groups tuple/privileged), `IdentityUnavailable`, `IdentityResolver`(Protocol: resolve(username)->ResolvedIdentity|None), `StubIdentityResolver`, `resolve_job_identity(...)`.
- `dms.stepper.JobStepper`: `_build_spec(job, phase, dryrun)` — JobSpec 생성. `_poll_or_submit_execution` 등. paths는 operation별 상대경로.
- `dms.repositories`: `.storages.get(name)`(mount_path/managed_root/backend_type), `.data_jobs`(get_job/set_job_state/set_phase_ref/list_jobs), `.requests.finalize_from_job`.
- `dms.config.Settings`: `_SERVER_INT_KEYS`, `_parse_int`, `_parse_bool`, `_parse_csv_set`, `_is_placeholder`, `artifact_base_uri`, `preview_ttl_seconds`, `stepper_interval_seconds`.
- `dms.controller.build_loops(settings, repos, *, identity_resolver=None, execution_adapter=None)`; `run_forever(...)`. `dms.api.app.create_app` — `app.state.identity_resolver`/`app.state.execution_adapter`.
- worker_pool(planner): `{tool, identity{username,uid,gid,groups,privileged}, candidates{primary|source,destination}, node_count, process_count, queue, priority_class, ...}`.

## 실증 시 테스트베드 사실 (플랜엔 상수로 안 박고 env로 — 참고용)

- LDAP: `ldap://10.10.10.30:389`, base `dc=dms,dc=local`, users `ou=People,dc=dms,dc=local`, groups `ou=Groups,dc=dms,dc=local`, 샘플 `alice`(uid 10001)/`bob`(10002), group `dmsusers`(gid 10000), 비번 `Passw0rd!`, anonymous bind로 검색.
- Volcano: queue `dms-data`, PriorityClass `dms-low/dms-mid/dms-high`.
- CephFS storages: `cephfs-dms`(/cephfs, 전 워커), `cephfs-third`(/cephfs-third, w1-3), `cephfs-secondary`(/cephfs-secondary, w4-5). artifact base `file:///cephfs/dms/artifacts`.
- 잡 이미지: mpifileutils(dscan/dsync/nsync/drm) + openmpi + sshd + dms-job-runner. 레지스트리 `pkg-01:5000`.

## File Structure

```
pyproject.toml                        # (수정) ldap3, kubernetes optional deps
src/dms/config.py                     # (수정) LDAP 설정 + 실행 백엔드/잡 이미지/네임스페이스
src/dms/identity_ldap.py              # LdapIdentityResolver (주입된 connection factory), build_ldap_resolver
src/dms/execution_manifests.py        # 순수 매니페스트 빌더: build_volcano_job, build_preflight_pod, 명령/플래그 렌더
src/dms/execution_volcano.py          # VolcanoExecutionAdapter (K8sClient 주입), K8sClient 프로토콜 + 실 구현
src/dms/stepper.py                    # (수정) _build_spec에 스토리지 절대경로 enrich + Executing preflight 재검증
src/dms/repositories/data_jobs.py     # (수정) attempt count / orphan 조회
src/dms/repositories/requests.py      # (수정) recover_orphans (터미널 잡 + 비터미널 요청)
src/dms/controller.py                 # (수정) orphan 복구 스윕 루프 스텝
src/dms/api/app.py, src/dms/cli.py    # (수정) 설정 기반 live 어댑터/리졸버 선택
src/dms_job_runner/__init__.py
src/dms_job_runner/commands.py        # 순수: mpirun/tool 명령 조립, 플래그, identity passwd, hostfile/role-map 파싱
src/dms_job_runner/runner.py          # main 오케스트레이션 (subprocess/fs/time 주입)
tests/test_identity_ldap.py
tests/test_config_phase3c.py
tests/test_execution_manifests.py
tests/test_execution_volcano.py
tests/test_stepper_enrich.py
tests/test_recover_orphans.py
tests/test_job_runner_commands.py
tests/test_job_runner_runner.py
tests/test_wiring_phase3c.py
```

---

### Task 1: LDAP IdentityResolver (`identity_ldap.py`)

**Files:**
- Modify: `pyproject.toml` (optional dep `ldap = ["ldap3>=2.9"]`)
- Create: `src/dms/identity_ldap.py`
- Test: `tests/test_identity_ldap.py`

**Interfaces:**
- Consumes: `dms.identity`의 `ResolvedIdentity`, `IdentityUnavailable`.
- Produces:
  - `LdapIdentityResolver(*, connect, user_base, group_base)` — `connect: Callable[[], Connection]`(주입된 connection factory, ldap3.Connection 유사 객체). `.resolve(username) -> ResolvedIdentity | None`:
    - user 검색: `connect()`로 연결, `search(user_base, "(uid=<username>)", attributes=["uidNumber","gidNumber"])`. 결과 없으면 None. uidNumber/gidNumber를 int로.
    - group 검색: `search(group_base, "(memberUid=<username>)", attributes=["cn"])` → group cn들 정렬 tuple.
    - 연결/검색 예외(ldap3.core.exceptions 류 또는 임의 Exception) → `IdentityUnavailable(str(exc)[:200])`.
    - 반환 `ResolvedIdentity(username, uid, gid, groups, privileged=False)`.
  - `build_ldap_resolver(settings) -> LdapIdentityResolver | None` — settings의 ldap_uri/user_base/group_base가 모두 있으면 resolver, 하나라도 placeholder/없으면 None(fail-closed → resolve_job_identity가 ldap_not_configured). connect factory는 ldap3.Server/Connection을 anonymous 또는 bind_dn/bind_pw로 생성(bind_dn 있으면 사용). 이 함수는 ldap3를 lazy import.
  - fake 주입으로 단위 테스트 — 실제 ldap3 서버 불필요.

- [ ] **Step 1: 환경 셋업 + 실패 테스트**

먼저 venv: `python3 -m venv .venv && .venv/bin/pip install -q -e ".[test]"` 후 `.venv/bin/pytest -q`로 기존 231 passed 확인.

```python
# tests/test_identity_ldap.py
import pytest
from dms.identity import IdentityUnavailable, ResolvedIdentity
from dms.identity_ldap import LdapIdentityResolver


class _FakeEntry:
    def __init__(self, attrs):
        self._attrs = attrs

    def __getitem__(self, key):
        return _FakeAttr(self._attrs[key])


class _FakeAttr:
    def __init__(self, value):
        self.value = value


class _FakeConn:
    """ldap3.Connection 유사: search가 self.entries를 채운다."""
    def __init__(self, users, groups, *, broken=False):
        self._users = users      # {uid: (uidNumber, gidNumber)}
        self._groups = groups    # {uid: [cn,...]}
        self._broken = broken
        self.entries = []

    def search(self, base, filt, attributes=None):
        if self._broken:
            raise RuntimeError("ldap down")
        if "memberUid" in filt:
            uid = filt.split("memberUid=")[1].rstrip(")")
            self.entries = [_FakeEntry({"cn": cn}) for cn in self._groups.get(uid, [])]
        else:
            uid = filt.split("uid=")[1].rstrip(")")
            if uid in self._users:
                un, gn = self._users[uid]
                self.entries = [_FakeEntry({"uidNumber": un, "gidNumber": gn})]
            else:
                self.entries = []
        return bool(self.entries)


def _resolver(users, groups, *, broken=False):
    return LdapIdentityResolver(
        connect=lambda: _FakeConn(users, groups, broken=broken),
        user_base="ou=People,dc=dms,dc=local",
        group_base="ou=Groups,dc=dms,dc=local")


def test_resolve_hit():
    r = _resolver({"alice": (10001, 10000)}, {"alice": ["dmsusers", "eng"]})
    out = r.resolve("alice")
    assert out == ResolvedIdentity("alice", 10001, 10000, ("dmsusers", "eng"), False)


def test_resolve_miss_returns_none():
    r = _resolver({"alice": (10001, 10000)}, {})
    assert r.resolve("ghost") is None


def test_resolve_no_groups():
    r = _resolver({"bob": (10002, 10000)}, {})
    assert r.resolve("bob").groups == ()


def test_broken_connection_raises_unavailable():
    r = _resolver({}, {}, broken=True)
    with pytest.raises(IdentityUnavailable):
        r.resolve("alice")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_identity_ldap.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

pyproject.toml `[project.optional-dependencies]`에 `ldap = ["ldap3>=2.9"]` 추가.

```python
# src/dms/identity_ldap.py
"""ldap3 기반 실행 신원 resolver. LDAP 접근은 주입된 connection factory 뒤에 있다."""
from .identity import IdentityUnavailable, ResolvedIdentity


class LdapIdentityResolver:
    def __init__(self, *, connect, user_base, group_base):
        self._connect = connect
        self._user_base = user_base
        self._group_base = group_base

    def resolve(self, username: str):
        try:
            conn = self._connect()
            conn.search(self._user_base, f"(uid={username})",
                        attributes=["uidNumber", "gidNumber"])
            if not conn.entries:
                return None
            entry = conn.entries[0]
            uid = int(entry["uidNumber"].value)
            gid = int(entry["gidNumber"].value)
            conn.search(self._group_base, f"(memberUid={username})",
                        attributes=["cn"])
            groups = tuple(sorted(e["cn"].value for e in conn.entries))
        except IdentityUnavailable:
            raise
        except Exception as exc:
            raise IdentityUnavailable(str(exc)[:200])
        return ResolvedIdentity(username, uid, gid, groups, False)


def build_ldap_resolver(settings):
    from .config import _is_placeholder  # local import to avoid cycle
    uri = getattr(settings, "ldap_uri", None)
    user_base = getattr(settings, "ldap_user_base", None)
    group_base = getattr(settings, "ldap_group_base", None)
    if _is_placeholder(uri) or _is_placeholder(user_base) or _is_placeholder(group_base):
        return None

    def connect():
        import ldap3
        server = ldap3.Server(uri)
        bind_dn = getattr(settings, "ldap_bind_dn", "") or None
        bind_pw = getattr(settings, "ldap_bind_pw", "") or None
        conn = ldap3.Connection(server, user=bind_dn, password=bind_pw,
                                auto_bind=True)
        return conn

    return LdapIdentityResolver(connect=connect, user_base=user_base,
                                group_base=group_base)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_identity_ldap.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/dms/identity_ldap.py tests/test_identity_ldap.py
git commit -m "feat: ldap3 IdentityResolver (주입 connection, 검색결과 파싱)"
```

---

### Task 2: 설정 확장 (`config.py`)

**Files:**
- Modify: `src/dms/config.py`
- Test: `tests/test_config_phase3c.py`

**Interfaces:**
- Consumes: 기존 Settings 인프라.
- Produces: Settings 필드 추가 (전부 기본값, env 선택 — 실증 때만 채움):
  - `ldap_uri: str = ""` (`DMS_LDAP_URI`), `ldap_user_base: str = ""` (`DMS_LDAP_USER_BASE`), `ldap_group_base: str = ""` (`DMS_LDAP_GROUP_BASE`), `ldap_bind_dn: str = ""` (`DMS_LDAP_BIND_DN`), `ldap_bind_pw: str = ""` (`DMS_LDAP_BIND_PW`) — 문자열, environ.get.
  - `execution_backend: str = "stub"` (`DMS_EXECUTION_BACKEND`, "stub"|"volcano"), `job_image: str = ""` (`DMS_JOB_IMAGE`), `k8s_namespace: str = "dms"` (`DMS_K8S_NAMESPACE`).
  - `job_max_attempts: int = 3` (`DMS_JOB_MAX_ATTEMPTS`, `_SERVER_INT_KEYS`로).

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_config_phase3c.py
from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_phase3c_defaults():
    s = Settings.from_env(VALID)
    assert s.ldap_uri == "" and s.ldap_user_base == "" and s.ldap_group_base == ""
    assert s.execution_backend == "stub"
    assert s.k8s_namespace == "dms"
    assert s.job_max_attempts == 3


def test_phase3c_overrides():
    s = Settings.from_env({**VALID,
        "DMS_LDAP_URI": "ldap://10.10.10.30:389",
        "DMS_LDAP_USER_BASE": "ou=People,dc=dms,dc=local",
        "DMS_LDAP_GROUP_BASE": "ou=Groups,dc=dms,dc=local",
        "DMS_EXECUTION_BACKEND": "volcano",
        "DMS_JOB_IMAGE": "pkg-01:5000/dms-mpifileutils:latest",
        "DMS_JOB_MAX_ATTEMPTS": "5"})
    assert s.ldap_uri == "ldap://10.10.10.30:389"
    assert s.execution_backend == "volcano"
    assert s.job_image == "pkg-01:5000/dms-mpifileutils:latest"
    assert s.job_max_attempts == 5
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_config_phase3c.py -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: 구현**

`_SERVER_INT_KEYS`에 `("DMS_JOB_MAX_ATTEMPTS", "job_max_attempts", 3)` 추가. Settings 필드 추가(기존 필드 뒤, 전부 기본값):
```python
    ldap_uri: str = ""
    ldap_user_base: str = ""
    ldap_group_base: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_pw: str = ""
    execution_backend: str = "stub"
    job_image: str = ""
    k8s_namespace: str = "dms"
    job_max_attempts: int = 3
```
`from_env`의 `return cls(...)`에 문자열 필드 추가:
```python
            ldap_uri=environ.get("DMS_LDAP_URI", ""),
            ldap_user_base=environ.get("DMS_LDAP_USER_BASE", ""),
            ldap_group_base=environ.get("DMS_LDAP_GROUP_BASE", ""),
            ldap_bind_dn=environ.get("DMS_LDAP_BIND_DN", ""),
            ldap_bind_pw=environ.get("DMS_LDAP_BIND_PW", ""),
            execution_backend=environ.get("DMS_EXECUTION_BACKEND", "stub"),
            job_image=environ.get("DMS_JOB_IMAGE", ""),
            k8s_namespace=environ.get("DMS_K8S_NAMESPACE", "dms"),
```
(`job_max_attempts`는 `_SERVER_INT_KEYS` extra로 자동 포함.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_config_phase3c.py tests/test_config.py tests/test_config_phase2.py tests/test_config_phase3.py tests/test_config_phase3b.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/config.py tests/test_config_phase3c.py
git commit -m "feat: LDAP + 실행 백엔드/잡 이미지 설정"
```

---

### Task 3: Volcano 매니페스트 빌더 — 명령/플래그 (`execution_manifests.py` 1/3)

**Files:**
- Create: `src/dms/execution_manifests.py`
- Test: `tests/test_execution_manifests.py`

**Interfaces:**
- Consumes: `dms.execution.JobSpec`.
- Produces (순수 함수):
  - `render_tool_flags(tool: str, options: dict) -> list[str]` — 옵션 dict를 mpifileutils 플래그 리스트로. sync(dsync/nsync): `--delete`/`--contents`/`--direct`/`--open-noatime`/`--quiet`(bool True면 플래그), `--batch-files N`/`--bufsize N`(int), `--chmod X`/`--chown Y`(str). rm(drm): `--stat`/`--lite`/`--quiet`. scan(dscan): `--print`(항상). 값은 그대로(escape는 job-runner의 shlex 몫). bool False/부재는 생략.
  - `tool_argv(spec: JobSpec, *, abs_paths: dict) -> list[str]` — 도구별 argv(프로그램명 제외): dscan → `["--directory", abs_paths["target"], "--output", "<report>", "--print"]`(report는 job-runner가 채우는 placeholder `"$DMS_SCAN_REPORT"`), dsync/nsync → `[*flags, "--dryrun"?, src, dst]`, drm → `[*flags, "--dryrun"?, target]`. dryrun은 spec.dryrun True일 때만. abs_paths는 절대경로(managed_root+상대).
  - 이 태스크는 순수 argv/flags 렌더만. 실제 매니페스트는 Task 4·5.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_execution_manifests.py
from dms.execution import JobSpec
from dms.execution_manifests import render_tool_flags, tool_argv


def _spec(**kw):
    base = dict(job_id="j1", phase="execution", operation="scan", tool="dscan",
                dryrun=False, identity={"uid": 10001}, paths={}, options={},
                candidates={"primary": ["n1"]}, process_count=8, queue="dms-data",
                priority_class="dms-mid", artifact_base="file:///cephfs/dms/artifacts")
    base.update(kw)
    return JobSpec(**base)


def test_sync_flags():
    flags = render_tool_flags("dsync", {"delete": True, "quiet": False,
                                        "batch_files": 1000, "chown": "alice:dev"})
    assert "--delete" in flags and "--quiet" not in flags
    assert flags[flags.index("--batch-files") + 1] == "1000"
    assert flags[flags.index("--chown") + 1] == "alice:dev"


def test_rm_flags():
    assert render_tool_flags("drm", {"stat": True, "lite": False}) == ["--stat"]


def test_scan_argv():
    argv = tool_argv(_spec(operation="scan", tool="dscan"),
                     abs_paths={"target": "/cephfs/dms/team/data"})
    assert argv == ["--directory", "/cephfs/dms/team/data",
                    "--output", "$DMS_SCAN_REPORT", "--print"]


def test_sync_argv_with_dryrun():
    spec = _spec(operation="sync", tool="dsync", dryrun=True, options={"delete": True})
    argv = tool_argv(spec, abs_paths={"source": "/cephfs/a", "destination": "/cephfs/b"})
    assert argv == ["--delete", "--dryrun", "/cephfs/a", "/cephfs/b"]


def test_rm_argv():
    spec = _spec(operation="rm", tool="drm", dryrun=False,
                 options={"recursive": True})
    argv = tool_argv(spec, abs_paths={"target": "/cephfs/junk"})
    assert argv == ["/cephfs/junk"]  # recursive는 drm 기본 재귀 — 플래그 아님
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_execution_manifests.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/execution_manifests.py
"""Volcano 매니페스트 + 도구 명령 빌더. 전부 순수 함수 — 실제 제출은 어댑터(Task 6)."""

_SYNC_BOOL_FLAGS = {"delete": "--delete", "contents": "--contents",
                    "direct": "--direct", "open_noatime": "--open-noatime",
                    "quiet": "--quiet"}
_SYNC_VALUE_FLAGS = {"batch_files": "--batch-files", "bufsize": "--bufsize",
                     "chmod": "--chmod", "chown": "--chown"}
_RM_BOOL_FLAGS = {"stat": "--stat", "lite": "--lite", "quiet": "--quiet"}


def render_tool_flags(tool: str, options: dict) -> list[str]:
    options = options or {}
    flags: list[str] = []
    if tool in ("dsync", "nsync"):
        for key, flag in _SYNC_BOOL_FLAGS.items():
            if options.get(key) is True:
                flags.append(flag)
        for key, flag in _SYNC_VALUE_FLAGS.items():
            if key in options:
                flags.extend([flag, str(options[key])])
    elif tool == "drm":
        for key, flag in _RM_BOOL_FLAGS.items():
            if options.get(key) is True:
                flags.append(flag)
    return flags


def tool_argv(spec, *, abs_paths: dict) -> list[str]:
    if spec.tool == "dscan":
        return ["--directory", abs_paths["target"],
                "--output", "$DMS_SCAN_REPORT", "--print"]
    flags = render_tool_flags(spec.tool, spec.options)
    dry = ["--dryrun"] if spec.dryrun else []
    if spec.tool in ("dsync", "nsync"):
        return [*flags, *dry, abs_paths["source"], abs_paths["destination"]]
    # drm
    return [*flags, *dry, abs_paths["target"]]
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_execution_manifests.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/execution_manifests.py tests/test_execution_manifests.py
git commit -m "feat: mpifileutils 도구 플래그/argv 렌더 (순수)"
```

---

### Task 4: Volcano Job 매니페스트 — scan/dsync/drm (`execution_manifests.py` 2/3)

**Files:**
- Modify: `src/dms/execution_manifests.py`
- Test: `tests/test_execution_manifests.py`

**Interfaces:**
- Consumes: Task 3.
- Produces:
  - `build_volcano_job(spec: JobSpec, *, job_image: str, namespace: str, volumes: list[dict]) -> dict` — `batch.volcano.sh/v1alpha1 Job` 매니페스트 dict (primary 후보용: scan/dsync/drm). 구성:
    - metadata.name = `dms-{operation}-{phase}-{job_id[:12]}`, namespace, labels `{"dms.io/job-id": job_id, "dms.io/phase": phase, "dms.io/tool": tool}`.
    - spec.schedulerName="volcano", spec.queue=spec.queue, spec.minAvailable = worker_count + 1, spec.plugins={"ssh": [], "svc": []}, spec.priorityClassName=spec.priority_class.
    - spec.policies=[{"event":"TaskCompleted","action":"CompleteJob"},{"event":"PodFailed","action":"AbortJob"}].
    - tasks: `launcher`(replicas 1) + `worker`(replicas = worker_count = min(len(candidates["primary"]), ceil(process_count/ppn))... 간단히 len(candidates["primary"])). launcher는 job-runner 실행(command `["/usr/local/bin/dms-job-runner"]`, env 주입), worker는 sshd(command sshd). 둘 다 image=job_image, volumes 마운트(mountPropagation "HostToContainer"), securityContext runAsUser 0.
    - nodeAffinity: 후보 노드 hostname in candidates["primary"]. worker task는 podAntiAffinity(노드당 1 worker).
  - env는 dms-job-runner가 읽을 값: `DMS_JR_TOOL`, `DMS_JR_OPERATION`, `DMS_JR_DRYRUN`, `DMS_JR_PROCESS_COUNT`, `DMS_JR_ARGV`(json), `DMS_JR_UID`, `DMS_JR_GID`, `DMS_JR_USERNAME`, `DMS_JR_ARTIFACT_DIR`, `DMS_JR_PHASE`. (argv는 tool_argv 결과 json.)
  - `_worker_count(spec) -> int` 헬퍼: primary 후보 수(≥1).
- **매니페스트 정확성의 최종 판정은 실증**이다 — 단위 테스트는 dict 구조(필수 키·라벨·task 이름·plugins·minAvailable·env)만 검증.

- [ ] **Step 1: 실패 테스트 (추가)**

```python
from dms.execution_manifests import build_volcano_job

_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]


def test_build_volcano_job_scan_structure():
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 process_count=8)
    m = build_volcano_job(spec, job_image="reg/img:1", namespace="dms", volumes=_VOL)
    assert m["apiVersion"] == "batch.volcano.sh/v1alpha1" and m["kind"] == "Job"
    assert m["metadata"]["namespace"] == "dms"
    assert m["metadata"]["labels"]["dms.io/job-id"] == "j1"
    assert m["spec"]["schedulerName"] == "volcano"
    assert m["spec"]["queue"] == "dms-data"
    assert m["spec"]["priorityClassName"] == "dms-mid"
    assert m["spec"]["plugins"] == {"ssh": [], "svc": []}
    names = [t["name"] for t in m["spec"]["tasks"]]
    assert "launcher" in names and "worker" in names
    launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
    assert launcher["replicas"] == 1
    worker = next(t for t in m["spec"]["tasks"] if t["name"] == "worker")
    assert worker["replicas"] == 1  # 후보 1개
    assert m["spec"]["minAvailable"] == 2  # worker 1 + launcher 1
    # launcher env에 도구/argv 전달
    env = {e["name"]: e["value"]
           for e in launcher["template"]["spec"]["containers"][0]["env"]}
    assert env["DMS_JR_TOOL"] == "dscan"
    assert env["DMS_JR_UID"] == "10001"
    import json
    assert json.loads(env["DMS_JR_ARGV"])[0] == "--directory"


def test_worker_replicas_follow_candidates():
    spec = _spec(operation="sync", tool="dsync",
                 candidates={"primary": ["dms-w1", "dms-w2", "dms-w3"]},
                 paths={"source": "s", "source_storage": "src",
                        "destination": "d", "destination_storage": "dst"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    worker = next(t for t in m["spec"]["tasks"] if t["name"] == "worker")
    assert worker["replicas"] == 3 and m["spec"]["minAvailable"] == 4
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_execution_manifests.py -v`
Expected: 새 테스트 FAIL — ImportError

- [ ] **Step 3: 구현 (execution_manifests.py에 추가)**

```python
import json


def _worker_count(spec) -> int:
    return max(1, len(spec.candidates.get("primary", [])))


def _abs_paths(spec):
    # paths는 상대. 절대경로는 stepper가 spec.paths에 이미 절대로 넣어준다(Task 7).
    # 여기선 spec.paths를 그대로 절대로 취급.
    return spec.paths


def _container(name, image, command, env, volumes):
    return {
        "name": name, "image": image, "command": command,
        "securityContext": {"runAsUser": 0},
        "env": [{"name": k, "value": v} for k, v in env.items()],
        "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                          "mountPropagation": "HostToContainer"} for v in volumes],
    }


def _pod_volumes(volumes):
    return [{"name": v["name"], "hostPath": {"path": v["hostPath"]["path"],
                                             "type": "Directory"}} for v in volumes]


def _artifact_dir(spec):
    # artifact_base는 URI(file:///cephfs/...) — 파드 안 파일 연산용으로 스킴 제거.
    base = spec.artifact_base.replace("file://", "")
    return f"{base}/{spec.job_id}/{spec.phase}"


def _launcher_env(spec):
    ap = _abs_paths(spec)
    argv = tool_argv(spec, abs_paths=ap)
    ident = spec.identity or {}
    return {
        "DMS_JR_TOOL": spec.tool, "DMS_JR_OPERATION": spec.operation,
        "DMS_JR_PHASE": spec.phase, "DMS_JR_DRYRUN": "1" if spec.dryrun else "0",
        "DMS_JR_PROCESS_COUNT": str(spec.process_count),
        "DMS_JR_ARGV": json.dumps(argv),
        "DMS_JR_UID": str(ident.get("uid", 0)), "DMS_JR_GID": str(ident.get("gid", 0)),
        "DMS_JR_USERNAME": ident.get("username", "root"),
        "DMS_JR_ARTIFACT_DIR": _artifact_dir(spec),  # 스킴 제거된 파일시스템 경로
    }


def _job_name(spec):
    return f"dms-{spec.operation}-{spec.phase}-{spec.job_id[:12]}"


def _node_affinity(nodes):
    return {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
        "nodeSelectorTerms": [{"matchExpressions": [
            {"key": "kubernetes.io/hostname", "operator": "In", "values": nodes}]}]}}}


def build_volcano_job(spec, *, job_image, namespace, volumes):
    workers = _worker_count(spec)
    nodes = spec.candidates.get("primary", [])
    launcher = {
        "name": "launcher", "replicas": 1,
        "template": {"spec": {
            "restartPolicy": "Never",
            "affinity": _node_affinity(nodes) if nodes else {},
            "containers": [_container("launcher", job_image,
                ["/usr/local/bin/dms-job-runner"], _launcher_env(spec), volumes)],
            "volumes": _pod_volumes(volumes)}}}
    worker = {
        "name": "worker", "replicas": workers,
        "template": {"spec": {
            "restartPolicy": "Never",
            "affinity": _node_affinity(nodes) if nodes else {},
            "containers": [_container("worker", job_image,
                ["/usr/sbin/sshd", "-D"], {}, volumes)],
            "volumes": _pod_volumes(volumes)}}}
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": {"schedulerName": "volcano", "queue": spec.queue,
                 "minAvailable": workers + 1, "priorityClassName": spec.priority_class,
                 "plugins": {"ssh": [], "svc": []},
                 "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                              {"event": "PodFailed", "action": "AbortJob"}],
                 "tasks": [launcher, worker]}}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_execution_manifests.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/execution_manifests.py tests/test_execution_manifests.py
git commit -m "feat: Volcano Job 매니페스트 빌더 (scan/dsync/drm, launcher+worker gang)"
```

---

### Task 5: nsync 3-task Job + preflight Pod 매니페스트 (`execution_manifests.py` 3/3)

**Files:**
- Modify: `src/dms/execution_manifests.py`
- Test: `tests/test_execution_manifests.py`

**Interfaces:**
- Consumes: Task 4.
- Produces:
  - `build_volcano_job`이 nsync(candidates에 "source"/"destination")를 처리하도록 확장: tasks `launcher`(1) + `source-worker`(len(source)) + `destination-worker`(len(destination)), minAvailable = S+D+1. source-worker는 source 노드에, destination-worker는 destination 노드에 nodeAffinity. launcher env에 role 힌트(`DMS_JR_SOURCE_NODES`, `DMS_JR_DEST_NODES` json). (primary가 있으면 기존 launcher+worker, 없고 source/destination이 있으면 nsync 3-task.)
  - `build_preflight_pod(spec: JobSpec, *, job_image: str, namespace: str, volumes: list[dict], node: str) -> dict` — 단일 Pod 매니페스트. `runAsUser`=identity uid, `runAsGroup`=identity gid로 도구 없이 sh 권한 검사 실행. command는 operation별 검사 sh(source 읽기/traverse, destination 부모 쓰기 등). 실패 시 종료코드/마커. node에 nodeSelector 고정. labels dms.io/phase=preflight. **간소화**: sh가 `test -r`/`test -w`/`test -x`로 검사하고 실패 사유를 stdout 마커(`DMS_PREFLIGHT_REASON=<code>`)로 출력.

- [ ] **Step 1: 실패 테스트 (추가)**

```python
from dms.execution_manifests import build_preflight_pod


def test_nsync_three_tasks():
    spec = _spec(operation="sync", tool="nsync",
                 candidates={"source": ["dms-w1", "dms-w2"],
                             "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    names = [t["name"] for t in m["spec"]["tasks"]]
    assert names == ["launcher", "source-worker", "destination-worker"]
    src = next(t for t in m["spec"]["tasks"] if t["name"] == "source-worker")
    dst = next(t for t in m["spec"]["tasks"] if t["name"] == "destination-worker")
    assert src["replicas"] == 2 and dst["replicas"] == 1
    assert m["spec"]["minAvailable"] == 4  # 2 + 1 + launcher


def test_preflight_pod_runs_as_identity():
    spec = _spec(operation="scan", tool="dscan", identity={"uid": 10001, "gid": 10000,
                 "username": "alice"}, paths={"target": "/cephfs/dms/a"})
    m = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=_VOL,
                            node="dms-w1")
    assert m["kind"] == "Pod"
    sc = m["spec"]["containers"][0]["securityContext"]
    assert sc["runAsUser"] == 10001 and sc["runAsGroup"] == 10000
    assert m["spec"]["nodeSelector"]["kubernetes.io/hostname"] == "dms-w1"
    assert m["metadata"]["labels"]["dms.io/phase"] == "preflight"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_execution_manifests.py -v`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현 (execution_manifests.py 수정)**

`build_volcano_job`에서 nsync 분기 추가 (primary 없고 source/destination 있을 때):
```python
def build_volcano_job(spec, *, job_image, namespace, volumes):
    if "primary" not in spec.candidates:
        return _build_nsync_job(spec, job_image=job_image, namespace=namespace,
                                volumes=volumes)
    # ...기존 launcher+worker 코드...


def _build_nsync_job(spec, *, job_image, namespace, volumes):
    src_nodes = spec.candidates["source"]
    dst_nodes = spec.candidates["destination"]
    env = _launcher_env(spec)
    env["DMS_JR_SOURCE_NODES"] = json.dumps(src_nodes)
    env["DMS_JR_DEST_NODES"] = json.dumps(dst_nodes)
    launcher = {"name": "launcher", "replicas": 1, "template": {"spec": {
        "restartPolicy": "Never",
        "containers": [_container("launcher", job_image,
            ["/usr/local/bin/dms-job-runner"], env, volumes)],
        "volumes": _pod_volumes(volumes)}}}
    src_worker = {"name": "source-worker", "replicas": len(src_nodes),
        "template": {"spec": {"restartPolicy": "Never",
            "affinity": _node_affinity(src_nodes),
            "containers": [_container("source-worker", job_image,
                ["/usr/sbin/sshd", "-D"], {}, volumes)],
            "volumes": _pod_volumes(volumes)}}}
    dst_worker = {"name": "destination-worker", "replicas": len(dst_nodes),
        "template": {"spec": {"restartPolicy": "Never",
            "affinity": _node_affinity(dst_nodes),
            "containers": [_container("destination-worker", job_image,
                ["/usr/sbin/sshd", "-D"], {}, volumes)],
            "volumes": _pod_volumes(volumes)}}}
    return {
        "apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
        "metadata": {"name": _job_name(spec), "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": spec.phase, "dms.io/tool": spec.tool}},
        "spec": {"schedulerName": "volcano", "queue": spec.queue,
                 "minAvailable": len(src_nodes) + len(dst_nodes) + 1,
                 "priorityClassName": spec.priority_class,
                 "plugins": {"ssh": [], "svc": []},
                 "policies": [{"event": "TaskCompleted", "action": "CompleteJob"},
                              {"event": "PodFailed", "action": "AbortJob"}],
                 "tasks": [launcher, src_worker, dst_worker]}}


def _preflight_script(spec):
    ap = _abs_paths(spec)
    if spec.operation == "sync":
        return ("set -e; "
                f'test -r "{ap["source"]}" || {{ echo DMS_PREFLIGHT_REASON=source_not_readable; exit 1; }}; '
                f'dest_parent=$(dirname "{ap["destination"]}"); '
                'test -w "$dest_parent" || { echo DMS_PREFLIGHT_REASON=destination_parent_not_writable; exit 1; }; '
                "echo DMS_PREFLIGHT_OK")
    if spec.operation == "rm":
        return ("set -e; "
                f'parent=$(dirname "{ap["target"]}"); '
                'test -w "$parent" || { echo DMS_PREFLIGHT_REASON=parent_not_writable; exit 1; }; '
                "echo DMS_PREFLIGHT_OK")
    return ("set -e; "
            f'test -r "{ap["target"]}" || {{ echo DMS_PREFLIGHT_REASON=target_not_readable; exit 1; }}; '
            "echo DMS_PREFLIGHT_OK")


def build_preflight_pod(spec, *, job_image, namespace, volumes, node):
    ident = spec.identity or {}
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": f"dms-preflight-{spec.job_id[:12]}-{node}"[:63],
                     "namespace": namespace,
                     "labels": {"dms.io/job-id": spec.job_id,
                                "dms.io/phase": "preflight"}},
        "spec": {"restartPolicy": "Never",
                 "nodeSelector": {"kubernetes.io/hostname": node},
                 "containers": [{
                     "name": "preflight", "image": job_image,
                     "command": ["sh", "-c", _preflight_script(spec)],
                     "securityContext": {"runAsUser": ident.get("uid", 0),
                                         "runAsGroup": ident.get("gid", 0)},
                     "volumeMounts": [{"name": v["name"], "mountPath": v["mountPath"],
                                       "mountPropagation": "HostToContainer"}
                                      for v in volumes]}],
                 "volumes": _pod_volumes(volumes)}}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_execution_manifests.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/execution_manifests.py tests/test_execution_manifests.py
git commit -m "feat: nsync 3-task Job + preflight Pod 매니페스트"
```

---

### Task 6: VolcanoExecutionAdapter (`execution_volcano.py`)

**Files:**
- Create: `src/dms/execution_volcano.py`
- Test: `tests/test_execution_volcano.py`

**Interfaces:**
- Consumes: `dms.execution`(ExecStatus, ExecutionError, JobSpec, ExecutionAdapter), `dms.execution_manifests`(build_volcano_job, build_preflight_pod).
- Produces:
  - `class K8sClient(Protocol)`: `create(manifest: dict) -> None`, `get(kind: str, name: str, namespace: str) -> dict | None`, `delete(kind: str, name: str, namespace: str) -> None`, `read_pod_log(name, namespace) -> str`. (실 구현 `KubernetesClient`는 실증 태스크에서 kubernetes 파이썬 클라이언트로 — 이 태스크는 Protocol + fake로 로직만.)
  - `VolcanoExecutionAdapter(k8s, *, job_image, namespace, storages_lookup, read_text)`:
    - `storages_lookup: Callable[[str], dict]` — storage_name → {mount_path, managed_root} (stepper가 이미 절대경로를 spec.paths에 넣지만, 어댑터는 hostPath 볼륨 구성에 mount_path 필요). read_text: 아티팩트 summary.json 읽기(주입).
    - `submit(spec) -> str`: preflight면 build_preflight_pod(대표 노드=첫 후보), 아니면 build_volcano_job. `k8s.create(manifest)`. ref = manifest metadata.name (kind 접두 포함: `"pod/<name>"` 또는 `"vcjob/<name>"`). create 예외 → `ExecutionError("submit_failed", ...)`.
    - `poll(ref) -> ExecStatus`: kind/name 파싱. pod면 `get("Pod",...)`의 status.phase 매핑(Pending→PENDING, Running→RUNNING, Succeeded→SUCCEEDED, Failed→FAILED), 없으면 FAILED. vcjob면 `get(...)`의 status.state.phase 매핑(Pending/Running→해당, Completed→SUCCEEDED, Failed/Aborted→FAILED, Terminating→RUNNING). 없으면 FAILED.
    - `read_summary(ref) -> dict | None`: ref가 vcjob면 job_id를 라벨/이름에서 못 얻으므로, 어댑터는 summary 경로를 알아야 한다 — **간소화**: submit 시 ref에 job_id·phase·artifact_base를 인코딩하지 말고, adapter가 `_summary_path`를 별도 dict(ref→path)로 기억. read_summary(ref)는 그 경로를 `read_text`로 읽어 json 파싱, 파일 없음/빈 → None.
    - `terminate(ref) -> None`: kind/name 파싱 후 `k8s.delete(...)`. 존재 안 하면 무시(멱등). delete 예외 → `ExecutionError("terminate_failed", ...)`.
  - hostPath 볼륨: spec에서 필요한 storage들(scan/rm: storage, sync: source+destination) + artifact base 마운트를 storages_lookup으로 구성.
- 단위 테스트는 fake K8sClient(create 기록, get 스크립트, delete 기록)와 fake read_text로.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_execution_volcano.py
import pytest
from dms.execution import ExecStatus, ExecutionError, JobSpec
from dms.execution_volcano import VolcanoExecutionAdapter


class _FakeK8s:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._objs = {}      # (kind, name) -> obj
        self.fail_create = False
        self.fail_delete = False

    def create(self, manifest):
        if self.fail_create:
            raise RuntimeError("boom")
        self.created.append(manifest)
        key = (manifest["kind"], manifest["metadata"]["name"])
        self._objs[key] = manifest

    def set_status(self, kind, name, status):
        self._objs.setdefault((kind, name), {"kind": kind})["status"] = status

    def get(self, kind, name, namespace):
        return self._objs.get((kind, name))

    def delete(self, kind, name, namespace):
        if self.fail_delete:
            raise RuntimeError("boom")
        self.deleted.append((kind, name))
        self._objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return ""


def _spec(phase="execution", op="scan", tool="dscan", cand=None, paths=None):
    return JobSpec(job_id="job123456789abc", phase=phase, operation=op, tool=tool,
                   dryrun=(phase == "preview"),
                   identity={"uid": 10001, "gid": 10000, "username": "alice"},
                   paths=paths or {"target": "/cephfs/dms/a", "storage": "cephfs-dms"},
                   options={}, candidates=cand or {"primary": ["dms-w1"]},
                   process_count=8, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")


def _adapter(k8s, summaries=None):
    summaries = summaries or {}
    return VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: summaries.get(path))


def test_submit_preflight_creates_pod():
    k8s = _FakeK8s()
    ref = _adapter(k8s).submit(_spec(phase="preflight"))
    assert ref.startswith("pod/")
    assert k8s.created[0]["kind"] == "Pod"


def test_submit_execution_creates_vcjob():
    k8s = _FakeK8s()
    ref = _adapter(k8s).submit(_spec(phase="execution"))
    assert ref.startswith("vcjob/")
    assert k8s.created[0]["kind"] == "Job"


def test_poll_pod_phase_mapping():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec(phase="preflight"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Pod", name, {"phase": "Running"})
    assert a.poll(ref) == ExecStatus.RUNNING
    k8s.set_status("Pod", name, {"phase": "Succeeded"})
    assert a.poll(ref) == ExecStatus.SUCCEEDED


def test_poll_vcjob_state_mapping():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec(phase="execution"))
    name = ref.split("/", 1)[1]
    k8s.set_status("Job", name, {"state": {"phase": "Completed"}})
    assert a.poll(ref) == ExecStatus.SUCCEEDED
    k8s.set_status("Job", name, {"state": {"phase": "Failed"}})
    assert a.poll(ref) == ExecStatus.FAILED


def test_poll_missing_is_failed():
    a = _adapter(_FakeK8s())
    assert a.poll("vcjob/nonexistent") == ExecStatus.FAILED


def test_read_summary_reads_artifact():
    k8s = _FakeK8s()
    spec = _spec(phase="execution")
    a = _adapter(k8s, summaries={
        "/cephfs/dms/artifacts/job123456789abc/execution/summary.json": '{"files": 3}'})
    ref = a.submit(spec)
    assert a.read_summary(ref) == {"files": 3}


def test_read_summary_missing_is_none():
    a = _adapter(_FakeK8s())
    ref = a.submit(_spec())
    assert a.read_summary(ref) is None


def test_terminate_idempotent_and_error():
    k8s = _FakeK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec(phase="execution"))
    a.terminate(ref)
    a.terminate(ref)  # 이미 삭제 — 멱등
    assert len(k8s.deleted) >= 1
    k8s.fail_delete = True
    ref2 = _adapter(k8s).submit(_spec(phase="execution", op="rm", tool="drm",
                                      paths={"target": "/cephfs/x", "storage": "cephfs-dms"}))
    with pytest.raises(ExecutionError):
        a.terminate(ref2)


def test_submit_failure_raises():
    k8s = _FakeK8s(); k8s.fail_create = True
    with pytest.raises(ExecutionError):
        _adapter(k8s).submit(_spec())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_execution_volcano.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/execution_volcano.py
"""Volcano/k8s 실행 어댑터. k8s 접근은 주입된 K8sClient 뒤, 아티팩트 읽기는 read_text 뒤."""
import json
from typing import Protocol

from .execution import ExecStatus, ExecutionError
from .execution_manifests import build_preflight_pod, build_volcano_job

_POD_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
              "Succeeded": ExecStatus.SUCCEEDED, "Failed": ExecStatus.FAILED,
              "Unknown": ExecStatus.FAILED}
_VCJOB_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
                "Completed": ExecStatus.SUCCEEDED, "Completing": ExecStatus.RUNNING,
                "Failed": ExecStatus.FAILED, "Aborted": ExecStatus.FAILED,
                "Aborting": ExecStatus.RUNNING, "Terminating": ExecStatus.RUNNING,
                "Restarting": ExecStatus.RUNNING}
_KIND = {"pod": "Pod", "vcjob": "Job"}


class K8sClient(Protocol):
    def create(self, manifest: dict) -> None: ...
    def get(self, kind: str, name: str, namespace: str) -> "dict | None": ...
    def delete(self, kind: str, name: str, namespace: str) -> None: ...
    def read_pod_log(self, name: str, namespace: str) -> str: ...


class VolcanoExecutionAdapter:
    def __init__(self, k8s, *, job_image, namespace, storages_lookup, read_text):
        self._k8s = k8s
        self._job_image = job_image
        self._namespace = namespace
        self._storages = storages_lookup
        self._read_text = read_text
        self._summary_paths = {}   # ref -> artifact summary.json path

    def _volumes(self, spec):
        names = set()
        if spec.operation == "sync":
            names.update([spec.paths.get("source_storage"),
                          spec.paths.get("destination_storage")])
        else:
            names.add(spec.paths.get("storage"))
        vols = []
        seen = set()
        for sname in names:
            if not sname:
                continue
            meta = self._storages(sname)
            mp = meta["mount_path"]
            if mp in seen:
                continue
            seen.add(mp)
            vols.append({"name": mp.strip("/").replace("/", "-") or "root",
                         "hostPath": {"path": mp}, "mountPath": mp})
        return vols

    def submit(self, spec) -> str:
        try:
            if spec.phase == "preflight":
                nodes = (spec.candidates.get("primary")
                         or spec.candidates.get("source") or ["dms-w1"])
                manifest = build_preflight_pod(
                    spec, job_image=self._job_image, namespace=self._namespace,
                    volumes=self._volumes(spec), node=nodes[0])
                prefix = "pod"
            else:
                manifest = build_volcano_job(
                    spec, job_image=self._job_image, namespace=self._namespace,
                    volumes=self._volumes(spec))
                prefix = "vcjob"
            self._k8s.create(manifest)
        except Exception as exc:
            raise ExecutionError("submit_failed", str(exc)[:200])
        name = manifest["metadata"]["name"]
        ref = f"{prefix}/{name}"
        base = spec.artifact_base
        self._summary_paths[ref] = (
            f"{base}/{spec.job_id}/{spec.phase}/summary.json".replace("file://", ""))
        return ref

    def poll(self, ref) -> ExecStatus:
        prefix, name = ref.split("/", 1)
        obj = self._k8s.get(_KIND[prefix], name, self._namespace)
        if obj is None:
            return ExecStatus.FAILED
        status = obj.get("status") or {}
        if prefix == "pod":
            return _POD_PHASE.get(status.get("phase"), ExecStatus.FAILED)
        phase = (status.get("state") or {}).get("phase")
        return _VCJOB_PHASE.get(phase, ExecStatus.FAILED)

    def read_summary(self, ref):
        path = self._summary_paths.get(ref)
        if path is None:
            return None
        text = self._read_text(path)
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    def terminate(self, ref) -> None:
        prefix, name = ref.split("/", 1)
        try:
            self._k8s.delete(_KIND[prefix], name, self._namespace)
        except Exception as exc:
            raise ExecutionError("terminate_failed", str(exc)[:200])
```

주의: `read_summary`의 경로는 `file://` 접두 제거 후 `read_text`에 넘긴다. 테스트의 summaries dict 키가 `/cephfs/...`(접두 없음)와 맞아야 한다 — 위 코드가 `.replace("file://", "")`로 제거하므로 일치.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_execution_volcano.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/execution_volcano.py tests/test_execution_volcano.py
git commit -m "feat: VolcanoExecutionAdapter (제출/폴링/summary/종료, K8sClient 주입)"
```

---

### Task 7: Stepper JobSpec enrich + Executing preflight 재검증 (`stepper.py`)

**Files:**
- Modify: `src/dms/stepper.py`
- Test: `tests/test_stepper_enrich.py`

**Interfaces:**
- Consumes: 기존 JobStepper, `repos.storages.get`.
- Produces:
  - `_build_spec(job, phase, dryrun)` 수정: paths를 **절대경로**로. scan/rm: `{"target": managed_root + "/" + rel_target, "storage": storage_name}`; sync: `{"source": src_managed_root + "/" + src, "source_storage": ..., "destination": dst_managed_root + "/" + dst, "destination_storage": ...}`. managed_root는 `repos.storages.get(storage_name)["managed_root"]`. (스토리지 없으면 managed_root 없이 상대경로 fallback — planner 어드미션이 이미 거른 상태라 정상 경로에선 항상 존재.)
  - **Executing preflight 재검증** (Phase 3b 파킹 백로그): `_poll_or_submit_execution`에서 execution ref 없을 때(confirm 직후), execution을 바로 submit하지 말고 **먼저 재-preflight**를 한 번 더 돌린다 — 간소화: phase_refs에 `"exec_preflight"` ref가 없으면 preflight(dryrun=False, phase="preflight"의 재검증)를 submit하고 상태 Executing 유지(다음 스텝에서 그 ref를 poll). exec_preflight ref가 있으면 poll: Running→유지, Succeeded→execution submit, Failed→Rejected(execution_recheck_failed). execution ref가 이미 있으면 기존대로 poll.
    - **주의**: 이 재검증은 stub 어댑터에선 즉시 Succeeded라 scan/기존 테스트에 영향 없어야 한다. 기존 Phase 3b sync 테스트(`test_confirmed_job_executes`)가 깨지지 않도록 — 그 테스트는 stub이 즉시 Succeeded를 주므로, exec_preflight를 한 스텝 더 거치게 되면 run_once 횟수가 하나 늘어난다. **기존 테스트를 이 새 흐름에 맞게 갱신**하라(어서션 약화가 아니라 스텝 수 조정): confirm 후 run_once ①exec_preflight submit ②exec_preflight poll succeeded→execution submit ③execution poll succeeded→Succeeded.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_stepper_enrich.py
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///cephfs/dms/artifacts"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _scan_job(repos):
    repos.storages.create(storage_name="cephfs-dms", mount_path="/cephfs",
                          managed_root="/cephfs/dms", backend_type="cephfs",
                          actor="admin")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "cephfs-dms", "target": "team/data"},
        priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="cephfs-dms", target="team/data", options={}, tool="dscan",
        worker_pool={"identity": {"uid": 10001, "gid": 10000, "username": "alice",
            "groups": [], "privileged": False}, "candidates": {"primary": ["dms-w1"]},
            "process_count": 8, "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def test_build_spec_uses_absolute_paths(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_Settings()).run_once()  # preflight submit
    spec = adapter.submitted_specs()[0]
    assert spec.paths["target"] == "/cephfs/dms/team/data"  # managed_root + rel


def test_sync_recheck_preflight_before_execution(db):
    repos = Repositories(db)
    repos.storages.create(storage_name="src", mount_path="/cephfs-third",
        managed_root="/cephfs-third", backend_type="cephfs", actor="admin")
    repos.storages.create(storage_name="dst", mount_path="/cephfs-secondary",
        managed_root="/cephfs-secondary", backend_type="cephfs", actor="admin")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k2", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"identity": {"uid": 10001, "gid": 10000, "username": "alice",
            "groups": [], "privileged": False}, "candidates": {"primary": ["dms-w1"]},
            "process_count": 8, "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = JobStepper(repos, adapter, settings=_Settings())
    stepper.run_once()  # preflight
    stepper.run_once()  # preview submit
    stepper.run_once()  # preview succeeded → ConfirmPending
    fp = repos.data_jobs.get_job(jid)["preview_fingerprint"]
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()  # Executing: exec_preflight submit (재검증)
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
    assert [s for s in adapter.submitted_specs() if s.phase == "preflight"]  # 재검증 preflight
    stepper.run_once()  # exec_preflight succeeded → execution submit
    stepper.run_once()  # execution succeeded → Succeeded
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    # 절대경로 확인
    exec_spec = [s for s in adapter.submitted_specs() if s.phase == "execution"][0]
    assert exec_spec.paths["source"] == "/cephfs-third/a"
    assert exec_spec.paths["destination"] == "/cephfs-secondary/b"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_stepper_enrich.py -v`
Expected: FAIL

- [ ] **Step 3: 구현 (stepper.py 수정 + 기존 sync 테스트 갱신)**

`_build_spec`에서 paths를 절대경로로:
```python
    def _abs(self, storage_name, rel):
        storage = self._repos.storages.get(storage_name)
        if storage and storage.get("managed_root"):
            return f"{storage['managed_root']}/{rel}"
        return rel

    def _build_spec(self, job, phase, dryrun):
        wp = job["worker_pool"] or {}
        op = job["operation"]
        if op == "sync":
            paths = {"source": self._abs(job["source_storage"], job["source"]),
                     "source_storage": job["source_storage"],
                     "destination": self._abs(job["destination_storage"], job["destination"]),
                     "destination_storage": job["destination_storage"]}
        else:
            paths = {"target": self._abs(job["storage_name"], job["target"]),
                     "storage": job["storage_name"]}
        return JobSpec(job_id=job["job_id"], phase=phase, operation=op, tool=job["tool"],
            dryrun=dryrun, identity=wp.get("identity", {}), paths=paths,
            options=job["options"] or {}, candidates=wp.get("candidates", {}),
            process_count=wp.get("process_count", 1), queue=wp.get("queue", "dms-data"),
            priority_class=wp.get("priority_class", "dms-mid"),
            artifact_base=self._settings.artifact_base_uri)
```

`_poll_or_submit_execution` 교체(재검증 추가):
```python
    def _poll_or_submit_execution(self, job):
        refs = job["phase_refs"] or {}
        if "execution" in refs:
            return self._poll_execution(job)
        # confirm 후 execution 전 preflight 재검증
        if "exec_preflight" not in refs:
            try:
                ref = self._exec.submit(self._build_spec(job, "preflight", dryrun=False))
            except ExecutionError as exc:
                self._finalize(job, DataJobState.FAILED,
                               reason_code=f"execution_recheck_submit_failed:{exc.reason_code}")
                return "Failed"
            self._repos.data_jobs.set_phase_ref(job["job_id"], "exec_preflight", ref)
            return "Executing"
        status = self._exec.poll(refs["exec_preflight"])
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "Executing"
        if status == ExecStatus.SUCCEEDED:
            return self._submit_execution(job, DataJobState.EXECUTING)
        self._finalize(job, DataJobState.REJECTED, reason_code="execution_recheck_failed")
        return "Rejected"
```

기존 `tests/test_stepper_sync.py::test_confirmed_job_executes`를 갱신: confirm 후 run_once 횟수를 3회(exec_preflight submit → poll succeeded+execution submit → execution poll succeeded)로 맞춘다. 어서션(최종 Succeeded, execution phase submit 존재)은 유지.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_stepper_enrich.py tests/test_stepper_sync.py tests/test_stepper_scan.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/stepper.py tests/test_stepper_enrich.py tests/test_stepper_sync.py
git commit -m "feat: stepper 절대경로 enrich + execution 전 preflight 재검증"
```

---

### Task 8: 고아 복구 스윕 (`requests.py`, `data_jobs.py`, `controller.py`)

**Files:**
- Modify: `src/dms/repositories/requests.py`, `src/dms/repositories/data_jobs.py`
- Modify: `src/dms/controller.py`
- Test: `tests/test_recover_orphans.py`

**Interfaces:**
- Consumes: 기존 저장소, `DataJobState`/`RequestState`.
- Produces (Phase 3b 파킹 해소):
  - `DataJobsRepository.terminal_jobs_with_live_request() -> list[dict]` — 잡이 터미널인데 그 request가 비터미널인 (job_id, request_id, state) 목록. SQL: data_jobs terminal ∩ requests non-terminal 조인.
  - `controller`의 orphan 복구를 job-stepper 루프 스텝에 포함: `_stepper_step`에서 preview 만료 스윕 다음에, 고아를 순회하며 `finalize_from_job(job_state, reason_code="orphan_recovery")`. finalize는 idempotent라 안전.
  - `job_max_attempts`는 이 태스크 범위 밖(별도 백로그로 남김 — 스텝퍼가 이미 fail-soft라 무한 재claim 위험은 stub에선 없음. live에서 필요 시 후속). **이 태스크는 orphan 복구만.**

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_recover_orphans.py
from dms.domain import DataJobState, RequestState
from dms.execution import StubExecutionAdapter
from dms.repositories import Repositories


class _Settings:
    agent_report_stale_seconds = 300
    reconcile_interval_seconds = 30
    retention_interval_seconds = 3600
    planner_interval_seconds = 10
    stepper_interval_seconds = 5
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    agent_report_retention_days = 30
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _orphan(repos):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s", target="a", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    # 잡만 터미널(크래시 흉내), 요청은 Planned로 남음
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    return rid, jid


def test_terminal_jobs_with_live_request(db):
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    orphans = repos.data_jobs.terminal_jobs_with_live_request()
    assert [(o["job_id"], o["request_id"]) for o in orphans] == [(jid, rid)]


def test_orphan_recovery_via_controller(db):
    from dms.controller import build_loops, run_all_once
    repos = Repositories(db)
    rid, jid = _orphan(repos)
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")  # job-stepper 스텝이 고아 복구
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert repos.data_jobs.terminal_jobs_with_live_request() == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_recover_orphans.py -v`
Expected: FAIL

- [ ] **Step 3: 구현**

`data_jobs.py`에 (터미널/비터미널 목록은 domain enum에서):
```python
    def terminal_jobs_with_live_request(self):
        from ..domain import TERMINAL_DATA_JOB_STATES, TERMINAL_REQUEST_STATES
        job_terminal = tuple(s.value for s in TERMINAL_DATA_JOB_STATES)
        req_terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        jt = ", ".join(f":jt{i}" for i in range(len(job_terminal)))
        rt = ", ".join(f":rt{i}" for i in range(len(req_terminal)))
        params = {f"jt{i}": v for i, v in enumerate(job_terminal)}
        params.update({f"rt{i}": v for i, v in enumerate(req_terminal)})
        return self._db.query(
            f"""SELECT d.job_id, d.request_id, d.state FROM data_jobs d
                JOIN requests r ON r.request_id = d.request_id
                WHERE d.state IN ({jt}) AND r.state NOT IN ({rt})""", params)
```
(`TERMINAL_REQUEST_STATES` import는 domain에 있음.)

`controller.py`의 `_stepper_step`에 orphan 복구 추가 (preview 만료 스윕 뒤):
```python
    def _stepper_step():
        JobStepper(repos, adapter, settings=settings).run_once()
        for job_id in repos.data_jobs.expire_previews(now_iso=utc_now_iso()):
            job = repos.data_jobs.get_job(job_id)
            repos.requests.finalize_from_job(job["request_id"],
                DataJobState.PREVIEW_EXPIRED, reason_code="preview_expired", actor="stepper")
        for orphan in repos.data_jobs.terminal_jobs_with_live_request():
            repos.requests.finalize_from_job(orphan["request_id"],
                DataJobState(orphan["state"]), reason_code="orphan_recovery",
                actor="stepper")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_recover_orphans.py tests/test_controller_stepper.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/repositories/data_jobs.py src/dms/controller.py tests/test_recover_orphans.py
git commit -m "feat: 고아 복구 스윕 (터미널 잡 + 비터미널 요청 → 요청 종결)"
```

---

### Task 9: dms-job-runner — 명령/파싱 (`dms_job_runner/commands.py`)

**Files:**
- Create: `src/dms_job_runner/__init__.py` (빈 파일)
- Create: `src/dms_job_runner/commands.py`
- Modify: `pyproject.toml` (packages에 dms_job_runner 포함, `dms-job-runner` 콘솔 스크립트)
- Test: `tests/test_job_runner_commands.py`

**Interfaces:**
- Consumes: 없음 (순수).
- Produces:
  - `passwd_line(username: str, uid: int, gid: int, home: str) -> str` — `/etc/passwd` 한 줄: `f"{username}:x:{uid}:{gid}:dms:{home}:/bin/sh"`.
  - `parse_hostfile(text: str) -> list[str]` — hostfile(호스트명 또는 `host slots=N` 줄)에서 호스트명만 순서대로. 빈 줄/주석 무시.
  - `mpirun_command(*, process_count: int, hostfile: str, username: str, rank_script: str) -> list[str]` — `["runuser","-u",username,"--","mpirun","--allow-run-as-root","--mca","pml","ob1","--mca","btl","tcp,self","-np",str(process_count),"--hostfile",hostfile,rank_script]` (실증에서 조정 가능한 최소 셋).
  - `nsync_role_map(source_hosts: list[str], dest_hosts: list[str], *, slots_per_host: int) -> str` — nsync `--role-map` 문자열: source 랭크는 `:src`, dest 랭크는 `:dst`. 예 `"0:src,1:src,2:dst"` (source 호스트×slots 먼저, 그 다음 dest).

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_job_runner_commands.py
from dms_job_runner.commands import (
    passwd_line, parse_hostfile, mpirun_command, nsync_role_map)


def test_passwd_line():
    assert passwd_line("alice", 10001, 10000, "/tmp/h") == \
        "alice:x:10001:10000:dms:/tmp/h:/bin/sh"


def test_parse_hostfile():
    text = "dms-w1 slots=8\n# comment\n\ndms-w2 slots=8\n"
    assert parse_hostfile(text) == ["dms-w1", "dms-w2"]


def test_mpirun_command():
    cmd = mpirun_command(process_count=16, hostfile="/tmp/hf", username="alice",
                         rank_script="/tmp/rank.sh")
    assert cmd[:3] == ["runuser", "-u", "alice"]
    assert "mpirun" in cmd and cmd[cmd.index("-np") + 1] == "16"
    assert cmd[-1] == "/tmp/rank.sh"
    assert cmd[cmd.index("--hostfile") + 1] == "/tmp/hf"


def test_nsync_role_map():
    rm = nsync_role_map(["s1", "s2"], ["d1"], slots_per_host=2)
    # source 2호스트×2slots = rank 0..3 = src, dest 1호스트×2slots = rank 4..5 = dst
    assert rm == "0:src,1:src,2:src,3:src,4:dst,5:dst"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_job_runner_commands.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

pyproject.toml: `[tool.setuptools.packages.find]`가 `where=["src"]`이므로 `dms_job_runner`가 자동 포함. `[project.scripts]`에 `dms-job-runner = "dms_job_runner.runner:main"` 추가(runner는 Task 10).

```python
# src/dms_job_runner/commands.py
"""잡 파드 안에서 도는 job-runner의 순수 헬퍼. 실제 실행은 runner.main (Task 10)."""


def passwd_line(username, uid, gid, home):
    return f"{username}:x:{uid}:{gid}:dms:{home}:/bin/sh"


def parse_hostfile(text):
    hosts = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hosts.append(line.split()[0])
    return hosts


def mpirun_command(*, process_count, hostfile, username, rank_script):
    return ["runuser", "-u", username, "--",
            "mpirun", "--allow-run-as-root",
            "--mca", "pml", "ob1", "--mca", "btl", "tcp,self",
            "-np", str(process_count), "--hostfile", hostfile, rank_script]


def nsync_role_map(source_hosts, dest_hosts, *, slots_per_host):
    ranks = []
    rank = 0
    for _ in source_hosts:
        for _ in range(slots_per_host):
            ranks.append(f"{rank}:src")
            rank += 1
    for _ in dest_hosts:
        for _ in range(slots_per_host):
            ranks.append(f"{rank}:dst")
            rank += 1
    return ",".join(ranks)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_job_runner_commands.py -v` 후 `.venv/bin/pip install -q -e ".[test]"` (엔트리포인트 재등록) 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/dms_job_runner/ tests/test_job_runner_commands.py
git commit -m "feat: dms-job-runner 순수 헬퍼 (passwd, hostfile, mpirun, nsync role-map)"
```

---

### Task 10: dms-job-runner — 오케스트레이션 (`dms_job_runner/runner.py`)

**Files:**
- Create: `src/dms_job_runner/runner.py`
- Test: `tests/test_job_runner_runner.py`

**Interfaces:**
- Consumes: Task 9의 commands.
- Produces:
  - `run_job(env: dict, *, run, write_text, read_text, sleep, wait_hostfile) -> int` — env(DMS_JR_* )를 읽어:
    1. identity 물질화: `write_text("/etc/passwd", append=passwd_line(...))` (append 시맨틱은 주입 함수가 처리; 테스트는 기록만 확인).
    2. hostfile 대기: `wait_hostfile()` → 호스트 목록(주입; 실제론 Volcano가 준 파일 폴링).
    3. rank script 작성: `write_text(rank_path, script)` — script는 `runuser -u <user> -- <tool> <argv...>` 형태(argv는 env DMS_JR_ARGV json). scan은 `$DMS_SCAN_REPORT`를 실제 경로로 치환.
    4. mpirun 실행: `run(mpirun_command(...))` → returncode. stdout/stderr는 artifact dir에 write_text.
    5. summary 작성: tool 출력에서 summary를 만들거나(scan은 dscan-report에서, sync/rm은 실행 결과에서) — **간소화**: run 결과의 stdout 마지막 줄이 JSON이면 그걸 summary.json으로, 아니면 `{"returncode": rc}`를 summary.json으로 write_text. 반환 rc.
  - `main()` — 실제 env(os.environ) + 실제 subprocess/파일/time으로 run_job 호출, sys.exit(rc). (실증에서 실행.)
  - 단위 테스트는 전부 주입으로 — 실제 mpirun/파일 없이 오케스트레이션 흐름(물질화→hostfile→rank script→mpirun→summary)만 검증.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_job_runner_runner.py
import json
from dms_job_runner.runner import run_job


class _Recorder:
    def __init__(self, rc=0, stdout=""):
        self.writes = {}       # path -> content (마지막)
        self.appends = []      # (path, content)
        self.ran = []          # command list
        self._rc = rc
        self._stdout = stdout

    def write_text(self, path, content, *, append=False):
        if append:
            self.appends.append((path, content))
        else:
            self.writes[path] = content

    def read_text(self, path):
        return ""

    def run(self, command):
        self.ran.append(command)
        class R:
            returncode = self._rc
            stdout = self._stdout
            stderr = ""
        return R()


def _env(**kw):
    base = {"DMS_JR_TOOL": "dscan", "DMS_JR_OPERATION": "scan", "DMS_JR_PHASE": "execution",
            "DMS_JR_DRYRUN": "0", "DMS_JR_PROCESS_COUNT": "8", "DMS_JR_UID": "10001",
            "DMS_JR_GID": "10000", "DMS_JR_USERNAME": "alice",
            "DMS_JR_ARTIFACT_DIR": "/cephfs/dms/artifacts/j1/execution",
            "DMS_JR_ARGV": json.dumps(["--directory", "/cephfs/dms/a",
                                       "--output", "$DMS_SCAN_REPORT", "--print"])}
    base.update(kw)
    return base


def test_run_job_materializes_identity_and_runs_mpirun():
    rec = _Recorder(rc=0, stdout='{"files": 5}')
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"))
    assert rc == 0
    # identity 물질화(append)
    assert any("alice:x:10001:10000" in c for _, c in rec.appends)
    # mpirun 실행됨
    assert any("mpirun" in cmd for cmd in rec.ran)
    # summary.json 기록
    summary_writes = [p for p in rec.writes if p.endswith("summary.json")]
    assert summary_writes
    assert json.loads(rec.writes[summary_writes[0]]) == {"files": 5}


def test_run_job_nonjson_stdout_writes_returncode_summary():
    rec = _Recorder(rc=3, stdout="some non-json output")
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"))
    assert rc == 3
    sp = [p for p in rec.writes if p.endswith("summary.json")][0]
    assert json.loads(rec.writes[sp]) == {"returncode": 3}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_job_runner_runner.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms_job_runner/runner.py
"""잡 파드 launcher에서 도는 오케스트레이션. 모든 I/O는 주입 — main()이 실제 구현을 넣는다."""
import json
import os
import sys

from .commands import mpirun_command, passwd_line


def run_job(env, *, run, write_text, read_text, sleep, wait_hostfile) -> int:
    username = env["DMS_JR_USERNAME"]
    uid = int(env["DMS_JR_UID"])
    gid = int(env["DMS_JR_GID"])
    home = f"/tmp/dms-home-{uid}"
    artifact_dir = env["DMS_JR_ARTIFACT_DIR"]
    process_count = int(env["DMS_JR_PROCESS_COUNT"])
    argv = json.loads(env["DMS_JR_ARGV"])

    # 1. identity 물질화
    write_text("/etc/passwd", passwd_line(username, uid, gid, home) + "\n", append=True)

    # 2. hostfile 대기
    hosts, hostfile = wait_hostfile()

    # 3. rank script — scan은 리포트 경로 치환
    report_path = f"{artifact_dir}/dscan-report.json"
    rendered = [report_path if a == "$DMS_SCAN_REPORT" else a for a in argv]
    tool = env["DMS_JR_TOOL"]
    rank_body = " ".join(_shquote(a) for a in [tool, *rendered])
    rank_path = f"{artifact_dir}/rank.sh"
    write_text(rank_path, f"#!/bin/sh\nexec {rank_body}\n")

    # 4. mpirun
    proc = run(mpirun_command(process_count=process_count, hostfile=hostfile,
                              username=username, rank_script=rank_path))
    write_text(f"{artifact_dir}/stdout.log", proc.stdout or "")
    write_text(f"{artifact_dir}/stderr.log", proc.stderr or "")

    # 5. summary
    summary = _summary_from_stdout(proc.stdout, proc.returncode)
    write_text(f"{artifact_dir}/summary.json", json.dumps(summary))
    return proc.returncode


def _summary_from_stdout(stdout, returncode):
    last = (stdout or "").strip().splitlines()
    if last:
        try:
            return json.loads(last[-1])
        except (ValueError, TypeError):
            pass
    return {"returncode": returncode}


def _shquote(s):
    import shlex
    return shlex.quote(str(s))


def main():  # pragma: no cover - 실증에서 실행
    import subprocess
    import time

    def run(command):
        return subprocess.run(command, capture_output=True, text=True)

    def write_text(path, content, *, append=False):
        os.makedirs(os.path.dirname(path), exist_ok=True) if "/" in path[1:] else None
        with open(path, "a" if append else "w") as f:
            f.write(content)

    def read_text(path):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return ""

    def wait_hostfile():
        # Volcano ssh plugin이 /etc/volcano/<task>.host 또는 VC_*_HOSTS 제공
        hostfile = os.environ.get("DMS_JR_HOSTFILE", "/etc/volcano/worker.host")
        for _ in range(60):
            if os.path.exists(hostfile):
                with open(hostfile) as f:
                    hosts = [ln.split()[0] for ln in f if ln.strip()]
                if hosts:
                    return hosts, hostfile
            time.sleep(1)
        return [], hostfile

    sys.exit(run_job(dict(os.environ), run=run, write_text=write_text,
                     read_text=read_text, sleep=time.sleep, wait_hostfile=wait_hostfile))
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_job_runner_runner.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms_job_runner/runner.py tests/test_job_runner_runner.py
git commit -m "feat: dms-job-runner 오케스트레이션 (identity 물질화→hostfile→rank→mpirun→summary)"
```

---

### Task 11: live 어댑터/리졸버 배선 (`cli.py`, `app.py`, K8s 실 클라이언트)

**Files:**
- Modify: `src/dms/execution_volcano.py` (실 `KubernetesClient` 추가)
- Modify: `src/dms/cli.py`, `src/dms/api/app.py`
- Test: `tests/test_wiring_phase3c.py`

**Interfaces:**
- Consumes: `build_ldap_resolver`(Task 1), `VolcanoExecutionAdapter`(Task 6), `Settings`.
- Produces:
  - `build_execution_adapter(settings, repos) -> ExecutionAdapter` — settings.execution_backend가 `"volcano"`면 `VolcanoExecutionAdapter(KubernetesClient(namespace), job_image=settings.job_image, namespace=settings.k8s_namespace, storages_lookup=lambda n: repos.storages.get(n), read_text=<파일읽기>)`, 아니면 `StubExecutionAdapter()`. (모듈 함수, cli/app 공용.)
  - `KubernetesClient` (execution_volcano.py) — kubernetes 파이썬 클라이언트로 `create/get/delete/read_pod_log` 구현. Volcano Job은 CustomObjectsApi(group `batch.volcano.sh`, version `v1alpha1`, plural `jobs`), Pod은 CoreV1Api. **lazy import kubernetes**, in-cluster config. 이 클래스는 단위 테스트 안 함(실증 대상) — `# pragma: no cover` 표시.
  - `cli.py`: controller/api 분기에서 `identity_resolver = build_ldap_resolver(settings)`, `execution_adapter = build_execution_adapter(settings, repos)`를 만들어 `build_loops`/`run_forever`/`create_app`에 전달. (api는 create_app이 app.state에 세팅 — create_app 시그니처는 유지하고, cli가 만든 걸 넘기도록 create_app에 옵셔널 인자 추가하거나, create_app 내부에서 build_*를 호출. **간소화**: create_app이 settings 기반으로 내부에서 build_execution_adapter/build_ldap_resolver 호출.)
  - `app.py`: `create_app`에서 `app.state.identity_resolver = build_ldap_resolver(settings)`, `app.state.execution_adapter = build_execution_adapter(settings, Repositories(db))`.
- 단위 테스트: settings.execution_backend="stub"이면 StubExecutionAdapter, ldap 미설정이면 resolver None. (volcano 경로는 실증.)

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_wiring_phase3c.py
from dms.config import Settings
from dms.execution import StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter
from dms.repositories import Repositories
from dms.wiring import build_execution_adapter, build_identity_resolver

BASE = {"DMS_DATABASE_URL": "sqlite:///tmp/x.db", "DMS_SHARED_TOKEN": "t",
        "DMS_ADMIN_TOKEN": "a", "DMS_SESSION_SECRET": "s"}


def test_stub_backend_default(db):
    settings = Settings.from_env(BASE)
    adapter = build_execution_adapter(settings, Repositories(db))
    assert isinstance(adapter, StubExecutionAdapter)
    assert build_identity_resolver(settings) is None


def test_volcano_backend_builds_adapter(db):
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    adapter = build_execution_adapter(settings, Repositories(db))
    assert isinstance(adapter, VolcanoExecutionAdapter)


def test_ldap_resolver_built_when_configured(db):
    settings = Settings.from_env({**BASE, "DMS_LDAP_URI": "ldap://x:389",
        "DMS_LDAP_USER_BASE": "ou=People", "DMS_LDAP_GROUP_BASE": "ou=Groups"})
    r = build_identity_resolver(settings)
    assert r is not None and hasattr(r, "resolve")
```

주의: 순환 import 회피를 위해 build_* 함수를 `src/dms/wiring.py` 새 모듈에 둔다(execution_volcano/identity_ldap을 import). app.py/cli.py는 wiring에서 가져온다.

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_wiring_phase3c.py -v`
Expected: FAIL — ModuleNotFoundError(dms.wiring)

- [ ] **Step 3: 구현**

`src/dms/wiring.py`:
```python
"""설정 기반 live 어댑터/리졸버 선택. cli/app 공용."""
from .execution import StubExecutionAdapter
from .identity_ldap import build_ldap_resolver


def build_identity_resolver(settings):
    return build_ldap_resolver(settings)


def build_execution_adapter(settings, repos):
    if settings.execution_backend != "volcano":
        return StubExecutionAdapter()
    from .execution_volcano import KubernetesClient, VolcanoExecutionAdapter

    def read_text(path):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return None

    return VolcanoExecutionAdapter(
        KubernetesClient(settings.k8s_namespace),
        job_image=settings.job_image, namespace=settings.k8s_namespace,
        storages_lookup=lambda n: repos.storages.get(n), read_text=read_text)
```

`execution_volcano.py`에 `KubernetesClient` 추가 (`# pragma: no cover`):
**중요**: `KubernetesClient.__init__`은 in-cluster config를 로드하면 안 된다(단위 테스트 `test_volcano_backend_builds_adapter`가 클러스터 밖에서 어댑터를 생성함). config·API 클라이언트 생성을 **lazy**로(첫 메서드 호출 시 1회) 미룬다.

```python
class KubernetesClient:  # pragma: no cover - 실증 대상
    _VC = {"group": "batch.volcano.sh", "version": "v1alpha1", "plural": "jobs"}

    def __init__(self, namespace):
        self._namespace = namespace
        self._core = None
        self._custom = None

    def _ensure(self):
        if self._core is None:
            import kubernetes
            kubernetes.config.load_incluster_config()
            self._core = kubernetes.client.CoreV1Api()
            self._custom = kubernetes.client.CustomObjectsApi()

    def create(self, manifest):
        self._ensure()
        if manifest["kind"] == "Pod":
            self._core.create_namespaced_pod(self._namespace, manifest)
        else:
            self._custom.create_namespaced_custom_object(
                self._VC["group"], self._VC["version"], self._namespace,
                self._VC["plural"], manifest)

    def get(self, kind, name, namespace):
        self._ensure()
        import kubernetes
        try:
            if kind == "Pod":
                obj = self._core.read_namespaced_pod_status(name, namespace)
                return obj.to_dict()
            return self._custom.get_namespaced_custom_object(
                self._VC["group"], self._VC["version"], namespace,
                self._VC["plural"], name)
        except kubernetes.client.ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def delete(self, kind, name, namespace):
        self._ensure()
        import kubernetes
        try:
            if kind == "Pod":
                self._core.delete_namespaced_pod(name, namespace)
            else:
                self._custom.delete_namespaced_custom_object(
                    self._VC["group"], self._VC["version"], namespace,
                    self._VC["plural"], name)
        except kubernetes.client.ApiException as exc:
            if exc.status != 404:
                raise

    def read_pod_log(self, name, namespace):
        self._ensure()
        return self._core.read_namespaced_pod_log(name, namespace)
```

`pyproject.toml`: optional dep `kubernetes = ["kubernetes>=30"]` (또는 `[kubernetes]` extra에 이미 있으면 재사용).

`app.py`의 create_app: `app.state.identity_resolver = build_identity_resolver(settings)`, `app.state.execution_adapter = build_execution_adapter(settings, app.state.repos)` (기존 `= None`/`= StubExecutionAdapter()` 대체). import는 `from ..wiring import ...`.

`cli.py`의 controller 분기: `resolver = build_identity_resolver(settings)`, `adapter = build_execution_adapter(settings, repos)`, `run_forever(settings, repos, holder, identity_resolver=resolver, execution_adapter=adapter)`. import 추가.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_wiring_phase3c.py -v` 후 `.venv/bin/pytest -q` (전체, 0 warnings)
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/wiring.py src/dms/execution_volcano.py src/dms/api/app.py src/dms/cli.py pyproject.toml tests/test_wiring_phase3c.py
git commit -m "feat: 설정 기반 live 어댑터/리졸버 배선 (volcano/ldap or stub)"
```

---

## Phase 3c(코드) 완료 기준

- `.venv/bin/pytest -q` 전체 통과 (서비스 없이, fake 주입, 0 warnings).
- stub 백엔드에선 기존 전 라이프사이클이 그대로 동작(무회귀). volcano/ldap 백엔드는 설정 시 실 어댑터가 선택되고, 매니페스트 빌더·LDAP 파싱·job-runner 오케스트레이션이 단위 테스트로 검증됨.
- 고아 복구 + execution 전 preflight 재검증(Phase 3b 파킹)이 닫힘.

## 이후: 테스트베드 실증 (별도 단계, 코드 머지 후)

코드가 main에 머지되면 실증을 진행한다 (이 플랜의 범위 밖, 인프라 작업):
1. **이미지 3종 빌드**: `dms-mpifileutils`(mpifileutils 포크 + openmpi + sshd + dms-job-runner), `dms`(api/controller), `dms-agent`(dms + mpifileutils 오버레이). 인터넷 되는 pkg-01(빌드 노드)에서 buildah로 빌드 → `pkg-01:5000` push.
2. **최소 배포**: 네임스페이스 `dms`(privileged PSA), migrate Job, `dms-api`/`dms-controller` Deployment, `dms-agent` DaemonSet, Secret/ConfigMap(`DMS_*` env: DB=PostgreSQL dmsdb, LDAP, execution_backend=volcano, job_image, artifact_base=file:///cephfs/dms/artifacts). 컨트롤/API/잡 파드에 cephfs hostPath 마운트.
3. **시드**: 스토리지 3종(cephfs-dms/third/secondary) 등록, 정책(scan/dsync/nsync/rm) 등록, LDAP alice/bob.
4. **실증 시나리오**: (a) scan 잡 제출 → 에이전트 fresh 리포트 → planner Planned → stepper preflight→execution → `/cephfs/dms/artifacts/<job>/execution/summary.json` 생성 → Succeeded. (b) sync(dsync) preview→confirm→execution. (c) nsync(cephfs-third→cephfs-secondary) role 분리 실행. (d) cancel. 각 단계 실패 시 사유 코드로 디버깅.
5. 실증에서 드러난 매니페스트/명령/타이밍 결함을 후속 커밋으로 수정(어댑터 코드는 이미 단위 테스트가 있으므로 회귀 안전).
