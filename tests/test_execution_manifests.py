import pytest

from dms.execution import JobSpec
from dms.execution_manifests import render_tool_flags, tool_argv, build_volcano_job, build_preflight_pod


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


def test_scan_argv_with_options():
    # batch_files/broken_limit(값)·verbose(불리언)가 dscan 명령에 렌더된다.
    # --print는 기본 유지. --broken-limit은 dscan에서 롱네임뿐(-B 없음)이다.
    spec = _spec(operation="scan", tool="dscan",
                 options={"batch_files": 0, "broken_limit": 500, "verbose": True})
    argv = tool_argv(spec, abs_paths={"target": "/cephfs/dms/t"})
    assert argv == ["--directory", "/cephfs/dms/t", "--verbose",
                    "--batch-files", "0", "--broken-limit", "500",
                    "--output", "$DMS_SCAN_REPORT", "--print"]


def test_scan_argv_quiet_omits_print():
    # quiet면 --quiet를 넣고, 상충하는 --print는 생략한다.
    spec = _spec(operation="scan", tool="dscan", options={"quiet": True})
    argv = tool_argv(spec, abs_paths={"target": "/cephfs/dms/t"})
    assert "--quiet" in argv and "--print" not in argv
    assert argv[-2:] == ["--output", "$DMS_SCAN_REPORT"]


def test_render_tool_flags_dscan():
    assert render_tool_flags("dscan", {"batch_files": 3}) == ["--batch-files", "3"]
    assert render_tool_flags("dscan", {"broken_limit": 0}) == ["--broken-limit", "0"]
    assert render_tool_flags("dscan", {"verbose": True}) == ["--verbose"]
    assert render_tool_flags("dscan", {}) == []
    # top_k는 신 dscan(1b93d54)에서 기능 삭제 — 렌더 맵에 남아 있으면 안 된다.
    assert render_tool_flags("dscan", {"top_k": 3}) == []


def test_sync_argv_with_dryrun():
    # 비특권 요청자 → 목적지를 요청자 소유로 강제하는 --chown 자동 주입.
    spec = _spec(operation="sync", tool="dsync", dryrun=True, options={"delete": True},
                 identity={"uid": 10001, "gid": 10000, "privileged": False})
    argv = tool_argv(spec, abs_paths={"source": "/cephfs/a", "destination": "/cephfs/b"})
    assert argv == ["--delete", "--chown", "10001:10000", "--dryrun",
                    "/cephfs/a", "/cephfs/b"]


def test_sync_argv_privileged_preserves_source_owner():
    # 특권(root) 요청자 → --chown 미주입(소스 소유권 그대로 보존, root가 chown 가능).
    spec = _spec(operation="sync", tool="dsync",
                 identity={"uid": 0, "gid": 0, "privileged": True})
    argv = tool_argv(spec, abs_paths={"source": "/cephfs/a", "destination": "/cephfs/b"})
    assert "--chown" not in argv
    assert argv == ["/cephfs/a", "/cephfs/b"]


def test_sync_argv_user_chown_wins():
    # 사용자가 chown 옵션을 명시하면 그 값이 우선 — auto-chown 미주입(중복 없음).
    spec = _spec(operation="sync", tool="nsync", options={"chown": "bob:staff"},
                 identity={"uid": 10001, "gid": 10000, "privileged": False})
    argv = tool_argv(spec, abs_paths={"source": "/cephfs/a", "destination": "/cephfs/b"})
    assert argv.count("--chown") == 1
    assert argv[argv.index("--chown") + 1] == "bob:staff"


def test_rm_argv():
    spec = _spec(operation="rm", tool="drm", dryrun=False,
                 options={"recursive": True})
    argv = tool_argv(spec, abs_paths={"target": "/cephfs/junk"})
    assert argv == ["/cephfs/junk"]  # recursive는 drm 기본 재귀 — 플래그 아님


_VOL = [{"name": "cephfs", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]


def test_build_volcano_job_scan_structure():
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 process_count=8, paths={"target": "/cephfs/data"})
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


