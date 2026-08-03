import json
from dms_job_runner.runner import run_job


class _Recorder:
    def __init__(self, rc=0, stdout="", run_fn=None):
        self.writes = {}       # path -> content (마지막)
        self.appends = []      # (path, content)
        self.ran = []          # command list
        self.made_exec = []    # paths made executable
        self._rc = rc
        self._stdout = stdout
        self._run_fn = run_fn  # optional command -> R override

    def write_text(self, path, content, *, append=False):
        if append:
            self.appends.append((path, content))
        else:
            self.writes[path] = content

    def read_text(self, path):
        return ""

    def run(self, command):
        self.ran.append(command)
        if self._run_fn is not None:
            return self._run_fn(command)
        class R:
            returncode = self._rc
            stdout = self._stdout
            stderr = ""
        return R()

    def make_executable(self, path):
        self.made_exec.append(path)


def _env(**kw):
    base = {"DMS_JR_TOOL": "dscan", "DMS_JR_OPERATION": "scan", "DMS_JR_PHASE": "execution",
            "DMS_JR_DRYRUN": "0", "DMS_JR_PROCESS_COUNT": "8", "DMS_JR_UID": "10001",
            "DMS_JR_GID": "10000", "DMS_JR_USERNAME": "alice",
            "DMS_JR_PROCESSES_PER_NODE": "8",
            "DMS_JR_ARTIFACT_DIR": "/cephfs/dms/artifacts/j1/execution",
            "DMS_JR_ARGV": json.dumps(["--directory", "/cephfs/dms/a",
                                       "--output", "$DMS_SCAN_REPORT", "--print"])}
    base.update(kw)
    return base


class _R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_job_materializes_identity_and_runs_mpirun():
    rec = _Recorder(rc=0, stdout='{"files": 5}')
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    # identity 물질화(append)
    assert any("alice:x:10001:10000" in c for _, c in rec.appends)
    # mpirun 실행됨
    assert any("mpirun" in cmd for cmd in rec.ran)
    # mpirun 전에 execution 디렉터리를 요청자 소유로 chown(도구가 report를 쓸 수 있게)
    assert ["chown", "-R", "10001:10000",
            "/cephfs/dms/artifacts/j1/execution"] in rec.ran
    chown_idx = rec.ran.index(["chown", "-R", "10001:10000",
                               "/cephfs/dms/artifacts/j1/execution"])
    mpirun_idx = next(i for i, c in enumerate(rec.ran) if "mpirun" in c)
    assert chown_idx < mpirun_idx
    # rank.sh가 executable로 표시됨
    assert any(p.endswith("rank.sh") for p in rec.made_exec)
    # rank.sh 본문에서 $DMS_SCAN_REPORT가 치환됨
    rank_path = "/cephfs/dms/artifacts/j1/execution/rank.sh"
    body = rec.writes[rank_path]
    assert "$DMS_SCAN_REPORT" not in body
    assert "dscan-report.json" in body
    assert body.startswith("#!/bin/sh")
    # summary.json 기록
    summary_writes = [p for p in rec.writes if p.endswith("summary.json")]
    assert summary_writes
    assert json.loads(rec.writes[summary_writes[0]]) == {"files": 5}


def test_run_job_nonjson_stdout_writes_returncode_summary():
    rec = _Recorder(rc=3, stdout="some non-json output")
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 3
    sp = [p for p in rec.writes if p.endswith("summary.json")][0]
    assert json.loads(rec.writes[sp]) == {"returncode": 3}


