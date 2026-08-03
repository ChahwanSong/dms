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
