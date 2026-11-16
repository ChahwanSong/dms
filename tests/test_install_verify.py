"""S12 verify.sh 의 판정과 입구 프로브(ingress-probe.sh)의 판정(2026-11-03 ⑦ 리뷰 1 — 판정을 뒤집어도 통과하던 변이 V01~V06 → 2026-11-16 설계
D6·D8: Envoy Gateway 폐기, 클러스터의 Traefik 하나 — 옛 host-net-probe.sh(마스터 :443 LISTEN·sysctl) 자리에 ingress-probe.sh).

실제 sh(dash)로 스크립트를 돌린다. 클러스터는 가짜다:
  - verify.sh: PATH 앞의 kubectl 심(fixtures/install_stages/fake_kubectl_verify.py — 읽기만, 변경 동사가 오면 97)과 curl 심(FAKE_CURL_443·
    FAKE_CURL_80), 도우미(pg-path-probe.py·ingress-probe.sh·install-detect.py)는 fake_helper.py(인자·rc 기록).
  - ingress-probe.sh: kubectl 심(fixtures/install_stages/fake_kubectl_ingress.py — Traefik 파드·노드 목록)과 curl·openssl 심(노드 IP 마다 HTTP
    코드·인증서). 인증서 텍스트는 진짜 openssl 이 만든 `x509 -text` 모양이다(없으면 그 테스트는 건너뛴다) — 파싱이 실제 형식을 읽는지 본다.
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
         "tags": '{"name":"dms","tags":["d6","d7"]}', "ingress": "traefik", "readyz": '{"status":"ok","reconnects":0}', "logs": {}}
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
        (d / "ingress-probe.sh").write_text('#!/bin/sh\nexec python3 "$(CDPATH= cd "$(dirname "$0")" && pwd)/_fake_helper.py" '
                                            'ingress-probe.sh "$@"\n')
        b = tmp / "bin"
        b.mkdir()
        (b / "kubectl").write_text('#!/bin/sh\nexec python3 %s "$@"\n' % (FIX / "fake_kubectl_verify.py"))
        (b / "curl").write_text(FAKE_CURL)
        for f in b.iterdir():
            f.chmod(0o755)
        self.rendered = tmp / "rendered" / "testbed-k8s"
        self.rendered.mkdir(parents=True)
        (self.rendered / "render.env").write_text("SITE=testbed-k8s\nREGISTRY_CLUSTER_IP=10.96.0.50\nREGISTRY=10.96.0.50:5000\n"
                                                  'INGRESS_LAYERS="platform/ingress"\nINGRESS_CLASS=traefik\nTRAEFIK_ENTRYPOINT=websecure\n'
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
                   "레지스트리에 dms:d7 있음", "Service clusterIP 10.96.0.50 == REGISTRY_CLUSTER_IP",
                   "Ingress dms/dms-portal-traefik(class traefik)", "Traefik 경유 readyz 200·인증서 OK",
                   "readyz ok(apiserver 서비스 프록시", "readyz 200 via https://portal.example", "입구(LB·Traefik·TLS)까지",
                   "http://portal.example/ 응답 없음 — 80 VS 없음", "애드온 판정 PASS"):
        assert needle in r.stdout, needle
    assert [h["name"] for h in vrig.helpers()] == ["pg-path-probe.py", "ingress-probe.sh", "install-detect.py"]
    pp = vrig.helpers()[0]["argv"]
    assert pp[pp.index("--mode") + 1] == "contrast" and pp[pp.index("--namespace") + 1] == "dms"
    assert vrig.helpers()[1]["argv"] == ["--context", "known", "--domain", "portal.example", "--timeout", "5"]
    det = vrig.helpers()[2]["argv"]
    assert det[det.index("--namespace") + 1] == "dms"                               # env DMS_NS 를 읽지 않게(⑦ 리뷰 1)
    curls = vrig.paths["curl"].read_text()
    assert "--resolve portal.example:443:10.20.20.10" in curls and "--noproxy" in curls
    assert all(c.startswith("--context known ") or c.startswith("config get-contexts") for c in vrig.kube())
    # 2026-11-16: Envoy 입구의 흔적(Gateway·마스터 리스너 포트)은 없다
    for gone in ("Gateway", "LISTEN", "Envoy", "80 불가"):
        assert gone not in r.stdout, gone


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
    # 입구(2026-11-16 설계 D8 — Traefik): Ingress 존재·class, ingress-probe(UNKNOWN 은 WARN — 모름 ≠ 실패)
    ({"ingress": None}, {}, {}, 1, "Ingress dms/dms-portal-traefik 없음(S8", "FAIL"),
    ({"ingress": "nginx"}, {}, {}, 0, "class 가 nginx — 렌더는 traefik", "WARN"),
    ({"ingress": "forbidden"}, {}, {}, 0, "Ingress dms/dms-portal-traefik 를 읽지 못했다", "WARN"),
    ({}, {"ingress-probe.sh": {"rc": 1}}, {}, 1, "Traefik 경유 FAIL", "FAIL"),
    ({}, {"ingress-probe.sh": {"rc": 4}}, {}, 0, "Traefik 경유 모름", "WARN"),
    ({}, {"ingress-probe.sh": {"rc": 2}}, {}, 0, "ingress-probe 를 돌리지 못했다(rc=2", "WARN"),
    # readyz — apiserver 서비스 프록시가 판정, VIP 무응답은 Avi 미개통일 수 있어 WARN(⑦ 리뷰 1)
    ({"readyz": None}, {}, {}, 1, "readyz 실패(apiserver 서비스 프록시", "FAIL"),
    ({"readyz": '{"status":"degraded"}'}, {}, {}, 1, "readyz 가 ok 가 아니다", "FAIL"),
    ({}, {}, {"FAKE_CURL_443": "000"}, 0, "VIP 경유 readyz 응답 없음", "WARN"),
    ({}, {}, {"FAKE_CURL_443": "503"}, 1, "VIP 경유 readyz 503", "FAIL"),
    # http 80(설계 D8): 404 = Traefik web 에 DMS 라우터 없음(정상 INFO), 200 = 평문 노출(WARN), 그 밖은 INFO
    ({}, {}, {"FAKE_CURL_80": "404"}, 0, "http://portal.example/ 404 — Traefik web 진입점에 DMS 라우터 없음", "INFO"),
    ({}, {}, {"FAKE_CURL_80": "200"}, 0, "http://portal.example/ 가 200 — 포탈이 평문", "WARN"),
    ({}, {}, {"FAKE_CURL_80": "308"}, 0, "http://portal.example/ 가 308 로 답한다", "INFO"),
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


def test_verify_passes_the_ca_to_the_ingress_probe_and_skips_it_without_a_domain(vrig):
    r = vrig.run(good_world(), None, CACERT="/etc/pki/corp-ca.pem")
    probe = [h["argv"] for h in vrig.helpers() if h["name"] == "ingress-probe.sh"][0]
    assert probe[-2:] == ["--cacert", "/etc/pki/corp-ca.pem"] and r.returncode == 0, (probe, r.stdout)
    r = vrig.run(good_world(), rendered=False)                                    # PORTAL_DOMAIN 을 모른다
    assert "Traefik 경유 검사(ingress-probe) 건너뜀" in r.stdout and "ingress-probe.sh" not in [h["name"] for h in vrig.helpers()]
    assert "Ingress dms/dms-portal-traefik(class traefik)" in r.stdout              # 렌더 없이도 기본 class(traefik)로 대조


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
    # V06: --no-probes 는 파드를 띄우는 검사(PG 경로 대조 DS)를 건너뛴다 — 라이브 사이트의 순수 읽기 재검증. ingress-probe 는 파드를 띄우지 않는
    # HTTPS GET 이라 그대로 돈다(2026-11-16).
    r = vrig.run(good_world(), None, "--no-probes")
    assert r.returncode == 0, r.stdout
    assert [h["name"] for h in vrig.helpers()] == ["ingress-probe.sh", "install-detect.py"]
    assert "PG 경로 대조 건너뜀(--no-probes)" in r.stdout
    assert not [c for c in vrig.kube() if re.search(r" (create|apply|delete|patch|run) ", f" {c} ")]


# --- ingress-probe.sh(S8·S12 — Traefik 이 도는 노드마다 :443, 2026-11-16 설계 D6) ------------------------------------------------------

DOMAIN = "dms-k8s.local"
TRAEFIK_IMG = "docker.io/rancher/hardened-traefik:v3.7.4-build20260608"
# `get pods -A -o jsonpath=…` 의 줄 모양: 노드 \t hostIP \t phase \t 이미지들. Traefik(Running) cp1·w1 + 다른 파드 + 끝난 Traefik 파드.
PODS = ("k8s-cp1\t10.0.0.1\tRunning\t%s \n" % TRAEFIK_IMG
        + "k8s-w1\t10.0.0.2\tRunning\t%s \n" % TRAEFIK_IMG
        + "k8s-w1\t10.0.0.2\tRunning\t10.112.0.50:5000/dms:d26 \n"
        + "k8s-w2\t10.0.0.3\tRunning\ttraefik-mirror.corp/team/nginx:1.27 \n"              # 레지스트리 이름에만 traefik — 아니다
        + "k8s-w3\t10.0.0.4\tSucceeded\t%s \n" % TRAEFIK_IMG)
NODES = "k8s-cp1 10.0.0.1\nk8s-w1 10.0.0.2\nk8s-w2 10.0.0.3\n"

PROBE_CURL = r'''#!/bin/sh
echo "curl $*" >> "$FAKE_LOG"
url="" res=""
prev=""
for a; do [ "$prev" = --resolve ] && res=$a; prev=$a; url=$a; done
ip=${res##*:}; ip=${ip#[}; ip=${ip%]}
case "$res" in *:\[*\]) ip=${res#*:*:}; ip=${ip#[}; ip=${ip%]} ;; esac
n=0; [ -f "$FAKE_STATE/$ip.n" ] && n=$(cat "$FAKE_STATE/$ip.n"); echo $((n + 1)) > "$FAKE_STATE/$ip.n"
codes=$(sed -n "s/^$ip=//p" "$FAKE_STATE/codes" | head -n 1); codes=${codes:-000}
code=$(printf '%s' "$codes" | tr ',' '\n' | sed -n "$((n + 1))p"); [ -n "$code" ] || code=$(printf '%s' "$codes" | tr ',' '\n' | tail -n 1)
printf '%s' "$code"
[ "$code" != 000 ] || exit "${FAKE_CURL_RC:-7}"
case "$code" in 000) ;; *) [ -z "${FAKE_CURL_RC_OK:-}" ] || exit "$FAKE_CURL_RC_OK" ;; esac
exit 0
'''

PROBE_OPENSSL = r'''#!/bin/sh
echo "openssl $*" >> "$FAKE_LOG"
case "$1" in
  s_client)
    hp="" sn=""
    while [ $# -gt 0 ]; do case "$1" in -connect) hp=$2; shift ;; -servername) sn=$2; shift ;; esac; shift; done
    ip=${hp%:*}; ip=${ip#[}; ip=${ip%]}
    echo "SNI=$sn CONNECT=$hp" >> "$FAKE_LOG"
    [ -f "$FAKE_CERTS/$ip" ] && printf 'IP=%s\n' "$ip"
    exit 0 ;;
  x509)
    ip=$(sed -n 's/^IP=//p')
    [ -n "$ip" ] && cat "$FAKE_CERTS/$ip"
    exit 0 ;;
esac
exit 1
'''


@pytest.fixture(scope="module")
def cert_texts(tmp_path_factory):
    """진짜 openssl 로 만든 인증서의 `x509 -noout -text` 출력(형식이 실제와 같게) — 없으면 이 절의 테스트를 건너뛴다."""
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl 없음")
    d = tmp_path_factory.mktemp("certs")
    out = {}
    for key, subj, san in (("ok", "/O=DMS/CN=%s" % DOMAIN, "DNS:%s,IP:10.20.20.10" % DOMAIN),     # SAN = PORTAL_DOMAIN + PORTAL_VIP
                           ("default", "/CN=TRAEFIK DEFAULT CERT", "DNS:0a1b2c3d4e.5f6a7b8c9d.traefik.default"),
                           ("other", "/CN=other.example", "DNS:other.example,DNS:www.other.example"),
                           ("wild", "/CN=*.corp.example", "DNS:*.corp.example")):
        crt = d / (key + ".crt")
        subprocess.run([openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                        "-keyout", str(d / (key + ".key")), "-out", str(crt), "-days", "1", "-subj", subj, "-addext",
                        "subjectAltName=" + san], check=True, capture_output=True, timeout=60)
        out[key] = subprocess.run([openssl, "x509", "-noout", "-text", "-in", str(crt)], check=True, capture_output=True,
                                  text=True, timeout=60).stdout
    return out


class PRig:
    def __init__(self, tmp, cert_texts):
        self.tmp, self.certs = tmp, cert_texts
        b = tmp / "pbin"
        b.mkdir()
        (b / "kubectl").write_text('#!/bin/sh\nexec python3 %s "$@"\n' % (FIX / "fake_kubectl_ingress.py"))
        (b / "curl").write_text(PROBE_CURL)
        (b / "openssl").write_text(PROBE_OPENSSL)
        for f in b.iterdir():
            f.chmod(0o755)
        self.bin = b

    def run(self, *args, world=None, certs=None, codes=None, **env):
        state = self.tmp / "state"
        cdir = self.tmp / "certdir"
        for d in (state, cdir):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
        for ip, key in (certs or {}).items():
            (cdir / ip).write_text(self.certs[key])
        (state / "codes").write_text("".join("%s=%s\n" % kv for kv in (codes or {}).items()))
        wp, klog, log = self.tmp / "pworld.json", self.tmp / "pkube.log", self.tmp / "ptools.log"
        for p in (klog, log):
            if p.exists():
                p.unlink()
        wp.write_text(json.dumps(world if world is not None else {"pods": PODS, "nodes": NODES}))
        e = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(self.tmp), "FAKE_WORLD": str(wp),
             "FAKE_KUBE_LOG": str(klog), "FAKE_LOG": str(log), "FAKE_STATE": str(state), "FAKE_CERTS": str(cdir), "PROBE_INTERVAL": "0"}
        e.update(env)
        r = subprocess.run(["sh", str(DEPLOY / "ingress-probe.sh"), *args], env=e, capture_output=True, text=True, timeout=60)
        r.kube = klog.read_text().splitlines() if klog.exists() else []
        r.tools = log.read_text().splitlines() if log.exists() else []
        r.nodes = {ln.split()[1]: ln for ln in r.stdout.splitlines() if ln.startswith("NODE ")}
        return r


@pytest.fixture
def prig(tmp_path, cert_texts):
    return PRig(tmp_path, cert_texts)


BASE = ("--context", "known", "--domain", DOMAIN, "--timeout", "0")


def test_ingress_probe_all_traefik_nodes_pass(prig):
    r = prig.run(*BASE, certs={"10.0.0.1": "ok", "10.0.0.2": "ok"}, codes={"10.0.0.1": "200", "10.0.0.2": "200"})
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "RESULT: PASS", (r.stdout, r.stderr)
    # 대상 = Running Traefik 파드의 hostIP(이미지 이름 기준 — 레지스트리 이름만 traefik 인 파드·끝난 파드는 아니다), 노드 이름으로 표시
    assert sorted(r.nodes) == ["k8s-cp1", "k8s-w1"], r.stdout
    assert "NODE k8s-cp1 PASS 10.0.0.1:443 readyz 200 · 인증서 OK(dms-k8s.local" in r.stdout, r.stdout
    curls = [t for t in r.tools if t.startswith("curl ")]
    assert all("--noproxy *" in c and " -k " in c and "--resolve %s:443:" % DOMAIN in c for c in curls), curls
    assert any(c.endswith("https://%s/readyz" % DOMAIN) for c in curls)
    assert "SNI=%s CONNECT=10.0.0.1:443" % DOMAIN in r.tools
    assert r.kube and all(c.startswith("--context known ") or c.startswith("config get-contexts") for c in r.kube), r.kube
    assert not [c for c in r.kube if re.search(r" (create|apply|delete|patch|run|label|annotate) ", f" {c} ")]


@pytest.mark.parametrize("cert, code, verdict, rc, needle", [
    ("ok", "404", "FAIL", 1, "readyz 404 — Traefik 이 Ingress 를 안 집었다(ingressClassName·router.entrypoints·ns)"),
    ("default", "404", "FAIL", 1, "TRAEFIK DEFAULT CERT — Traefik 에 SNI dms-k8s.local 의 인증서가 없다"),
    ("default", "200", "FAIL", 1, "TRAEFIK DEFAULT CERT"),                         # 라우팅은 Host 로 되어도 인증서가 기본이면 FAIL
    ("other", "200", "FAIL", 1, "인증서 주체/SAN 에 dms-k8s.local 이 없다(other.example www.other.example)"),
    ("ok", "503", "FAIL", 1, "readyz 503 — 라우터는 있는데 백엔드(dms-api)가 503"),
    (None, "000", "UNKNOWN", 4, "연결 실패(설치 호스트 → 노드 :443 — 방화벽이거나 그 노드에 Traefik 이 없다"),
])
def test_ingress_probe_verdicts(prig, cert, code, verdict, rc, needle):
    r = prig.run(*BASE, "--nodes", "10.0.0.1", certs={"10.0.0.1": cert} if cert else {}, codes={"10.0.0.1": code})
    assert r.returncode == rc and r.stdout.strip().splitlines()[-1] == "RESULT: %s" % verdict, (r.stdout, r.stderr)
    assert r.nodes["10.0.0.1"].startswith("NODE 10.0.0.1 %s 10.0.0.1:443 " % verdict) and needle in r.nodes["10.0.0.1"], r.stdout
    assert [c for c in r.kube if not c.startswith("config get-contexts")] == []        # --nodes 면 파드·노드를 읽지 않는다


def test_ingress_probe_fail_beats_unknown(prig):
    r = prig.run(*BASE, "--nodes", "10.0.0.1,10.0.0.2", certs={"10.0.0.1": "default"}, codes={"10.0.0.1": "404", "10.0.0.2": "000"})
    assert r.returncode == 1 and "NODE 10.0.0.2 UNKNOWN" in r.stdout and "NODE 10.0.0.1 FAIL" in r.stdout, r.stdout


@pytest.mark.parametrize("cert, code, verdict", [("ok", "404", "PASS"), ("ok", "200", "PASS"), ("default", "404", "FAIL"),
                                                 (None, "000", "UNKNOWN")])
def test_ingress_probe_tls_only_judges_the_certificate(prig, cert, code, verdict):
    # 첫 설치(앱 전): Traefik 은 백엔드 없는 라우터를 만들지 않아 readyz 가 404 — 인증서만 판정(Traefik 은 class 가 맞는 Ingress 의 tls 를 읽는다).
    r = prig.run(*BASE, "--nodes", "10.0.0.1", "--tls-only", certs={"10.0.0.1": cert} if cert else {}, codes={"10.0.0.1": code})
    assert r.nodes["10.0.0.1"].split()[2] == verdict, r.stdout
    if verdict == "PASS":
        assert "--tls-only — 판정 밖" in r.nodes["10.0.0.1"]


@pytest.mark.parametrize("domain, verdict", [("dms.corp.example", "PASS"), ("a.dms.corp.example", "FAIL"), ("corp.example", "FAIL")])
def test_ingress_probe_wildcard_matches_one_label_only(prig, domain, verdict):
    r = prig.run("--context", "known", "--domain", domain, "--timeout", "0", "--nodes", "10.0.0.1", certs={"10.0.0.1": "wild"},
                 codes={"10.0.0.1": "200"})
    assert r.nodes["10.0.0.1"].split()[2] == verdict, r.stdout


def test_ingress_probe_with_a_ca_lets_curl_verify_the_chain(prig):
    r = prig.run(*BASE, "--nodes", "10.0.0.1", "--cacert", str(prig.tmp / "pbin" / "curl"), certs={"10.0.0.1": "ok"},
                 codes={"10.0.0.1": "200"})
    curls = [t for t in r.tools if t.startswith("curl ")]
    assert r.returncode == 0 and all("--cacert" in c and " -k " not in f"{c} " for c in curls), (curls, r.stdout)
    # CA 가 이 인증서를 서명하지 않았으면(curl rc 60 — 응답 000) 연결 문제가 아니라 FAIL
    r = prig.run(*BASE, "--nodes", "10.0.0.1", "--cacert", str(prig.tmp / "pbin" / "curl"), certs={"10.0.0.1": "ok"},
                 codes={"10.0.0.1": "000"}, FAKE_CURL_RC="60")
    assert r.returncode == 1 and "TLS 검증 실패(curl rc 60" in r.stdout, r.stdout
    # CA 없이 같은 실패(-k 라 일어나지 않지만, 응답이 없으면) → 모름
    r = prig.run(*BASE, "--nodes", "10.0.0.1", certs={"10.0.0.1": "ok"}, codes={"10.0.0.1": "000"}, FAKE_CURL_RC="28")
    assert r.returncode == 4 and "readyz 연결 실패(curl rc 28) · 인증서 OK" in r.stdout, r.stdout


def test_ingress_probe_unread_certificate_is_not_blamed_on_a_missing_openssl(prig):
    # openssl 은 있는데 s_client 가 인증서를 내지 못했고(8초 시간 초과·핸드셰이크 — 가짜 openssl 은 인증서 파일이 없는 IP 에 빈 출력) curl 은
    # 200: 신원을 못 봤으니 모름(UNKNOWN)이되, 근거가 「openssl 없음」이면 안 된다(운영자가 openssl 설치로 오독하고 실제 원인을 놓친다).
    for extra in ((), ("--tls-only",)):
        r = prig.run(*BASE, "--nodes", "10.0.0.1", *extra, codes={"10.0.0.1": "200"})
        line = r.nodes["10.0.0.1"]
        assert r.returncode == 4 and line.startswith("NODE 10.0.0.1 UNKNOWN 10.0.0.1:443 ") and "readyz 200" in line, (extra, r.stdout)
        assert "인증서를 읽지 못했다(openssl s_client 응답 없음 — 시간 초과·핸드셰이크" in line and "openssl 없음" not in line, line
        assert "SNI=%s CONNECT=10.0.0.1:443" % DOMAIN in r.tools, r.tools
    # --cacert 면 curl 이 체인·호스트 이름까지 검증하고 응답을 받았다 — PASS
    r = prig.run(*BASE, "--nodes", "10.0.0.1", "--cacert", str(prig.tmp / "pbin" / "curl"), codes={"10.0.0.1": "200"})
    assert r.returncode == 0 and "인증서 OK(--cacert 로 curl 이 검증)" in r.nodes["10.0.0.1"], r.stdout


def test_ingress_probe_without_openssl_says_so(prig):
    # openssl 이 PATH 에 없을 때만 근거가 「openssl 없음」이다(위 경우와 갈린다). PATH = 가짜 curl·kubectl + 스크립트·심이 쓰는 도구(openssl 뺌).
    tools = prig.tmp / "nossl"
    tools.mkdir()
    for name in ("curl", "kubectl"):
        (tools / name).symlink_to(prig.bin / name)
    for name in ("sh", "awk", "sort", "tr", "date", "grep", "sleep", "timeout", "cat", "sed", "head", "tail", "python3"):
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    r = prig.run(*BASE, "--nodes", "10.0.0.1", codes={"10.0.0.1": "200"}, PATH=str(tools))
    line = r.nodes["10.0.0.1"]
    assert r.returncode == 4 and line.startswith("NODE 10.0.0.1 UNKNOWN 10.0.0.1:443 readyz 200 · "), (r.stdout, r.stderr)
    assert "인증서를 확인하지 못했다(openssl 없음 — --cacert 를 주면 curl 이 검증한다)" in line and "s_client" not in line, line
    assert not [t for t in r.tools if t.startswith("openssl ")] and [t for t in r.tools if t.startswith("curl ")], r.tools


def test_ingress_probe_retries_only_failures_until_the_timeout(prig):
    # Traefik 이 막 적용된 Ingress 를 집는 몇 초 — 404 다음 200 이면 PASS. 연결 실패(UNKNOWN)는 기다려도 같아 다시 재지 않는다.
    r = prig.run("--context", "known", "--domain", DOMAIN, "--timeout", "20", "--nodes", "10.0.0.1,10.0.0.2",
                 certs={"10.0.0.1": "ok"}, codes={"10.0.0.1": "404,404,200", "10.0.0.2": "000"})
    assert r.returncode == 4 and "NODE 10.0.0.1 PASS" in r.stdout and "NODE 10.0.0.2 UNKNOWN" in r.stdout, r.stdout
    assert (prig.tmp / "state" / "10.0.0.1.n").read_text().strip() == "3"
    assert (prig.tmp / "state" / "10.0.0.2.n").read_text().strip() == "1"


def test_ingress_probe_falls_back_to_every_node_and_reports_no_nodes_as_unknown(prig):
    r = prig.run(*BASE, world={"pods": "k8s-w1\t10.0.0.2\tRunning\t10.112.0.50:5000/dms:d26 \n", "nodes": NODES},
                 certs={"10.0.0.1": "ok"}, codes={"10.0.0.1": "200"})
    assert "INFO Traefik 파드(이미지 이름에 traefik — Running)를 찾지 못했다 — 노드 InternalIP 전부" in r.stdout, r.stdout
    assert sorted(r.nodes) == ["k8s-cp1", "k8s-w1", "k8s-w2"] and r.returncode == 4      # Traefik 없는 노드는 연결 실패 = 모름
    r = prig.run(*BASE, world={"pods": "", "nodes": "", "fail": ["pods"]})
    assert r.returncode == 4 and r.stdout.strip().splitlines()[-1] == "RESULT: UNKNOWN" and "--nodes" in r.stderr, (r.stdout, r.stderr)


def test_ingress_probe_ipv6_node_and_other_port(prig):
    r = prig.run(*BASE, "--nodes", "fd00::1", "--port", "8443", certs={"fd00::1": "ok"}, codes={"fd00::1": "200"})
    assert r.returncode == 0, (r.stdout, r.stderr)
    curl = next(t for t in r.tools if t.startswith("curl "))
    assert "--resolve %s:8443:[fd00::1]" % DOMAIN in curl and curl.endswith("https://%s:8443/readyz" % DOMAIN), curl
    assert "SNI=%s CONNECT=[fd00::1]:8443" % DOMAIN in r.tools


@pytest.mark.parametrize("args", [
    ["--context", "known"],                                                    # --domain 없음
    ["--domain", DOMAIN],                                                      # --context 없음
    ["--context", "known", "--domain", "*.corp.example"],                      # 와일드카드
    ["--context", "known", "--domain", "dms.example:443"],                     # 포트
    ["--context", "known", "--domain", "10.20.20.10"],                         # IP
    ["--context", "known", "--domain", DOMAIN, "--port", "0"],
    ["--context", "known", "--domain", DOMAIN, "--timeout", "x"],
    ["--context", "known", "--domain", DOMAIN, "--nodes", "k8s-w1"],           # curl --resolve 는 숫자 주소만
    ["--context", "known", "--domain", DOMAIN, "--cacert", "/nonexistent/ca.pem"],
    ["--context", "nope", "--domain", DOMAIN],                                 # kubeconfig 에 없는 컨텍스트
    ["--context", "known", "--domain", DOMAIN, "--bogus"],
])
def test_ingress_probe_usage_errors_stop_before_the_cluster(prig, args):
    r = prig.run(*args)
    assert r.returncode == 2, (args, r.stdout, r.stderr)
    assert [c for c in r.kube if not c.startswith("config get-contexts")] == [] and not [t for t in r.tools if t.startswith("curl ")]
