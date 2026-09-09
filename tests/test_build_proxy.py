"""빌드 노드 프록시(2026-09-08): control_state → BuildRunner → 빌드/프로브 파드 env,
프로브의 CONNECT 검사, 스탬프의 레지스트리 치환. 테스트베드 실증(프록시 로그)이
"env 만으로 npm/pip/apt/pull 이 프록시를 탄다"를 확정하고, 여기서는 배선을 고정한다."""
import os
import subprocess
import tempfile

import pytest
from dms.build_manifests import (_SCRIPT, build_build_pod, build_probe_pod,
                                 host_network_for, proxy_env)
from dms.build_runner import BuildRunner

PROXY = {"http_proxy": "http://proxy.corp:3128", "https_proxy": None, "no_proxy": None}


def _env(pod):
    return {e["name"]: e["value"] for e in pod["spec"]["containers"][0]["env"]}


def test_proxy_env_is_empty_without_a_proxy():
    assert proxy_env(None, "pkg-01:5000") == {}
    assert proxy_env({"http_proxy": "", "https_proxy": None, "no_proxy": "a"}, "pkg-01:5000") == {}


def test_proxy_env_fills_both_cases_and_mirrors_the_missing_scheme():
    env = proxy_env(PROXY, "pkg-01:5000")
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == "http://proxy.corp:3128"
    assert env["http_proxy"] == env["https_proxy"] == "http://proxy.corp:3128"
    env = proxy_env({"http_proxy": None, "https_proxy": "http://s:1", "no_proxy": None}, "r:5000")
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == "http://s:1"


def test_no_proxy_always_covers_the_site_registry_and_localhost():
    # push 가 프록시로 나가면 사내 레지스트리는 대개 프록시 너머에 없다 -- 포트
    # 유무 둘 다(Go 의 NO_PROXY 매칭은 호스트 기준이지만 host:port 도 허용).
    env = proxy_env({**PROXY, "no_proxy": ".corp.example, 10.0.0.0/8"}, "pkg-01:5000")
    items = env["NO_PROXY"].split(",")
    assert items[:2] == [".corp.example", "10.0.0.0/8"]        # 운영자 항목이 먼저
    for must in ("pkg-01:5000", "pkg-01", "localhost", "127.0.0.1"):
        assert must in items
    assert env["no_proxy"] == env["NO_PROXY"]
    # 중복은 한 번만
    env = proxy_env({**PROXY, "no_proxy": "pkg-01,localhost"}, "pkg-01:5000")
    assert env["NO_PROXY"].split(",").count("pkg-01") == 1


def _pod(**kw):
    return build_build_pod(build_id="0123456789abcdef0123456789abcdef",
                           source_path="/src/dms", tag="d1", images=["dms"],
                           node="n1", namespace="dms", registry="pkg-01:5000",
                           builder_image="pkg-01:5000/buildah:stable",
                           timeout_seconds=7200, **kw)


def test_build_pod_carries_proxy_env_only_when_configured():
    assert not any(k.lower() == "https_proxy" for k in _env(_pod()))
    env = _env(_pod(proxy=PROXY))
    assert env["HTTPS_PROXY"] == "http://proxy.corp:3128"
    assert "pkg-01" in env["NO_PROXY"]
    # 스크립트가 어느 프록시로 나갔는지 로그에 남긴다(실증·진단 재료)
    assert "DMS_BUILD_PROXY" in _SCRIPT


def _probe(**kw):
    return build_probe_pod(build_id="0123456789abcdef0123456789abcdef",
                           source_path="/src/dms", node="n1", namespace="dms",
                           registry="pkg-01:5000", job_image="pkg-01:5000/dms-mpifileutils:d1",
                           timeout_seconds=180, **kw)


def test_probe_gets_the_https_proxy_and_checks_egress_through_it():
    assert _env(_probe())["DMS_PF_PROXY"] == ""
    assert _env(_probe(proxy=PROXY))["DMS_PF_PROXY"] == "http://proxy.corp:3128"
    script = _probe()["spec"]["containers"][0]["command"][2]
    assert "CONNECT %s:%d HTTP/1.1" in script
    assert "build_proxy_unreachable" in script
    # 프록시 없이도 종전 직접 검사 경로가 그대로 남는다
    assert "reachable(h, 443)" in script


def test_runner_reads_the_proxy_at_submit_time():
    class K8s:
        created = []

        def create(self, m):
            self.created.append(m)

        def get(self, *a):
            return None

    k8s = K8s()
    current = {"proxy": None}
    runner = BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                         builder_image="b", timeout_seconds=1,
                         proxy=lambda: current["proxy"])
    build = {"build_id": "0123456789abcdef0123456789abcdef", "repo_url": "/src",
             "git_ref": "local", "images": ["dms"], "node_name": "n1"}
    runner.submit(build)
    assert "HTTPS_PROXY" not in _env(k8s.created[-1])
    current["proxy"] = PROXY                     # 운영자가 포탈에서 바꿈 -- 재시작 없이
    runner.submit_preflight(build)
    assert _env(k8s.created[-1])["DMS_PF_PROXY"] == "http://proxy.corp:3128"
    runner.submit(build)
    assert _env(k8s.created[-1])["https_proxy"] == "http://proxy.corp:3128"


