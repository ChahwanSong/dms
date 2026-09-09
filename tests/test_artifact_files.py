"""artifact_files -- API 와 컨트롤러가 공유하는 봉쇄 사슬(제어면 root 전환, 2026-09-09).

root 로 도는 프로세스에서 **실제로 의미 있는** 케이스만 건다: 파일 mode 가 아니라
소유자·nlink·심링크·FIFO·크기가 장벽이다. 컨트롤러 쪽은 wiring.build_summary_reader 가
만드는 실제 클로저(read_summary 가 쓰는 그것)를 대상으로 한다 -- 어댑터가 조립하는
<base>/<job_id>/<phase>/summary.json 경로를 그대로 준다.
"""
import os
import threading

import pytest

from dms.api.artifacts import (ArtifactError, list_artifacts, open_artifact_stream,
                               read_artifact)
from dms.artifact_files import (SUMMARY_MAX_BYTES, job_owner_uid, open_artifact_fd,
                                read_contained_text)
from dms.domain import RequestState
from dms.repositories import Repositories
from dms.wiring import build_summary_reader

JOB = "0" * 32
ME = os.getuid()


def _phase_dir(tmp_path, job=JOB, phase="execution"):
    d = tmp_path / job / phase
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job(repos, identity):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    wp = {"candidates": {"primary": ["n1"]}}
    if identity is not None:
        wp["identity"] = identity
    return repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool=wp, precondition={}, actor="planner")


# --- job_owner_uid: bool/None/str 은 uid 가 아니다 -----------------------------

@pytest.mark.parametrize("identity,expected", [
    ({"uid": 1001, "gid": 1, "username": "alice"}, 1001),
    ({"uid": 0, "gid": 0, "username": "root", "privileged": True}, 0),
    ({"uid": True}, None), ({"uid": "1001"}, None), ({}, None), (None, None),
])
def test_job_owner_uid_shapes(identity, expected):
    assert job_owner_uid({"worker_pool": {"identity": identity}}) == expected
    assert job_owner_uid(None) is None


# --- 소유자 검사: 남의 소유(rename 으로 들여온 파일)는 열지도, 목록에 뜨지도 않는다 --

def test_foreign_owner_is_not_found_and_not_listed(tmp_path, monkeypatch):
    d = _phase_dir(tmp_path)
    (d / "stdout.log").write_text("mine")
    (d / "leak.log").write_text("theirs")
    real_fstat, real_stat = os.fstat, os.DirEntry.stat

    class _Foreign:
        def __init__(self, st):
            self._st = st
        def __getattr__(self, name):
            return 4242 if name == "st_uid" else getattr(self._st, name)

    def fake_fstat(fd):
        st = real_fstat(fd)
        return _Foreign(st) if os.path.basename(
            os.path.realpath(f"/proc/self/fd/{fd}")) == "leak.log" else st

    monkeypatch.setattr(os, "fstat", fake_fstat)
    with pytest.raises(ArtifactError) as e:
        open_artifact_stream(str(tmp_path), JOB, "execution", "leak.log",
                             max_bytes=None, owner_uid=ME)
    assert e.value.reason_code == "artifact_not_found"
    # 자기 소유는 그대로 열린다(소유자 검사가 정상 파일을 막지 않는다)
    assert read_artifact(str(tmp_path), JOB, "execution", "stdout.log",
                         owner_uid=ME)["content"] == "mine"
    # 목록도 같은 판정(inode_allowed)을 쓴다 -- 여기서는 entry.stat 을 흉내 낸다
    def fake_entry_stat(self, *, follow_symlinks=True):
        st = real_stat(self, follow_symlinks=follow_symlinks)
        return _Foreign(st) if self.name == "leak.log" else st
    monkeypatch.setattr(os.DirEntry, "stat", fake_entry_stat)
    names = [e["name"] for e in list_artifacts(str(tmp_path), JOB, owner_uid=ME)["entries"]]
    assert names == ["stdout.log"]


def test_root_owned_files_are_allowed_for_any_requester(tmp_path, monkeypatch):
    # 러너가 chown 뒤 root 로 쓰는 stdout.log/stderr.log/summary.json 이 이 경우다.
    d = _phase_dir(tmp_path)
    (d / "stdout.log").write_text("root wrote this")
    real_fstat = os.fstat

    class _Root:
        def __init__(self, st):
            self._st = st
        def __getattr__(self, name):
            return 0 if name == "st_uid" else getattr(self._st, name)

    monkeypatch.setattr(os, "fstat", lambda fd: _Root(real_fstat(fd)))
    assert read_artifact(str(tmp_path), JOB, "execution", "stdout.log",
                         owner_uid=777777)["content"] == "root wrote this"


# --- 하드링크: realpath 봉쇄가 못 잡는 유일한 형태 -- nlink 로 거른다 ----------

