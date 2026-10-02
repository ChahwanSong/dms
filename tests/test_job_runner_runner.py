import json

import pytest

from dms_job_runner.runner import _build_summary, run_job


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
        # 기본: 워커는 곧바로 준비된다 -- getent 는 IP 를, ssh 탐침은 성공을 돌려준다(2026-10-02 부터 러너는
        # IP 가 나오고 ssh 가 될 때까지 기다리므로, 도구 rc 를 흉내 내는 self._rc 를 여기에 섞지 않는다).
        if command[0] == "getent":
            return _R(returncode=0, stdout=f"10.0.0.{len(self.ran)}   {command[-1]}\n")
        if command[0] == "ssh":
            return _R(returncode=0)
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


# 실측 캡처 픽스처 -- tests/test_job_runner_parsers.py와 의도적으로 중복(테스트
# 파일끼리 import로 결합하지 않는다). 내용은 잡 60d24700 dsync stdout,
# drm stdout, dscan-report.json 실 스키마 그대로.
DSYNC_STDOUT = """\
[2026-08-04T02:14:12] Walked 10 items in 0.001 seconds (15463.025 items/sec)
[2026-08-04T02:14:12] Started   : Aug-04-2026, 02:14:12
[2026-08-04T02:14:12] Items     : 0
[2026-08-04T02:14:12] Copying items to destination
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12]   Directories: 3
[2026-08-04T02:14:12]   Files: 7
[2026-08-04T02:14:12] Data: 50.000 B (7.000 B per file)
[2026-08-04T02:14:12] Copy data: 50.000 B (50 bytes)
[2026-08-04T02:14:12] Copy rate: 3.284 KiB/s (50 bytes in 0.015 seconds)
[2026-08-04T02:14:12] Items: 10
[2026-08-04T02:14:12]   Directories: 3
[2026-08-04T02:14:12]   Files: 7
[2026-08-04T02:14:12]   Links: 0
[2026-08-04T02:14:12] Data: 50.000 B (50 bytes)
[2026-08-04T02:14:12] Rate: 0.991 KiB/s (050 bytes in 0.049 seconds)
[2026-08-04T02:14:12] Completed sync
"""

# 실측 nsync stdout(잡 abc0b559 실행 단계, 설계 부록 A) -- 파싱에 쓰이는 줄만.
NSYNC_STDOUT = """\
[2026-08-10T01:28:39] Progress 100.0% batch 1/1 actions=9 copied-files=7 copied-volume=50.000 B recent(actions=9 files=7 volume=50.000 B, 27.35 files/s, 195.333 B/s over 0.256 s) avg(27.35 files/s, 195.333 B/s)
[2026-08-10T01:28:39] Metadata diff summary: only-src=10 only-dst=0 common=0 changed=0
[2026-08-10T01:28:39] Planned actions: copy=7 mkdir=3 symlink-update=0 meta-update=0 remove=0 skipped-dst-only=0
[2026-08-10T01:28:39] Execution completed successfully
"""

DRM_STDOUT = """\
[2026-08-04T02:31:08] Walked 1 items in 0.001 seconds (1035.197 items/sec)
[2026-08-04T02:31:08] Removing 1 items
[2026-08-04T02:31:08] Removed 1 items (0.482 items/sec) in 2.077 seconds
"""

# 신 스키마(dscan 1b93d54): top_k·oldest 없음, broken_paths_total/limit 신설.
DSCAN_REPORT = {
    "directory": "/cephfs/dms/smoke-src",
    "generated_at_epoch": 1754273652,
    "thresholds": {},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0, "scan_errors": 0},
    "file_size_histogram": [],
    "broken_paths_total": 0,
    "broken_paths_limit": 100,
    "broken_paths": [],
}


def _summary(rec):
    path = [p for p in rec.writes if p.endswith("summary.json")][0]
    return json.loads(rec.writes[path])


