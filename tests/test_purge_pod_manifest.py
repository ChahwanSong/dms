"""purge 파드 매니페스트 계약(purge_runner.build_purge_pod). 이 파드는 제어면이 아니고, 이미 도는 잡 launcher(root + 기본
cap 전부 + base·스토리지 rw)보다 엄격히 약해야 한다 -- 볼륨 하나·cap 둘·SA 토큰 없음·고정 스크립트·positional 이름.
모양이 넓어지면(스토리지 마운트·cap 추가·스크립트 보간) 여기서 빨간불이 된다."""
import hashlib

import pytest

from dms.artifact_base import ARTIFACT_MOUNT
from dms.purge_runner import (MAX_PURGE_NAMES, PURGE_POD_DEADLINE_SECONDS, PURGE_SCRIPT, build_purge_pod,
                              purge_pod_name)

A, B = "a" * 32, "b" * 32
BASE = "/cephfs/dms/artifacts"


def _pod(**kw):
    args = {"names": [B, A], "base": BASE, "nodes": ["dms-w2", "dms-w1"], "image": "reg/dms-mpifileutils:d1",
            "namespace": "dms"}
    args.update(kw)
    return build_purge_pod(**args)


def test_exactly_one_volume_the_artifact_base_hostpath():
    spec = _pod()["spec"]
    assert spec["volumes"] == [{"name": "dms-artifact-base", "hostPath": {"path": BASE, "type": "Directory"}}]
    (c,) = spec["containers"]
    assert c["volumeMounts"] == [{"name": "dms-artifact-base", "mountPath": ARTIFACT_MOUNT,
                                  "mountPropagation": "HostToContainer"}]
    assert ARTIFACT_MOUNT == "/dms-artifact-base"


def test_capabilities_are_exactly_drop_all_add_dac_override_fowner():
    (c,) = _pod()["spec"]["containers"]
    sc = c["securityContext"]
    assert sc == {"runAsUser": 0, "runAsGroup": 0, "allowPrivilegeEscalation": False,
                  "readOnlyRootFilesystem": True,
                  "capabilities": {"drop": ["ALL"], "add": ["DAC_OVERRIDE", "FOWNER"]}}
    assert "privileged" not in sc


def test_pod_level_hardening():
    pod = _pod()
    spec = pod["spec"]
    assert pod["apiVersion"] == "v1" and pod["kind"] == "Pod"
    assert spec["restartPolicy"] == "Never"
    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False
    assert spec["activeDeadlineSeconds"] == PURGE_POD_DEADLINE_SECONDS == 3600
    assert spec["terminationGracePeriodSeconds"] == 5
    assert pod["metadata"]["labels"] == {"dms.io/purge": "1"}
    assert pod["metadata"]["namespace"] == "dms"
    # 노드·호스트 네임스페이스·추가 컨테이너 없음
    for key in ("hostNetwork", "hostPID", "hostIPC", "initContainers", "serviceAccountName", "securityContext"):
        assert key not in spec
    (c,) = spec["containers"]
    assert c["name"] == "purge" and c["image"] == "reg/dms-mpifileutils:d1"
    assert "env" not in c and "envFrom" not in c
    assert c["resources"] == {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"memory": "256Mi"}}


def test_command_is_fixed_script_plus_positional_sorted_names():
    (c,) = _pod()["spec"]["containers"]
    assert c["command"] == ["sh", "-c", PURGE_SCRIPT, "sh", A, B]
    assert A not in PURGE_SCRIPT and BASE not in PURGE_SCRIPT       # 보간 0(규칙 7)
    assert "args" not in c


def test_name_is_deterministic_over_the_name_set():
    p1 = _pod(names=[A, B])
    p2 = _pod(names=[B, A, A])
    digest = hashlib.sha256(f"{A},{B}".encode()).hexdigest()[:12]
    assert p1["metadata"]["name"] == p2["metadata"]["name"] == f"dms-artifact-purge-{digest}"
    assert purge_pod_name([B, A]) == p1["metadata"]["name"]
    assert _pod(names=[A])["metadata"]["name"] != p1["metadata"]["name"]
    assert len(p1["metadata"]["name"]) <= 63


def test_node_affinity_is_exactly_the_given_nodes():
    aff = _pod(nodes=["dms-w2", "dms-w1", "dms-w2"])["spec"]["affinity"]
    assert aff == {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
        {"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In",
                               "values": ["dms-w1", "dms-w2"]}]}]}}}


@pytest.mark.parametrize("bad", ["..", "a/b", "A" * 32, "a" * 31, "", None, "a" * 32 + "\n"])
def test_non_hex_names_are_rejected(bad):
    with pytest.raises(ValueError):
        _pod(names=[A, bad])


def test_name_count_bounds():
    with pytest.raises(ValueError):
        _pod(names=[])
    many = [f"{i:032x}" for i in range(MAX_PURGE_NAMES + 1)]
    with pytest.raises(ValueError):
        _pod(names=many)
    assert len(_pod(names=many[:MAX_PURGE_NAMES])["spec"]["containers"][0]["command"]) == 4 + MAX_PURGE_NAMES


@pytest.mark.parametrize("kw", [{"base": "relative/path"}, {"base": ""}, {"base": None}, {"base": "/x\x00y"},
                                {"nodes": []}, {"nodes": None}, {"nodes": [""]}, {"image": ""}, {"image": None}])
def test_other_inputs_are_validated(kw):
    with pytest.raises(ValueError):
        _pod(**kw)