def _stamp_block():
    start = _SCRIPT.index("for img in $DMS_BUILD_IMAGES; do\n  sed -i")
    end = _SCRIPT.index("done", start) + len("done")
    return _SCRIPT[start:end]


@pytest.mark.skipif(not os.path.exists("/bin/sh"), reason="needs sh")
def test_stamp_rewrites_registry_and_tag_of_built_images_only():
    """소스 트리의 매니페스트는 테스트베드 레지스트리(pkg-01:5000)를 담고 있다 --
    다른 사이트에서도 빌드하는 이미지 줄이 그 사이트 레지스트리·태그로 스탬프되고,
    빌드하지 않는 줄(dms-agent)과 다른 리포(buildah)는 그대로여야 한다."""
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "deploy", "k8s"))
        path = os.path.join(d, "deploy", "k8s", "x.yaml")
        with open(path, "w") as f:
            f.write("          image: pkg-01:5000/dms:d119\n"
                    "          image: pkg-01:5000/dms-agent:d118\n"
                    '  DMS_JOB_IMAGE: "pkg-01:5000/dms-mpifileutils:d110"\n'
                    '  DMS_BUILD_BUILDER_IMAGE: "pkg-01:5000/buildah:stable"\n'
                    "          image: registry.example.com:5000/dms:v9\n")
        env = {**os.environ, "DMS_BUILD_IMAGES": "dms dms-mpifileutils",
               "DMS_BUILD_REGISTRY": "reg.ssc.example:5000", "DMS_BUILD_TAG": "d1"}
        subprocess.run(["sh", "-c", _stamp_block()], cwd=d, env=env, check=True)
        with open(path) as f:
            out = f.read().splitlines()
    assert out == ["          image: reg.ssc.example:5000/dms:d1",
                   "          image: pkg-01:5000/dms-agent:d118",
                   '  DMS_JOB_IMAGE: "reg.ssc.example:5000/dms-mpifileutils:d1"',
                   '  DMS_BUILD_BUILDER_IMAGE: "pkg-01:5000/buildah:stable"',
                   "          image: reg.ssc.example:5000/dms:d1"]


# --- 호스트 네트워크 모드(2026-09-09): loopback 프록시(ssh -R 터널) ---

@pytest.mark.parametrize("proxy,expected", [
    (None, False),
    ({"http_proxy": "http://10.0.0.1:3128"}, False),
    ({"https_proxy": "http://localhost:7227"}, True),
    ({"http_proxy": "http://127.0.0.1:7227"}, True),
    ({"http_proxy": "http://[::1]:7227"}, True),
    ({"http_proxy": "http://LOCALHOST:7227"}, True),
    ({"http_proxy": "http://10.0.0.1:3128", "host_network": True}, True),
    ({"host_network": True}, True),
    ({"http_proxy": "http://proxy.corp:3128", "host_network": False}, False),
])
def test_host_network_is_automatic_for_loopback_proxies_or_the_switch(proxy, expected):
    assert host_network_for(proxy) is expected


def test_host_network_mode_shapes_both_pods_and_the_buildah_network_flag():
    loop = {"http_proxy": "http://127.0.0.1:7227", "https_proxy": None, "no_proxy": None}
    pod = _pod(proxy=loop)
    assert pod["spec"]["hostNetwork"] is True
    # hostNetwork 파드의 기본 DNS 는 호스트 resolv.conf -- 클러스터 이름을 그대로
    # 쓰려면 ClusterFirstWithHostNet(SSC ingress-nginx 애드온과 같은 함정).
    assert pod["spec"]["dnsPolicy"] == "ClusterFirstWithHostNet"
    assert _env(pod)["DMS_BUILD_NETWORK"] == "host"
    assert _env(pod)["HTTPS_PROXY"] == "http://127.0.0.1:7227"
    probe = _probe(proxy=loop)
    assert probe["spec"]["hostNetwork"] is True
    assert probe["spec"]["dnsPolicy"] == "ClusterFirstWithHostNet"
    assert _env(probe)["DMS_PF_PROXY"] == "http://127.0.0.1:7227"
    # 파드 hostNetwork 만으로는 부족하다: buildah RUN 단계는 기본 --network=private 라
    # 자기 netns 의 localhost 를 본다 -- 같은 스위치로 --network=host 를 건다.
    assert 'net_flag="--network=host"' in _SCRIPT
    assert _SCRIPT.count("buildah bud $net_flag") == 3


def test_pod_network_stays_default_without_the_mode():
    pod = _pod(proxy=PROXY)                       # 비-loopback 프록시
    assert "hostNetwork" not in pod["spec"] and "dnsPolicy" not in pod["spec"]
    assert "DMS_BUILD_NETWORK" not in _env(pod)
    assert "hostNetwork" not in _probe(proxy=PROXY)["spec"]
    assert "hostNetwork" not in _pod()["spec"]