def _run(rec, env, wait_hostfile=None):
    return run_job(env, run=rec.run, write_text=rec.write_text,
                   read_text=rec.read_text, sleep=lambda s: None,
                   wait_hostfile=wait_hostfile
                   or (lambda: (["dms-w1"], "/tmp/hostfile")),
                   make_executable=rec.make_executable)


def test_run_job_materializes_identity_and_runs_mpirun(tmp_path):
    # artifact_dir은 tmp_path로 격리한다 -- summary 단계가 dscan 리포트를 실제로
    # open()하므로, 절대경로 /cephfs를 쓰면 CephFS가 마운트된 테스트베드에서
    # 남은 j1 아티팩트를 읽어 all-null 단언이 깨질 수 있다.
    rec = _Recorder(rc=0, stdout="tool output")
    rc = run_job(_env(DMS_JR_ARTIFACT_DIR=str(tmp_path)),
                 run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    # identity 물질화(append)
    assert any("alice:x:10001:10000" in c for _, c in rec.appends)
    # mpirun 실행됨
    assert any("mpirun" in cmd for cmd in rec.ran)
    # mpirun 전에 execution 디렉터리를 요청자 소유로 chown(도구가 report를 쓸 수 있게)
    assert ["chown", "-R", "10001:10000", str(tmp_path)] in rec.ran
    chown_idx = rec.ran.index(["chown", "-R", "10001:10000", str(tmp_path)])
    mpirun_idx = next(i for i, c in enumerate(rec.ran) if "mpirun" in c)
    assert chown_idx < mpirun_idx
    # rank.sh가 executable로 표시됨
    assert any(p.endswith("rank.sh") for p in rec.made_exec)
    # rank.sh 본문에서 $DMS_SCAN_REPORT가 치환됨
    rank_path = f"{tmp_path}/rank.sh"
    body = rec.writes[rank_path]
    assert "$DMS_SCAN_REPORT" not in body
    assert "dscan-report.json" in body
    assert body.startswith("#!/bin/sh")
    # summary.json은 항상 3키 계약(설계 §2.3) -- dscan인데 리포트가 없으니
    # files/bytes는 fail-soft로 null, returncode만 실린다
    assert _summary(rec) == {"returncode": 0, "files": None, "bytes": None}


def test_run_job_dsync_summary_parses_final_items_and_bytes():
    rec = _Recorder(rc=0, stdout=DSYNC_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="dsync", DMS_JR_OPERATION="sync"))
    assert rc == 0
    # 실 캡처에는 중간·최종 요약이 공존한다 -- 최종 블록의 10/50이 잡혀야 한다
    # (순서 규칙 자체는 파서 단위 테스트가 값을 달리해 고정한다)
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": 50}


def test_run_job_drm_summary_parses_removed_items():
    rec = _Recorder(rc=0, stdout=DRM_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="drm", DMS_JR_OPERATION="rm"))
    assert rc == 0
    # drm은 바이트를 보고하지 않는다(설계 §1) -- bytes는 null이 정답이다
    assert _summary(rec) == {"returncode": 0, "files": 1, "bytes": None}


def test_run_job_dscan_summary_reads_report(tmp_path):
    # dscan의 구조화 수치는 stdout이 아니라 {artifact_dir}/dscan-report.json에 있다
    (tmp_path / "dscan-report.json").write_text(json.dumps(DSCAN_REPORT))
    rec = _Recorder(rc=0, stdout="human readable listing\n")
    rc = _run(rec, _env(DMS_JR_ARTIFACT_DIR=str(tmp_path)))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": None}


def test_run_job_dscan_summary_fail_soft_when_report_missing(tmp_path):
    # 도구가 실패해 리포트가 없어도 파싱은 잡을 죽이지 않는다(설계 §4) --
    # returncode는 보존되고 files/bytes만 null로 강등된다
    rec = _Recorder(rc=3, stdout="some non-json output")
    rc = _run(rec, _env(DMS_JR_ARTIFACT_DIR=str(tmp_path)))
    assert rc == 3
    assert _summary(rec) == {"returncode": 3, "files": None, "bytes": None}


