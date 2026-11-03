"""S12 verify.sh 의 판정과 마스터 호스트 프로브(host-net-probe.sh)의 판정(2026-11-03 ⑦ 리뷰 1 — 판정을 뒤집어도 통과하던 변이 V01~V06·H01·H02).

실제 sh(dash)로 스크립트를 돌린다. 클러스터는 가짜다:
  - verify.sh: PATH 앞의 kubectl 심(fixtures/install_stages/fake_kubectl_verify.py — 읽기만, 변경 동사가 오면 97)과 curl 심(FAKE_CURL_443·
    FAKE_CURL_80), 파드를 띄우는 도우미(pg-path-probe.py·host-net-probe.sh)와 install-detect.py 는 fake_helper.py(인자·rc 기록).
  - host-net-probe.sh: kubectl 심(fixtures/install_stages/fake_kubectl_hostnet.py)이 create 한 프로브 파드를 기억하고 노드별 VALUE= 로그를 준다.
이 호스트의 진짜 kubectl(실 테스트베드)은 절대 부르지 않는다.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"
FIX = REPO / "tests" / "fixtures" / "install_stages"

FAKE_CURL = r'''#!/bin/sh
echo "$*" >> "$FAKE_CURL_LOG"
for a; do url=$a; done
case "$url" in
  https://*) printf '%s' "${FAKE_CURL_443:-200}" ;;
  http://*) printf '%s' "${FAKE_CURL_80:-000}" ;;
esac
'''


def good_world(**over):
    img = "10.96.0.50:5000/dms:d7"
    w = {"migrate": "1",
         "workloads": {"dms-api": {"ready": "1", "want": "1", "image": img}, "dms-controller": {"ready": "1", "want": "1", "image": img},
                       "dms-agent": {"ready": "2", "want": "2", "image": "10.96.0.50:5000/dms-agent:d7"}, "dms-registry": {"ready": "1"}},
         "cluster": {"instances": "3", "ready": "3", "phase": "Cluster in healthy state"}, "eps": "10.0.0.9",
         "pvs": "dms-pgdata-01 Bound\ndms-pgdata-02 Bound\ndms-pgdata-03 Bound\ndms-pgdata-04 Available\nother-pv Available\n",
         "cronjob": "2026-11-03T07:17:04Z", "svc_registry": {"clusterIP": "10.96.0.50"},
         "tags": '{"name":"dms","tags":["d6","d7"]}', "gateway": "True", "readyz": '{"status":"ok","reconnects":0}', "logs": {}}
    w.update(over)
    return w


class VRig:
    def __init__(self, tmp):
        d = tmp / "deploy"
        d.mkdir()
        shutil.copy(DEPLOY / "verify.sh", d / "verify.sh")
        shutil.copy(FIX / "fake_helper.py", d / "_fake_helper.py")
        for n in ("pg-path-probe.py", "install-detect.py"):
            (d / n).write_text("import os, runpy, sys\nsys.argv = [sys.argv[0], %r] + sys.argv[1:]\n"
                               "runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_fake_helper.py'), "
                               "run_name='__main__')\n" % n)
        (d / "host-net-probe.sh").write_text('#!/bin/sh\nexec python3 "$(CDPATH= cd "$(dirname "$0")" && pwd)/_fake_helper.py" '
                                             'host-net-probe.sh "$@"\n')
        b = tmp / "bin"
        b.mkdir()
        (b / "kubectl").write_text('#!/bin/sh\nexec python3 %s "$@"\n' % (FIX / "fake_kubectl_verify.py"))
        (b / "curl").write_text(FAKE_CURL)
        for f in b.iterdir():
            f.chmod(0o755)
        self.rendered = tmp / "rendered" / "testbed-k8s"
        self.rendered.mkdir(parents=True)
        (self.rendered / "render.env").write_text("SITE=testbed-k8s\nREGISTRY_CLUSTER_IP=10.96.0.50\nREGISTRY=10.96.0.50:5000\n"
                                                  "PORTAL_DOMAIN=portal.example\nPORTAL_VIP=10.20.20.10\n")
        self.tmp, self.deploy, self.bin = tmp, d, b
        self.paths = {k: tmp / f"{k}.log" for k in ("kube", "helper", "curl")}
        self.world_path, self.scenario_path = tmp / "world.json", tmp / "scenario.json"

    def run(self, world, scenario=None, *args, rendered=True, **env):
        for p in self.paths.values():
            if p.exists():
                p.unlink()
        self.world_path.write_text(json.dumps(world))
        self.scenario_path.write_text(json.dumps(scenario or {}))
        e = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(self.tmp),
             "FAKE_WORLD": str(self.world_path), "FAKE_SCENARIO": str(self.scenario_path), "FAKE_KUBE_LOG": str(self.paths["kube"]),
             "FAKE_HELPER_LOG": str(self.paths["helper"]), "FAKE_CURL_LOG": str(self.paths["curl"]), "PYTHONDONTWRITEBYTECODE": "1"}
        e.update(env)
        argv = ["sh", str(self.deploy / "verify.sh"), "--context", "known"] + (["--rendered", str(self.rendered)] if rendered else [])
        r = subprocess.run(argv + list(args), env=e, capture_output=True, text=True, timeout=120)
        r.all = r.stdout + r.stderr
        return r

    def helpers(self):
        p = self.paths["helper"]
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def kube(self):
        p = self.paths["kube"]
        return p.read_text().splitlines() if p.exists() else []


@pytest.fixture
def vrig(tmp_path):
    return VRig(tmp_path)


def _lines(r, tag):
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("[%s]" % tag)]


def test_verify_all_good(vrig):
    r = vrig.run(good_world())
    assert r.returncode == 0 and "FAIL=0" in r.stdout and _lines(r, "FAIL") == [] and _lines(r, "WARN") == [], r.stdout
    for needle in ("Cluster dms-pg readyInstances 3/3", "Available 여분 PV 1개", "PG 경로 대조(contrast", "CronJob dms-pg-dump 마지막 성공",
                   "레지스트리에 dms:d7 있음", "Service clusterIP 10.96.0.50 == REGISTRY_CLUSTER_IP", "Gateway dms-portal Programmed",
                   "마스터마다 :443 LISTEN", "readyz ok(apiserver 서비스 프록시", "readyz 200 via https://portal.example",
                   "80 불가 = 정상", "애드온 판정 PASS"):
        assert needle in r.stdout, needle
    assert [h["name"] for h in vrig.helpers()] == ["pg-path-probe.py", "host-net-probe.sh", "install-detect.py"]
    pp = vrig.helpers()[0]["argv"]
    assert pp[pp.index("--mode") + 1] == "contrast" and pp[pp.index("--namespace") + 1] == "dms"
    det = vrig.helpers()[2]["argv"]
    assert det[det.index("--namespace") + 1] == "dms"                               # env DMS_NS 를 읽지 않게(⑦ 리뷰 1)
    curls = vrig.paths["curl"].read_text()
    assert "--resolve portal.example:443:10.20.20.10" in curls and "--noproxy" in curls
    assert all(c.startswith("--context known ") or c.startswith("config get-contexts") for c in vrig.kube())


@pytest.mark.parametrize("over, scenario, env, rc, needle, tag", [
    # CNPG readyInstances
    ({"cluster": {"instances": "3", "ready": "2", "phase": "x"}}, {}, {}, 0, "readyInstances 2/3", "WARN"),
    ({"cluster": {"instances": "3", "ready": "0", "phase": "x"}}, {}, {}, 1, "readyInstances 0", "FAIL"),
    ({"cluster": None}, {}, {}, 1, "Cluster dms-pg 없음", "FAIL"),
    # Available 여분 PV(V01)
    ({"pvs": "dms-pgdata-01 Bound\ndms-pgdata-02 Bound\ndms-pgdata-03 Bound\ndms-pgdata-04 Released\n"}, {}, {}, 0, "여분 PV 0개", "WARN"),
    # PG 경로 대조(V02) — S10 과 같은 검사
    ({}, {"pg-path-probe.py contrast": {"rc": 1}}, {}, 1, "PG 가 공용 경로가 아닌 곳에 쓴다", "FAIL"),
    ({}, {"pg-path-probe.py contrast": {"rc": 3}}, {}, 0, "PG 경로 대조 WARN", "WARN"),
    ({}, {"pg-path-probe.py contrast": {"rc": 4}}, {}, 0, "PG 경로 대조 모름", "WARN"),
    # CronJob
    ({"cronjob": ""}, {}, {}, 0, "성공 기록이 아직 없다", "WARN"),
    ({"cronjob": None}, {}, {}, 1, "CronJob dms-pg-dump 없음", "FAIL"),
    # 레지스트리 태그(V04)
    ({"tags": '{"name":"dms","tags":["d6"]}'}, {}, {}, 1, "라이브 태그 d7 가 없다", "FAIL"),
    ({"tags": None}, {}, {}, 0, "레지스트리 태그를 읽지 못했다", "WARN"),
    ({"svc_registry": {"clusterIP": "10.96.0.99"}}, {}, {}, 1, "!= REGISTRY_CLUSTER_IP 10.96.0.50", "FAIL"),
    ({"workloads": dict(good_world()["workloads"], **{"dms-registry": {"ready": "0"}})}, {}, {}, 1, "dms-registry Ready 아님", "FAIL"),
    # 마스터 :443(V03)
    ({}, {"host-net-probe.sh --listen": {"rc": 1}}, {}, 1, "마스터 :443 LISTEN 없음", "FAIL"),
    ({}, {"host-net-probe.sh --listen": {"rc": 4}}, {}, 0, "마스터 :443 을 재지 못했다(rc=4)", "WARN"),
    ({"gateway": "False"}, {}, {}, 1, "Programmed=False", "FAIL"),
    # readyz — apiserver 서비스 프록시가 판정, VIP 무응답은 Avi 미개통일 수 있어 WARN(⑦ 리뷰 1)
    ({"readyz": None}, {}, {}, 1, "readyz 실패(apiserver 서비스 프록시", "FAIL"),
    ({"readyz": '{"status":"degraded"}'}, {}, {}, 1, "readyz 가 ok 가 아니다", "FAIL"),
    ({}, {}, {"FAKE_CURL_443": "000"}, 0, "VIP 경유 readyz 응답 없음", "WARN"),
    ({}, {}, {"FAKE_CURL_443": "503"}, 1, "VIP 경유 readyz 503", "FAIL"),
    ({}, {}, {"FAKE_CURL_80": "200"}, 0, "http://portal.example/ 가 200 로 답한다", "INFO"),
    # 애드온 요약
    ({}, {"install-detect.py detect": {"rc": 3}}, {}, 0, "애드온 판정 WARN", "WARN"),
    ({}, {"install-detect.py detect": {"rc": 1}}, {}, 1, "애드온 판정 FAIL", "FAIL"),
    # 마이그레이션·워크로드
    ({"migrate": "0"}, {}, {}, 1, "migrate 미완료", "FAIL"),
    ({"workloads": dict(good_world()["workloads"], **{"dms-agent": {"ready": "1", "want": "2"}})}, {}, {}, 1, "dms-agent 1/2", "FAIL"),
])
def test_verify_verdicts(vrig, over, scenario, env, rc, needle, tag):
    r = vrig.run(good_world(**over), scenario, **env)
    assert r.returncode == rc, r.stdout
    assert any(needle in ln for ln in _lines(r, tag)), (needle, tag, r.stdout)
    want_fail = 1 if rc else 0
    assert (len(_lines(r, "FAIL")) >= 1) == bool(want_fail), r.stdout


def test_verify_eacces_hint_names_the_site(vrig):
    # V05: FAIL 이고 api·controller 로그에 EACCES 면 S2b 를 다시 — install.sh 의 기본 사이트는 prod 라 --site 를 함께(⑦ 리뷰 1).
    logs = {"deploy/dms-api": "PermissionError: [Errno 13] Permission denied: '/proc/1/root'\n"}
    r = vrig.run(good_world(migrate="0", logs=logs))
    assert r.returncode == 1 and "[HINT]" in r.stdout
    assert "sh deploy/install.sh --context known --site testbed-k8s --only S2b" in r.stdout
    r = vrig.run(good_world(migrate="0", logs=logs), rendered=False)
    assert "sh deploy/install.sh --context known --site <site> --only S2b" in r.stdout
    r = vrig.run(good_world(migrate="0"))                                         # EACCES 가 없으면 힌트 없음
    assert r.returncode == 1 and "[HINT]" not in r.stdout
    r = vrig.run(good_world(logs=logs))                                           # FAIL 이 없으면 로그를 보지 않는다
    assert r.returncode == 0 and "[HINT]" not in r.stdout


def test_verify_no_probes_starts_no_pod(vrig):
    # V06: --no-probes 는 파드를 띄우는 검사(PG 경로 대조 DS·마스터 :443 일회용 파드)를 건너뛴다 — 라이브 사이트의 순수 읽기 재검증.
    r = vrig.run(good_world(), None, "--no-probes")
    assert r.returncode == 0, r.stdout
    assert [h["name"] for h in vrig.helpers()] == ["install-detect.py"]
    assert "PG 경로 대조 건너뜀(--no-probes)" in r.stdout and "마스터 :443 건너뜀(--no-probes)" in r.stdout
    assert not [c for c in vrig.kube() if re.search(r" (create|apply|delete|patch|run) ", f" {c} ")]


# --- host-net-probe.sh(S8 마스터 sysctl ≤ 443 · S8/S12 마스터마다 :443) -------------------------------------------------------------

def _hostnet(tmp, mode, world):
    b = tmp / "hbin"
    b.mkdir(exist_ok=True)
    (b / "kubectl").write_text('#!/bin/sh\nexec python3 %s "$@"\n' % (FIX / "fake_kubectl_hostnet.py"))
    (b / "kubectl").chmod(0o755)
    wp, log = tmp / "hworld.json", tmp / "hkube.log"
    wp.write_text(json.dumps(world))
    if log.exists():
        log.unlink()
    env = {"PATH": f"{b}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp), "FAKE_WORLD": str(wp),
           "FAKE_KUBE_LOG": str(log)}
    r = subprocess.run(["sh", str(DEPLOY / "host-net-probe.sh"), "--context", "known", "--image", "reg/dms:d7", mode, "--timeout", "2"],
                       env=env, capture_output=True, text=True, timeout=60)
    return r, json.loads(wp.read_text())


@pytest.mark.parametrize("mode, values, rc, result, needle", [
    ("--sysctl", {"cp1": "443", "cp2": "443"}, 0, "PASS", "NODE cp2 PASS ip_unprivileged_port_start=443"),
    ("--sysctl", {"cp1": "0", "cp2": "443"}, 0, "PASS", "NODE cp1 PASS ip_unprivileged_port_start=0"),
    ("--sysctl", {"cp1": "443", "cp2": "1024"}, 1, "FAIL", "NODE cp2 FAIL ip_unprivileged_port_start=1024 > 443"),
    ("--sysctl", {"cp1": "443", "cp2": "444"}, 1, "FAIL", "NODE cp2 FAIL"),
    ("--sysctl", {"cp1": "443", "cp2": None}, 4, "UNKNOWN", "NODE cp2 UNKNOWN 값을 읽지 못했다"),
    ("--listen", {"cp1": "2", "cp2": "1"}, 0, "PASS", "NODE cp2 PASS :443 LISTEN(1개)"),
    ("--listen", {"cp1": "2", "cp2": "0"}, 1, "FAIL", "NODE cp2 FAIL :443 LISTEN 없음"),
    ("--listen", {"cp1": None, "cp2": "1"}, 4, "UNKNOWN", "NODE cp1 UNKNOWN"),
    ("--listen", {"cp1": "0", "cp2": None}, 1, "FAIL", "NODE cp1 FAIL"),        # FAIL 이 모름보다 앞선다
])
def test_host_net_probe_verdicts(tmp_path, mode, values, rc, result, needle):
    r, w = _hostnet(tmp_path, mode, {"nodes": "cp1,cp2", "values": values})
    assert r.returncode == rc, (r.stdout, r.stderr)
    assert r.stdout.strip().splitlines()[-1] == "RESULT: %s" % result and needle in r.stdout, r.stdout
    assert sorted(p["node"] for p in w["pods"]) == ["cp1", "cp2"]                   # 노드마다 하나(control-plane 기본)


def test_host_net_probe_image_pull_failure_is_unknown(tmp_path):
    r, _ = _hostnet(tmp_path, "--sysctl", {"nodes": "cp1", "values": {"cp1": None}, "phase": {"cp1": "Pending"},
                                           "reason": {"cp1": "ImagePullBackOff"}})
    assert r.returncode == 4 and "ImagePullBackOff" in r.stdout


def _probe_regex():
    m = re.search(r"grep -cE '([^']+)'", (DEPLOY / "host-net-probe.sh").read_text(encoding="utf-8"))
    assert m, "host-net-probe.sh 의 :443 LISTEN 정규식을 찾지 못했다"
    return m.group(1)


def test_host_net_probe_listen_regex_counts_only_443_listen_sockets(tmp_path):
    # /proc/net/tcp{,6} 실제 줄 모양(sl local rem st …) — LISTEN=0A, 443=01BB. :80 LISTEN·:443 ESTABLISHED 는 세지 않는다(H02).
    sample = "\n".join([
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode",
        "   0: 00000000:01BB 00000000:0000 0A 00000000:00000000 00:00000000 00000000 65532        0 41234 1 0000000000000000 100 0 0 10 0",
        "   1: 0A0A0A0B:01BB 0A0A0A01:C350 01 00000000:00000000 02:000A7D4A 00000000 65532        0 41235 2 0000000000000000 20 4 30 10 -1",
        "   2: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 41236 1 0000000000000000 100 0 0 10 0",
        "   3: 0100007F:2742 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 41237 1 0000000000000000 100 0 0 10 0",
        "   4: 00000000000000000000000000000000:01BB 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 "
        "00000000 65532 0 41238 1 0000000000000000 100 0 0 10 0",
        "  10: 0A0A0A0B:C350 0A0A0A01:01BB 0A 00000000:00000000 00:00000000 00000000     0        0 41239 1 0000000000000000 100 0 0 10 0",
    ]) + "\n"
    r = subprocess.run(["grep", "-cE", _probe_regex()], input=sample, capture_output=True, text=True)
    assert r.stdout.strip() == "2", r.stdout                                         # IPv4 LISTEN 1 + IPv6 LISTEN 1 (원격 443·:80·ESTABLISHED 제외)