def test_hardlinked_file_is_not_found_and_not_listed(tmp_path):
    d = _phase_dir(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET")
    try:
        os.link(outside, d / "leak.log")
    except OSError as exc:
        pytest.skip(f"hardlink not permitted here: {exc}")
    (d / "stdout.log").write_text("ok")
    with pytest.raises(ArtifactError) as e:
        open_artifact_fd(str(tmp_path), JOB, "execution", "leak.log", owner_uid=ME)
    assert e.value.reason_code == "artifact_not_found"
    names = [e["name"] for e in list_artifacts(str(tmp_path), JOB, owner_uid=ME)["entries"]]
    assert names == ["stdout.log"]


# --- 컨트롤러 summary 읽기: 실제 wiring 클로저 -------------------------------

def _reader(db, identity={"uid": ME, "gid": os.getgid(), "username": "alice"}):
    repos = Repositories(db)
    jid = _job(repos, identity)
    return build_summary_reader(repos), jid


def test_summary_reader_reads_regular_summary(db, tmp_path):
    read_text, jid = _reader(db)
    d = _phase_dir(tmp_path, jid)
    (d / "summary.json").write_text('{"returncode": 0, "files": 2, "bytes": 10}')
    assert read_text(f"{tmp_path}/{jid}/execution/summary.json") == \
        '{"returncode": 0, "files": 2, "bytes": 10}'


def test_summary_reader_does_not_follow_symlink(db, tmp_path):
    read_text, jid = _reader(db)
    d = _phase_dir(tmp_path, jid)
    victim = tmp_path / "victim.json"
    victim.write_text('{"secret": "TOP-SECRET"}')
    os.symlink(victim, d / "summary.json")
    assert read_text(f"{tmp_path}/{jid}/execution/summary.json") is None


def test_summary_reader_rejects_symlinked_phase_dir(db, tmp_path):
    read_text, jid = _reader(db)
    (tmp_path / jid).mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "summary.json").write_text('{"secret": "TOP-SECRET"}')
    os.symlink(outside, tmp_path / jid / "execution")
    assert read_text(f"{tmp_path}/{jid}/execution/summary.json") is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo 없음")
def test_summary_reader_returns_promptly_on_fifo(db, tmp_path):
    # 예전 open(path).read() 는 writer 가 없는 FIFO 에서 영원히 블록했다 --
    # 단일 스레드 컨트롤러 전체 정지. 이제 즉시 None 이어야 한다.
    read_text, jid = _reader(db)
    d = _phase_dir(tmp_path, jid)
    os.mkfifo(d / "summary.json")
    result = []
    t = threading.Thread(target=lambda: result.append(
        read_text(f"{tmp_path}/{jid}/execution/summary.json")), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "summary.json FIFO 에서 블록됐다"
    assert result == [None]


def test_summary_reader_caps_size_and_folds_bad_utf8(db, tmp_path):
    read_text, jid = _reader(db)
    d = _phase_dir(tmp_path, jid)
    (d / "summary.json").write_bytes(b"x" * (SUMMARY_MAX_BYTES + 1))
    assert read_text(f"{tmp_path}/{jid}/execution/summary.json") is None
    # 비-UTF-8 은 ValueError(UnicodeDecodeError) -- 예전 wiring 은 OSError 만 잡아
    # stepper 를 매 틱 step_error 로 멈췄다. None 으로 접혀야 한다.
    (d / "summary.json").write_bytes(b"\xff\xfe{}")
    assert read_text(f"{tmp_path}/{jid}/execution/summary.json") is None


@pytest.mark.skipif(os.getuid() == 0, reason="소유자 게이트 검증엔 비root 테스트 uid 가 필요하다")
def test_summary_reader_without_identity_allows_only_root_owned(db, tmp_path, monkeypatch):
    # 요청자 uid 를 모르는 잡(변조 행) -> 러너가 root 로 쓴 summary 만 허용.
    read_text, jid = _reader(db, identity=None)
    d = _phase_dir(tmp_path, jid)
    (d / "summary.json").write_text("{}")
    assert read_text(f"{tmp_path}/{jid}/execution/summary.json") is None  # 테스트 uid 소유
    real_fstat = os.fstat

    class _Root:
        def __init__(self, st):
            self._st = st
        def __getattr__(self, name):
            return 0 if name == "st_uid" else getattr(self._st, name)

    monkeypatch.setattr(os, "fstat", lambda fd: _Root(real_fstat(fd)))
    assert read_text(f"{tmp_path}/{jid}/execution/summary.json") == "{}"


def test_summary_reader_rejects_paths_outside_the_artifact_shape(db, tmp_path):
    read_text, _ = _reader(db)
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "passwd").write_text("root:x:0:0")
    # job_id 자리(etc)가 32hex 가 아니고 phase 자리도 화이트리스트 밖 -- 사슬이 거른다
    assert read_text(f"{tmp_path}/etc/passwd") is None
    assert read_contained_text(str(tmp_path), "etc", "execution", "passwd",
                               max_bytes=1024, owner_uid=None) is None