def test_build_summary_unknown_tool_folds_to_nulls():
    # 미지 도구는 파싱 규칙이 없다 -- 출력이 있어도 (None, None)으로 강등(설계 §3).
    # 슬라이스 24 층3 전에는 이걸 run_job 으로 확인했지만(tool="dcp" 가 끝까지
    # 돌아 rc 0), 이제 allowlist 가 그 경로를 exec 전에 끊는다. 그래도
    # _build_summary 의 fold 분기는 남아 있으므로(도구별 elif 사슬의 기본값)
    # 순수 함수 층에서 계속 못박는다 -- 지우면 "미지 도구를 dsync 파서로
    # 보내는" 회귀를 아무도 못 잡는다.
    assert _build_summary("dcp", DSYNC_STDOUT, 0, "/tmp/nonexistent-artifacts") == {
        "returncode": 0, "files": None, "bytes": None}


def test_run_job_preview_phase_uses_same_summary_contract():
    # preview도 동일 계약(설계 §3): set_preview는 files/bytes를 무시하지만 dryrun
    # 예상치로 정보 가치가 있고, phase 분기가 없어 runner가 단순해진다
    rec = _Recorder(rc=0, stdout=DSYNC_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="dsync", DMS_JR_PHASE="preview"))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": 50}


def test_rank_script_quotes_argv():
    """Verify that rank.sh properly quotes arguments with special characters."""
    env = _env(**{"DMS_JR_ARGV": json.dumps(
        ["--directory", "/cephfs/a b$(x)", "--output", "$DMS_SCAN_REPORT", "--print"]
    )})
    rec = _Recorder(rc=0, stdout="ok")
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
    rec = _Recorder(rc=0, stdout="ok")
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
        return _R(returncode=0, stdout="ok")
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
    rec = _Recorder(rc=0, stdout="ok")
    rc = run_job(_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=lambda: (["dms-w1"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 0
    ssh_probe_calls = [i for i, cmd in enumerate(rec.ran) if cmd[0] == "ssh"]
    assert ssh_probe_calls, "no ssh readiness probe issued"
    mpirun_idx = next(i for i, cmd in enumerate(rec.ran) if "runuser" in cmd)
    assert all(i < mpirun_idx for i in ssh_probe_calls)


# ---- 2026-10-02 프로덕션 간헐 실패 수리: hostfile 은 IP 만, 워커 준비 실패는 mpirun 없이 사유 마커 ----
# 예전 러너는 getent 를 한 번만 하고 실패하면 DNS 이름(<pod>.<svc>)을 그대로 hostfile 에 썼고, ssh 대기는
# 끝까지 안 돼도 조용히 mpirun 으로 넘어갔다. launcher 가 워커보다 먼저 Ready 가 되면(실측) 앞 번호 워커가
# 이름으로 남고, mpirun 시점의 이름 조회가 한 번 더 실패하면 "Could not resolve hostname" rc 255 로 죽었다.

def _hostfile(rec, artifact_dir="/cephfs/dms/artifacts/j1/execution"):
    return rec.writes.get(f"{artifact_dir}/mpi-hostfile")


def test_hostfile_waits_until_every_worker_resolves_to_an_ip():
    # 첫 워커는 처음 두 번 못 풀린다(DNS 레코드가 아직 없음) -- 이름으로 쓰지 않고 IP 가 나올 때까지 기다린다.
    tries = {"w0.job": 0}

    def run_fn(cmd):
        if cmd[0] == "getent":
            host = cmd[-1]
            if host == "w0.job":
                tries[host] += 1
                if tries[host] <= 2:
                    return _R(returncode=2, stdout="")
                return _R(returncode=0, stdout="10.42.6.10   w0.job\n")
            return _R(returncode=0, stdout="10.42.6.11   w1.job\n")
        if cmd[0] == "ssh":
            return _R(returncode=0)
        return _R(returncode=0, stdout="ok")
    rec = _Recorder(run_fn=run_fn)
    rc = _run(rec, _env(), wait_hostfile=lambda: (["w0.job", "w1.job"], "/tmp/hostfile"))
    assert rc == 0
    assert _hostfile(rec) == "10.42.6.10 slots=8\n10.42.6.11 slots=8\n"
    assert tries["w0.job"] == 3
    # ssh 탐침은 이름이 아니라 IP 로 한다(mpirun 과 같은 경로)
    probed = [cmd[-2] for cmd in rec.ran if cmd[0] == "ssh"]
    assert set(probed) == {"10.42.6.10", "10.42.6.11"}
    assert any("runuser" in cmd for cmd in rec.ran)


def test_getent_output_that_is_not_an_ip_is_not_used():
    # getent 첫 칸이 IP 가 아니면(이상 출력) 못 푼 것으로 본다 -- 이름·쓰레기를 hostfile 에 싣지 않는다.
    calls = {"n": 0}

    def run_fn(cmd):
        if cmd[0] == "getent":
            calls["n"] += 1
            return _R(returncode=0, stdout="not-an-ip w0\n" if calls["n"] == 1 else "10.0.9.9 w0\n")
        if cmd[0] == "ssh":
            return _R(returncode=0)
        return _R(returncode=0, stdout="ok")
    rec = _Recorder(run_fn=run_fn)
    assert _run(rec, _env(), wait_hostfile=lambda: (["w0"], "/tmp/hostfile")) == 0
    assert _hostfile(rec) == "10.0.9.9 slots=8\n"


def _never(stage):
    def run_fn(cmd):
        if cmd[0] == "getent":
            return _R(returncode=2, stdout="") if stage == "resolve" else _R(returncode=0, stdout="10.1.1.1 w\n")
        if cmd[0] == "ssh":
            return _R(returncode=255)
        return _R(returncode=0, stdout="ok")
    return run_fn


def test_unresolvable_worker_fails_with_marker_and_no_mpirun(capsys, tmp_path):
    rec = _Recorder(run_fn=_never("resolve"))
    slept = []
    rc = run_job(_env(DMS_JR_ARTIFACT_DIR=str(tmp_path)), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: slept.append(s),
                 wait_hostfile=lambda: (["w0.job", "w1.job"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    assert rc == 1
    assert not any("runuser" in cmd for cmd in rec.ran)                 # mpirun 을 돌리지 않았다
    assert f"{tmp_path}/mpi-hostfile" not in rec.writes                 # 이름이 든 hostfile 을 만들지 않았다
    out = capsys.readouterr().out
    assert "DMS_EXEC_REASON=workers_unreachable" in out                 # 스테퍼가 사유로 승격하는 마커
    assert "DMS_JR_WORKER_UNREACHABLE host=w0.job stage=resolve" in out
    assert "ready=0/2" in out
    assert "DMS_JR_WORKER_UNREACHABLE host=w0.job stage=resolve" in rec.writes[f"{tmp_path}/stderr.log"]
    # 도구가 돌지 않았다 -- returncode 는 모름(null), 3키 계약 유지
    assert json.loads(rec.writes[f"{tmp_path}/summary.json"]) == {"returncode": None, "files": None, "bytes": None}
    # 기다린 시간은 기본 제한(300초) 근처 -- 1초 간격 재시도
    assert 290 <= sum(slept) <= 301


def test_worker_resolved_but_ssh_never_ready_reports_ssh_stage(capsys):
    rec = _Recorder(run_fn=_never("ssh"))
    rc = _run(rec, _env(DMS_JR_WORKER_READY_TIMEOUT_SECONDS="5"),
              wait_hostfile=lambda: (["w0.job"], "/tmp/hostfile"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "DMS_JR_WORKER_UNREACHABLE host=w0.job stage=ssh" in out
    assert not any("runuser" in cmd for cmd in rec.ran)


def test_timeout_is_shared_across_workers_not_per_worker(capsys):
    # 제한은 전체 공유다 -- 워커당 제한이면 N 개가 모두 늦을 때 N 배를 기다린다(예전 90회 x 워커 수).
    # w0 은 8초 뒤에야 풀리고 w1 은 끝내 안 풀린다: 공유 제한(10초)이면 w1 에 2초만 남아 합계 ~10초,
    # 워커당 제한이면 w1 이 다시 10초를 받아 합계 ~18초가 된다(리뷰 뮤테이션으로 확인한 구분선).
    tries = {"w0": 0}

    def run_fn(cmd):
        if cmd[0] == "getent":
            if cmd[-1] == "w0":
                tries["w0"] += 1
                return _R(returncode=0, stdout="10.0.0.1 w0\n") if tries["w0"] > 8 else _R(returncode=2)
            return _R(returncode=2)
        if cmd[0] == "ssh":
            return _R(returncode=0)
        return _R(returncode=0, stdout="ok")
    slept = []
    rec = _Recorder(run_fn=run_fn)
    rc = run_job(_env(DMS_JR_WORKER_READY_TIMEOUT_SECONDS="10"), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: slept.append(s),
                 wait_hostfile=lambda: (["w0", "w1", "w2", "w3"], "/tmp/hostfile"),
                 make_executable=rec.make_executable)
    out = capsys.readouterr().out
    assert rc == 1
    assert "DMS_JR_WORKER_READY host=w0 ip=10.0.0.1" in out
    assert "host=w1 stage=resolve" in out and "ready=1/4" in out
    assert sum(slept) <= 11


def test_main_passes_the_real_monotonic_clock(monkeypatch):
    # main 이 실제 시계를 넘기지 않으면 제한이 sleep 만 세는 가상 시계로 접혀, getent·ssh 안에서 보낸 시간
    # (airgap DNS 타임아웃 등)이 빠진다 -- 300초가 실제로는 수십 분이 된다(리뷰 뮤테이션으로 확인).
    import time
    from dms_job_runner import runner
    seen = {}

    def fake_run_job(env, **kw):
        seen.update(kw)
        return 0
    monkeypatch.setattr(runner, "run_job", fake_run_job)
    with pytest.raises(SystemExit) as e:
        runner.main()
    assert e.value.code == 0
    assert seen["clock"] is time.monotonic and seen["sleep"] is time.sleep


def test_real_clock_counts_time_spent_inside_probes():
    # main 은 time.monotonic 을 넘긴다: getent·ssh 자체가 오래 걸리면(DNS 타임아웃) 그 시간도 제한에 든다.
    now = {"t": 0.0}

    def run_fn(cmd):
        if cmd[0] == "getent":
            now["t"] += 4.0                       # 한 번의 조회가 4초 걸린다
            return _R(returncode=2, stdout="")
        return _R(returncode=0, stdout="ok")
    rec = _Recorder(run_fn=run_fn)
    rc = run_job(_env(DMS_JR_WORKER_READY_TIMEOUT_SECONDS="20"), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: now.__setitem__("t", now["t"] + s),
                 wait_hostfile=lambda: (["w0"], "/tmp/hostfile"),
                 make_executable=rec.make_executable, clock=lambda: now["t"])
    assert rc == 1
    assert len([c for c in rec.ran if c[0] == "getent"]) <= 5      # 20초 / (4+1)초


@pytest.mark.parametrize("raw", ["abc", "0", "-5", ""])
def test_bad_timeout_override_falls_back_to_default(raw):
    from dms_job_runner.runner import WORKER_READY_TIMEOUT_SECONDS, _ready_timeout
    assert _ready_timeout({"DMS_JR_WORKER_READY_TIMEOUT_SECONDS": raw}) == WORKER_READY_TIMEOUT_SECONDS
    assert _ready_timeout({}) == WORKER_READY_TIMEOUT_SECONDS == 300


def test_ready_lines_name_each_worker_ip(capsys):
    rec = _Recorder()
    assert _run(rec, _env(), wait_hostfile=lambda: (["w0.job", "w1.job"], "/tmp/hostfile")) == 0
    out = capsys.readouterr().out
    assert "DMS_JR_WORKER_READY host=w0.job ip=10.0.0." in out and "DMS_JR_WORKER_READY host=w1.job" in out


def test_runner_execution_marker_matches_the_control_plane():
    # 러너는 dms 를 import 하지 않는다 -- 마커 문자열과 사유가 제어면의 화이트리스트와 갈라지면 스테퍼가 사유를
    # 승격하지 못하고 다시 execution_failed 로 뭉갠다.
    from dms.execution_manifests import EXECUTION_REASON_MARKER, EXECUTION_REASONS
    from dms_job_runner import runner
    assert runner.EXECUTION_REASON_MARKER == EXECUTION_REASON_MARKER
    assert runner.WORKERS_UNREACHABLE in EXECUTION_REASONS


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
    rec = _Recorder(rc=0, stdout="ok")
    rc = run_job(_nsync_env(), run=rec.run, write_text=rec.write_text,
                 read_text=rec.read_text, sleep=lambda s: None,
                 wait_hostfile=_nsync_wait_hostfile(calls),
                 make_executable=rec.make_executable)
    assert rc == 0
    assert "source" in calls and "destination" in calls
    # source가 destination보다 먼저 (rank 순서 = source 먼저 -> role_map과 일치해야 함)
    assert calls.index("source") < calls.index("destination")


def test_run_job_nsync_computes_role_map_and_inserts_role_map_args():
    rec = _Recorder(rc=0, stdout="ok")
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
    rec = _Recorder(rc=0, stdout="ok")
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


def test_run_job_nsync_summary_uses_nsync_parser():
    # nsync 실 출력이 확보되어(설계 부록 A) 가정이 해소됐다 -- nsync는 "Items:"/
    # "(N bytes)"를 안 찍으므로 dsync 파서로 가면 null이 된다. 디스패치가
    # parse_nsync_counts로 가야 "Planned actions:" 합계 10과 50 B가 실린다.
    rec = _Recorder(rc=0, stdout=NSYNC_STDOUT)
    rc = _run(rec, _nsync_env(), wait_hostfile=_nsync_wait_hostfile([]))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": 10, "bytes": 50}


# ---- 슬라이스 24 §2.1 층3: allowlist 밖 tool 은 exec 없이 거부 ----

def test_unknown_tool_is_refused_before_any_side_effect(capsys):
    # rank.sh 는 `exec {tool} {argv}` 다 -- 명령 이름 자체가 tool 값이라(§1-3)
    # DB 에 "sh" 를 쓸 수 있는 자는 사용자 통제 파일을 워커 노드에서 요청자
    # 신원의 스크립트로 실행시킬 수 있다. 층1·2 는 제어면의 방어고, 이 층만이
    # "이미 제출된 매니페스트/env 의 사후 변조"까지 막는다 -- 그래서 부작용
    # (passwd append, ssh 복사, chown, mpirun)이 하나도 시작되기 전에 끊어야 한다.
    rec = _Recorder()
    rc = _run(rec, _env(DMS_JR_TOOL="sh", DMS_JR_ARGV=json.dumps(["/etc"])))
    assert rc != 0
    assert rec.ran == []        # mpirun 은 물론 어떤 명령도 안 돌았다
    assert rec.appends == []    # /etc/passwd 물질화도 없다
    # summary 는 3키 계약 유지(설계 §4) -- 모름(files/bytes)은 null 이지 0 이 아니다.
    assert _summary(rec) == {"returncode": 1, "files": None, "bytes": None}
    # 층3 발동은 층1·2 가 뚫렸다는 조사 신호 -- grep 가능한 마커로 남긴다.
    assert "DMS_JR_UNKNOWN_TOOL" in capsys.readouterr().err


def test_runner_allowlist_matches_the_control_plane_tool_names():
    # dms_job_runner 는 dms 를 import 하지 않는 독립 패키지라 튜플을 중복 정의한다
    # (설계 §2.1 층3). 두 값이 갈라지면 다섯째 도구를 추가했을 때 "제어면은
    # 아는데 러너가 거부"하는 조용한 실패가 된다 -- 이 저장소 테스트만이 둘을
    # 잇는다(테스트 층은 독립 패키지 규칙의 밖이다).
    from dms.config import AGENT_TOOL_NAMES
    from dms_job_runner.runner import ALLOWED_TOOLS
    assert ALLOWED_TOOLS == AGENT_TOOL_NAMES



# 2026-09-17: dsync --dryrun(미리보기)은 복사 요약을 찍지 않아 files/bytes 가 항상
# null 이었다(컨펌 창 "(요약 없음)" 조사에서 발견) -- 지문이 이 요약의 해시라 사실상
# 상수였다. dryrun 이면 소스 walk 항목 수를 files 로, bytes 는 정직하게 null.
DSYNC_DRYRUN_STDOUT = """\
[ts] Walking source path
[ts] Walked 2 items in 0.003 secs (780.071 items/sec) ...
[ts] Walked 2 items in 0.004 seconds (453.712 items/sec)
[ts] Walking destination path
[ts] Walked 0 items in 0.001 secs (0.000 items/sec) ...
[ts] Walked 0 items in 0.001 seconds (0.000 items/sec)
[ts] Items     : 0
[ts] Completed sync
"""


def test_build_summary_dsync_dryrun_uses_source_walk_items():
    assert _build_summary("dsync", DSYNC_DRYRUN_STDOUT, 0, "/tmp/x", dryrun=True) == {
        "returncode": 0, "files": 2, "bytes": None}
    # dryrun 이 아니면(실행) 같은 출력은 여전히 모름 -- 복사 요약이 없으니 지어내지 않는다
    assert _build_summary("dsync", DSYNC_DRYRUN_STDOUT, 0, "/tmp/x") == {
        "returncode": 0, "files": None, "bytes": None}


def test_run_job_detects_dryrun_from_argv_and_fills_preview_files():
    rec = _Recorder(rc=0, stdout=DSYNC_DRYRUN_STDOUT)
    rc = _run(rec, _env(DMS_JR_TOOL="dsync", DMS_JR_OPERATION="sync", DMS_JR_PHASE="preview",
                        DMS_JR_ARGV=json.dumps(["--batch-files", "1000000", "--dryrun",
                                                "/cephfs/managed/a", "/cephfs/managed/b"])))
    assert rc == 0
    assert _summary(rec) == {"returncode": 0, "files": 2, "bytes": None}


def test_nsync_hostfile_keeps_source_then_destination_order_as_ips():
    # 입력 순서·IP 순서·정렬 순서가 서로 다르게 -- 정렬·중복 제거 같은 재배열이 끼면 rank 의 역할(src/dst)이
    # 뒤집힌다(commands.nsync_role_map 은 위치로 역할을 준다). 정렬된 입력이면 그런 회귀를 못 잡는다.
    ips = {"dms-w5": "10.0.1.9", "dms-w2": "10.0.1.3", "dms-w0": "10.0.1.7"}

    def wait_hostfile(role=None):
        return (["dms-w5", "dms-w2"], "/tmp/s") if role == "source" else (["dms-w0"], "/tmp/d")

    def run_fn(cmd):
        if cmd[0] == "getent":
            return _R(returncode=0, stdout=f"{ips[cmd[-1]]} {cmd[-1]}\n")
        if cmd[0] == "ssh":
            return _R(returncode=0)
        return _R(returncode=0, stdout="ok")
    rec = _Recorder(run_fn=run_fn)
    rc = _run(rec, _nsync_env(), wait_hostfile=wait_hostfile)
    assert rc == 0
    assert _hostfile(rec) == "10.0.1.9 slots=2\n10.0.1.3 slots=2\n10.0.1.7 slots=2\n"
