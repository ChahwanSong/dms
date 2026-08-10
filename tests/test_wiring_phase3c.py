from dms.build_runner import BuildRunner, StubBuildRunner
from dms.config import Settings
from dms.execution import StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter
from dms.queue_reader import StubQueueReader, VolcanoQueueReader
from dms.repositories import Repositories
from dms.rollout_runner import RolloutRunner, StubRolloutRunner
from dms.wiring import (build_build_runner, build_execution_adapter,
                        build_identity_resolver, build_queue_reader,
                        build_rollout_runner)

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


def test_build_runner_is_stub_when_backend_is_not_volcano():
    settings = Settings.from_env(BASE)
    assert isinstance(build_build_runner(settings), StubBuildRunner)


def test_build_runner_reads_timeout_from_settings():
    # C2(a): wiring이 settings.build_timeout_seconds를 BuildRunner에 실제로
    # 전달하는지 -- 빠지면 파드에 activeDeadlineSeconds가 안 실린다.
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1",
                                  "DMS_BUILD_TIMEOUT_SECONDS": "111"})
    runner = build_build_runner(settings)
    assert isinstance(runner, BuildRunner)
    assert runner._timeout_seconds == 111


def test_rollout_runner_is_stub_when_backend_is_not_volcano():
    settings = Settings.from_env(BASE)
    assert isinstance(build_rollout_runner(settings), StubRolloutRunner)


def test_rollout_runner_builds_runner_when_volcano():
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    runner = build_rollout_runner(settings)
    assert isinstance(runner, RolloutRunner)
    assert runner._ns == settings.k8s_namespace


def test_queue_reader_is_stub_when_backend_is_not_volcano():
    # 기본 백엔드(stub)에서 스텁 페어가 안 꽂히면 conftest 의 create_app 경로
    # 전부가 /api/admin/metrics/queue 에서 500 이다(설계 §2.5).
    settings = Settings.from_env(BASE)
    assert isinstance(build_queue_reader(settings), StubQueueReader)


def test_queue_reader_builds_volcano_reader_when_volcano():
    settings = Settings.from_env({**BASE, "DMS_EXECUTION_BACKEND": "volcano",
                                  "DMS_JOB_IMAGE": "reg/img:1"})
    reader = build_queue_reader(settings)
    assert isinstance(reader, VolcanoQueueReader)
    assert reader._namespace == settings.k8s_namespace
    assert reader._queue == "dms-data"