def test_artifact_dir_strips_file_scheme():
    spec = _spec(operation="scan", tool="dscan",
                 artifact_base="file:///cephfs/dms/artifacts",
                 candidates={"primary": ["dms-w1"]}, paths={"target": "/cephfs/data"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
    env = {e["name"]: e["value"]
           for e in launcher["template"]["spec"]["containers"][0]["env"]}
    # file:// 스킴 제거된 파일시스템 경로 (job-runner가 open()하는 경로)
    assert env["DMS_JR_ARTIFACT_DIR"] == "/cephfs/dms/artifacts/j1/execution"


def test_job_name_format():
    spec = _spec(operation="scan", tool="dscan", candidates={"primary": ["dms-w1"]},
                 paths={"target": "/cephfs/data"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    assert m["metadata"]["name"] == "dms-scan-execution-j1"  # job_id "j1" < 12자


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
    # phase를 명시적으로 "preflight"로 지정한다 — build_preflight_pod의 라벨은
    # spec.phase를 그대로 쓰므로(하드코드 아님), _spec()의 기본값 phase="execution"을
    # 그대로 두면 아래 라벨 검증과 스펙이 어긋난다.
    spec = _spec(operation="scan", tool="dscan", phase="preflight",
                 identity={"uid": 10001, "gid": 10000, "username": "alice"},
                 paths={"target": "/cephfs/dms/a"})
    m = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=_VOL,
                            node="dms-w1")
    assert m["kind"] == "Pod"
    sc = m["spec"]["containers"][0]["securityContext"]
    assert sc["runAsUser"] == 10001 and sc["runAsGroup"] == 10000
    assert m["spec"]["nodeSelector"]["kubernetes.io/hostname"] == "dms-w1"
    assert m["metadata"]["labels"]["dms.io/phase"] == "preflight"


def test_preflight_pod_name_scoped_by_phase_and_dns_safe():
    # initial and exec-recheck preflight must get distinct, DNS-1123-valid names
    scan_paths = {"target": "/cephfs/dms/a"}
    n1 = build_preflight_pod(_spec(operation="scan", tool="dscan", phase="preflight",
                                   paths=scan_paths),
                             job_image="i", namespace="dms", volumes=_VOL, node="dms-w1"
                             )["metadata"]["name"]
    n2 = build_preflight_pod(_spec(operation="scan", tool="dscan", phase="exec_preflight",
                                   paths=scan_paths),
                             job_image="i", namespace="dms", volumes=_VOL, node="dms-w1"
                             )["metadata"]["name"]
    assert n1 != n2                       # no collision between the two preflights
    assert "_" not in n1 and "_" not in n2  # underscores are illegal in Pod names
    assert "exec-preflight" in n2          # underscore sanitized to hyphen


def test_nsync_three_tasks_node_affinity():
    spec = _spec(operation="sync", tool="nsync",
                 candidates={"source": ["dms-w1", "dms-w2"],
                             "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    src = next(t for t in m["spec"]["tasks"] if t["name"] == "source-worker")
    dst = next(t for t in m["spec"]["tasks"] if t["name"] == "destination-worker")
    # 소스 워커는 source 노드에 affinity
    src_values = src["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert src_values == ["dms-w1", "dms-w2"]
    # 목적지 워커는 destination 노드에 affinity
    dst_values = dst["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert dst_values == ["dms-w4"]


def test_preflight_pod_shell_injection_safety():
    # 셸 인젝션 공격을 시뮬레이트: 경로에 명령 치환 포함
    malicious_target = 'a/$(touch /tmp/pwned)'
    spec = _spec(operation="scan", tool="dscan", identity={"uid": 10001, "gid": 10000},
                 paths={"target": malicious_target})
    m = build_preflight_pod(spec, job_image="i", namespace="dms", volumes=_VOL,
                            node="dms-w1")
    cmd = m["spec"]["containers"][0]["command"]
    # command는 ["sh", "-c", script, "sh", *path_args] 형태여야 함
    assert cmd[0] == "sh" and cmd[1] == "-c"
    script = cmd[2]
    # 악성 경로가 스크립트에 직접 인라인되지 않음 (f-string 인젝션 없음)
    assert malicious_target not in script
    # 스크립트는 positional 파라미터 참조 (예: "$1")
    assert '"$1"' in script or "'$1'" in script or "$1" in script
    # 악성 경로는 argv의 positional args에만 있음
    assert malicious_target in cmd[4:]
    # 검사 마커 있음
    assert "DMS_PREFLIGHT_REASON" in script or "DMS_PREFLIGHT_OK" in script


def _worker_container(m, name="worker"):
    task = next(t for t in m["spec"]["tasks"] if t["name"] == name)
    return task["template"]["spec"]["containers"][0]


def test_worker_container_has_identity_env():
    spec = _spec(operation="scan", tool="dscan",
                 identity={"uid": 10001, "gid": 10000, "username": "alice"},
                 candidates={"primary": ["dms-w1"]}, paths={"target": "/cephfs/data"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    c = _worker_container(m)
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_JR_UID"] == "10001"
    assert env["DMS_JR_GID"] == "10000"
    assert env["DMS_JR_USERNAME"] == "alice"


def test_worker_command_materializes_identity_and_execs_sshd():
    spec = _spec(operation="scan", tool="dscan",
                 identity={"uid": 10001, "gid": 10000, "username": "alice"},
                 candidates={"primary": ["dms-w1"]}, paths={"target": "/cephfs/data"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    c = _worker_container(m)
    cmd = c["command"]
    assert cmd[0] == "sh" and cmd[1] == "-c"
    script = cmd[2]
    # 물질화(case-guard) 참조 — env var로만, f-string 보간 없음
    assert "DMS_JR_USERNAME" in script and "DMS_JR_UID" in script
    assert "getent passwd" in script
    # ssh-keygen 호스트키 생성
    assert "ssh-keygen -A" in script
    # 요청자 홈으로 authorized_keys 복사
    assert "authorized_keys" in script
    # UsePAM=no, StrictModes=no로 sshd 기동 (root passwd 없는 요청자도 SSH 가능)
    assert "sshd -D -e -o StrictModes=no -o UsePAM=no" in script


def test_worker_security_context_has_sys_chroot():
    spec = _spec(operation="scan", tool="dscan",
                 identity={"uid": 10001, "gid": 10000, "username": "alice"},
                 candidates={"primary": ["dms-w1"]}, paths={"target": "/cephfs/data"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    sc = _worker_container(m)["securityContext"]
    assert sc["runAsUser"] == 0  # 물질화/sshd는 root 필요
    assert sc["capabilities"]["add"] == ["SYS_CHROOT"]


def test_worker_command_injection_safe():
    # 악성 username이 spec.identity에 들어와도 셸 스크립트 자체는 절대 변하지 않는다
    # (env var 참조만, f-string 보간 없음) — 스크립트는 매니페스트 생성 시점에 고정.
    malicious = "alice; rm -rf / #"
    spec1 = _spec(operation="scan", tool="dscan",
                  identity={"uid": 10001, "gid": 10000, "username": "alice"},
                  candidates={"primary": ["dms-w1"]}, paths={"target": "/cephfs/data"})
    spec2 = _spec(operation="scan", tool="dscan",
                  identity={"uid": 10001, "gid": 10000, "username": malicious},
                  candidates={"primary": ["dms-w1"]}, paths={"target": "/cephfs/data"})
    m1 = build_volcano_job(spec1, job_image="i", namespace="dms", volumes=_VOL)
    m2 = build_volcano_job(spec2, job_image="i", namespace="dms", volumes=_VOL)
    script1 = _worker_container(m1)["command"][2]
    script2 = _worker_container(m2)["command"][2]
    assert malicious not in script1 and malicious not in script2
    assert script1 == script2  # 스크립트는 identity와 무관하게 고정


def test_nsync_worker_containers_have_identity_and_sshd_command():
    spec = _spec(operation="sync", tool="nsync",
                 identity={"uid": 10001, "gid": 10000, "username": "alice"},
                 candidates={"source": ["dms-w1", "dms-w2"], "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    for name in ("source-worker", "destination-worker"):
        c = _worker_container(m, name)
        env = {e["name"]: e["value"] for e in c["env"]}
        assert env["DMS_JR_USERNAME"] == "alice"
        assert c["command"][0] == "sh" and c["command"][1] == "-c"
        assert "exec /usr/sbin/sshd -D -e -o StrictModes=no -o UsePAM=no" in c["command"][2]
        assert c["securityContext"]["capabilities"]["add"] == ["SYS_CHROOT"]


def _anti_affinity_rule(task):
    rules = task["template"]["spec"]["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"]
    assert len(rules) == 1
    return rules[0]


def test_colocated_worker_spreads_with_required_anti_affinity():
    # 이것이 없으면 max_nodes 는 레플리카 수만 제한할 뿐, 스케줄러가 워커들을 한
    # 노드에 몰아넣을 수 있다(설계 §2.4 -- MPI 팬아웃 붕괴). required 로 걸어도
    # 안전한 근거: resolve_fanout 이 레플리카를 후보 노드 수 이하로 자른다(§1-7).
    spec = _spec(operation="sync", tool="dsync",
                 candidates={"primary": ["dms-w1", "dms-w2", "dms-w3"]},
                 paths={"source": "s", "source_storage": "src",
                        "destination": "d", "destination_storage": "dst"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    worker = next(t for t in m["spec"]["tasks"] if t["name"] == "worker")
    labels = worker["template"]["metadata"]["labels"]
    assert labels == {"dms.io/job-id": "j1", "dms.io/task": "worker"}
    rule = _anti_affinity_rule(worker)
    assert rule["topologyKey"] == "kubernetes.io/hostname"
    assert rule["labelSelector"]["matchLabels"] == labels   # 자기참조: 같은 잡·같은 task
    # nodeAffinity(후보 고정)는 그대로 남는다 -- 병합이지 교체가 아니다
    aff = worker["template"]["spec"]["affinity"]
    values = aff["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert values == ["dms-w1", "dms-w2", "dms-w3"]
    # 런처는 산개 대상이 아니다(rank0 하나뿐) -- 라벨도 안티어피니티도 없다
    launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
    assert "metadata" not in launcher["template"]
    assert "podAntiAffinity" not in launcher["template"]["spec"]["affinity"]


def test_nsync_workers_get_task_scoped_anti_affinity():
    spec = _spec(operation="sync", tool="nsync",
                 candidates={"source": ["dms-w1", "dms-w2"],
                             "destination": ["dms-w4"]},
                 paths={"source": "/cephfs-third/a", "source_storage": "cephfs-third",
                        "destination": "/cephfs-secondary/b",
                        "destination_storage": "cephfs-secondary"})
    m = build_volcano_job(spec, job_image="i", namespace="dms", volumes=_VOL)
    for task_name in ("source-worker", "destination-worker"):
        task = next(t for t in m["spec"]["tasks"] if t["name"] == task_name)
        # task 별 셀렉터 -- source 가 destination 을 밀어내면 각자 자기 풀 안에서
        # 퍼진다는 설계(§2.4)가 깨진다.
        assert _anti_affinity_rule(task)["labelSelector"]["matchLabels"] == {
            "dms.io/job-id": "j1", "dms.io/task": task_name}
        assert task["template"]["metadata"]["labels"]["dms.io/task"] == task_name
    launcher = next(t for t in m["spec"]["tasks"] if t["name"] == "launcher")
    assert "metadata" not in launcher["template"]
    assert "affinity" not in launcher["template"]["spec"]   # nsync 런처는 원래 affinity 없음


# ---- 슬라이스 24 §2.1 층2: 미지 도구는 drm 꼴 argv 로 흘러가면 안 된다 ----

def test_unknown_tool_argv_raises_instead_of_falling_through_to_drm():
    # 현행 마지막 분기는 주석 `# drm` 짜리 fall-through 다 -- dscan/dsync/nsync 가
    # 아닌 **모든** 문자열이 맨몸 절대경로 argv(= drm 꼴)를 받는다(실측: "dwalk"
    # -> ['/cephfs/x']). 미래의 다섯째 도구를 placement 에 추가하고 여기를 잊으면
    # 그 도구가 경로 positional 을 삭제 대상으로 해석할 때 스캔 의도가 삭제가
    # 된다(설계 §2.1 시나리오 a). 층1(스테퍼)이 앞서 막지만, 순수 함수 스스로도
    # 조용히 틀리면 안 된다.
    with pytest.raises(ValueError) as e:
        tool_argv(_spec(operation="scan", tool="dwalk"),
                  abs_paths={"target": "/cephfs/x"})
    assert "dwalk" in str(e.value)   # 어댑터 submit_failed 의 detail 로 보존된다


def test_drm_dryrun_argv_is_unchanged_by_the_explicit_branch():
    # 명시 분기 치환의 무회귀 앵커(기존 test_rm_argv 는 dryrun=False 만 고정한다).
    spec = _spec(operation="rm", tool="drm", dryrun=True,
                 options={"recursive": True})
    assert tool_argv(spec, abs_paths={"target": "/cephfs/junk"}) == [
        "--dryrun", "/cephfs/junk"]