def test_rank_script_quotes_argv():
    """Verify that rank.sh properly quotes arguments with special characters."""
    env = _env(**{"DMS_JR_ARGV": json.dumps(
        ["--directory", "/cephfs/a b$(x)", "--output", "$DMS_SCAN_REPORT", "--print"]
    )})
    rec = _Recorder(rc=0, stdout='{"files": 1}')
    rc = run_job(env, run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    rank_path = "/cephfs/dms/artifacts/j1/execution/rank.sh"
    body = rec.writes[rank_path]
    # The special chars should be quoted, not executed
    assert "$(x)" not in body or "'" in body  # either not there, or quoted
    # The space in the path should be quoted
    assert "/cephfs/a b" not in body or "'" in body or '"' in body


def test_run_job_copies_ssh_keys_to_requester_home_before_mpirun():
    rec = _Recorder(rc=0, stdout='{"files": 5}')
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    key_copy_calls = [cmd for cmd in rec.ran
                      if cmd[:2] == ["sh", "-c"] and "/tmp/dms-home-10001" in cmd]
    assert key_copy_calls, "ssh key copy command not issued"
    key_copy_idx = rec.ran.index(key_copy_calls[0])
    mpirun_idx = next(i for i, cmd in enumerate(rec.ran) if "runuser" in cmd)
    assert key_copy_idx < mpirun_idx  # 키 복사가 mpirun보다 먼저


def test_run_job_resolves_hosts_and_writes_slotted_hostfile():
    def run_fn(cmd):
        if cmd[0] == "getent":
            return _R(returncode=0, stdout="10.0.0.5   dms-w1\n")
        return _R(returncode=0, stdout='{"files": 1}')
    rec = _Recorder(run_fn=run_fn)
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    hostfile_writes = [c for p, c in rec.writes.items()
                       if p != "/cephfs/dms/artifacts/j1/execution/rank.sh"
                       and "slots=" in c]
    assert hostfile_writes
    assert "10.0.0.5 slots=8" in hostfile_writes[0]
    # mpirun이 참조하는 --hostfile은 원본이 아니라 새로 만든 슬롯 첨부 hostfile
    mpirun_cmd = next(cmd for cmd in rec.ran if "runuser" in cmd)
    hostfile_arg = mpirun_cmd[mpirun_cmd.index("--hostfile") + 1]
    assert hostfile_arg != "/tmp/hostfile"


def test_run_job_ssh_readiness_barrier_probes_before_mpirun():
    rec = _Recorder(rc=0, stdout='{"files": 1}')
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    ssh_probe_calls = [i for i, cmd in enumerate(rec.ran) if cmd[0] == "ssh"]
    assert ssh_probe_calls, "no ssh readiness probe issued"
    mpirun_idx = next(i for i, cmd in enumerate(rec.ran) if "runuser" in cmd)
    assert all(i < mpirun_idx for i in ssh_probe_calls)


def test_run_job_ssh_barrier_gives_up_after_bounded_attempts():
    # ssh probe는 절대 성공하지 않음 -> barrier가 job을 막지 않고 결국 mpirun까지 진행
    slept = []

    def run_fn(cmd):
        if cmd[0] == "ssh":
            return _R(returncode=1)
        if cmd[0] == "getent":
            return _R(returncode=1, stdout="")
        return _R(returncode=0, stdout='{"files": 1}')
    rec = _Recorder(run_fn=run_fn)
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: slept.append(s),
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0  # barrier 포기 후에도 mpirun은 실행됨 (legacy: proceeding)
    assert any("runuser" in cmd for cmd in rec.ran)
    assert slept  # bounded 재시도 동안 sleep이 호출됨


def _nsync_env(**kw):
    base = _env(
        DMS_JR_TOOL="nsync", DMS_JR_OPERATION="sync",
        DMS_JR_PROCESSES_PER_NODE="2",
        DMS_JR_SOURCE_NODES=json.dumps(["dms-w1", "dms-w2"]),
        DMS_JR_DEST_NODES=json.dumps(["dms-w4"]),
        DMS_JR_ARGV=json.dumps(["/cephfs-third/a", "/cephfs-secondary/b"]))
    base.update(kw)
    return base


def _nsync_wait_hostfile(calls):
    def wait_hostfile(role=None):
        calls.append(role)
        if role == "source":
            return ["dms-w1", "dms-w2"], "/tmp/source.host"
        if role == "destination":
            return ["dms-w4"], "/tmp/dest.host"
        raise AssertionError(f"unexpected role: {role!r}")
    return wait_hostfile


def test_run_job_nsync_waits_for_source_and_destination_hostfiles():
    calls = []
    rec = _Recorder(rc=0, stdout='{"files": 1}')
    rc = run_job(_nsync_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=_nsync_wait_hostfile(calls),
                 make_executable=rec.make_executable)
    assert rc == 0
    assert "source" in calls and "destination" in calls
    # source가 destination보다 먼저 (rank 순서 = source 먼저 -> role_map과 일치해야 함)
    assert calls.index("source") < calls.index("destination")


def test_run_job_nsync_computes_role_map_and_inserts_role_map_args():
    rec = _Recorder(rc=0, stdout='{"files": 1}')
    rc = run_job(_nsync_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=_nsync_wait_hostfile([]),
                 make_executable=rec.make_executable)
    assert rc == 0
    rank_path = "/cephfs/dms/artifacts/j1/execution/rank.sh"
    body = rec.writes[rank_path]
    assert body.startswith("#!/bin/sh\nexec nsync ")
    assert "--role-mode map" in body or "--role-mode' 'map'" in body
    # 2호스트*2슬롯=src rank 0..3, 1호스트*2슬롯=dst rank 4..5 (commands.nsync_role_map과 동일 계산)
    assert "0:src" in body and "4:dst" in body
    assert "/cephfs-third/a" in body and "/cephfs-secondary/b" in body


def test_run_job_mpirun_has_ompi_env_and_runuser_preserve_environment():
    rec = _Recorder(rc=0, stdout='{"files": 1}')
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    mpirun_cmd = next(cmd for cmd in rec.ran if "runuser" in cmd)
    assert "OMPI_ALLOW_RUN_AS_ROOT=1" in mpirun_cmd
    assert any(c.startswith("OMPI_MCA_plm_rsh_agent=") for c in mpirun_cmd)
    assert "--preserve-environment" in mpirun_cmd
    i = mpirun_cmd.index("runuser")
    assert mpirun_cmd[i:i + 3] == ["runuser", "-u", "alice"]
    assert mpirun_cmd.count("-x") >= 2
