"""설치기 단계(deploy/install.sh — 2026-11-03 ⑦, 설계 v6 8-1 S1~S13·8-2): 단계 순서, S4 뒤 멈춤(PAUSE)과 `--stage S5` 재개, S5 대조 FAIL,
S7 종료 코드 처리, in-cluster 대 external, 멱등 재실행, `--plan` 무변경, C 애드온 비적용, 감지 FAIL 정지, 설정 해시 재시작, --context 필수.
리뷰 1(2026-11-03): 단계별 멈춤 조건 표(STOPS — 판정·사유 문구·다음 단계 없음·적용되면 안 되는 층), S10 「사전 검사 → Cluster」 순서, migrate
선삭제 순서, 재개 명령의 플래그·env, env DMS_NS 차단, 가드 allow-image(레지스트리·CNPG), --adopt db 의 S9 dry-run, 빈 --stage·--only, S12 의
verify 인자·FAIL 뒤 S13, S13 첫 관리자. verify.sh·host-net-probe.sh 의 판정 자체는 test_install_verify.py.

실제 sh(dash)로 install.sh 를 돌린다. 클러스터·도우미는 전부 가짜다:
  - PATH 앞의 kubectl 심(fixtures/install_stages/fake_kubectl.py) — 세계 JSON 을 읽고 고쳐 써서 apply·create·annotate 가 다음 실행에 보인다.
  - deploy/ 의 도우미(install-detect.py·pg-secrets.py·…·push-images.sh·verify.sh·host-net-probe.sh·guard-images.sh)는 같은 가짜
    (fixtures/install_stages/fake_helper.py)로 바꿔 놓고 인자·모드를 기록한다 — 판정 로직 자체는 각 도우미의 테스트가 본다.
  - render.sh 도 가짜(층 디렉터리와 render.env 만) — 렌더 계약은 test_platform_registry_contract·test_addons_render 가 본다.
  - curl 심(레지스트리 HEAD — FAKE_CURL_404 의 repo:tag 만 404, `/v2/` 는 FAKE_CURL_V2(기본 200)).
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
PY_HELPERS = ("install-detect.py", "pg-secrets.py", "selinux-probe.py", "pull-probe.py", "shared-storage-probe.py",
              "pg-path-probe.py", "images-lock.py")
SH_HELPERS = ("preflight-cluster.sh", "verify.sh", "push-images.sh", "host-net-probe.sh", "overlays/guard-images.sh")
ALL = ["S1", "S2", "S2b", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S9b", "S10", "S11", "S12", "S13"]

FAKE_RENDER = r'''#!/bin/sh
echo "$*" >> "$FAKE_RENDER_LOG"
site=$1
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
OUT="$HERE/.rendered/$site"
for d in app platform/priorityclass platform/registry platform/gateway platform/db-volumes platform/db-cluster platform/db-backup \
         platform/db-probe addons/cnpg addons/gateway-api addons/envoy-gateway; do mkdir -p "$OUT/$d"; done
{ echo "SITE=$site"; echo "REGISTRY_MODE=${FAKE_RMODE:-in-cluster}"; echo "REGISTRY=${FAKE_REGISTRY:-10.96.0.50:5000}"
  echo "REGISTRY_CLUSTER_IP=${FAKE_RCIP-10.96.0.50}"; echo "REGISTRY_PORT=5000"; echo "REGISTRY_TLS_VERIFY=${FAKE_TLS:-false}"
  echo "ADDON_REGISTRY=${FAKE_ADDON_REGISTRY:-10.96.0.50:5000}"; echo "SHARED_DIR=/srv/shared"; echo "PORTAL_TLS_SECRET=dms-portal-tls"
  echo "PORTAL_DOMAIN="; echo "PORTAL_VIP="; } > "$OUT/render.env"
[ -z "${FAKE_PIN:-}" ] || echo "$FAKE_PIN" > "$OUT/app/kube-context"
echo "PLATFORM: $OUT/platform/priorityclass"
echo "RENDERED: $OUT/app"
'''

FAKE_CURL = r'''#!/bin/sh
echo "$*" >> "$FAKE_CURL_LOG"
for a; do url=$a; done
case "$url" in */v2/) printf "${FAKE_CURL_V2:-200}"; exit 0 ;; esac
p=${url#*/v2/}; repo=${p%%/manifests/*}; tag=${p##*/manifests/}
case " ${FAKE_CURL_404:-} " in *" $repo:$tag "*) printf 404 ;; *) printf 200 ;; esac
'''

APP = ["10.96.0.50:5000/dms:d7", "10.96.0.50:5000/dms-agent:d7", "10.96.0.50:5000/dms-mpifileutils:d7"]


def installed_world(**over):
    w = {"contexts": ["known"], "ns": ["dms", "cnpg-system", "envoy-gateway-system"],
         "secrets": {"dms/dms-secrets": {"rv": "10"}, "dms/dms-pg-app": {}, "dms/dms-portal-tls": {"type": "kubernetes.io/tls"},
                     "dms/dms-registry-http": {}, "envoy-gateway-system/envoy-gateway": {}, "envoy-gateway-system/envoy": {}},
         "cm_rv": "5", "gen": {"deploy/dms-api": "3", "deploy/dms-controller": "3", "ds/dms-agent": "2"},
         "annotations": {w: "cm=5,secret=10" for w in ("deploy/dms-api", "deploy/dms-controller", "ds/dms-agent")},
         "cronjob_last": "2026-11-03T07:17:04Z", "jobs": {}, "cluster": {"instances": "3", "ready": "3", "primary": "dms-pg-1"},
         "api_image": APP[0], "masters": "cp1", "app_images": list(APP)}
    w.update(over)
    return w


def fresh_world(**over):
    w = {"contexts": ["known"], "ns": [], "secrets": {"dms/dms-portal-tls": {"type": "kubernetes.io/tls"}}, "cm_rv": "1",
         "gen": {}, "annotations": {}, "cronjob_last": "", "jobs": {}, "cluster": None, "api_image": APP[0], "masters": "cp1",
         "app_images": list(APP)}
    w.update(over)
    return w


FRESH_DETECT = {"priorityclass": "A install 1 PASS", "registry": "A install 1 PASS", "gateway-api": "A install 1 PASS",
                "envoy-gateway": "A install 1 PASS", "gateway": "A install 1 PASS", "cnpg": "A install 1 PASS",
                "volcano": "C reuse 0 PASS", "db": "A install 1 PASS"}


class Rig:
    def __init__(self, tmp_path, real_render=False):
        self.tmp = tmp_path
        d = tmp_path / "deploy"
        if real_render:
            # 실제 렌더러(render.sh)와 오버레이·base·층 — render.env 의 키·RENDERED 줄을 설치기가 그대로 읽는지(명시 태그라 kubectl 을 부르지 않는다).
            d.mkdir(parents=True)
            for sub in ("overlays", "k8s", "platform", "addons"):
                shutil.copytree(DEPLOY / sub, d / sub, ignore=shutil.ignore_patterns("values.env", "__pycache__"))
            shutil.copy(DEPLOY / "overlays" / "testbed-k8s" / "values.env", d / "overlays" / "testbed-k8s" / "values.env")
            for name in ("render.sh", "versions.env"):
                shutil.copy(DEPLOY / name, d / name)
        else:
            (d / "overlays").mkdir(parents=True)
            (d / "k8s").mkdir()
            shutil.copy(DEPLOY / "k8s" / "00-namespace.yaml", d / "k8s" / "00-namespace.yaml")
            (d / "render.sh").write_text(FAKE_RENDER)
        shutil.copy(DEPLOY / "install.sh", d / "install.sh")
        shutil.copy(DEPLOY / "images.lock", d / "images.lock")
        shutil.copy(FIX / "fake_helper.py", d / "_fake_helper.py")
        for n in PY_HELPERS:
            (d / n).write_text("import os, runpy, sys\nsys.argv = [sys.argv[0], %r] + sys.argv[1:]\n"
                               "runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_fake_helper.py'), "
                               "run_name='__main__')\n" % n)
        for n in SH_HELPERS:
            up = "../" * n.count("/")
            (d / n).write_text('#!/bin/sh\nexec python3 "$(CDPATH= cd "$(dirname "$0")" && pwd)/%s_fake_helper.py" %s "$@"\n'
                               % (up, n.rsplit("/", 1)[-1]))
        b = tmp_path / "bin"
        b.mkdir()
        (b / "kubectl").write_text('#!/bin/sh\nexec python3 %s "$@"\n' % (FIX / "fake_kubectl.py"))
        (b / "curl").write_text(FAKE_CURL)
        for f in b.iterdir():
            f.chmod(0o755)
        self.deploy = d
        self.world_path = tmp_path / "world.json"
        self.scenario_path = tmp_path / "scenario.json"
        self.logs = {k: tmp_path / f"{k}.log" for k in ("kube", "helper", "render", "curl")}
        self.scenario({})

    def world(self, w=None):
        if w is not None:
            self.world_path.write_text(json.dumps(w))
        return json.loads(self.world_path.read_text())

    def scenario(self, s):
        self.scenario_path.write_text(json.dumps(s))

    def reset_logs(self):
        for p in self.logs.values():
            if p.exists():
                p.unlink()

    def run(self, *args, **env):
        e = {"PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(self.tmp),
             "FAKE_WORLD": str(self.world_path), "FAKE_SCENARIO": str(self.scenario_path),
             "FAKE_KUBE_LOG": str(self.logs["kube"]), "FAKE_HELPER_LOG": str(self.logs["helper"]),
             "FAKE_RENDER_LOG": str(self.logs["render"]), "FAKE_CURL_LOG": str(self.logs["curl"]),
             "POLL_INTERVAL": "0", "DB_WAIT_SECONDS": "2", "PYTHONDONTWRITEBYTECODE": "1"}
        e.update(env)
        r = subprocess.run(["sh", str(self.deploy / "install.sh"), *args], env=e, capture_output=True, text=True, timeout=120)
        r.all = r.stdout + r.stderr
        return r

    def kube(self):
        p = self.logs["kube"]
        return p.read_text().splitlines() if p.exists() else []

    def helpers(self):
        p = self.logs["helper"]
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def helper_calls(self, name):
        return [h["argv"] for h in self.helpers() if h["name"] == name]


def stages(r):
    return re.findall(r"(?m)^STAGE: (\S+) (\S+)$", r.stdout)


def cluster_calls(rig):
    return [c for c in rig.kube() if not c.startswith("config get-contexts")]


def assert_context(rig, ctx="known"):
    calls = cluster_calls(rig)
    bad = [c for c in calls if not c.startswith(f"--context {ctx} ")]
    assert not bad, bad


MUTATING = re.compile(r"(^| )(apply|create|delete|annotate|patch|label|replace|scale|set|edit)( |$)|rollout restart")


def mutations(rig):
    return [c for c in cluster_calls(rig) if MUTATING.search(c) and "--dry-run=server" not in c]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# --- --context 필수 ------------------------------------------------------------------------------------------------------------

def test_context_is_mandatory_and_nothing_touches_a_cluster_without_it(rig):
    rig.world(installed_world())
    r = rig.run("--site", "testbed-k8s", KUBE_CONTEXT="known")      # env 만으로는 안 된다(설치기는 명시 플래그)
    assert r.returncode == 2 and "--context" in r.stderr, r.all
    assert rig.kube() == [] and rig.helpers() == []


def test_unknown_context_stops_before_touching_a_cluster(rig):
    rig.world(installed_world())
    r = rig.run("--context", "nope")
    assert r.returncode == 2 and "nope" in r.stderr
    assert cluster_calls(rig) == []


def test_render_pinned_to_another_context_is_refused(rig):
    rig.world(installed_world())
    r = rig.run("--context", "known", "--only", "S1", FAKE_PIN="dmsk8s")
    assert r.returncode == 2 and "dmsk8s" in r.all
    assert mutations(rig) == []


@pytest.mark.parametrize("bad", [["--context", "known", "--plan", "--stage", "S5"], ["--context", "known", "--stage", "S99"],
                                 ["--context", "known", "--only", "S3,S77"], ["--context", "known", "--dry-run"],
                                 ["--context", "known", "--stage", "S5", "--only", "S6"],
                                 ["--context", "known", "--upgrade-addon", "Bad Name"],
                                 # 빈 값(2026-11-03 ⑦ 리뷰 1 — `--only ""` 는 S1~S13 전부(변경), `--only ,` 는 아무것도 안 하고 PASS 였다)
                                 ["--context", "known", "--only", ""], ["--context", "known", "--stage", ""],
                                 ["--context", "known", "--only", ","], ["--context", "known", "--only=,"], ["--context", "known", "--only="],
                                 ["--context", "known", "--stage="], ["--context", "known", "--plan", "--only", ","]])
def test_usage_errors_exit_2_before_touching_a_cluster(rig, bad):
    rig.world(installed_world())
    r = rig.run(*bad)
    assert r.returncode == 2, (bad, r.all)
    assert cluster_calls(rig) == []


# --- 단계 순서 · S4 뒤 멈춤 · --stage S5 재개 ------------------------------------------------------------------------------------

def test_fresh_install_runs_s1_to_s4_then_pauses_with_push_commands(rig):
    rig.world(fresh_world())
    rig.scenario({"detect": FRESH_DETECT, "images-lock.py compare-registry": {"rc": 1},
                  "pg-secrets.py init-secrets": {"add_secrets": ["dms/dms-secrets"]},
                "pg-secrets.py ensure-app": {"add_secrets": ["dms/dms-pg-app"]}})
    r = rig.run("--context", "known", "--site", "testbed-k8s")
    assert r.returncode == 10, r.all
    assert stages(r) == [("S1", "PASS"), ("S2", "PASS"), ("S2b", "PASS"), ("S3", "PASS"), ("S4", "PASS"), ("S5", "PAUSE")]
    assert r.stdout.strip().splitlines()[-1] == "RESULT: PAUSE"
    assert "docker://10.96.0.50:5000/dest:tag" in r.stdout                      # push-images.sh --print 출력(레지스트리 ClusterIP)
    assert "--stage S5" in r.stdout
    assert rig.helper_calls("push-images.sh") == [["--registry", "10.96.0.50:5000", "--print"]]   # A 경로 — lock 전부
    assert rig.helper_calls("pull-probe.py") == []                              # S6 는 아직
    calls = cluster_calls(rig)
    # S2: ns → 시크릿, S4: PriorityClass SSA → http 시크릿 → 레지스트리 SSA → rollout → port-forward
    order = [next(i for i, c in enumerate(calls) if pat in c) for pat in (
        "apply -f", "apply --server-side --field-manager=dms-install -k", "create secret generic dms-registry-http",
        "platform/registry", "rollout status deploy/dms-registry", "port-forward svc/dms-registry")]
    assert order == sorted(order), calls
    assert any("platform/priorityclass" in c and "--server-side" in c for c in calls)
    secret = next(c for c in calls if "dms-registry-http" in c and "create" in c)
    assert "--from-file=http-secret=/dev/stdin" in secret and rig.world()["stdin_lengths"] == [64]   # 값은 argv 에 없다
    pg = [a[-1] for a in rig.helper_calls("pg-secrets.py")]
    assert pg[:3] == ["ensure-app", "init-secrets", "check"]
    assert_context(rig)


def test_resume_with_stage_s5_continues_to_the_end(rig):
    rig.world(fresh_world())
    rig.scenario({"detect": FRESH_DETECT, "images-lock.py compare-registry": {"rc": [1, 0]},
                  "pg-secrets.py init-secrets": {"add_secrets": ["dms/dms-secrets"]},
                "pg-secrets.py ensure-app": {"add_secrets": ["dms/dms-pg-app"]}})
    assert rig.run("--context", "known", "--site", "testbed-k8s").returncode == 10
    rig.reset_logs()
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--stage", "S5")
    assert r.returncode == 0, r.all
    assert [s for s, _ in stages(r)] == ALL[ALL.index("S5"):]
    assert rig.helper_calls("selinux-probe.py") == [] and rig.helper_calls("preflight-cluster.sh") == []   # 앞 단계는 다시 돌지 않는다
    w = rig.world()
    assert "dms-pg-dump-install" in w["created_jobs"]                           # 첫 덤프(성공한 덤프가 아직 없다)
    assert any(p.endswith("/app") for p in w["applied"])
    assert w["annotations"]["deploy/dms-api"] == "cm=%s,secret=1" % w["cm_rv"]
    modes = [a[a.index("--mode") + 1] for a in rig.helper_calls("pg-path-probe.py")]
    assert modes == ["pv-check", "precheck", "contrast"]
    applied = [p.split("/.rendered/testbed-k8s/")[-1] for p in w["applied"]]
    # 설치 순서: 입구 → CNPG → DB 층(volumes → cluster → backup) → 앱
    order = ["addons/gateway-api", "addons/envoy-gateway", "platform/gateway", "addons/cnpg", "platform/db-volumes",
             "platform/db-cluster", "platform/db-backup", "app"]
    assert [p for p in applied if p in order] == order, applied
    pp = rig.helper_calls("pull-probe.py")
    assert pp[0][:6] == ["--context", "known", "--namespace", "dms", "--render-env", pp[0][5]] and pp[0][5].endswith("render.env")
    assert "--nodes" in pp[-1] and pp[-1][pp[-1].index("--nodes") + 1] == "cp1"     # S11 마스터 사전 pull
    assert pp[-1][pp[-1].index("--image") + 1] == APP[0]
    assert_context(rig)


def test_rerun_on_an_installed_cluster_is_idempotent(rig):
    rig.world(installed_world())
    for _ in range(2):
        rig.reset_logs()
        r = rig.run("--context", "known", "--site", "testbed-k8s")
        assert r.returncode == 0, r.all
        assert [s for s, _ in stages(r)] == ALL and r.stdout.strip().endswith("RESULT: PASS")
        muts = mutations(rig)
        assert not [c for c in muts if " create " in f" {c} "], muts                # 시크릿·Job·ns 를 다시 만들지 않는다
        assert not [c for c in muts if "rollout restart" in c], muts                # 설정이 그대로면 재시작 없음
        assert not [c for c in muts if "/addons/" in c], muts                       # D= 서드파티는 no-op
        ssa = sorted(c.split("/.rendered/testbed-k8s/")[-1] for c in muts if "--server-side" in c)
        assert ssa == ["platform/db-backup", "platform/db-cluster", "platform/db-volumes", "platform/gateway",
                       "platform/priorityclass", "platform/registry"], ssa
        assert all("--field-manager=dms-install" in c for c in muts if "--server-side" in c)
        app = [c for c in muts if c.endswith("/app") and " apply " in f" {c} "]
        assert len(app) == 1 and "--server-side" not in app[0]                      # 앱은 client-side
        assert rig.world().get("created_jobs") is None
        assert_context(rig)


# --- S5 대조 ---------------------------------------------------------------------------------------------------------------------

def test_only_s5_with_a_lock_mismatch_fails_instead_of_pausing(rig):
    rig.world(installed_world())
    rig.scenario({"images-lock.py compare-registry": {"rc": 1, "out": "FAIL cloudnative-pg:1.30.1 태그가 다른 digest"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5")
    assert r.returncode == 1, r.all
    assert stages(r) == [("S5", "FAIL")] and "RESULT: FAIL" in r.stdout and "PAUSE" not in r.stdout
    assert "skopeo copy" in r.stdout                                            # 고치는 길(push 명령)을 같이 보인다
    cr = rig.helper_calls("images-lock.py")
    assert cr and cr[0][:3] == ["compare-registry", "--registry-url", "http://127.0.0.1:41234"]   # port-forward 로
    assert any("port-forward svc/dms-registry :5000" in c for c in cluster_calls(rig))
    # port-forward 는 설치기가 끝나면 남지 않는다(2026-11-03 ⑦ 실측 — 함수를 & 로 돌리면 kill 이 서브셸만 죽였다).
    pids = [int(p) for p in Path(str(rig.world_path) + ".pf-pids").read_text().split()]
    assert pids
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_s5_fails_when_a_dms_image_tag_is_missing_from_the_registry(rig):
    # 옛 install.sh 의 in-cluster 게이트(2026-11-03 ④ 리뷰 2)를 S5·S11 로 옮겼다 — S5 미완이면 apply 전에 한 줄 사유로 멈춘다.
    rig.world(installed_world())
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5", FAKE_CURL_404="dms-agent:d7")
    assert r.returncode == 1 and "dms-agent:d7(HTTP 404)" in r.all, r.all
    assert "build-and-push.sh" in r.stdout
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S11", FAKE_CURL_404="dms-agent:d7")
    assert r.returncode == 1 and "S5 미완" in r.all
    assert not [c for c in mutations(rig) if c.endswith("/app")]


def test_s5_reports_a_port_forward_that_never_listens(rig):
    rig.world(installed_world(pf_fail=True))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5")
    assert r.returncode == 1 and "port-forward 가 열리지 않았다" in r.all and "unable to forward port" in r.all
    assert rig.helper_calls("images-lock.py") == []


def test_s5_unknown_registry_is_unknown_not_fail(rig):
    rig.world(installed_world())
    rig.scenario({"images-lock.py compare-registry": {"rc": 4}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5")
    assert r.returncode == 4 and stages(r) == [("S5", "UNKNOWN")]


# --- S7 종료 코드 ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("probe_rc, rc, stage, continues", [(0, 0, "PASS", True), (3, 0, "WARN", True), (1, 1, "FAIL", False),
                                                             (4, 4, "UNKNOWN", False), (2, 2, "FAIL", False)])
def test_s7_exit_codes(rig, probe_rc, rc, stage, continues):
    rig.world(installed_world())
    rig.scenario({"shared-storage-probe.py": {"rc": probe_rc}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--stage", "S7")
    assert r.returncode == rc, r.all
    got = dict(stages(r))
    assert got["S7"] == stage
    assert ("S8" in got) is continues
    if probe_rc == 3:
        assert r.stdout.strip().endswith("RESULT: WARN")
    argv = rig.helper_calls("shared-storage-probe.py")[0]
    assert argv[argv.index("--shared-dir") + 1] == "/srv/shared" and argv[argv.index("--image") + 1] == APP[0]


# --- external 대 in-cluster ---------------------------------------------------------------------------------------------------

def test_external_registry_site_skips_the_registry_layer_and_compares_the_site_registry(rig):
    ext = ["reg.example:5000/dms:d7", "reg.example:5000/dms-agent:d7", "reg.example:5000/dms-mpifileutils:d7"]
    w = installed_world(app_images=ext, api_image=ext[0])
    del w["secrets"]["dms/dms-registry-http"]
    rig.world(w)
    rig.scenario({"detect": {"registry": "- skip 0 INFO"}})
    env = {"FAKE_RMODE": "external", "FAKE_RCIP": "", "FAKE_REGISTRY": "reg.example:5000", "FAKE_ADDON_REGISTRY": "reg.example:5000"}
    r = rig.run("--context", "known", "--only", "S4,S5,S6", **env)
    assert r.returncode == 0, r.all
    calls = cluster_calls(rig)
    assert not [c for c in calls if "port-forward" in c or "dms-registry" in c], calls
    assert [c for c in mutations(rig) if "--server-side" in c][0].endswith("platform/priorityclass")
    cr = rig.helper_calls("images-lock.py")[0]
    assert cr[:3] == ["compare-registry", "--registry-url", "http://reg.example:5000"] and "--skip" in cr
    assert cr[cr.index("--skip") + 1] == "registry"                              # 레지스트리 자기 이미지는 이 사이트에 필요 없다
    pp = rig.helper_calls("pull-probe.py")[0]
    assert "--render-env" not in pp and pp[pp.index("--image") + 1] == ext[0]
    curls = rig.logs["curl"].read_text()
    assert "http://reg.example:5000/v2/dms/manifests/d7" in curls and "--noproxy" in curls

    # external 에서 lock 이 다르면 push 명령은 그 REGISTRY 로(인터넷 노드가 lock 전부를 사이트 레지스트리로 — ⑥ 리뷰 2)
    rig.scenario({"detect": {"registry": "- skip 0 INFO"}, "images-lock.py compare-registry": {"rc": 1}})
    r = rig.run("--context", "known", "--only", "S5", **env)
    assert r.returncode == 1 and rig.helper_calls("push-images.sh")[-1][:2] == ["--registry", "reg.example:5000"]


def test_c_cnpg_lock_line_is_skipped_and_never_installed(rig):
    rig.world(installed_world())
    rig.scenario({"detect": {"cnpg": "C reuse 0 PASS", "gateway-api": "C reuse 0 PASS", "envoy-gateway": "C! reuse 0 WARN"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5,S8,S9")
    assert r.returncode == 0, r.all
    cr = rig.helper_calls("images-lock.py")[0]
    assert cr[cr.index("--skip") + 1] == "cloudnative-pg"
    muts = mutations(rig)
    assert not [c for c in muts if "/addons/" in c], muts                           # C 는 다시 설치하지 않는다(설계 8-2)
    assert not [c for c in muts if "create namespace" in c]
    assert [c for c in muts if c.endswith("platform/gateway")]                      # C 경로에서도 DMS 소유 입구 층은 적용한다
    dry = [c for c in cluster_calls(rig) if "--dry-run=server" in c]
    assert len(dry) == 1 and dry[0].endswith("platform/db-cluster")              # 9443 경로 = Cluster CR dry-run(최종 판정)


def test_a_addons_are_installed_in_order_with_namespaces_first(rig):
    w = installed_world()
    w["ns"] = ["dms"]
    w["secrets"] = {k: v for k, v in w["secrets"].items() if not k.startswith("envoy-gateway-system/")}
    rig.world(w)
    rig.scenario({"detect": {"gateway-api": "A install 1 PASS", "envoy-gateway": "A install 1 PASS", "cnpg": "A install 1 PASS"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S8,S9")
    assert r.returncode == 0, r.all
    muts = mutations(rig)
    # ns 는 처음부터 DMS 라벨로 SSA(2026-11-03 ⑦ 리뷰 1 — 라벨 없이 만들고 층 SSA 전에 끊기면 다음 감지가 그 ns 를 「남의 것」(P)으로 봤다)
    ns_ssa = [i for i, c in enumerate(muts) if c.endswith("apply --server-side --field-manager=dms-install -f -")]
    i_eg = next(i for i, c in enumerate(muts) if c.endswith("addons/envoy-gateway"))
    i_cnpg = next(i for i, c in enumerate(muts) if c.endswith("addons/cnpg"))
    assert len(ns_ssa) == 2 and ns_ssa[0] < i_eg < ns_ssa[1] < i_cnpg, muts
    assert not [c for c in muts if "create namespace" in c]
    w = rig.world()
    assert w["ns_applied"] == ["envoy-gateway-system", "cnpg-system"]
    assert all(w["ns_labels"][n] == {"app.kubernetes.io/managed-by": "dms-install"} for n in w["ns_applied"])
    probes = [a for a in rig.helper_calls("host-net-probe.sh")]
    assert [a[-1] for a in probes] == ["--sysctl", "--listen"]                       # sysctl 은 platform/gateway 전에, :443 은 Programmed 뒤
    guards = [a[0].split("/.rendered/testbed-k8s/")[-1] for a in rig.helper_calls("guard-images.sh")]
    assert guards == ["addons/envoy-gateway", "addons/cnpg"]


def test_upgrade_and_adopt_flags_reach_detection_and_ssa(rig):
    rig.world(installed_world())
    rig.scenario({"detect": {"registry": "P adopt 1 WARN", "cnpg": "D< upgrade 1 PASS"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S4,S9", "--adopt", "registry", "--upgrade-addon", "cnpg")
    assert r.returncode == 0, r.all
    det = rig.helper_calls("install-detect.py")[0]
    assert det[det.index("--adopt") + 1] == "registry" and det[det.index("--upgrade-addon") + 1] == "cnpg"
    reg = [c for c in mutations(rig) if c.endswith("platform/registry")]
    assert reg and "--force-conflicts" in reg[0]
    assert "dms-pg-dump-pre-upgrade-" in " ".join(rig.world()["created_jobs"])     # CNPG 업그레이드 직전 덤프(설계 6-5)
    g = [a for a in rig.helper_calls("guard-images.sh") if a[0].endswith("addons/cnpg")]
    assert g and "--allow-image-change" in g[0]


# --- 감지 정지 · --plan ----------------------------------------------------------------------------------------------------------

def test_detection_fail_stops_at_s1_before_any_change(rig):
    rig.world(installed_world())
    rig.scenario({"detect": {"db": "X stop 0 FAIL"}, "install-detect.py detect": {"rc": 1}})
    r = rig.run("--context", "known", "--site", "testbed-k8s")
    assert r.returncode == 1 and stages(r) == [("S1", "FAIL")], r.all
    assert mutations(rig) == []


def test_volcano_to_install_without_a_layer_stops_at_s1(rig):
    rig.world(installed_world())
    rig.scenario({"detect": {"volcano": "A install 1 PASS"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s")
    assert r.returncode == 1 and stages(r) == [("S1", "FAIL")] and "addons/volcano" in r.all
    assert mutations(rig) == []


@pytest.mark.parametrize("world", [fresh_world, installed_world])
def test_plan_changes_nothing(rig, world):
    rig.world(world())
    rig.scenario({"detect": FRESH_DETECT if world is fresh_world else {}})
    before = rig.world()
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--plan")
    assert r.returncode == 0, r.all
    assert [s for s, _ in stages(r)] == ["S1", "S2", "S3"]
    assert mutations(rig) == []
    assert rig.world() == before                                                   # 세계가 그대로
    modes = [(h["name"], next((a for a in h["argv"] if a in ("detect", "plan", "check", "ensure-app", "init-secrets")), None))
             for h in rig.helpers()]
    allowed = {("preflight-cluster.sh", None), ("install-detect.py", "detect"), ("install-detect.py", "plan"), ("pg-secrets.py", "check")}
    assert set(modes) <= allowed, modes
    assert ("install-detect.py", "plan") in modes
    assert_context(rig)


def test_plan_reports_a_bad_secret_but_still_shows_the_plan(rig):
    rig.world(installed_world())
    rig.scenario({"pg-secrets.py check": {"rc": 1, "out": "FAIL: DMS_DATABASE_URL 이 어긋난다: host"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--plan")
    assert r.returncode == 1 and ("S2", "FAIL") in stages(r) and ("S3", "PASS") in stages(r), r.all
    assert mutations(rig) == []


# --- S2 · S10 · S11 ------------------------------------------------------------------------------------------------------------

def test_missing_tls_secret_warns_at_s2_and_stops_at_s8_before_the_gateway_layer(rig):
    # 2026-11-03 ⑦ 리뷰 1: 인증서를 쓰는 곳은 S8(platform/gateway) 뿐이다(설계 8-1 S2 판정 「—」) — S2 는 알리기만, S8 이 SSA 전에 멈춘다.
    w = installed_world()
    del w["secrets"]["dms/dms-portal-tls"]
    rig.world(w)
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S2")
    assert r.returncode == 0 and stages(r) == [("S2", "WARN")], r.all
    assert "create secret tls dms-portal-tls" in r.all and "S8" in r.all
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--stage", "S8")
    assert r.returncode == 1 and stages(r) == [("S8", "FAIL")], r.all
    assert "create secret tls dms-portal-tls" in r.all and "--stage S8" in r.all
    assert not [c for c in mutations(rig) if c.endswith("platform/gateway")]
    assert rig.helper_calls("host-net-probe.sh") == []


def test_s2b_selinux_fail_and_unknown_stop(rig):
    rig.world(installed_world())
    for probe_rc, rc in ((1, 1), (4, 4), (0, 0)):
        rig.scenario({"selinux-probe.py": {"rc": probe_rc}})
        r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S2b")
        assert r.returncode == rc, (probe_rc, r.all)
    assert "--image" not in rig.helper_calls("selinux-probe.py")[0]                # 기본: 프로브가 Cilium 이미지를 찾는다
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S2b", SELINUX_PROBE_IMAGE="docker.io/library/busybox:1.36")
    argv = rig.helper_calls("selinux-probe.py")[-1]
    assert r.returncode == 0 and argv[argv.index("--image") + 1] == "docker.io/library/busybox:1.36"   # Cilium 없는 클러스터


@pytest.mark.parametrize("ready, rc, stage", [("3", 0, "PASS"), ("2", 0, "WARN"), ("0", 1, "FAIL")])
def test_s10_poll_verdicts(rig, ready, rc, stage):
    rig.world(installed_world(cluster={"instances": "3", "ready": ready, "primary": "dms-pg-1"}))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S10", DB_WAIT_SECONDS="1")
    assert r.returncode == rc and stages(r) == [("S10", stage)], r.all


def test_s10_first_dump_failure_is_reported_not_waited_out(rig):
    rig.world(installed_world(cronjob_last="", new_job_state="Failed"))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S10")
    assert r.returncode == 1 and "첫 덤프 실패" in r.all


@pytest.mark.parametrize("prev, restart", [("cm=5,secret=10", False), ("cm=4,secret=10", True), ("cm=5,secret=9", True)])
def test_s11_restarts_only_when_config_changed(rig, prev, restart):
    w = installed_world()
    w["annotations"] = {k: prev for k in w["annotations"]}
    rig.world(w)
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S11")
    assert r.returncode == 0, r.all
    got = rig.world()
    assert bool(got.get("restarts")) is restart
    if restart:
        assert got["restarts"] == ["deploy/dms-api", "deploy/dms-controller", "ds/dms-agent"]
    assert set(got["annotations"].values()) == {"cm=5,secret=10"}
    guard = rig.helper_calls("guard-images.sh")
    assert guard and guard[0][0].endswith("/app") and guard[0][1:] == ["--context", "known"]


def test_s11_template_change_does_not_double_restart(rig):
    w = installed_world(app_bumps=True, app_changes_config=True)
    rig.world(w)
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S11")
    assert r.returncode == 0, r.all
    assert not rig.world().get("restarts")                                       # 템플릿이 바뀌어 이미 새 파드로 돌았다


def test_s11_guard_refusal_stops_before_apply(rig):
    rig.world(installed_world())
    rig.scenario({"guard-images.sh": {"rc": 3}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S11")
    assert r.returncode == 1 and "ALLOW_IMAGE_CHANGE" in r.all
    assert not [c for c in mutations(rig) if " apply " in f" {c} "]


# --- 단계별 멈춤 조건(설계 8-1 판정 칸 — 2026-11-03 ⑦ 리뷰 1: 각 멈춤을 die→warn·judge 0 으로 바꿔도 통과하던 16건) ------------------------

_WEBHOOK = ('Error from server (InternalError): Internal error occurred: failed calling webhook "mcluster.cnpg.io": failed to call '
            'webhook: Post "https://cnpg-webhook-service.cnpg-system.svc:443/mutate-postgresql-cnpg-io-v1-cluster?timeout=10s": '
            'dial tcp 10.0.0.1:443: connect: connection refused')
_EVENTS = ("dms-registry-6d9f-abc   cp2      FailedMount        MountVolume.SetUp failed for volume \"registry\" : hostPath type "
           "check failed: /srv/shared/dms/registry is not a directory\n"
           "dms-registry-6d9f-xyz   <none>   FailedScheduling   0/3 nodes are available: 3 Insufficient cpu.\n"
           "other-pod-1             cp1      FailedMount        unrelated\n")

# (id, 시작 단계, 시나리오, 세계 덮어쓰기, env, rc, 그 단계 판정, 출력에 있어야 할 말, 적용되면 안 되는 층(렌더 디렉터리 기준))
STOPS = [
    ("S2-key-missing", "S2", {}, {"secret_data": "DMS_DATABASE_URL=x\nDMS_SHARED_TOKEN=x\nDMS_SESSION_SECRET=x\n"}, {}, 1, "FAIL",
     ["키가 없다: DMS_ADMIN_TOKEN"], []),
    ("S2-placeholder", "S2", {}, {"secret_data": "DMS_DATABASE_URL=x\nDMS_SHARED_TOKEN=CHANGE_ME\nDMS_ADMIN_TOKEN=x\n"
                                                 "DMS_SESSION_SECRET=x\n"}, {}, 1, "FAIL", ["자리표시자"], []),
    ("S2-unreadable", "S2", {}, {"fail": {"go-template": [1, 'Error from server (Forbidden): secrets "dms-secrets" is forbidden']}},
     {}, 4, "UNKNOWN", ["dms-secrets 를 읽지 못했다", "Forbidden"], []),
    ("S3-plan-fail", "S3", {"install-detect.py plan": {"rc": 1}}, {}, {}, 1, "FAIL", ["계획 FAIL"], ["platform/priorityclass"]),
    ("S4-rollout", "S4", {}, {"fail": {"rollout status deploy/dms-registry": [1, "error: timed out waiting for the condition"]},
                              "events": _EVENTS, "pod_nodes": "cp2 ", "logs": {"deploy/dms-registry": "FAIL: 센티널 없음"}}, {}, 1, "FAIL",
     ["마스터 cp2 에 공용 디렉터리 없음", "hostPath type check failed", "0/3 nodes are available", "init: FAIL: 센티널 없음",
      "ImagePullBackOff", "S0-5 반입"], []),
    ("S4-v2-not-200", "S4", {}, {}, {"FAKE_CURL_V2": "401"}, 1, "FAIL", ["/v2/ 가 200 이 아니다(HTTP 401)"], []),
    ("S6-pull-fail", "S6", {"pull-probe.py": {"rc": 1}}, {}, {}, 1, "FAIL", ["pull 프로브"], []),
    ("S8-sysctl", "S8", {"host-net-probe.sh --sysctl": {"rc": 1}}, {}, {}, 1, "FAIL", ["ip_unprivileged_port_start"], ["platform/gateway"]),
    ("S8-programmed", "S8", {}, {"fail": {"wait --for=condition=Programmed": [1, "error: timed out"]}}, {}, 1, "FAIL",
     ["Programmed 가 아니다"], []),
    ("S9-webhook", "S9", {}, {"fail": {"--dry-run=server -k": [1, _WEBHOOK]}}, {"WEBHOOK_RETRIES": "2"}, 1, "FAIL",
     ["failed calling webhook", "9443"], []),
    ("S9-dry-run", "S9", {}, {"fail": {"--dry-run=server -k": [1, 'The Cluster "dms-pg" is invalid: spec.instances: Invalid value']}},
     {}, 1, "FAIL", ["Cluster CR server dry-run 실패(위 오류)", "spec.instances"], []),
    # DMS 소유 CNPG 를 적용하지 않는 재실행(D= none)도 오퍼레이터 Ready 를 읽기로 본다 — 앞선 실행이 rollout 에서 멈춘 경우(⑦ 리뷰 1)
    ("S9-cnpg-not-ready", "S9", {}, {"fail": {"rollout status deploy/cnpg-controller-manager": [1, "error: timed out"]}}, {}, 1, "FAIL",
     ["CNPG 오퍼레이터(DMS 설치)가 Ready 가 아니다", "--only S9 --upgrade-addon cnpg"], []),
    ("S9b-vcjob", "S9b", {"install-detect.py vcjob": {"rc": 1}}, {}, {}, 1, "FAIL", ["vcjob server dry-run"], []),
    ("S10-pv-check", "S10", {"pg-path-probe.py pv-check": {"rc": 1}}, {}, {}, 1, "FAIL", ["pv-check"],
     ["platform/db-volumes", "platform/db-cluster", "platform/db-backup"]),
    ("S10-precheck", "S10", {"pg-path-probe.py precheck": {"rc": 1}}, {}, {}, 1, "FAIL", ["precheck"],
     ["platform/db-cluster", "platform/db-backup"]),
    ("S10-contrast", "S10", {"pg-path-probe.py contrast": {"rc": 1}}, {}, {}, 1, "FAIL", ["경로 대조"], ["platform/db-backup"]),
    ("S11-migrate", "S11", {}, {"migrate_state": "Failed", "logs": {"job/dms-migrate": "psycopg.OperationalError: connection refused"}},
     {}, 1, "FAIL", ["migrate 실패", "psycopg.OperationalError"], []),
    ("S11-guard-context", "S11", {"guard-images.sh": {"rc": 4}}, {}, {}, 2, "FAIL", ["가드 거부(컨텍스트)"], ["app"]),
    ("S11-guard-hostpath", "S11", {"guard-images.sh": {"rc": 5}}, {}, {}, 1, "FAIL", ["ALLOW_HOSTPATH_CHANGE"], ["app"]),
]


@pytest.mark.parametrize("case", STOPS, ids=[c[0] for c in STOPS])
def test_each_stage_stops_on_its_failure_with_the_reason(rig, case):
    _id, start, scenario, over, env, rc, verdict, needles, not_applied = case
    rig.world(installed_world(**over))
    rig.scenario(scenario)
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--stage", start, **env)
    assert r.returncode == rc, r.all
    assert stages(r) == [(start, verdict)], r.all                                  # 다음 단계는 돌지 않는다
    assert r.stdout.strip().splitlines()[-1] == "RESULT: %s" % verdict
    for needle in needles:
        assert needle in r.all, (needle, r.all)
    applied = [p.split("/.rendered/testbed-k8s/")[-1] for p in rig.world().get("applied", [])]
    for layer in not_applied:
        assert layer not in applied, (layer, applied)
    if _id == "S9-webhook":
        assert len([c for c in cluster_calls(rig) if "--dry-run=server -k" in c]) == 2   # 웹훅 인증서가 늦을 수 있어 다시(WEBHOOK_RETRIES)
    if _id == "S9-dry-run":
        assert len([c for c in cluster_calls(rig) if "--dry-run=server -k" in c]) == 1   # 웹훅이 아닌 오류는 다시 하지 않는다
    if _id == "S8-programmed":
        assert [a[-1] for a in rig.helper_calls("host-net-probe.sh")] == ["--sysctl"]   # :443 은 Programmed 뒤
    if _id == "S10-contrast":
        assert rig.world().get("created_jobs") is None                             # 첫 덤프도 없다
    if _id.startswith("S2-"):
        assert "check" not in [a[-1] for a in rig.helper_calls("pg-secrets.py")]   # URL 대조까지 가지 않는다
        assert "4키·자리표시자 없음" not in r.stdout


def test_migrate_job_is_deleted_before_the_app_apply(rig):
    rig.world(installed_world(jobs={"dms-migrate": "Complete"}))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S11")
    assert r.returncode == 0, r.all
    calls = cluster_calls(rig)
    i_del = next(i for i, c in enumerate(calls) if "delete job dms-migrate" in c)
    i_app = next(i for i, c in enumerate(calls) if c.endswith("/app") and " apply " in f" {c} ")
    assert i_del < i_app                                                            # Job spec 은 바꿀 수 없다 — 지우고 다시 만든다


def test_s10_prechecks_run_before_the_layers_they_guard(rig):
    # 설계 S10 세부: 사전 검사는 Cluster 를 만들기 **전에**(마운트 빠진 노드의 26:26·센티널을 PG 가 쓰기 전에), 값 대조(pv-check)는 db-volumes
    # SSA 전에(SSA 가 Bound PV 의 용량·SC 를 조용히 바꾼다), 경로 대조는 Cluster 뒤·db-backup 전에(2026-11-03 ⑦ 리뷰 1 — 변이 M02 가 살았다).
    rig.world(fresh_world(ns=["dms"], secrets={"dms/dms-pg-app": {}}))
    rig.scenario({"detect": dict(FRESH_DETECT)})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S10")
    assert r.returncode == 0, r.all
    seen = {}
    for h in rig.helpers():
        if h["name"] == "pg-path-probe.py":
            seen[h["argv"][h["argv"].index("--mode") + 1]] = [p.split("/.rendered/testbed-k8s/")[-1] for p in h["applied"]]
    assert list(seen) == ["pv-check", "precheck", "contrast"]
    assert "platform/db-volumes" not in seen["pv-check"]
    assert "platform/db-volumes" in seen["precheck"] and "platform/db-cluster" not in seen["precheck"]
    assert "platform/db-cluster" in seen["contrast"] and "platform/db-backup" not in seen["contrast"]
    assert "platform/db-backup" in [p.split("/.rendered/testbed-k8s/")[-1] for p in rig.world()["applied"]]


# --- 재개 안내 · 플래그 · env(2026-11-03 ⑦ 리뷰 1) --------------------------------------------------------------------------------

def test_pause_resume_command_keeps_the_upgrade_flags_and_env(rig):
    # 재개 명령에서 --upgrade-addon 이 빠지면 재개한 S9 가 업그레이드도 직전 덤프도 없이 RESULT WARN·rc 0 으로 끝났다.
    rig.world(fresh_world())
    rig.scenario({"detect": dict(FRESH_DETECT, cnpg="D< upgrade 1 PASS"), "images-lock.py compare-registry": {"rc": 1},
                  "pg-secrets.py init-secrets": {"add_secrets": ["dms/dms-secrets"]},
                  "pg-secrets.py ensure-app": {"add_secrets": ["dms/dms-pg-app"]}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--upgrade-addon", "cnpg", "--adopt", "registry",
                ADDON_INSTALL="never", SELINUX_PROBE_IMAGE="reg.example/cilium:v1")
    assert r.returncode == 10, r.all
    want = ("ADDON_INSTALL=never SELINUX_PROBE_IMAGE=reg.example/cilium:v1 sh deploy/install.sh --context known --site testbed-k8s "
            "--stage S5 --upgrade-addon cnpg --adopt registry")
    assert want in r.stdout, r.stdout


def test_unknown_resume_command_keeps_the_flags(rig):
    rig.world(installed_world())
    rig.scenario({"images-lock.py compare-registry": {"rc": 4}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5", "--upgrade-addon", "envoy-gateway")
    assert r.returncode == 4
    assert "sh deploy/install.sh --context known --site testbed-k8s --stage S5 --upgrade-addon envoy-gateway" in r.all, r.all


def test_env_dms_ns_never_reaches_the_helpers(rig):
    # 옛 install.sh 의 DMS_NS 가 셸에 남아 있으면 감지가 엉뚱한 ns 를 봤다(Cluster 없음 → A → major 업그레이드 차단을 건너뜀).
    rig.world(installed_world())
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S1,S3,S9b", DMS_NS="dms-old")
    assert r.returncode == 0, r.all
    assert "DMS_NS=dms-old 는 무시한다" in r.stderr
    det = rig.helper_calls("install-detect.py")
    assert len(det) == 3 and all(a[a.index("--namespace") + 1] == "dms" for a in det), det
    assert all(h["dms_ns"] is None for h in rig.helpers())


def test_cnpg_upgrade_without_a_cluster_opens_the_guard_but_skips_the_dump(rig):
    rig.world(installed_world(cluster=None))
    rig.scenario({"detect": {"cnpg": "D< upgrade 1 PASS"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S9", "--upgrade-addon", "cnpg")
    assert r.returncode == 0, r.all
    g = [a for a in rig.helper_calls("guard-images.sh") if a[0].endswith("addons/cnpg")]
    assert g and g[0][1:] == ["--context", "known", "--allow-image-change"], g
    assert rig.world().get("created_jobs") is None                                 # Cluster 가 없으면 직전 덤프 없음


@pytest.mark.parametrize("det, flags, guarded, allow, applied", [
    ("D< upgrade 1 PASS", ["--upgrade-addon", "registry"], True, True, True),   # 의도한 교체 — 이 층에서만 가드를 연다
    ("D< keep 0 WARN", [], False, False, False),                                # 적용하지 않을 층 때문에 멈추지 않는다
    ("D= reapply 1 PASS", [], True, False, True),
])
def test_s4_guards_the_registry_layer_only_when_it_applies_it(rig, det, flags, guarded, allow, applied):
    rig.world(installed_world())
    rig.scenario({"detect": {"registry": det}, "guard-images.sh": {"rc": 0 if guarded else 3}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S4", *flags)
    assert r.returncode == 0, r.all
    g = [a for a in rig.helper_calls("guard-images.sh") if a[0].endswith("platform/registry")]
    assert bool(g) is guarded, g
    if guarded:
        assert ("--allow-image-change" in g[0]) is allow
    assert bool([c for c in mutations(rig) if c.endswith("platform/registry")]) is applied
    if allow:
        assert "99-dms-pinned.conf" in r.stdout                                    # 업그레이드 순서(설계 7-6)를 같이 보인다


def test_adopt_db_dry_runs_the_cluster_cr_with_force_conflicts(rig):
    rig.world(installed_world())
    rig.scenario({"detect": {"db": "P adopt 1 WARN"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S9,S10", "--adopt", "db")
    assert r.returncode == 0, r.all
    dry = [c for c in cluster_calls(rig) if "--dry-run=server" in c and c.endswith("platform/db-cluster")]
    ssa = [c for c in mutations(rig) if c.endswith("platform/db-cluster")]
    assert dry and "--force-conflicts" in dry[0] and ssa and "--force-conflicts" in ssa[0], (dry, ssa)


def test_plan_secret_read_failure_is_unknown_and_never_printed_ok(rig):
    rig.world(installed_world(fail={"go-template": [1, 'Error from server (Forbidden): secrets "dms-secrets" is forbidden']}))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--plan")
    assert r.returncode == 4 and ("S2", "UNKNOWN") in stages(r) and ("S3", "PASS") in stages(r), r.all
    assert "4키·자리표시자 없음" not in r.stdout and "키가 없다" not in r.all
    rig.world(installed_world(secret_data="DMS_DATABASE_URL=x\n"))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--plan")
    assert r.returncode == 1 and "키가 없다" in r.all and "4키·자리표시자 없음" not in r.stdout


def test_rerun_after_an_interrupted_eg_install_checks_and_points_to_the_upgrade_flag(rig):
    # 앞선 실행이 EG SSA 뒤 certgen 에서 멈췄으면 재실행 감지는 D=(none) — 확인 없이 지나가 「Programmed 아님」으로만 드러났다.
    w = installed_world()
    w["secrets"] = {k: v for k, v in w["secrets"].items() if not k.startswith("envoy-gateway-system/")}
    rig.world(w)
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S8")
    assert r.returncode == 1 and "--only S8 --upgrade-addon envoy-gateway" in r.all, r.all
    assert not [c for c in mutations(rig) if c.endswith("platform/gateway")]
    # 그 길로 다시: 실패한 certgen Job 을 지운 뒤 SSA(Job spec 은 바꿀 수 없다)
    w["jobs"] = {"eg-gateway-helm-certgen": "Failed"}
    rig.world(w)
    rig.reset_logs()
    rig.scenario({"detect": {"envoy-gateway": "D= reapply 1 PASS"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S8", "--upgrade-addon", "envoy-gateway")
    assert r.returncode == 0, r.all
    calls = cluster_calls(rig)
    i_del = next(i for i, c in enumerate(calls) if "delete job eg-gateway-helm-certgen" in c)
    i_eg = next(i for i, c in enumerate(calls) if c.endswith("addons/envoy-gateway") and "--dry-run" not in c)
    assert i_del < i_eg


def test_s2b_without_cilium_points_to_the_installer_env(rig):
    rig.world(installed_world())
    rig.scenario({"selinux-probe.py": {"rc": 2, "out": "selinux-probe: Cilium agent DS 가 없다 — 셸 포함 이미지를 --image 로 준다"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S2b")
    assert r.returncode == 2, r.all
    assert "SELINUX_PROBE_IMAGE=<ref> sh deploy/install.sh --context known --site testbed-k8s --stage S2b" in r.all


@pytest.mark.parametrize("images, want", [
    (APP, "REGISTRY=10.96.0.50:5000 TAG=d7 ./deploy/docker/build-and-push.sh"),
    (["10.96.0.50:5000/dms:d7", "10.96.0.50:5000/dms-agent:d8", "10.96.0.50:5000/dms-mpifileutils:d7"], "세 태그가 다르다"),
])
def test_s5_shows_the_real_bootstrap_tag(rig, images, want):
    rig.world(installed_world(app_images=images))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5", FAKE_CURL_404="dms:d7")
    assert r.returncode == 1 and want in r.stdout, r.stdout
    assert "TAG=<dN>" not in r.stdout


# --- S12 · S13 ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("env, extra", [({}, []), ({"VERIFY_NO_PROBES": "1"}, ["--no-probes"])])
def test_s12_runs_verify_with_the_rendered_site(rig, env, extra):
    rig.world(installed_world())
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S12", **env)
    assert r.returncode == 0 and stages(r) == [("S12", "PASS")], r.all
    v = rig.helper_calls("verify.sh")
    assert len(v) == 1 and v[0][:3] == ["--context", "known", "--rendered"] and v[0][3].endswith("/.rendered/testbed-k8s")
    assert v[0][4:] == extra


def test_s12_failure_still_prints_the_s13_checklist(rig):
    rig.world(installed_world())
    rig.scenario({"verify.sh": {"rc": 1, "out": "  [FAIL] readyz 실패"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--stage", "S12")
    assert r.returncode == 1 and stages(r) == [("S12", "FAIL")], r.all
    assert "사후 체크리스트" in r.stdout and "Avi VS-443" in r.stdout and "--only S12" in r.all


def test_s13_checklist_bootstraps_the_first_admin_without_printing_the_token(rig):
    rig.world(installed_world())
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S13")
    assert r.returncode == 0, r.all
    out = r.stdout
    assert "port-forward svc/dms-api 18080:http" in out and "/api/admin/accounts" in out and "-H @\"$H\"" in out
    assert "jsonpath='{.data.DMS_ADMIN_TOKEN}' | base64 -d | sed 's/^/x-admin-token: /' > \"$H\"" in out   # 파일로만(출력·argv 없음)
    assert "<LOCAL_ADMIN>" in out and "read -r PW" in out
    assert not [c for c in cluster_calls(rig) if "secret" in c]                     # 체크리스트는 출력만 — 시크릿을 읽지 않는다


# --- 리뷰 2(2026-11-03 ⑦): --plan 의 다음 명령 · 도우미 WARN · 가드 안내 · S5 출력 · 0열 RESULT · --help ---------------------------------

def _no_stage_resume(r):
    # --plan 은 읽기만 한다 — 「확인한 뒤」·「다시」 줄이 S~S13 을 적용하는 `--stage` 를 권하면 안 된다(설치를 돌리게 된다).
    assert "--stage" not in r.all, r.all


@pytest.mark.parametrize("case", ["secret-unreadable", "detect-unknown", "volcano-A"])
def test_plan_never_points_to_a_mutating_stage_command(rig, case):
    if case == "secret-unreadable":
        rig.world(installed_world(fail={"go-template": [1, 'Error from server (Forbidden): secrets "dms-secrets" is forbidden']}))
        want_rc = 4
    elif case == "detect-unknown":
        rig.world(installed_world())
        rig.scenario({"install-detect.py detect": {"rc": 4}})
        want_rc = 4
    else:
        rig.world(installed_world())
        rig.scenario({"detect": {"volcano": "A install 1 PASS"}})
        want_rc = 1
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--plan", "--adopt", "cnpg", ADDON_INSTALL="never")
    assert r.returncode == want_rc, r.all
    _no_stage_resume(r)
    assert [s for s, _ in stages(r)] == ["S1", "S2", "S3"]                          # 계획은 끝까지 보인다
    assert ("S3", "PASS") in stages(r)                                              # dry-run 이 PASS 면 S3 는 PASS
    again = "ADDON_INSTALL=never sh deploy/install.sh --context known --site testbed-k8s --plan --adopt cnpg"
    assert "위 FAIL·모름을 해소한 뒤 계획부터 다시: " + again in r.stdout, r.stdout
    assert "실제 설치:" not in r.stdout
    if case == "volcano-A":
        assert r.all.count("렌더에 addons/volcano 층이 없다") == 1                   # S1 이 본 게이트를 S3 가 다시 부르지 않는다
    assert mutations(rig) == []


def test_plan_end_line_keeps_the_flags_and_env_of_the_plan(rig):
    rig.world(installed_world())
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--plan", "--adopt", "cnpg", ADDON_INSTALL="never",
                VALUES_ENV="/srv/site/values.env")
    assert r.returncode == 0, r.all
    want = ("실제 설치: ADDON_INSTALL=never VALUES_ENV=/srv/site/values.env sh deploy/install.sh --context known --site testbed-k8s "
            "--adopt cnpg")
    assert want in r.stdout, r.stdout


def test_volcano_layer_gate_still_stops_s3_alone(rig):
    rig.world(installed_world())
    rig.scenario({"detect": {"volcano": "A install 1 PASS"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S3")
    assert r.returncode == 1 and stages(r) == [("S3", "FAIL")] and "addons/volcano" in r.all, r.all


_PF_WARN = ("== 5. 공용 디렉터리 · 레지스트리 값 (render.env) ==\n"
            "  [WARN] REGISTRY_CLUSTER_IP 10.96.5.0 가 ServiceCIDR 10.96.0.0/12 안이지만 하단 정적 대역 밖\n"
            "    WARN 들여 쓴 하위 줄은 세지 않는다\n"
            "== 요약 ==\n  PASS 10 · WARN 1 · FAIL 0\n  => 배포 가능하나 WARN 항목을 확인/조정 권장")


def test_preflight_warn_makes_s1_and_the_result_warn(rig):
    rig.world(installed_world())
    rig.scenario({"preflight-cluster.sh": {"rc": 0, "out": _PF_WARN}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S1")
    assert r.returncode == 0 and stages(r) == [("S1", "WARN")], r.all
    assert r.stdout.strip().splitlines()[-1] == "RESULT: WARN"
    assert "preflight WARN 1건" in r.stdout
    assert "      [WARN] REGISTRY_CLUSTER_IP 10.96.5.0" in r.stdout                  # 도우미 줄은 4칸 들여 쓴다
    rig.scenario({})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S1")
    assert r.returncode == 0 and stages(r) == [("S1", "PASS")], r.all


def test_preflight_warn_makes_the_plan_result_warn(rig):
    # dmsk8s --plan 실측: preflight 「WARN 1」(control-plane 1대)인데 RESULT PASS 였다.
    rig.world(installed_world())
    rig.scenario({"preflight-cluster.sh": {"rc": 0, "out": "  [WARN] control-plane 노드 1대 — prod 는 3대"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--plan")
    assert r.returncode == 0 and ("S1", "WARN") in stages(r), r.all
    assert r.stdout.strip().splitlines()[-1] == "RESULT: WARN"


def test_verify_warn_makes_s12_and_the_result_warn(rig):
    rig.world(installed_world())
    rig.scenario({"verify.sh": {"rc": 0, "out": "  [PASS] readyz(서비스 프록시) 200\n"
                                                "  [WARN] VIP 경유 readyz 응답 없음(000) — Avi VS-443 미개통일 수 있다\n"
                                                "  [WARN] Available 여분 PV 0개\n"
                                                "\n== 요약 ==  PASS=12  WARN=2  FAIL=0"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S12")
    assert r.returncode == 0 and stages(r) == [("S12", "WARN")], r.all
    assert "verify WARN 2건" in r.stdout and r.stdout.strip().splitlines()[-1] == "RESULT: WARN"


def test_s12_resume_line_keeps_the_verify_env(rig):
    # 재개 줄에서 CACERT 가 빠지면 VIP readyz 가 TLS 실패(000)로 WARN — 입구 문제를 「Avi 미개통」으로 읽게 했다.
    rig.world(installed_world())
    rig.scenario({"verify.sh": {"rc": 1, "out": "  [FAIL] Gateway dms-portal Programmed 아님"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S12", CACERT="/etc/pki/corp-ca.pem",
                PORTAL_DOMAIN="dms.corp.example", PORTAL_VIP="10.0.0.9", VERIFY_NO_PROBES="1")
    assert r.returncode == 1, r.all
    want = ("고친 뒤: CACERT=/etc/pki/corp-ca.pem PORTAL_DOMAIN=dms.corp.example PORTAL_VIP=10.0.0.9 VERIFY_NO_PROBES=1 "
            "sh deploy/install.sh --context known --site testbed-k8s --only S12")
    assert want in r.all, r.all
    assert "CACERT=<사내CA>" not in r.stdout                                      # S13 의 VIP 검증 줄도 준 CACERT 를 그대로(자리표시자와 겹치지 않게)


def test_addon_guard_refusal_points_to_the_layer_flag_not_the_global_switch(rig):
    # 전역 ALLOW_IMAGE_CHANGE=1 은 _envp 로 이후 재개 줄에 실려 S11 의 앱 가드(포탈 릴리스 되돌림 방지)까지 연다.
    rig.world(installed_world())
    rig.scenario({"detect": {"envoy-gateway": "D< upgrade 1 PASS"}, "guard-images.sh": {"rc": 3}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S8", "--adopt", "db")
    assert r.returncode == 1 and stages(r) == [("S8", "FAIL")], r.all
    # (감지가 upgrade 면 allow-image 로 부르므로 이 경로는 rc 3 을 흉내 낸 것 — 안내 문구만 본다)
    assert "--only S8 --adopt db --upgrade-addon envoy-gateway" in r.all, r.all
    assert "S11 의 앱 가드까지" in r.all and "ALLOW_IMAGE_CHANGE=1 로 다시" not in r.all
    assert not [c for c in mutations(rig) if "addons/envoy-gateway" in c]


def test_app_guard_refusal_keeps_the_app_advice(rig):
    rig.world(installed_world())
    rig.scenario({"guard-images.sh": {"rc": 3}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S11")
    assert r.returncode == 1 and "DMS_TAG·DMS_AGENT_TAG·MFU_TAG" in r.all and "'live'" in r.all, r.all
    assert "--upgrade-addon" not in r.all


@pytest.mark.parametrize("stage, item, layer, det_key", [
    ("S8", "envoy-gateway", "addons/envoy-gateway", "envoy-gateway"),
    ("S9", "cnpg", "addons/cnpg", "cnpg"),
    ("S4", "registry", "platform/registry", "registry"),
])
def test_upgrade_addon_reapply_opens_only_that_layer_guard(rig, stage, item, layer, det_key):
    # 같은 버전에서 lock digest 만 바뀐 D= 를 --upgrade-addon 으로 다시 적용하면 감지 action 은 reapply 다 — 그것도 의도한 교체다.
    rig.world(installed_world())
    rig.scenario({"detect": {det_key: "D= reapply 1 PASS"}})
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", stage, "--upgrade-addon", item)
    assert r.returncode == 0, r.all
    g = [a for a in rig.helper_calls("guard-images.sh") if a[0].endswith(layer)]
    assert g and "--allow-image-change" in g[0], g
    # 플래그 없이 reapply 인 층(레지스트리 — 매번 reapply)은 가드를 열지 않는다
    rig.reset_logs()
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", stage)
    g = [a for a in rig.helper_calls("guard-images.sh") if a[0].endswith(layer)]
    assert all("--allow-image-change" not in a for a in g), g


def test_s5_with_the_lock_in_place_shows_only_the_dms_images(rig):
    rig.world(installed_world())
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S5", FAKE_CURL_404="dms-agent:d7")
    assert r.returncode == 1, r.all
    assert rig.helper_calls("push-images.sh") == [] and "skopeo copy --preserve-digests docker://src" not in r.stdout
    assert "lock 이미지는 모두 10.96.0.50:5000 에 lock digest 로 있다" in r.stdout
    assert "같은 커밋의 DMS 저장소 루트에서" in r.stdout and "dms-agent:d7(HTTP 404)" in r.all


def test_external_tls_registry_push_and_compare_use_tls(rig):
    ext = ["reg.example:5000/dms:d7", "reg.example:5000/dms-agent:d7", "reg.example:5000/dms-mpifileutils:d7"]
    w = installed_world(app_images=ext, api_image=ext[0])
    rig.world(w)
    rig.scenario({"detect": {"registry": "- skip 0 INFO"}, "images-lock.py compare-registry": {"rc": 1}})
    env = {"FAKE_RMODE": "external", "FAKE_RCIP": "", "FAKE_REGISTRY": "reg.example:5000", "FAKE_ADDON_REGISTRY": "reg.example:5000",
           "FAKE_TLS": "true"}
    r = rig.run("--context", "known", "--only", "S5", FAKE_CURL_404="dms:d7", **env)
    assert r.returncode == 1, r.all
    cr = rig.helper_calls("images-lock.py")[0]
    assert cr[:3] == ["compare-registry", "--registry-url", "https://reg.example:5000"]
    push = rig.helper_calls("push-images.sh")[0]
    assert push[:2] == ["--registry", "reg.example:5000"] and push[push.index("--dest-tls-verify") + 1] == "true", push
    assert "--dest-tls-verify=true docker://<옛 레지스트리>" in r.stdout
    # 평문(기본)·클러스터 내 레지스트리는 --dest-tls-verify 를 싣지 않는다(push-images.sh 기본 false)
    rig.reset_logs()
    r = rig.run("--context", "known", "--only", "S5", **dict(env, FAKE_TLS="false"))
    assert "--dest-tls-verify" not in rig.helper_calls("push-images.sh")[0]
    assert rig.helper_calls("images-lock.py")[0][2] == "http://reg.example:5000"


def test_only_the_installer_writes_result_at_column_0(rig):
    rig.world(installed_world())
    loud = {"out": "== 도우미 ==\nRESULT: WARN\nPASS: 도우미 줄"}
    rig.scenario({n: dict(loud) for n in ("images-lock.py", "pull-probe.py", "shared-storage-probe.py", "pg-path-probe.py",
                                          "selinux-probe.py", "pg-secrets.py")})
    r = rig.run("--context", "known", "--site", "testbed-k8s")
    assert r.returncode == 0, r.all
    col0 = [ln for ln in r.stdout.splitlines() if ln.startswith(("RESULT:", "DETECT:", "PLAN:", "PASS:", "== 도우미"))]
    assert col0 == ["RESULT: PASS"], col0
    assert "    RESULT: WARN" in r.stdout                                                # 도우미 줄은 들여 써서 그대로 보인다


def test_help_shows_the_header_and_exits_0(rig):
    r = rig.run("--help")
    assert r.returncode == 0, r.all
    for needle in ("--upgrade-addon", "ADDON_INSTALL", "SELINUX_PROBE_IMAGE", "VERIFY_NO_PROBES", "10 PAUSE", "S9b"):
        assert needle in r.stdout, needle
    assert not r.stdout.startswith("#!") and "\n# " not in r.stdout                  # 주석 기호를 떼어 보인다
    assert rig.kube() == [] and rig.helpers() == []
    r = rig.run("--context", "known", "--bogus")
    assert r.returncode == 2 and "sh deploy/install.sh --help" in r.stderr


# --- 정적 ------------------------------------------------------------------------------------------------------------------------

def _code(path):
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))


def test_installer_is_posix_sh():
    dash = shutil.which("dash") or "sh"
    for name in ("install.sh", "preflight-cluster.sh", "verify.sh", "host-net-probe.sh", "push-images.sh"):
        r = subprocess.run([dash, "-n", str(DEPLOY / name)], capture_output=True, text=True)
        assert r.returncode == 0, (name, r.stderr)


def test_old_ingress_gates_and_host_push_are_gone():
    code = _code(DEPLOY / "install.sh")
    for gone in ("metallb", "ingressclass", "ingress-nginx", "podman", "docker push", "--dry-run\" ]"):
        assert gone not in code.lower(), gone
    assert "PORTAL_TLS_SECRET" in code                                           # TLS 게이트는 render.env 의 이름으로


# --- 실제 렌더러와 이어서(옛 install.sh in-cluster 게이트 테스트 — 2026-11-03 ④ 리뷰 2 — 를 S5·S11 로 옮김) ------------------------------

def _prod_values(tmp_path):
    text = (DEPLOY / "overlays" / "prod" / "values.env.example").read_text()
    text = re.sub(r"\bREPLACE_([A-Z_]+)", lambda m: "v-" + m.group(1).lower().replace("_", "-"), text)
    text = re.sub(r"(?m)^SHARED_DIR=\S+", "SHARED_DIR=/v-shared-dir", text)
    text = re.sub(r"(?m)^REGISTRY_CLUSTER_IP=\S+", "REGISTRY_CLUSTER_IP=10.96.0.50", text)
    path = tmp_path / "prod.values.env"
    path.write_text(text)
    return str(path)


PROD_APP = ["10.96.0.50:5000/dms:v-dms-tag", "10.96.0.50:5000/dms-agent:v-dms-agent-tag",
            "10.96.0.50:5000/dms-mpifileutils:v-mfu-tag"]


def test_real_prod_render_s5_passes_when_every_app_tag_is_in_the_registry(tmp_path):
    rig = Rig(tmp_path, real_render=True)
    rig.world(installed_world(app_images=PROD_APP, api_image=PROD_APP[0]))
    r = rig.run("--context", "known", "--only", "S5", VALUES_ENV=_prod_values(tmp_path))
    assert r.returncode == 0, r.all
    for img in PROD_APP:
        assert "레지스트리에 %s 있음" % img.split("/", 1)[1] in r.stdout
    assert [c for c in cluster_calls(rig) if "port-forward" in c] == ["--context known -n dms port-forward svc/dms-registry :5000"]
    curls = rig.logs["curl"].read_text().splitlines()
    assert len(curls) == 3 and all(" -I " in f" {c} " and "application/vnd.oci.image.manifest.v1+json" in c for c in curls), curls
    cr = rig.helper_calls("images-lock.py")[0]
    assert cr[:3] == ["compare-registry", "--registry-url", "http://127.0.0.1:41234"]
    det = rig.helper_calls("install-detect.py")[0]
    assert det[det.index("--rendered") + 1].endswith("/.rendered/prod")            # 렌더 디렉터리(… /app 이 아니라 사이트)


def test_real_prod_render_stops_before_apply_when_s5_is_missing(tmp_path):
    rig = Rig(tmp_path, real_render=True)
    rig.world(installed_world(app_images=PROD_APP, api_image=PROD_APP[0]))
    r = rig.run("--context", "known", "--only", "S11", VALUES_ENV=_prod_values(tmp_path), FAKE_CURL_404="dms-agent:v-dms-agent-tag")
    assert r.returncode == 1 and "S5 미완" in r.all and "dms-agent:v-dms-agent-tag(HTTP 404)" in r.all, r.all
    assert not [c for c in mutations(rig) if " apply " in f" {c} "]


def test_real_testbed_render_is_pinned_to_dmsk8s(tmp_path):
    rig = Rig(tmp_path, real_render=True)
    rig.world(installed_world(contexts=["known", "dmsk8s"]))
    r = rig.run("--context", "known", "--site", "testbed-k8s", "--only", "S1")
    assert r.returncode == 2 and "dmsk8s" in r.all                                # 오버레이 kube-context 고정과 다르다
    rig.reset_logs()
    r = rig.run("--context", "dmsk8s", "--site", "testbed-k8s", "--only", "S1")
    assert r.returncode == 0, r.all
    pf = rig.helper_calls("preflight-cluster.sh")[0]
    assert pf[:2] == ["--context", "dmsk8s"] and pf[pf.index("--render-env") + 1].endswith("/.rendered/testbed-k8s/render.env")
    assert_context(rig, "dmsk8s")


# --- preflight-cluster.sh(S1) — 새 점검 항목 ---------------------------------------------------------------------------------------

_PREFLIGHT_KUBECTL = r'''#!/bin/sh
printf "%s\n" "$*" >> "$FAKE_KUBE_LOG"
[ "$1" = config ] && { [ "$3" = known ] && exit 0; exit 1; }
shift 2
case "$*" in
  "version -o json") printf '{"serverVersion": {"gitVersion": "%s"}}' "$FAKE_VER"; exit 0 ;;
  version) exit 0 ;;
  "get nodes -l node-role.kubernetes.io/control-plane --no-headers") printf 'cp1 Ready\ncp2 Ready\ncp3 Ready\n'; exit 0 ;;
  "get nodes -l node-role.kubernetes.io/control-plane -o jsonpath="*) printf 'cp1:node-role.kubernetes.io/control-plane=NoSchedule, '; exit 0 ;;
  "get crd jobs.batch.volcano.sh -o jsonpath="*) printf 'v1alpha1'; exit 0 ;;
  "get crd jobs.batch.volcano.sh"|"get crd queues.scheduling.volcano.sh") [ -z "${FAKE_NO_VOLCANO:-}" ]; exit $? ;;
  "get ns dms") [ -n "${FAKE_NS_ENFORCE+x}" ]; exit $? ;;
  "get ns dms -o jsonpath="*) printf '%s' "${FAKE_NS_ENFORCE:-}"; exit 0 ;;
  "get deploy -A -l app=volcano-scheduler -o jsonpath="*) printf 'docker.io/volcanosh/vc-scheduler:v1.15.0'; exit 0 ;;
  "get servicecidr -o jsonpath="*) printf '%s' "$FAKE_CIDRS"; exit 0 ;;
  "get ipaddresses.networking.k8s.io "*) [ -n "$FAKE_PARENT" ] || exit 1; printf '%s' "$FAKE_PARENT"; exit 0 ;;
  "get svc -A -o jsonpath="*) printf '%s' "${FAKE_SVCS:-}"; exit 0 ;;
esac
exit 1
'''


def _preflight(tmp_path, ver="v1.34.6", cidrs="10.96.0.0/12 ", parent="", rcip="10.96.0.50", shared="/srv/shared", svcs="",
               mode="in-cluster", registry="", tls="false", **env_over):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    (b / "kubectl").write_text(_PREFLIGHT_KUBECTL)
    (b / "kubectl").chmod(0o755)
    renv = tmp_path / "render.env"
    renv.write_text(f"REGISTRY_MODE={mode}\nREGISTRY_CLUSTER_IP={rcip}\nREGISTRY_PORT=5000\nSHARED_DIR={shared}\n"
                    f"REGISTRY={registry or (rcip + ':5000' if rcip else '')}\nREGISTRY_TLS_VERIFY={tls}\n")
    log = tmp_path / "kube.log"
    if log.exists():
        log.unlink()
    env = {"PATH": f"{b}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path), "FAKE_KUBE_LOG": str(log),
           "FAKE_VER": ver, "FAKE_CIDRS": cidrs, "FAKE_PARENT": parent, "FAKE_SVCS": svcs, "PREFLIGHT_SKIP_DETECT": "1", **env_over}
    r = subprocess.run(["sh", str(DEPLOY / "preflight-cluster.sh"), "--context", "known", "--render-env", str(renv)],
                       env=env, capture_output=True, text=True, timeout=60)
    return r, log.read_text().splitlines()


def test_preflight_passes_a_good_cluster_and_prints_the_node_checklist(tmp_path):
    r, calls = _preflight(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "[PASS] K8s minor 1.34~1.36 안(v1.34.6)" in r.stdout
    assert "하단 정적 대역(오프셋 50 < 256)" in r.stdout and "10.96.0.50 미사용" in r.stdout
    assert "SHARED_DIR=/srv/shared 형식" in r.stdout
    assert any("-l app=volcano-scheduler" in c for c in calls)                    # 상류 라벨(옛 app.kubernetes.io/name 은 늘 빈 결과)
    assert not any("app.kubernetes.io/name=volcano-scheduler" in c for c in calls)
    for needle in ("getenforce", "pinned_images", "crictl inspecti 10.96.0.50:5000/registry:", "NO_PROXY 에 10.96.0.50",
                   "findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS -T /srv/shared", "ip_unprivileged_port_start", "signature_policy"):
        assert needle in r.stdout, needle
    assert "MetalLB 미설치" not in r.stdout and "IngressClass 'nginx' 없음" not in r.stdout
    assert [c for c in calls if not (c.startswith("--context known ") or c.startswith("config get-contexts"))] == []


@pytest.mark.parametrize("kw, needle", [
    ({"ver": "v1.33.2"}, "[FAIL] K8s v1.33.2 — 1.34~1.36 밖"),
    ({"ver": "v1.37.0"}, "[FAIL] K8s v1.37.0 — 1.34~1.36 밖"),
    ({"rcip": "10.200.0.50"}, "[FAIL] REGISTRY_CLUSTER_IP 10.200.0.50 가 ServiceCIDR"),
    ({"parent": "kube-system/kube-dns"}, "[FAIL] REGISTRY_CLUSTER_IP 10.96.0.50 를 kube-system/kube-dns 가 쓴다"),
    ({"svcs": "10.96.0.50 team/other\n"}, "[FAIL] REGISTRY_CLUSTER_IP 10.96.0.50 를 Service team/other 가 쓴다"),
    ({"shared": "/srv//x"}, "[FAIL] SHARED_DIR='/srv//x'"),
    # 2026-11-03 ⑦ 리뷰 1: Volcano 층이 아직 없어 이 줄이 A 사이트를 S1 에서 멈추는 preflight 근거다(P01), PSA 비특권 ns(P02)
    ({"FAKE_NO_VOLCANO": "1"}, "[FAIL] Volcano 미설치"),
    ({"FAKE_NS_ENFORCE": "baseline"}, "[FAIL] ns 'dms' enforce=baseline"),
    ({"FAKE_NS_ENFORCE": "restricted"}, "[FAIL] ns 'dms' enforce=restricted"),
])
def test_preflight_fails_on_the_s1_items(tmp_path, kw, needle):
    r, _ = _preflight(tmp_path, **kw)
    assert r.returncode == 1 and needle in r.stdout, r.stdout


@pytest.mark.parametrize("enforce, needle", [("privileged", "[PASS] ns 'dms' enforce=privileged"),
                                             ("", "[WARN] ns 'dms' 존재하나 PSA enforce 라벨 없음")])
def test_preflight_psa_of_an_existing_dms_namespace(tmp_path, enforce, needle):
    r, _ = _preflight(tmp_path, FAKE_NS_ENFORCE=enforce)
    assert r.returncode == 0 and needle in r.stdout, r.stdout


def test_preflight_checks_the_registry_ip_from_env_alone(tmp_path):
    # prod README §0: 레지스트리 주소는 S0-4·S0-5(전 노드 drop-in·마스터 반입 이름)에 박히기 전에 고른다 — 렌더 없이 env 만으로 §5 를 잰다.
    b = tmp_path / "bin"
    b.mkdir()
    (b / "kubectl").write_text(_PREFLIGHT_KUBECTL)
    (b / "kubectl").chmod(0o755)
    log = tmp_path / "kube.log"
    env = {"PATH": f"{b}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path), "FAKE_KUBE_LOG": str(log),
           "FAKE_VER": "v1.34.6", "FAKE_CIDRS": "10.96.0.0/12 ", "FAKE_PARENT": "kube-system/kube-dns", "FAKE_SVCS": "",
           "PREFLIGHT_SKIP_DETECT": "1", "REGISTRY_MODE": "in-cluster", "REGISTRY_CLUSTER_IP": "10.96.0.10"}
    r = subprocess.run(["sh", str(DEPLOY / "preflight-cluster.sh"), "--context", "known"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 1 and "[FAIL] REGISTRY_CLUSTER_IP 10.96.0.10 를 kube-system/kube-dns 가 쓴다" in r.stdout, r.stdout


def test_preflight_warns_outside_the_static_band_and_accepts_its_own_registry(tmp_path):
    r, _ = _preflight(tmp_path, rcip="10.96.5.0")
    assert r.returncode == 0 and "하단 정적 대역 밖" in r.stdout
    r, _ = _preflight(tmp_path, parent="dms/dms-registry")
    assert r.returncode == 0 and "DMS 레지스트리 — 재실행" in r.stdout


@pytest.mark.parametrize("tls, trust", [("false", 'location="registry.corp.example:5000" insecure=true'),
                                        ("true", "/etc/containers/certs.d/registry.corp.example:5000/ca.crt")])
def test_preflight_external_site_checklist_names_the_site_registry(tmp_path, tls, trust):
    # 2026-11-03 ⑦ 리뷰 2: external 사이트에 클러스터 내 레지스트리 전용 확인(자리표시자 <REGISTRY_CLUSTER_IP>·pinned_images·crictl inspecti)을
    # 보였다 — 만족시킬 수 없는 줄이고, 정작 사이트 REGISTRY 신뢰·NO_PROXY 확인은 없었다.
    r, _ = _preflight(tmp_path, rcip="", mode="external", registry="registry.corp.example:5000", tls=tls)
    assert r.returncode == 0, r.stdout
    out = r.stdout
    assert "<REGISTRY_CLUSTER_IP>" not in out and "pinned_images" not in out and "crictl inspecti" not in out, out
    assert trust in out, out
    assert "NO_PROXY 에 registry.corp.example(" in out and "reject 기본이면 registry.corp.example:5000 허용" in out


def test_preflight_in_cluster_checklist_is_unchanged_without_a_render_env(tmp_path):
    # 값을 모르는 손 실행(렌더 없이·env 없이)은 in-cluster 자리표시자 틀을 그대로 보인다(기본 모드).
    b = tmp_path / "bin"
    b.mkdir()
    (b / "kubectl").write_text(_PREFLIGHT_KUBECTL)
    (b / "kubectl").chmod(0o755)
    env = {"PATH": f"{b}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(tmp_path), "FAKE_KUBE_LOG": str(tmp_path / "k.log"),
           "FAKE_VER": "v1.34.6", "FAKE_CIDRS": "10.96.0.0/12 ", "FAKE_PARENT": "", "FAKE_SVCS": "", "PREFLIGHT_SKIP_DETECT": "1"}
    r = subprocess.run(["sh", str(DEPLOY / "preflight-cluster.sh"), "--context", "known"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0 and "crictl inspecti <REGISTRY_CLUSTER_IP>:5000/registry:" in r.stdout, r.stdout
