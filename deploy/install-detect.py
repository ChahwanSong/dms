#!/usr/bin/env python3
"""애드온 감지 상태 기계 + 설치 계획(2026-11-03 ⑦, 설계 v6 8-2 「감지 → 없으면 설치」·8-1 S1·S3·S4·S8·S9·S9b·S10).

  python3 deploy/install-detect.py --context <ctx> detect [--rendered deploy/.rendered/<site>] [--json | --json-out <파일>]
                                   [--upgrade-addon <x>]... [--adopt <x>]... [--namespace dms] [--versions-env <파일>]
  python3 deploy/install-detect.py --context <ctx> plan --rendered deploy/.rendered/<site> [위와 같은 플래그]
  python3 deploy/install-detect.py --context <ctx> vcjob [--rendered …]   # S9b: Volcano 판정 + DMS 모양 vcjob server dry-run 만
                                   (install.sh 가 Volcano 적용 뒤 부른다 — RESULT 는 volcano 항목과 그 층만으로, 2026-11-03 ⑦)

이 스크립트는 **판정만** 한다 — 클러스터를 바꾸지 않는다(kubectl 은 get·version·kustomize 와 `--dry-run=server` 인 apply·create 만 부른다;
래퍼가 그 밖의 호출을 거절한다). 설치(SSA·롤아웃·대기)는 install.sh 몫이고, 여기는 항목마다 「무엇을 할지(action)」 계획을 낸다.

항목 x(설치 순서 — S4 → S8 → S9 → S9b → S10):
  priorityclass  PriorityClass dms-addon-critical(platform/priorityclass)        — 늘 DMS 소유
  registry       Deployment·Service dms-registry(platform/registry, in-cluster 만) — 늘 DMS 소유. 버전 = 라벨 dms.io/addon-version
  traefik        클러스터의 Traefik(IngressClass + DaemonSet/Deployment)          — 남의 것. **DMS 는 설치·변경하지 않는다**(2026-11-16 —
                 입구는 클러스터에 이미 있는 Traefik 하나; Envoy Gateway 경로 폐기). 있으면 C(재사용), IngressClass 가 없거나 Traefik 이
                 아니면 X(운영자에게 — RKE2 는 rke2-traefik), 워크로드를 못 찾거나 Ready 0 이면 U(최종 판정은 S8 의 ingress-probe)
  ingress        Ingress dms/dms-portal-traefik(platform/ingress)                — 늘 DMS 소유(Traefik 이 집는 DMS 객체는 이것 하나)
  cnpg           CRD clusters.postgresql.cnpg.io + 오퍼레이터(addons/cnpg)        — 서드파티. 고정 = versions.env CNPG_VERSION
  volcano        CRD 3종 + volcano-scheduler·controllers(addons/volcano)          — 서드파티. 고정 = versions.env VOLCANO_VERSION
  db             Cluster dms-pg·SC dms-pg-shared·PV dms-pgdata-*·CronJob dms-pg-dump(platform/db-*) — 늘 DMS 소유

코드(설계 8-2 표)와 동작(action — install.sh 가 읽는다):
  A    없음                       auto → install(적용) / ADDON_INSTALL=never → stop(FAIL「클러스터 운영자에게 요청」, 서드파티만)
  D=   DMS 소유·같은 버전          서드파티 → none(no-op), 플랫폼 → reapply(SSA 멱등 — 사이트 값 변경 반영); --upgrade-addon x → reapply
  D<   DMS 소유·낮음              keep(WARN「--upgrade-addon x」) / --upgrade-addon x → upgrade(막는 사유가 있으면 stop — CNPG minor 하나씩 등)
  D>   DMS 소유·높음              keep(WARN — 다운그레이드하지 않는다)
  C    남이 설치·호환              reuse — 컨트롤러·CRD 는 건드리지 않는다(apply 를 부르지 않는다 — plan 의 dry-run 도 그 층은 건너뛴다)
  C!   남이 설치·EOL·범위 밖·경고  reuse + WARN + 소유자 요청 문구
  X    비호환                      stop(FAIL + 사유)
  U    버전 모름                   verify — 최종 판정은 DMS 가 만들 CR 의 server dry-run(plan): 통과하면 WARN, 실패하면 FAIL
  P    부분 설치·중복·이름 충돌    stop(FAIL) / --adopt x 이고 채택할 수 있는 모양이면 adopt(WARN — SSA --force-conflicts 로 dms-install 소유로)
  -    해당 없음(external 레지스트리 등) skip
  ?    읽기 실패(권한 등)          stop(UNKNOWN — 모름 ≠ 실패)
  apply = action 이 install·reapply·upgrade·adopt 인가(install.sh 가 그 층을 SSA 하는가).

출력: 사람용 표 + 항목별 근거, 그리고 줄 단위 기계 출력
  DETECT: <항목> <코드> <action> <apply 0|1> <판정>
  PLAN: <층> <PASS|FAIL|SKIP> <항목>                       (plan 만)
  RESULT: <PASS|WARN|FAIL|UNKNOWN>                         (마지막 줄)
  --json 이면 표 대신 JSON 하나만 stdout 에(items·layers·result), --json-out <파일> 이면 표와 함께 그 파일에 JSON.
종료 코드(S7·pg-path-probe 와 같은 규약 — 모름 ≠ 실패): 0 PASS · 3 WARN 만 · 1 FAIL 하나라도 · 4 모름(FAIL 없음) · 2 사용법·환경(클러스터를
건드리기 전 — 컨텍스트 없음·kube-context 불일치·고정값 못 읽음·클러스터에 못 붙음).

plan(S3): 감지 뒤 렌더된 사이트 디렉터리(deploy/render.sh 의 deploy/.rendered/<site>)의 층을 설치 순서대로 server dry-run 한다 — 애드온·플랫폼
층은 `kubectl apply --server-side --dry-run=server --field-manager=dms-install -k <층>`(adopt 면 --force-conflicts), 앱 오버레이는 지금처럼
client-side(`apply --dry-run=server -k app` — 포탈 릴리스가 strategic-merge 로 image 를 바꾼다, 설계 8-2). Volcano 는 렌더 층 대신 DMS 모양
vcjob 을 `create --dry-run=server`(S9b — 웹훅 8443 경로). 그 층의 action 이 적용이 아니면(none·keep·reuse·verify·stop) 건너뛴다 — **C 인 서드파티
층에는 dry-run 조차 부르지 않는다**(Traefik 은 층 자체가 없다 — DMS 가 설치하지 않는다). 단 소비 층(traefik → platform/ingress, cnpg →
platform/db-cluster, volcano → vcjob)의 dry-run 은 그 애드온의 최종 판정이다(설계 8-2 「최종 판정은 DMS 가 만들 CR 의 server-side dry-run」):
실패하면 C·D·U 라도 FAIL, U 는 통과해야 WARN(Traefik 의 U 는 Ingress dry-run 이 Traefik 을 확인하지 못하므로 S8 ingress-probe 가 최종 판정).
**첫 설치 함정**(kubernetes#83562 — server dry-run 은 같은 묶음의 Namespace 를 저장하지 않는다): `namespaces "X" not found` 가 그 층 자신이 만들
네임스페이스(Namespace 객체가 층 안에 있음)이거나 S2 가 만드는 DMS 네임스페이스면 실패가 아니라 `SKIP — ns X will be created`, 아직 설치 전인
애드온의 CRD 때문인 `no matches for kind` 도 같은 SKIP 이다(그 밖의 객체는 통과했다는 뜻). 판정이 stop 인 애드온(ADDON_INSTALL=never 의 A·P·X·?)의
CRD 때문인 `no matches for kind` 는 날것의 FAIL 이 아니라 그 애드온 이름을 단 SKIP 이다(「cnpg 판정 FAIL(P stop) — Cluster 는 확인하지
않는다」 — vcjob 층과 같은 처리, 그 애드온의 FAIL 이 이미 사유다; 2026-11-03 T9 — 그 층이 FAIL 이면 같은 소비 층의 멀쩡한 애드온까지
「최종 판정 FAIL」로 번졌다). **앱 업그레이드 함정**(2026-11-03 ⑦ 리뷰 1): 앱 층의
이미 끝난 Job dms-migrate 는 spec 을 바꿀 수 없어 태그를 올린 dry-run 이 `field is immutable` 이다 — S11 이 apply 전에 지우고 다시 만드는 그
Job 이라 앱 층은 PASS(그 사유를 적어서)로 본다.

감지 세부(왜 그렇게 보는지는 각 함수 docstring):
  - 「DMS 소유」 = 라벨 app.kubernetes.io/managed-by=dms-install(설계 8-2 A — addons·platform 층이 붙인다). 버전은 dms.io/addon-version, 없으면
    이미지 태그(@digest 앞), 없으면 app.kubernetes.io/version(「latest」 제외).
  - CNPG 는 **어디에든 있으면 두 번째를 설치하지 않는다**(#34): CRD 만 있거나 오퍼레이터만 있어도 A 가 아니라 P, 오퍼레이터 둘 이상도 P.
    오퍼레이터는 라벨 app.kubernetes.io/name=cloudnative-pg · 이름 cnpg-controller-manager · 이미지 이름 cloudnative-pg 중 하나로 찾는다(Helm·OLM·
    손 설치 모두). watch 범위 = Secret > ConfigMap(--secret-name·--config-map-name, 기본 cnpg-controller-manager-config) > env WATCH_NAMESPACE —
    Secret 은 WATCH_NAMESPACE 키 하나만 jsonpath 로 읽는다(다른 값은 읽지도 찍지도 않는다).
  - Volcano 스케줄러는 이름 volcano-scheduler · 라벨 app=volcano-scheduler(상류 매니페스트의 실제 라벨) · 이미지 vc-scheduler 로 찾는다 —
    preflight-cluster.sh 가 보던 app.kubernetes.io/name=volcano-scheduler 는 상류에 없는 라벨이라 늘 빈 결과였다(2026-11-03 ⑦ 버그 수정).
  - Traefik(2026-11-16)은 IngressClass <INGRESS_CLASS>(render.env, 기본 traefik)의 spec.controller == traefik.io/ingress-controller 와, 아무
    ns 의 DaemonSet·Deployment 중 컨테이너 이미지 이름(저장소 경로의 마지막 조각)에 traefik 이 든 워크로드로 찾는다(RKE2 rke2-traefik =
    kube-system DaemonSet, 이미지 rancher/hardened-traefik). 정적 설정은 그 컨테이너의 args 로 읽는다(차트·RKE2 는 args 로 준다 — 플래그는 대소문자를
    가리지 않는다): --providers.kubernetesingress·--entryPoints.<TRAEFIK_ENTRYPOINT>.address 가 없으면 X, http.tls·proxyProtocol·forwardedHeaders·
    readTimeout 은 근거(notes)와 경고. --configFile 등 args 밖이면 판정하지 않고 경고(C!). Traefik 은 바꾸지 않는다 — 고칠 곳은 사이트의 Traefik
    설정(Rancher chartValues·HelmChartConfig kube-system/rke2-traefik — deploy/README §2g).
  - 플랫폼 항목은 이름이 같은데 DMS 라벨이 없으면 P(이름 충돌 — --adopt 로만 채택), 바꿀 수 없는 필드(PriorityClass value·preemptionPolicy,
    Service clusterIP, SC·PV — pg-path-probe.py 의 pv-check 를 그대로 부른다)가 다르면 X — **조용히 다시 만들지
    않는다**(설계 8-2 「레지스트리·DB 층·PriorityClass」 행).
kubectl 은 모든 클러스터 호출에 --context 를 붙인다(필수 — 두 테스트베드가 같은 네임스페이스라 current-context 에 기대지 않는다). --context 가 없으면
KUBE_CONTEXT, 둘 다 없으면 2. 설치 호스트의 python3(RHEL 9 = 3.9), 표준 라이브러리만.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
FIELD_MANAGER = "dms-install"
OWNER = "dms-install"
MANAGED_BY = "app.kubernetes.io/managed-by"
ADDON_VERSION = "dms.io/addon-version"

ITEMS = ("priorityclass", "registry", "traefik", "ingress", "cnpg", "volcano", "db")
THIRD_PARTY = ("traefik", "cnpg", "volcano")
STAGES = {"priorityclass": "S4", "registry": "S4", "traefik": "S8", "ingress": "S8", "cnpg": "S9", "volcano": "S9b", "db": "S10"}
# 2026-11-16 에 없앤 항목(Envoy Gateway 입구 — Gateway API CRD·EG·platform/gateway). --upgrade-addon·--adopt 에 오면 바뀐 이름을 알려 준다.
RETIRED_ITEMS = ("gateway-api", "envoy-gateway", "gateway")

CNPG_CRD = "clusters.postgresql.cnpg.io"
CNPG_FLOOR = (1, 25, 0)            # dataDurability(설계 #28 — preferred)가 들어온 버전. 그 아래는 DMS Cluster 를 못 만든다 → X
CNPG_DEFAULT_CONFIG = "cnpg-controller-manager-config"
# 입구(2026-11-16 — 클러스터의 Traefik 하나). IngressClass·진입점 이름은 render.env(values.env INGRESS_CLASS·TRAEFIK_ENTRYPOINT)가 이긴다.
TRAEFIK_CONTROLLER = "traefik.io/ingress-controller"
DEFAULT_INGRESS_CLASS = "traefik"
DEFAULT_ENTRYPOINT = "websecure"
INGRESS_NAME = "dms-portal-traefik"   # platform/ingress — base 앱의 Ingress dms-portal(class nginx, 테스트베드 되돌림)과 다른 이름
VOLCANO_CRDS = ("jobs.batch.volcano.sh", "queues.scheduling.volcano.sh", "podgroups.scheduling.volcano.sh")
VOLCANO_FLOOR = (1, 15, 0)         # 설계 8-2 「≥ v1.15」(테스트베드 실증 v1.15.0)
VOLCANO_FOR_K8S_136 = (1, 16, 0)   # 「1.36 은 v1.16 부터」(6-5)
VOLCANO_SCHEDULER_NAME = "volcano"  # DMS vcjob 의 schedulerName(execution_manifests.py)
# versions.env 에 VOLCANO_VERSION 이 아직 없을 때의 고정값(설계 8-1 S9b·7-5 ③ — ⑦ 의 Volcano 층이 versions.env 에 덧붙이면 그 값이 이긴다).
VOLCANO_DEFAULT_PIN = "v1.16.0"
ALL_CRDS = (CNPG_CRD,) + VOLCANO_CRDS
# DMS 애드온 층이 만들고 라벨(PSA 등)을 붙이는 네임스페이스(addons/<x> — 설계 8-1 S1 「애드온 ns 를 남이 쓰는지」).
ADDON_NAMESPACES = {"cnpg": "cnpg-system", "volcano": "volcano-system"}

PC_NAME = "dms-addon-critical"
REGISTRY_NAME = "dms-registry"
PG_CLUSTER = "dms-pg"
PG_SC = "dms-pg-shared"
PG_PVS = tuple("dms-pgdata-0%d" % n for n in range(1, 6))
PG_CRON = "dms-pg-dump"
# 지원 K8s minor(고정 버전을 **설치할 때**의 경고 — S1 이 K8s 1.34~1.36 을 따로 본다). CNPG 1.30 = 1.34~1.36(6-5).
K8S_RANGE = {"cnpg": {(1, 30): (34, 36)}}

BASE_LEVEL = {"A": "PASS", "D=": "PASS", "D<": "WARN", "D>": "WARN", "C": "PASS", "C!": "WARN", "X": "FAIL", "U": "WARN",
              "P": "FAIL", "-": "INFO", "?": "UNKNOWN"}
_RANK = {"INFO": 0, "PASS": 1, "SKIP": 0, "WARN": 2, "UNKNOWN": 3, "FAIL": 4}
EXIT = {"PASS": 0, "INFO": 0, "WARN": 3, "FAIL": 1, "UNKNOWN": 4}
APPLY_ACTIONS = ("install", "reapply", "upgrade", "adopt")


class UsageError(Exception):
    pass


class KubeError(Exception):
    pass


# --- 작은 도우미 ----------------------------------------------------------------------------------------------------------------

def _meta(obj):
    return (obj or {}).get("metadata") or {}


def _labels(obj):
    return _meta(obj).get("labels") or {}


def _owned(obj):
    return _labels(obj).get(MANAGED_BY) == OWNER


def _ref(obj):
    m = _meta(obj)
    return "%s/%s" % (m.get("namespace"), m.get("name")) if m.get("namespace") else str(m.get("name"))


def _podspec(dep):
    return (((dep or {}).get("spec") or {}).get("template") or {}).get("spec") or {}


def _containers(dep):
    return _podspec(dep).get("containers") or []


_VER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text):
    """「v1.9.3」「1.30.1」「v1.16」 → (1, 9, 3). 버전 모양이 아니면(latest·distroless·빈 값) None."""
    if not text:
        return None
    m = _VER.match(str(text).strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def image_tag(image):
    """「reg:5000/a/b:1.2@sha256:…」 → 「1.2」. 태그가 없으면(digest 만) None. 레지스트리 포트의 : 는 태그가 아니다."""
    last = str(image or "").split("@", 1)[0].rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def image_base(image):
    """「ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1」 → 「cloudnative-pg」(저장소 경로의 마지막 조각)."""
    return str(image or "").split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]


def _flag_values(tokens, flag):
    """args 안의 `--flag=값`·`--flag 값` 값들(나온 순서)."""
    out = []
    for i, tok in enumerate(tokens):
        if tok.startswith(flag + "="):
            out.append(tok[len(flag) + 1:])
        elif tok == flag and i + 1 < len(tokens):
            out.append(tokens[i + 1])
    return out


def _fmt(v):
    return "%d.%d.%d" % v if v else "?"


def compare(installed, pinned):
    if installed == pinned:
        return "D="
    return "D<" if installed < pinned else "D>"


def read_env_file(path):
    """`KEY=값`·`KEY="값"` 줄만(sh 로 . 하지 않는다 — versions.env·render.env 공용)."""
    values = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r'^([A-Z][A-Z0-9_]*)="?([^"\n]*)"?\s*$', line)
            if m:
                values[m.group(1)] = m.group(2)
    return values


def _unquote(text):
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    return t


# --- kubectl(읽기 전용 래퍼) ------------------------------------------------------------------------------------------------------

_READ_VERBS = ("get", "version", "kustomize")


class Kube:
    """모든 호출에 --context 를 붙이는 kubectl 래퍼. 클러스터를 바꾸는 호출은 거절한다(dry-run=server 인 apply·create 만 예외)."""

    def __init__(self, context):
        if not context:
            raise UsageError("--context 가 필요하다")
        self.context = context

    def _exec(self, args, stdin=None, timeout=120):
        verb = args[0]
        if verb not in _READ_VERBS and not (verb in ("apply", "create") and "--dry-run=server" in args):
            raise RuntimeError("install-detect 는 클러스터를 바꾸지 않는다 — 허용되지 않은 kubectl 호출: %s" % " ".join(args))
        cmd = ["kubectl", "--context", self.context] + list(args)
        try:
            r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", "kubectl %s: 시간 초과(%ss)" % (" ".join(args[:3]), timeout)
        except OSError as exc:
            raise UsageError("kubectl 을 실행하지 못했다: %s" % exc)
        return r.returncode, r.stdout, r.stderr

    def run(self, args, stdin=None, timeout=120):
        rc, out, err = self._exec(args, stdin=stdin, timeout=timeout)
        if rc != 0:
            last = ((err or out).strip().splitlines() or ["rc=%d" % rc])[-1]
            raise KubeError("kubectl %s: %s" % (" ".join(args), last))
        return out

    def items(self, *args):
        """`get … -o json` → 객체 목록(빈 출력 = 없음, List 면 items, 객체 하나면 [그것])."""
        text = self.run(list(args) + ["-o", "json"])
        if not text.strip():
            return []
        data = json.loads(text)
        return data.get("items") or [] if "items" in data else [data]

    def one(self, *args):
        found = self.items(*(list(args) + ["--ignore-not-found"]))
        return found[0] if found else None

    def jsonpath(self, args, path):
        return self.run(list(args) + ["-o", "jsonpath=" + path]).strip()

    def dry_run(self, args, stdin=None):
        return self._exec(args, stdin=stdin, timeout=300)

    @staticmethod
    def context_exists(context):
        try:
            r = subprocess.run(["kubectl", "config", "get-contexts", context], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0


# --- 고정값(저장소) -------------------------------------------------------------------------------------------------------------

def _grep(path, pattern):
    with open(path, encoding="utf-8") as fh:
        m = re.search(pattern, fh.read(), re.M)
    return m.group(1) if m else None


def load_pins(versions_env=None):
    """설치할 버전·값의 원천: deploy/versions.env(CNPG·PG·Volcano) + platform 매니페스트(레지스트리 라벨·PriorityClass). 하나라도 못 읽으면
    UsageError(2) — 고정값 없이 D=/D< 를 가를 수 없다. 입구(Traefik)는 DMS 가 설치하지 않으므로 고정값이 없다(2026-11-16 — 옛
    Gateway API·Envoy Gateway 버전 키는 요구하지 않는다)."""
    path = versions_env or os.path.join(HERE, "versions.env")
    try:
        env = read_env_file(path)
        pg_image = env.get("PG_IMAGE") or ""
        pins = {
            "cnpg": env.get("CNPG_VERSION"),
            "volcano": env.get("VOLCANO_VERSION") or VOLCANO_DEFAULT_PIN,
            "db": image_tag(pg_image) if image_base(pg_image) == "postgresql" else None,
            "registry": _grep(os.path.join(HERE, "platform", "registry", "deployment.yaml"),
                              r'^\s+dms\.io/addon-version:\s*"?([^"\s]+)"?'),
            "pc_value": _grep(os.path.join(HERE, "platform", "priorityclass", "priorityclass.yaml"), r"^value:\s*(\d+)"),
            "pc_preemption": _grep(os.path.join(HERE, "platform", "priorityclass", "priorityclass.yaml"),
                                   r"^preemptionPolicy:\s*(\S+)"),
        }
    except OSError as exc:
        raise UsageError("고정값 파일을 읽지 못했다: %s" % exc)
    missing = [k for k, v in pins.items() if not v]
    if missing:
        raise UsageError("고정값을 읽지 못했다: %s (versions.env = %s)" % (", ".join(missing), path))
    for key in ("cnpg", "volcano", "registry"):
        if parse_version(pins[key]) is None:
            raise UsageError("고정 버전 모양이 아니다: %s=%r" % (key, pins[key]))
    pins["pc_value"] = int(pins["pc_value"])
    return pins


# --- 판정 결과 ------------------------------------------------------------------------------------------------------------------

class Finding:
    def __init__(self, item, pinned=None):
        self.item = item
        self.code = None
        self.message = ""
        self.notes = []          # 근거·안내(판정을 바꾸지 않는다)
        self.warnings = []       # WARN 으로 올리는 사유(C 면 C! 로)
        self.installed = None
        self.pinned = pinned
        self.where = None
        self.owner = None        # "dms-install" | "foreign" | None
        self.adoptable = False
        self.upgrade_blocker = None
        self.action = None
        self.level = None
        self.apply = False

    def set(self, code, message, **kw):
        self.code, self.message = code, message
        for key, value in kw.items():
            setattr(self, key, value)
        return self

    def to_dict(self):
        return {"item": self.item, "stage": STAGES[self.item], "third_party": self.item in THIRD_PARTY, "code": self.code,
                "level": self.level, "action": self.action, "apply": self.apply, "installed": self.installed,
                "pinned": self.pinned, "owner": self.owner, "where": self.where, "message": self.message,
                "warnings": list(self.warnings), "notes": list(self.notes)}


def decide(f, addon_install, upgrade, adopt):
    """코드 → action·level(설계 8-2 「동작」 열). ADDON_INSTALL 은 서드파티 A 에만 — 플랫폼 층은 DMS 가 늘 소유한다."""
    third = f.item in THIRD_PARTY
    if f.code == "C" and f.warnings:
        f.code = "C!"
    level = BASE_LEVEL[f.code]
    code = f.code
    if code == "A":
        if third and addon_install == "never":
            action, level = "stop", "FAIL"
            f.notes.append("ADDON_INSTALL=never — DMS 가 설치하지 않는다. 클러스터 운영자에게 요청: %s %s 설치(설계 8-2 A)"
                           % (f.item, f.pinned or ""))
        else:
            action = "install"
    elif code == "D=":
        action = "reapply" if (not third or f.item in upgrade) else "none"
        if third and f.item in upgrade:
            f.notes.append("--upgrade-addon: 같은 버전을 다시 적용한다(DMS 패치 층 변경 반영)")
    elif code == "D<":
        if f.item in upgrade and f.upgrade_blocker:
            action, level = "stop", "FAIL"
            f.notes.append("업그레이드를 막는 사유: %s" % f.upgrade_blocker)
        elif f.item in upgrade:
            action, level = "upgrade", "PASS"
        else:
            action = "keep"
            f.notes.append("올리려면 --upgrade-addon %s(설계 6-5 — 점검 창에서)" % f.item)
    elif code == "D>":
        action = "keep"
        f.notes.append("다운그레이드하지 않는다 — 고정값(versions.env·렌더)을 라이브에 맞춰 올리거나 그대로 둔다")
    elif code in ("C", "C!"):
        action = "reuse"
        if f.item in adopt:
            f.notes.append("--adopt 는 P 에만 — 남이 설치한 호환 애드온은 채택하지 않고 재사용한다")
        if f.item in upgrade:
            f.notes.append("남이 설치한 애드온의 업그레이드는 소유자 몫(설계 6-5)")
        if code == "C!" and f.pinned:
            f.notes.append("소유자에게 요청: %s 을(를) DMS 고정 %s 와 같은 지원 범위로 올려 달라(DMS 는 실행할 때마다 경고한다)"
                           % (f.item, f.pinned))
        elif code == "C!":
            # 고정 버전이 없는 남의 것(traefik — DMS 는 설치·변경하지 않는다): 버전이 아니라 위 WARN 의 설정을 소유자가 고친다.
            f.notes.append("소유자에게 요청: 위 WARN 을 해소해 달라 — DMS 는 %s 을(를) 바꾸지 않는다(실행할 때마다 경고한다)" % f.item)
    elif code == "X":
        action = "stop"
    elif code == "U":
        action = "verify"
        if f.item == "traefik":
            # Ingress 의 server dry-run 은 Traefik 을 부르지 않는다(Traefik 웹훅 없음) — 노드 :443 경유 실측이 최종 판정이다.
            f.notes.append("Traefik 을 확인하지 못했다 — 최종 판정은 S8 의 ingress-probe(Traefik 이 도는 노드 :443 경유 readyz·인증서, "
                           "deploy/README §2g)")
        else:
            f.notes.append("버전을 모른다 — 최종 판정은 DMS 가 만들 CR 의 server dry-run(plan): 통과하면 WARN")
    elif code == "P":
        if f.item in adopt and f.adoptable:
            action, level = "adopt", "WARN"
            f.notes.append("--adopt: dms-install 로 SSA(--force-conflicts) — 이후 DMS 소유(D)로 본다")
        else:
            action = "stop"
            f.notes.append("채택할 수 없는 모양이다(손으로 정리)" if f.item in adopt else
                           ("--adopt %s 일 때만 채택한다" % f.item if f.adoptable else "운영자가 정리해야 한다(--adopt 로도 채택하지 않는다)"))
    elif code == "-":
        action = "skip"
    else:  # "?"
        action = "stop"
    if f.warnings and level in ("PASS", "INFO"):
        level = "WARN"
    f.action, f.level, f.apply = action, level, action in APPLY_ACTIONS
    return f


# --- 클러스터 상태 ----------------------------------------------------------------------------------------------------------------

class State:
    """감지 공통 읽기(한 번씩): 서버 버전, 관심 CRD, 모든 Deployment, 모든 Namespace + (traefik 항목용) IngressClass·모든 DaemonSet.

    IngressClass·DaemonSet 읽기 실패(권한 등)는 공통 실패(모름 4 — 어느 항목도 판정 못 함)로 번지지 않게 담아 두었다가 그것을 쓰는 항목(traefik)만
    「?」로 만든다(read() — 모름 ≠ 실패)."""

    def __init__(self, kube, namespace):
        self.kube = kube
        self.ns = namespace
        try:
            ver = json.loads(kube.run(["version", "-o", "json"]))
        except (KubeError, ValueError) as exc:
            raise UsageError("클러스터에 붙지 못했다(--context %s): %s" % (kube.context, exc))
        server = ver.get("serverVersion") or {}
        self.k8s = server.get("gitVersion") or "?"
        m = re.match(r"(\d+)", str(server.get("minor") or ""))
        pv = parse_version(self.k8s)
        self.k8s_minor = int(m.group(1)) if m else (pv[1] if pv else None)
        self.crds = {_meta(c).get("name"): c for c in kube.items("get", "crd", *ALL_CRDS, "--ignore-not-found")}
        self.deploys = kube.items("get", "deploy", "-A")
        self.namespaces = {_meta(n).get("name"): n for n in kube.items("get", "ns")}
        self._optional = {}
        for key, args in (("ingressclasses", ("get", "ingressclass")), ("daemonsets", ("get", "ds", "-A"))):
            try:
                self._optional[key] = kube.items(*args)
            except (KubeError, ValueError) as exc:
                self._optional[key] = KubeError(str(exc))

    def read(self, key):
        """IngressClass·DaemonSet 목록(공통 읽기 때 담아 둔 것) — 읽기 실패였으면 그때의 KubeError 를 다시 던진다(그 항목만 「?」)."""
        value = self._optional[key]
        if isinstance(value, Exception):
            raise value
        return value

    def deploy(self, ns, name):
        for d in self.deploys:
            if _meta(d).get("namespace") == ns and _meta(d).get("name") == name:
                return d
        return None


def _version_of(dep, container=None, owned=None):
    """DMS 소유면 dms.io/addon-version, 아니면 이미지 태그, 없으면 app.kubernetes.io/version(latest 제외) → (텍스트, 파싱)."""
    labels = _labels(dep)
    owned = _owned(dep) if owned is None else owned
    candidates = []
    if owned:
        candidates.append(labels.get(ADDON_VERSION))
    cont = container or (_containers(dep)[0] if _containers(dep) else {})
    candidates.append(image_tag(cont.get("image")))
    candidates.append(labels.get("app.kubernetes.io/version"))
    for text in candidates:
        if parse_version(text):
            return text, parse_version(text)
    return None, None


def _ns_conflict(f, state, item):
    """A 경로인데 그 애드온 층의 네임스페이스를 남이 이미 쓰면 P(이름 충돌 — DMS 층이 그 네임스페이스에 라벨을 붙이고 객체를 넣는다).
    --adopt <x> 면 채택(그 네임스페이스에 설치)."""
    ns = ADDON_NAMESPACES.get(item)
    obj = state.namespaces.get(ns)
    if obj is None or _owned(obj):
        return False
    f.set("P", "설치할 네임스페이스 %s 를 남이 쓴다(DMS 라벨 없음 — 이름 충돌, 설계 8-1 S1). DMS 층이 그 네임스페이스에 라벨(PSA 등)을 붙이고 "
               "객체를 넣는다" % ns, where="ns " + ns, owner="foreign", adoptable=True)
    return True


def _k8s_note(f, state, item, pinned_v):
    rng = K8S_RANGE.get(item, {}).get(pinned_v[:2])
    if rng and state.k8s_minor is not None and not (rng[0] <= state.k8s_minor <= rng[1]):
        f.warnings.append("K8s %s 는 %s %s 의 지원 범위(1.%d~1.%d) 밖이다" % (state.k8s, item, f.pinned, rng[0], rng[1]))


# CNPG -------------------------------------------------------------------------------------------------------------------------

def _is_cnpg_operator(dep):
    if _labels(dep).get("app.kubernetes.io/name") == "cloudnative-pg" or _meta(dep).get("name") == "cnpg-controller-manager":
        return True
    return any(image_base(c.get("image")) == "cloudnative-pg" for c in _containers(dep))


def _cnpg_manager(dep):
    for c in _containers(dep):
        if c.get("name") == "manager" or image_base(c.get("image")) == "cloudnative-pg":
            return c
    return (_containers(dep) or [{}])[0]


def cnpg_watch(kube, dep):
    """오퍼레이터의 실효 WATCH_NAMESPACE → (목록 — []는 모든 네임스페이스, None = 모름, 출처).

    CNPG 는 env → ConfigMap → Secret 순으로 덮는다(--config-map-name·--secret-name, 기본 cnpg-controller-manager-config — 상류 Deployment 의
    args). Secret 은 그 키 하나만 jsonpath 로 읽는다(나머지 값은 받지도 않는다). envFrom 의 configMapRef·secretRef 도 같은 키를 본다."""
    c = _cnpg_manager(dep)
    ns = _meta(dep).get("namespace")
    tokens = [str(t) for t in (c.get("command") or []) + (c.get("args") or [])]
    value, source, unknown = None, None, None
    failed = []

    def read(kind, name):
        try:
            return kube.jsonpath(["get", kind, name, "-n", ns, "--ignore-not-found"], "{.data.WATCH_NAMESPACE}")
        except KubeError as exc:
            failed.append("%s %s/%s 읽기 실패(%s)" % (kind, ns, name, exc))
            return ""
    for ref in c.get("envFrom") or []:
        for kind, key in (("configmap", "configMapRef"), ("secret", "secretRef")):
            name = (ref.get(key) or {}).get("name")
            if name:
                got = read(kind, name)
                if got:
                    value = base64.b64decode(got).decode("utf-8", "replace") if kind == "secret" else got
                    source = "envFrom %s %s" % (kind, name)
    for e in c.get("env") or []:
        if e.get("name") == "WATCH_NAMESPACE":
            if "valueFrom" in e:
                unknown, value, source = "env WATCH_NAMESPACE 가 valueFrom 이다(값을 알 수 없다 — OLM 등)", None, None
            else:
                value, source, unknown = e.get("value") or "", "env", None
    cm = (_flag_values(tokens, "--config-map-name") or [CNPG_DEFAULT_CONFIG])[-1]
    sec = (_flag_values(tokens, "--secret-name") or [CNPG_DEFAULT_CONFIG])[-1]
    got = read("configmap", cm)
    if got:
        value, source, unknown = got, "ConfigMap %s/%s" % (ns, cm), None
    got = read("secret", sec)
    if got:
        try:
            value = base64.b64decode(got).decode("utf-8", "replace")
        except ValueError:
            value = None
            unknown = "Secret %s/%s 의 WATCH_NAMESPACE 를 풀지 못했다" % (ns, sec)
        else:
            source, unknown = "Secret %s/%s" % (ns, sec), None
    if unknown or failed:
        return None, "; ".join(([unknown] if unknown else []) + failed)
    if not value:
        return [], (source or "설정 없음") + " — 모든 네임스페이스"
    return [x.strip() for x in value.split(",") if x.strip()], source


def detect_cnpg(kube, state, pins):
    """CNPG(S9, 설계 8-2 CNPG 행·#34 「없을 때만 설치」).

    어디에든 흔적이 있으면 A 가 아니다 — CRD 만 있거나 오퍼레이터만 있으면 P(부분 설치·삭제 잔재), 오퍼레이터 둘 이상이면 P(이중 오퍼레이터는
    웹훅·리컨사일이 경합한다 — --adopt 로도 풀지 않는다). 하한 1.25(dataDurability) 미만·watch 범위에 dms 없음은 X. 지원 minor = 고정 minor 와
    그 하나 아래(CNPG 는 최근 두 minor 를 지원) — 그 밖(EOL 또는 고정보다 새 것)은 C!. DMS 소유면 고정과 비교해 D=/D</D>, 업그레이드는 minor
    하나씩(6-5 — 건너뛰면 stop)."""
    pin = parse_version(pins["cnpg"])
    f = Finding("cnpg", pins["cnpg"])
    crd = state.crds.get(CNPG_CRD)
    ops = [d for d in state.deploys if _is_cnpg_operator(d)]
    if not crd and not ops:
        if _ns_conflict(f, state, "cnpg"):
            return f
        f.set("A", "CNPG 없음(CRD %s·오퍼레이터 0) — 고정 %s 를 설치한다" % (CNPG_CRD, pins["cnpg"]))
        _k8s_note(f, state, "cnpg", pin)
        return f
    if len(ops) >= 2:
        return f.set("P", "CNPG 오퍼레이터가 %d개다(%s) — 이중 오퍼레이터(웹훅·리컨사일 경합). 하나만 남기도록 운영자에게(두 번째를 설치하지 않는다)"
                     % (len(ops), ", ".join(_ref(d) for d in ops)), where=", ".join(_ref(d) for d in ops))
    if not ops:
        return f.set("P", "CRD %s 는 있는데 오퍼레이터가 없다(부분 설치·삭제 잔재) — 두 번째를 설치하지 않는다" % CNPG_CRD,
                     where=CNPG_CRD, owner="dms-install" if _owned(crd) else "foreign", adoptable=True)
    op = ops[0]
    owned = _owned(op)
    text, ver = _version_of(op, _cnpg_manager(op), owned)
    f.set(None, "", where=_ref(op), owner="dms-install" if owned else "foreign", installed=text)
    if not crd:
        return f.set("P", "오퍼레이터 %s 는 있는데 CRD %s 가 없다(부분 설치)" % (_ref(op), CNPG_CRD), adoptable=owned)
    watch, source = cnpg_watch(kube, op)
    if watch is None:
        f.warnings.append("watch 범위를 확인하지 못했다(%s) — dms 를 보는지 소유자에게 확인" % source)
    elif watch and state.ns not in watch:
        return f.set("X", "오퍼레이터 %s 의 watch 범위(%s: %s)에 %s 가 없다 — Cluster %s 를 리컨사일하지 않는다(설계 8-2)"
                     % (_ref(op), source, ",".join(watch), state.ns, PG_CLUSTER))
    else:
        f.notes.append("watch: %s" % ("%s: %s" % (source, ",".join(watch)) if watch else source))
    if ver is None:
        return f.set("U", "오퍼레이터 %s 의 버전을 모른다(이미지 %s)" % (_ref(op), _cnpg_manager(op).get("image")))
    if ver < CNPG_FLOOR:
        return f.set("X", "CNPG %s 는 하한 1.25(dataDurability — 설계 #28) 미만이다" % text)
    if owned:
        code = compare(ver, pin)
        if code == "D<" and (ver[0] != pin[0] or pin[1] - ver[1] > 1):
            f.upgrade_blocker = "minor 는 하나씩 올린다(6-5 — %s → %d.%d 를 먼저)" % (text, ver[0], ver[1] + 1)
        _k8s_note(f, state, "cnpg", pin)
        return f.set(code, "DMS 가 설치한 CNPG %s(%s) — 고정 %s" % (text, _ref(op), pins["cnpg"]))
    if ver[:2] > pin[:2]:
        f.warnings.append("DMS 고정 %s 보다 새 minor 다(검증 범위 밖)" % pins["cnpg"])
    elif (ver[0], ver[1] + 1) < pin[:2]:
        f.warnings.append("지원 minor(%d.%d·%d.%d) 밖 — EOL" % (pin[0], pin[1] - 1, pin[0], pin[1]))
    return f.set("C", "남이 설치한 CNPG %s(%s) — 재사용(오퍼레이터·CRD 는 건드리지 않는다, 두 번째를 설치하지 않는다)" % (text, _ref(op)))


# Traefik(입구 — 2026-11-16: Envoy Gateway 폐기, 클러스터에 이미 있는 Traefik 하나) -----------------------------------------------------

def _is_traefik(workload):
    """컨테이너 이미지 이름(저장소 경로의 마지막 조각)에 traefik 이 든 워크로드 — rancher/hardened-traefik(RKE2)·traefik(상류·차트) 모두.
    레지스트리 호스트 이름에 traefik 이 든 남의 이미지를 잡지 않게 마지막 조각만 본다."""
    return any("traefik" in image_base(c.get("image")).lower() for c in _containers(workload))


def _traefik_container(workload):
    for c in _containers(workload):
        if "traefik" in image_base(c.get("image")).lower():
            return c
    return (_containers(workload) or [{}])[0]


def _ready_count(kind, workload):
    """DaemonSet numberReady · Deployment readyReplicas(없으면 0 — 아직 상태가 없는 새 워크로드)."""
    status = workload.get("status") or {}
    value = status.get("numberReady") if kind == "DaemonSet" else status.get("readyReplicas")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def traefik_static(tokens):
    """컨테이너 command+args 의 Traefik 정적 설정 → {소문자 키: 값}. `--a.b=값`·`--a.b 값`·`--a.b`(참). Traefik 플래그는 대소문자를 가리지
    않는다(차트는 --entryPoints.websecure.…, 옛 차트·손 설치는 --entrypoints.… 를 쓴다)."""
    out = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--") and len(tok) > 2:
            key, sep, value = tok[2:].partition("=")
            if not sep:
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    value = tokens[i + 1]
                    i += 1
                else:
                    value = "true"
            out[key.lower()] = value
        i += 1
    return out


def _flag_on(cfg, key):
    """그 절이 켜졌는가 — 키 자체(값 false 가 아님) 또는 그 아래 키가 하나라도 있으면(Traefik 은 하위 옵션을 주면 그 절이 켜진다)."""
    if key in cfg:
        return str(cfg[key]).lower() != "false"
    return any(k.startswith(key + ".") for k in cfg)


def _static_known(cfg):
    """정적 설정을 args 로 읽을 수 있는가 — --configFile 이 있거나 진입점·공급자 플래그가 하나도 없으면 설정이 args 밖(파일·env)이다."""
    if "configfile" in cfg:
        return False
    return any(k.startswith(("entrypoints.", "providers.")) for k in cfg)


def detect_traefik(state, render):
    """클러스터의 Traefik(S8, 2026-11-16 설계 D5) — **DMS 는 Traefik 을 설치·변경하지 않는다**(있으면 C, 없으면 X·U 뿐 — A 는 없다).

    IngressClass <INGRESS_CLASS>(render.env, 기본 traefik)가 없거나 spec.controller 가 traefik.io/ingress-controller 가 아니면 X(운영자에게 —
    RKE2 는 rke2-traefik). 워크로드(아무 ns 의 DaemonSet·Deployment 중 이미지 이름에 traefik)를 못 찾거나 Ready 0 이면 U(최종 판정은 S8
    ingress-probe). args 의 정적 설정: --providers.kubernetesingress 없음(또는 --providers.kubernetesingress.ingressClass 가 다른 이름)·
    --entryPoints.<TRAEFIK_ENTRYPOINT>.address 없음 → X. http.tls 없음 → note(DMS Ingress 의 router.tls 어노테이션이 켠다), proxyProtocol.trustedIPs
    → note(없으면 warning — LB 가 PROXY v2 를 붙이면 그 출발지가 있어야 한다), proxyProtocol.insecure=true → warning, forwardedHeaders·readTimeout →
    note. 고칠 곳은 사이트의 Traefik 설정(Rancher chartValues·HelmChartConfig kube-system/rke2-traefik)이다."""
    ic = (render or {}).get("INGRESS_CLASS") or DEFAULT_INGRESS_CLASS
    ep = ((render or {}).get("TRAEFIK_ENTRYPOINT") or DEFAULT_ENTRYPOINT).lower()
    f = Finding("traefik")
    owner_note = ("입구는 클러스터의 Traefik 하나다(2026-11-16) — 클러스터 운영자가 Traefik(IngressClass %s, controller %s)을 설치·관리한다. "
                  "RKE2 는 rke2-traefik(kube-system DaemonSet). DMS 는 Ingress %s/%s 만 만든다(deploy/README §2g)"
                  % (ic, TRAEFIK_CONTROLLER, state.ns, INGRESS_NAME))
    classes = {_meta(c).get("name"): c for c in state.read("ingressclasses")}
    obj = classes.get(ic)
    if obj is None:
        have = ", ".join("%s(%s)" % (n, (c.get("spec") or {}).get("controller") or "?") for n, c in sorted(classes.items())) or "없음"
        f.notes.append(owner_note)
        return f.set("X", "IngressClass %s 없음(있는 IngressClass: %s) — DMS 는 Traefik 을 설치하지 않는다. 클러스터 운영자에게 요청하거나 "
                          "values.env INGRESS_CLASS 를 Traefik 의 IngressClass 이름으로" % (ic, have), where="ingressclass " + ic)
    controller = (obj.get("spec") or {}).get("controller")
    if controller != TRAEFIK_CONTROLLER:
        f.notes.append(owner_note)
        return f.set("X", "IngressClass %s 의 controller 가 %s 다 — Traefik(%s)이 아니다(values.env INGRESS_CLASS 를 Traefik 의 IngressClass "
                          "이름으로)" % (ic, controller or "없음", TRAEFIK_CONTROLLER), where="ingressclass " + ic)
    workloads = [("DaemonSet", d) for d in state.read("daemonsets") if _is_traefik(d)] + \
                [("Deployment", d) for d in state.deploys if _is_traefik(d)]
    if not workloads:
        return f.set("U", "IngressClass %s(%s)는 있는데 Traefik 워크로드(이미지 이름에 traefik 이 든 DaemonSet·Deployment)를 찾지 못했다"
                     % (ic, TRAEFIK_CONTROLLER), where="ingressclass " + ic)
    judged = []
    for kind, w in workloads:
        c = _traefik_container(w)
        judged.append((kind, w, c, traefik_static([str(t) for t in (c.get("command") or []) + (c.get("args") or [])])))

    def label(j):
        return "%s(%s)" % (_ref(j[1]), j[0])

    def serves(cfg):
        wanted = cfg.get("providers.kubernetesingress.ingressclass")
        return _flag_on(cfg, "providers.kubernetesingress") and (not wanted or wanted == ic)
    known = [j for j in judged if _static_known(j[3])]
    unknown = [j for j in judged if not _static_known(j[3])]
    serving = [j for j in known if serves(j[3])]
    if not serving and not unknown:
        why = []
        for j in known:
            if not _flag_on(j[3], "providers.kubernetesingress"):
                why.append("%s 에 --providers.kubernetesingress 가 없다(Ingress 를 읽지 않는다)" % label(j))
            else:
                why.append("%s 의 --providers.kubernetesingress.ingressClass=%s(DMS Ingress 의 class 는 %s)"
                           % (label(j), j[3].get("providers.kubernetesingress.ingressclass"), ic))
        f.notes.append(owner_note)
        return f.set("X", "Traefik 이 Ingress(class %s)를 처리하지 않는다: %s — 사이트의 Traefik 설정에서 켠다(Rancher chartValues·HelmChartConfig "
                          "— DMS 는 바꾸지 않는다)" % (ic, "; ".join(why)), where=", ".join(label(j) for j in known), owner="foreign")
    pick = serving[0] if serving else unknown[0]
    kind, w, c, cfg = pick
    f.set(None, "", where=label(pick), owner="foreign", installed=image_tag(c.get("image")))
    if len(serving) > 1:
        f.warnings.append("Ingress(class %s)를 처리하는 Traefik 워크로드가 둘 이상이다(%s) — LB 풀이 어느 것을 가리키는지 확인"
                          % (ic, ", ".join(label(j) for j in serving)))
    if serving:
        p = "entrypoints.%s." % ep
        address = cfg.get(p + "address")
        if not address:
            others = sorted({k.split(".")[1] for k in cfg if k.startswith("entrypoints.") and k.endswith(".address")})
            f.notes.append(owner_note)
            return f.set("X", "%s 에 진입점 %s 가 없다(--entryPoints.%s.address 없음; 있는 진입점: %s) — values.env TRAEFIK_ENTRYPOINT 를 TLS "
                              "진입점 이름으로" % (label(pick), ep, ep, ", ".join(others) or "없음"))
        f.notes.append("진입점 %s address %s · IngressClass %s(%s)" % (ep, address, ic, TRAEFIK_CONTROLLER))
        if not _flag_on(cfg, p + "http.tls"):
            f.notes.append("진입점 %s 에 http.tls 가 없다 — DMS Ingress 의 router.tls 어노테이션이 그 라우터의 TLS 를 켠다" % ep)
        trusted = cfg.get(p + "proxyprotocol.trustedips")
        if trusted:
            f.notes.append("proxyProtocol.trustedIPs=%s — 이 출발지(LB SE)의 PROXY v2 헤더만 원 IP 로 쓴다(헤더 없는 연결도 받는다)" % trusted)
        else:
            f.warnings.append("진입점 %s 에 proxyProtocol.trustedIPs 가 없다 — LB 가 PROXY v2 를 붙이면 trustedIPs 에 LB 출발지(SE)가 있어야 한다"
                              "(없으면 PROXY 바이트가 TLS 앞에 붙어 연결이 깨진다). LB 가 PROXY 를 붙이지 않으면 그대로 둔다 — Rancher chartValues·"
                              "HelmChartConfig(사이트 몫, deploy/README §2g)" % ep)
        if str(cfg.get(p + "proxyprotocol.insecure", "")).lower() == "true":
            f.warnings.append("진입점 %s 의 proxyProtocol.insecure=true — 아무 출발지의 PROXY 헤더를 믿는다(원 IP 위조 가능)" % ep)
        if str(cfg.get(p + "forwardedheaders.insecure", "")).lower() == "true":
            f.notes.append("forwardedHeaders.insecure=true — 클라이언트의 X-Forwarded-* 를 지우지 않는다(앱은 XFF 마지막 항목 = Traefik 이 덧붙인 "
                           "실제 피어를 쓴다)")
        if cfg.get(p + "forwardedheaders.trustedips"):
            f.notes.append("forwardedHeaders.trustedIPs=%s" % cfg[p + "forwardedheaders.trustedips"])
        f.notes.append("readTimeout %s(요청 본문 읽기 상한 — 느린 큰 업로드만 끊긴다)"
                       % (cfg.get(p + "transport.respondingtimeouts.readtimeout") or "기본 60s"))
    else:
        f.warnings.append("%s 의 정적 설정이 args 밖이다(--configFile·env) — providers.kubernetesingress·진입점 %s 를 소유자에게 확인(판정하지 "
                          "않았다)" % (label(pick), ep))
    group = serving or [pick]
    ready = sum(_ready_count(k, wl) for k, wl, _, _ in group)
    if ready == 0:
        return f.set("U", "Traefik %s 의 Ready 파드가 0 이다" % ", ".join(label(j) for j in group))
    return f.set("C", "클러스터의 Traefik %s(%s, Ready %d) — 재사용: DMS 는 Traefik 을 설치·변경하지 않고 Ingress %s/%s(class %s, 진입점 %s)만 "
                      "만든다" % (f.installed or "?", label(pick), ready, state.ns, INGRESS_NAME, ic, ep))


# Volcano ----------------------------------------------------------------------------------------------------------------------

def _is_volcano(dep, name, app, image):
    return _meta(dep).get("name") == name or _labels(dep).get("app") == app or any(
        image_base(c.get("image")) == image for c in _containers(dep))


def _scheduler_names(dep):
    for c in _containers(dep):
        if image_base(c.get("image")) == "vc-scheduler" or c.get("name") == "volcano-scheduler":
            tokens = [str(t) for t in (c.get("command") or []) + (c.get("args") or [])]
            return _flag_values(tokens, "--scheduler-name") or [VOLCANO_SCHEDULER_NAME]
    tokens = [str(t) for c in _containers(dep) for t in (c.get("command") or []) + (c.get("args") or [])]
    return _flag_values(tokens, "--scheduler-name") or [VOLCANO_SCHEDULER_NAME]


def detect_volcano(state, pins):
    """Volcano(S9b, 설계 8-2 Volcano 행). CRD 3종 + 스케줄러(이름 volcano-scheduler · 라벨 app=volcano-scheduler · 이미지 vc-scheduler — 상류
    라벨은 app 이다; preflight-cluster.sh 의 app.kubernetes.io/name 셀렉터는 늘 빈 결과였다) + 컨트롤러(vc-controller-manager). 스케줄러의
    --scheduler-name(여러 번 가능, 기본 volcano)에 volcano 가 없으면 X(DMS vcjob 의 schedulerName). v1.15 미만·K8s 1.36 에 v1.16 미만은 X.
    C 면 다시 적용하지 않는다."""
    pin = parse_version(pins["volcano"])
    f = Finding("volcano", pins["volcano"])
    crds = [n for n in VOLCANO_CRDS if n in state.crds]
    scheds = [d for d in state.deploys if _is_volcano(d, "volcano-scheduler", "volcano-scheduler", "vc-scheduler")]
    ctrls = [d for d in state.deploys if _is_volcano(d, "volcano-controllers", "volcano-controller", "vc-controller-manager")]
    admission = [d for d in state.deploys if _is_volcano(d, "volcano-admission", "volcano-admission", "vc-webhook-manager")]
    if not crds and not scheds and not ctrls:
        if _ns_conflict(f, state, "volcano"):
            return f
        return f.set("A", "Volcano 없음(CRD·스케줄러 0) — 고정 %s 를 설치한다" % pins["volcano"])
    serving = [d for d in scheds if VOLCANO_SCHEDULER_NAME in _scheduler_names(d)]
    if scheds and not serving:
        return f.set("X", "Volcano 스케줄러의 이름이 %s 다 — DMS vcjob 은 schedulerName %s(설계 8-2)"
                     % (", ".join("%s=%s" % (_ref(d), ",".join(_scheduler_names(d))) for d in scheds), VOLCANO_SCHEDULER_NAME),
                     where=", ".join(_ref(d) for d in scheds))
    if len(serving) >= 2:
        return f.set("P", "schedulerName %s 인 Volcano 스케줄러가 %d개다(%s)" % (VOLCANO_SCHEDULER_NAME, len(serving),
                     ", ".join(_ref(d) for d in serving)), where=", ".join(_ref(d) for d in serving))
    missing = [n for n in VOLCANO_CRDS if n not in crds] + ([] if serving else ["volcano-scheduler"]) + \
        ([] if ctrls else ["volcano-controllers"])
    sched = serving[0] if serving else (ctrls[0] if ctrls else None)
    owned = _owned(sched) if sched is not None else False
    if missing:
        # CRD 만 남은 모양(삭제 잔재)이거나 DMS 소유면 채택(= DMS 층 적용)할 수 있다 — 남의 스케줄러가 반쯤 있으면 손으로.
        return f.set("P", "Volcano 일부만 있다(없음: %s)" % ", ".join(missing), where=_ref(sched) if sched is not None else None,
                     owner=("dms-install" if owned else "foreign") if sched is not None else None,
                     adoptable=owned or (not scheds and not ctrls))
    sched_c = next((c for c in _containers(sched) if image_base(c.get("image")) == "vc-scheduler"), None)
    text, ver = _version_of(sched, sched_c, owned)
    f.set(None, "", where=_ref(sched), owner="dms-install" if owned else "foreign", installed=text)
    if not admission:
        f.warnings.append("volcano-admission 이 없다 — vcjob 검증·기본값 웹훅 없이 돈다")
    if ver is None:
        return f.set("U", "Volcano 스케줄러 %s 의 버전을 모른다" % _ref(sched))
    too_old_for_k8s = state.k8s_minor is not None and state.k8s_minor >= 36 and ver < VOLCANO_FOR_K8S_136
    if owned:
        if too_old_for_k8s:
            f.warnings.append("K8s %s 에는 Volcano v1.16 이상이 필요하다(설계 6-5) — --upgrade-addon volcano" % state.k8s)
        return f.set(compare(ver, pin), "DMS 가 설치한 Volcano %s(%s) — 고정 %s" % (text, _ref(sched), pins["volcano"]))
    if too_old_for_k8s:
        return f.set("X", "K8s %s 에는 Volcano v1.16 이상이 필요하다(설계 6-5) — 지금 %s(소유자에게)" % (state.k8s, text))
    if ver < VOLCANO_FLOOR:
        return f.set("X", "Volcano %s 는 하한 v1.15(설계 8-2) 미만이다" % text)
    if ver[:2] > pin[:2]:
        f.warnings.append("DMS 고정 %s 보다 새 minor 다(검증 범위 밖)" % pins["volcano"])
    return f.set("C", "남이 설치한 Volcano %s(%s, schedulerName %s) — 재사용(다시 적용하지 않는다)" % (text, _ref(sched), VOLCANO_SCHEDULER_NAME))


# 플랫폼(늘 DMS 소유) ---------------------------------------------------------------------------------------------------------------

def detect_priorityclass(kube, pins):
    """PriorityClass dms-addon-critical(S4). value·preemptionPolicy 는 만든 뒤 바꿀 수 없다 — 다르면 X(조용히 지우고 다시 만들지 않는다).
    같은 이름에 DMS 라벨이 없으면 P(이름 충돌 — 값이 같으면 --adopt 로 채택)."""
    f = Finding("priorityclass", "%s/%s" % (pins["pc_value"], pins["pc_preemption"]))
    pc = kube.one("get", "priorityclass", PC_NAME)
    if pc is None:
        return f.set("A", "PriorityClass %s 없음 — 만든다" % PC_NAME)
    owned = _owned(pc)
    f.set(None, "", where=PC_NAME, owner="dms-install" if owned else "foreign",
          installed="%s/%s" % (pc.get("value"), pc.get("preemptionPolicy") or "PreemptLowerPriority"))
    diffs = []
    if pc.get("value") != pins["pc_value"]:
        diffs.append("value %r(렌더 %d)" % (pc.get("value"), pins["pc_value"]))
    if (pc.get("preemptionPolicy") or "PreemptLowerPriority") != pins["pc_preemption"]:
        diffs.append("preemptionPolicy %r(렌더 %s)" % (pc.get("preemptionPolicy"), pins["pc_preemption"]))
    if diffs:
        return f.set("X", "PriorityClass %s 가 렌더와 다르다: %s — 만든 뒤 바꿀 수 없는 필드다. 조용히 다시 만들지 않는다(누가 만든 것인지 확인한 뒤 "
                     "운영자가 지우고 다시)" % (PC_NAME, ", ".join(diffs)))
    if not owned:
        return f.set("P", "같은 이름의 PriorityClass %s 가 있다(DMS 라벨 없음 — 이름 충돌)" % PC_NAME, adoptable=True)
    return f.set("D=", "DMS 가 만든 PriorityClass %s(%s)" % (PC_NAME, f.installed))


def detect_registry(kube, state, pins, render):
    """클러스터 내 레지스트리(S4 — in-cluster 사이트만). Deployment dms-registry 의 라벨 dms.io/addon-version 으로 D=/D</D>(⑦ — BACKLOG ④).
    렌더가 있으면 Service clusterIP == REGISTRY_CLUSTER_IP(바꿀 수 없다 — 7-4), 아직 없으면 그 IP 를 다른 Service 가 쓰는지(「clusterIP 충돌」)."""
    f = Finding("registry", pins["registry"])
    layers = (render or {}).get("PLATFORM_LAYERS")
    dep = state.deploy(state.ns, REGISTRY_NAME)
    if render is not None and "registry" not in (layers or "").split():
        return f.set("-", "REGISTRY_MODE=%s, REGISTRY_CLUSTER_IP 없음 — 클러스터 내 레지스트리를 쓰지 않는 사이트(해당 없음)"
                     % (render.get("REGISTRY_MODE") or "external"))
    if render is None and dep is None:
        return f.set("-", "Deployment %s/%s 없음 — 이 사이트가 클러스터 내 레지스트리를 쓰는지는 렌더로 안다(--rendered)" % (state.ns, REGISTRY_NAME))
    rcip = (render or {}).get("REGISTRY_CLUSTER_IP")
    svc = kube.one("get", "svc", REGISTRY_NAME, "-n", state.ns) if state.ns in state.namespaces else None
    if rcip and svc is not None and (svc.get("spec") or {}).get("clusterIP") != rcip:
        return f.set("X", "Service %s/%s 의 clusterIP %s 가 REGISTRY_CLUSTER_IP %s 와 다르다 — 바꿀 수 없다(설계 7-4: 주소는 노드 신뢰·고정의 원천)"
                     % (state.ns, REGISTRY_NAME, (svc.get("spec") or {}).get("clusterIP"), rcip), where=_ref(svc))
    if rcip and svc is None:
        clash = [_ref(s) for s in kube.items("get", "svc", "-A") if (s.get("spec") or {}).get("clusterIP") == rcip]
        if clash:
            return f.set("X", "clusterIP 충돌: REGISTRY_CLUSTER_IP %s 를 %s 가 쓴다 — 다른 정적 대역 주소로(설계 7-4)" % (rcip, ", ".join(clash)))
    if dep is None:
        return f.set("A", "레지스트리 %s/%s 없음 — 만든다(S4)" % (state.ns, REGISTRY_NAME))
    owned = _owned(dep)
    text = _labels(dep).get(ADDON_VERSION) if owned else image_tag((_containers(dep) or [{}])[0].get("image"))
    f.set(None, "", where=_ref(dep), owner="dms-install" if owned else "foreign", installed=text)
    if not owned:
        return f.set("P", "같은 이름의 Deployment %s 가 있다(DMS 라벨 없음 — 이름 충돌)" % _ref(dep), adoptable=True)
    ver = parse_version(text)
    if ver is None:
        return f.set("U", "레지스트리 %s 에 dms.io/addon-version 라벨이 없다 — 버전 모름" % _ref(dep))
    return f.set(compare(ver, parse_version(pins["registry"])), "DMS 레지스트리 %s(%s) — 고정 %s" % (text, _ref(dep), pins["registry"]))


def detect_ingress(kube, state, render):
    """포탈 입구 층(platform/ingress, S8 — 2026-11-16) — Ingress dms/dms-portal-traefik(Traefik 이 집는 DMS 객체는 이것 하나). 없으면 A, DMS 라벨이면
    D=(SSA 멱등 재적용 — 사이트 값 변경 반영), 같은 이름에 DMS 라벨이 없으면 P(이름 충돌 — --adopt ingress 로만 채택). ingressClassName·host 는
    바꿀 수 있는 필드라 렌더와 달라도 SSA 가 맞춘다(근거로만 적는다)."""
    f = Finding("ingress")
    want = (render or {}).get("INGRESS_CLASS") or DEFAULT_INGRESS_CLASS
    if state.ns not in state.namespaces:
        return f.set("A", "Ingress %s/%s 없음(ns %s 는 S2 가 만든다) — S8 이 만든다(class %s)" % (state.ns, INGRESS_NAME, state.ns, want))
    ing = kube.one("get", "ingresses.networking.k8s.io", INGRESS_NAME, "-n", state.ns)
    if ing is None:
        return f.set("A", "Ingress %s/%s 없음 — S8 이 만든다(class %s)" % (state.ns, INGRESS_NAME, want))
    spec = ing.get("spec") or {}
    cls = spec.get("ingressClassName")
    hosts = ",".join(str(r.get("host")) for r in spec.get("rules") or [] if r.get("host")) or "-"
    owned = _owned(ing)
    f.set(None, "", where=_ref(ing), owner="dms-install" if owned else "foreign", installed="class %s" % (cls or "-"))
    if not owned:
        return f.set("P", "같은 이름의 Ingress %s 가 있다(DMS 라벨 없음 — 이름 충돌, class %s, host %s)" % (_ref(ing), cls or "-", hosts),
                     adoptable=True)
    if cls != want:
        f.notes.append("ingressClassName %s → 렌더 %s(SSA 가 렌더 값으로 바꾼다)" % (cls or "없음", want))
    return f.set("D=", "DMS Ingress %s(class %s, host %s)" % (_ref(ing), cls or "-", hosts))


_PG_TAG = re.compile(r"^(\d+)\.(\d+)(?:-(\d{8,14}))?")


def pg_version(tag):
    m = _PG_TAG.match(str(tag or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _load_pg_probe():
    """deploy/pg-path-probe.py(S10 pv-check 의 원천) — 같은 판정을 다시 쓰지 않고 그 함수를 부른다."""
    spec = importlib.util.spec_from_file_location("dms_pg_path_probe", os.path.join(HERE, "pg-path-probe.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _ProbeKube:
    """pg-path-probe 의 Kube.run 모양(namespaced 인자)을 이 래퍼로 잇는다 — 그 스크립트의 _pv_states·_sc_state 를 그대로 쓴다."""

    def __init__(self, kube, ns):
        self.kube, self.ns = kube, ns

    def run(self, *args, timeout=60, check=True, namespaced=True):
        return self.kube.run((["-n", self.ns] if namespaced else []) + list(args))


def detect_db(kube, state, pins, render):
    """DB 층(S10 — platform/db-volumes·db-cluster·db-backup). 앵커 = Cluster dms-pg. 같은 이름의 SC·PV·Cluster·CronJob 에 DMS 라벨이 없으면 P,
    렌더가 있으면 pg-path-probe.py 의 pv-check(이미 있는 SC·PV 의 경로·type·용량·SC 대조 — 적용 **전에** 봐야 한다: 용량·SC 이름은 SSA 가 Bound PV
    까지 조용히 바꾼다)가 FAIL 이면 X(조용히 다시 만들지 않는다). Released PV 는 WARN(건드리지 않는다). PG 버전은 Cluster imageName 태그 대
    versions.env PG_IMAGE — minor 차이 = D</D>(6-5 PG minor: digest 교체 → 롤링 + switchover), major 차이로 올리는 것은 별도 설계라 stop."""
    f = Finding("db", pins["db"])
    have_ns = state.ns in state.namespaces
    cluster = kube.one("get", CNPG_CRD, PG_CLUSTER, "-n", state.ns) if (CNPG_CRD in state.crds and have_ns) else None
    sc = kube.one("get", "sc", PG_SC)
    pvs = kube.items("get", "pv", *PG_PVS, "--ignore-not-found")
    cron = kube.one("get", "cronjob", PG_CRON, "-n", state.ns) if have_ns else None
    objs = [("Cluster", cluster), ("StorageClass", sc), ("CronJob", cron)] + [("PV", p) for p in pvs]
    foreign = ["%s %s" % (kind, _ref(o)) for kind, o in objs if o is not None and not _owned(o)]
    released = [_meta(p).get("name") for p in pvs if (p.get("status") or {}).get("phase") == "Released"]
    if released:
        f.warnings.append("Released PV %s — 건드리지 않는다(S10, 재활용 런북 deploy/README §2f-5)" % ", ".join(released))
    if render is not None and render.get("SHARED_DIR") and render.get("PG_STORAGE_SIZE") and (sc is not None or pvs):
        try:
            probe = _load_pg_probe()
            adapter = _ProbeKube(kube, state.ns)
            found = probe.check_pvs(probe._pv_states(adapter), probe._sc_state(adapter),
                                    {"shared_dir": render["SHARED_DIR"], "size": render["PG_STORAGE_SIZE"]})
        except (OSError, AttributeError, ImportError, SyntaxError) as exc:
            f.warnings.append("pv-check 를 부르지 못했다(%s) — S10 이 다시 본다" % exc)
        else:
            bad = [msg for level, msg in found if level == "FAIL"]
            if bad:
                return f.set("X", "이미 있는 SC·PV 가 렌더와 다르다(pg-path-probe pv-check): %s" % " | ".join(bad),
                             where="%s, %s" % (PG_SC, ",".join(_meta(p).get("name") for p in pvs)))
    if foreign:
        return f.set("P", "같은 이름이 있다(DMS 라벨 없음 — 이름 충돌): %s" % ", ".join(foreign), adoptable=True)
    if cluster is None:
        have = ([PG_SC] if sc is not None else []) + [_meta(p).get("name") for p in pvs]
        return f.set("A", "Cluster %s/%s 없음 — 만든다(S10)%s" % (state.ns, PG_CLUSTER, "; 이미 있음: " + ", ".join(have) if have else ""))
    image = (cluster.get("spec") or {}).get("imageName") or ""
    tag = image_tag(image)
    f.set(None, "", where=_ref(cluster), owner="dms-install", installed=tag)
    live, want = pg_version(tag), pg_version(pins["db"])
    if live is None or want is None:
        return f.set("U", "Cluster %s 의 PG 이미지 태그를 모른다(%s)" % (_ref(cluster), image or "imageName 없음"))
    if tag == pins["db"]:
        code = "D="
    elif live == want:
        code = "D<"
        f.notes.append("같은 PG 버전·다른 태그(%s) — 렌더 값으로 바꾸려면 --upgrade-addon db" % tag)
    else:
        code = compare(live, want)
    if code == "D<" and live[0] != want[0]:
        f.upgrade_blocker = "PG major(%d → %d)는 SSA 로 바꾸지 않는다 — 별도 설계(6-5: 새 Cluster + import)" % (live[0], want[0])
    return f.set(code, "DMS Cluster %s — PG %s, 고정 %s" % (_ref(cluster), tag, pins["db"]))


# --- 감지 묶음 -------------------------------------------------------------------------------------------------------------------

def detect_all(kube, state, pins, render):
    out = {}
    for item, fn in (("priorityclass", lambda: detect_priorityclass(kube, pins)),
                     ("registry", lambda: detect_registry(kube, state, pins, render)),
                     ("traefik", lambda: detect_traefik(state, render)),
                     ("ingress", lambda: detect_ingress(kube, state, render)),
                     ("cnpg", lambda: detect_cnpg(kube, state, pins)),
                     ("volcano", lambda: detect_volcano(state, pins)),
                     ("db", lambda: detect_db(kube, state, pins, render))):
        try:
            out[item] = fn()
        except (KubeError, ValueError) as exc:
            # 항목 하나의 읽기 실패(권한 등)는 그 항목만 모름 — 다른 판정은 그대로 낸다(모름 ≠ 실패).
            out[item] = Finding(item).set("?", "읽기 실패: %s" % exc)
    return out


# --- plan: 층별 server dry-run ----------------------------------------------------------------------------------------------------

LAYERS = (  # (층 — 렌더 디렉터리 기준, 항목) 설치 순서. vcjob 은 렌더 층이 아니라 DMS 모양 vcjob(S9b).
    ("platform/priorityclass", "priorityclass"),
    ("platform/registry", "registry"),
    ("platform/ingress", "ingress"),        # Traefik 은 층이 없다(DMS 가 설치하지 않는다) — 이 층의 dry-run 이 traefik 의 소비 층 판정
    ("addons/cnpg", "cnpg"),
    ("addons/volcano", "volcano"),
    ("platform/db-volumes", "db"),
    ("platform/db-cluster", "db"),
    ("platform/db-backup", "db"),
    ("vcjob", "volcano"),
    ("app", "app"),
)
CONSUMERS = {"traefik": "platform/ingress", "cnpg": "platform/db-cluster", "volcano": "vcjob"}
CRD_GROUP_ADDON = {"postgresql.cnpg.io": "cnpg", "batch.volcano.sh": "volcano", "scheduling.volcano.sh": "volcano", "bus.volcano.sh": "volcano",
                   "flow.volcano.sh": "volcano", "topology.volcano.sh": "volcano"}
_NS_MISSING = re.compile(r'namespaces? "([^"]+)" not found')
_NO_KIND = re.compile(r'no matches for kind "([^"]+)" in version "([^"/]+)/')
_WEBHOOK_HINT = {"cnpg": "apiserver → 워커 파드 9443(CNPG 웹훅 — 설계 #7: 방화벽·NetworkPolicy·CNI), 오퍼레이터 파드 Ready 먼저",
                 "volcano": "apiserver → volcano-admission 8443(설계 #7)"}


class LayerResult:
    def __init__(self, layer, item, status, detail="", args=None):
        self.layer, self.item, self.status, self.detail, self.args = layer, item, status, detail, args

    def to_dict(self):
        return {"layer": self.layer, "item": self.item, "status": self.status, "detail": self.detail,
                "command": ["kubectl", "--context", "<ctx>"] + list(self.args or [])}


def kustomize_objects(kube, path):
    """렌더 층 → [(kind, name, namespace)] — `kubectl kustomize`(로컬) 출력의 최상위 kind 와 metadata 의 name·namespace 만 읽는다. 실패하면 None."""
    try:
        text = kube.run(["kustomize", path])
    except KubeError:
        return None
    out = []
    for doc in re.split(r"(?m)^---[ \t]*$", text):
        kind = re.search(r"(?m)^kind:[ \t]*(\S+)", doc)
        meta = re.search(r"(?m)^metadata:[ \t]*\n((?:(?:[ ]{2}.*)?\n?)*)", doc)
        name = ns = None
        if meta:
            n = re.search(r"(?m)^  name:[ \t]*(\S+)", meta.group(1))
            s = re.search(r"(?m)^  namespace:[ \t]*(\S+)", meta.group(1))
            name = _unquote(n.group(1)) if n else None
            ns = _unquote(s.group(1)) if s else None
        if kind:
            out.append((kind.group(1), name, ns))
    return out


def vcjob_manifest(namespace, queue):
    """DMS 모양 vcjob(execution_manifests.build_volcano_job 의 뼈대 — schedulerName·queue·minAvailable·ssh/svc 플러그인·정책·launcher+worker)을
    server dry-run 한다(S9b — Volcano 웹훅 8443·CRD v1alpha1). 이미지는 자리표시자(dry-run 은 pull 하지 않는다)."""
    def task(name):
        return {"name": name, "replicas": 1, "template": {"spec": {"restartPolicy": "Never", "containers": [
            {"name": name, "image": "set-by-overlay.invalid/dms:install-detect", "command": ["/bin/true"]}]}}}
    return {"apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
            "metadata": {"name": "dms-install-detect-vcjob", "namespace": namespace,
                         "labels": {MANAGED_BY: OWNER, "dms.io/tool": "install-detect"}},
            "spec": {"schedulerName": VOLCANO_SCHEDULER_NAME, "queue": queue, "minAvailable": 2,
                     "plugins": {"ssh": [], "svc": []},
                     "policies": [{"event": "TaskCompleted", "action": "CompleteJob"}, {"event": "PodFailed", "action": "AbortJob"}],
                     "tasks": [task("launcher"), task("worker")]}}


MIGRATE_JOB = "dms-migrate"
_MIGRATE_IMMUTABLE = re.compile(r'\bJob(?:\.batch)? "%s" is invalid: .*field is immutable' % MIGRATE_JOB)
MIGRATE_NOTE = "Job %s 의 spec 은 바꿀 수 없다 — S11 이 지우고 다시 만든다" % MIGRATE_JOB


def _dep_note(addon, f, kinds=()):
    """의존 애드온의 판정이 stop(FAIL·모름)이라 그 애드온 CRD 의 객체는 확인하지 않는다는 SKIP 사유 — vcjob 층과 소비 층·앱 층이 같은 문구를 쓴다
    (2026-11-03 T9: 일회용 k3s 에서 애드온(CNPG·Volcano 등)이 stop 이면 소비 층·app 이 날것의 `no matches for kind` 로 FAIL 이었다)."""
    what = " — %s 는" % "·".join(kinds) if kinds else " —"
    return "%s 판정 %s(%s %s)%s 확인하지 않는다" % (addon, f.level, f.code, f.action, what)


_CONFLICT_MANAGER = re.compile(r'conflicts? with "([^"]+)"')


def conflict_managers(text):
    """SSA 충돌 오류(apiserver 문구 `conflict with "kubectl-edit" using v1: …` · `conflicts with "helm" using apps/v1:`)의 field manager 이름들."""
    return sorted(set(_CONFLICT_MANAGER.findall(text or "")))


def _classify(stderr, will_create_ns, will_install, layer=None, blocked=None):
    """dry-run 오류 줄 → (무해한 사유 목록, 진짜 오류 목록, {의존 애드온: 확인하지 않은 종류}). 무해 = 설치가 만들 네임스페이스·아직 없는 애드온
    CRD·(앱 층) 끝난 migrate Job 의 바꿀 수 없는 spec. 의존 = 판정이 stop 인 애드온(blocked)의 CRD 가 없어 생긴 `no matches for kind` — 그 애드온의
    FAIL 이 이미 사유이므로 이 층의 FAIL 로 다시 세지 않는다(T9)."""
    benign, real, deps = [], [], {}
    for line in (stderr or "").splitlines():
        line = line.strip()
        # stderr 의 줄은 경고·안내를 빼면 모두 오류다 — 「resource mapping not found … no matches for kind」 줄에는 error 라는 말이 없다
        # (2026-11-03 ⑦ dmsk8s 실측: 그 줄 + 「ensure CRDs are installed first」 + `Error from server (NotFound): namespaces "…" not found`).
        # 없는 kind 가 둘 이상이면 kubectl 이 오류를 `[a, b, c]` 로 묶어 마지막 줄이 「ensure CRDs are installed first]」가 된다(2026-11-13 OA 실측
        # — 첫 설치에서 EG CRD 3종이 한꺼번에 없을 때). 가운데 줄은 「…first, resource mapping not found …」라 아래 _NO_KIND 가 판정한다.
        if not line or line.startswith("Warning:") or line.rstrip("]") == "ensure CRDs are installed first" or line.endswith("(server dry run)"):
            continue
        # 앱 태그(또는 migrate 템플릿)를 바꾸면 이미 끝난 dms-migrate Job 의 dry-run 이 늘 `field is immutable` 이다 — S11 이 apply 전에 그 Job 을
        # 지우는 바로 그 이유다. 계획(S3·--plan)이 그보다 먼저 막으면 앱 업그레이드가 설치기로 진행되지 않는다(2026-11-03 ⑦ 리뷰 1 — dmsk8s
        # d19→d20 실측: `The Job "dms-migrate" is invalid: spec.template: Invalid value: …: field is immutable`). kubectl apply 는 한 객체가
        # 실패해도 나머지를 계속 보내므로 그 밖의 객체는 dry-run 된 것이다.
        if layer == "app" and _MIGRATE_IMMUTABLE.search(line):
            benign.append(MIGRATE_NOTE)
            continue
        m = _NS_MISSING.search(line)
        if m and m.group(1) in will_create_ns:
            benign.append("ns %s will be created(%s)" % (m.group(1), will_create_ns[m.group(1)]))
            continue
        m = _NO_KIND.search(line)
        if m and CRD_GROUP_ADDON.get(m.group(2)) in will_install:
            benign.append("CRD %s(%s) 는 %s 설치가 만든다" % (m.group(1), m.group(2), CRD_GROUP_ADDON[m.group(2)]))
            continue
        if m and CRD_GROUP_ADDON.get(m.group(2)) in (blocked or {}):
            deps.setdefault(CRD_GROUP_ADDON[m.group(2)], set()).add(m.group(1))
            continue
        real.append(line)
    return sorted(set(benign)), real, deps


def vcjob_layer(kube, state, findings, will_install=()):
    """DMS 모양 vcjob server dry-run 한 층(plan 의 「vcjob」, vcjob 모드 — install.sh S9b 가 Volcano 적용 뒤 부른다, 2026-11-03 ⑦)."""
    layer, item = "vcjob", "volcano"
    vf = findings["volcano"]
    if vf.action == "stop":
        return LayerResult(layer, item, "SKIP", _dep_note("volcano", vf))
    if "jobs.batch.volcano.sh" not in state.crds:
        return LayerResult(layer, item, "SKIP", "CRD jobs.batch.volcano.sh 는 volcano 설치가 만든다 — 설치 뒤 S9b 에서")
    if state.ns not in state.namespaces:
        return LayerResult(layer, item, "SKIP", "skipped: ns %s will be created(S2) — 그 뒤 S9b 에서" % state.ns)
    queue = "dms-data" if kube.one("get", "queues.scheduling.volcano.sh", "dms-data") is not None else "default"
    args = ["create", "--dry-run=server", "-f", "-", "-o", "name"]
    rc, out, err = kube.dry_run(args, stdin=json.dumps(vcjob_manifest(state.ns, queue)))
    r = _layer_result(layer, item, args, rc, err, {}, will_install, finding=vf, context=kube.context)
    if queue == "default":
        r.detail = (r.detail + "; " if r.detail else "") + "Queue dms-data 가 아직 없어 default 로 봤다(앱 05-*.yaml 이 만든다)"
    return r


def run_plan(kube, state, findings, rendered):
    results = []
    by_layer = {}
    will_install = {i for i, f in findings.items() if f.action in ("install", "upgrade", "adopt")}
    # 판정이 stop 인 애드온 — 그 CRD 가 없어 생긴 소비 층·앱 층의 `no matches for kind` 는 그 애드온 이름을 단 SKIP 이다(vcjob 과 같게, T9).
    blocked = {i: f for i, f in findings.items() if f.action == "stop"}
    # S2 가 deploy/k8s/00-namespace.yaml 로 DMS 네임스페이스를 만든다(설계 8-1 S2) — 그 전의 plan 이면 dms 안 객체는 「만들 예정」.
    will_create_ns = {state.ns: "S2 deploy/k8s/00-namespace.yaml"}
    consumer_of = {}
    for addon, layer in CONSUMERS.items():
        consumer_of.setdefault(layer, []).append(addon)
    for layer, item in LAYERS:
        f = findings.get(item)
        path = os.path.join(rendered, layer)
        consumers = [a for a in consumer_of.get(layer, []) if findings[a].action not in ("stop",)]
        if layer == "vcjob":
            r = vcjob_layer(kube, state, findings, will_install)
            results.append(r)
            by_layer[layer] = r
            continue
        if not os.path.isdir(path):
            if layer == "platform/registry" and findings["registry"].code == "-":
                reason = "해당 없음(external 레지스트리)"
            elif layer == "addons/volcano":
                reason = "렌더 층 없음(Volcano 층은 아직 동봉하지 않는다 — docs/BACKLOG.md 「⑦ 설치기 후속」) — %s" % ("설치 대상인데 층이 없다" if f.apply else "적용 대상 아님")
            else:
                reason = "렌더 층 없음"
            r = LayerResult(layer, item, "FAIL" if (f is not None and f.apply and layer != "addons/volcano") else "SKIP", reason)
            results.append(r)
            by_layer[layer] = r
            continue
        if f is not None and f.action == "stop":
            r = LayerResult(layer, item, "SKIP", "판정 %s — 적용하지 않는다" % f.code)
        elif f is not None and not f.apply and not consumers:
            # C·D=·D<·D>·U 인 서드파티 층은 dry-run 도 부르지 않는다(설계 8-2 「C 면 컨트롤러·CRD 는 건드리지 않음」 — 계획도 같다).
            r = LayerResult(layer, item, "SKIP", "%s — %s(적용하지 않는다)" % (f.code, f.action))
        else:
            objs = kustomize_objects(kube, path)
            ns_here = dict(will_create_ns)
            for kind, name, _ in objs or []:
                if kind == "Namespace" and name:
                    ns_here[name] = "이 층의 Namespace"
            if layer == "app":
                args = ["apply", "--dry-run=server", "-k", path]
            else:
                args = ["apply", "--server-side", "--dry-run=server", "--field-manager=" + FIELD_MANAGER, "-k", path]
                if f is not None and f.action == "adopt":
                    args.insert(4, "--force-conflicts")
            rc, out, err = kube.dry_run(args)
            r = _layer_result(layer, item, args, rc, err, ns_here, will_install, blocked=blocked, finding=f, context=kube.context)
            # 이 층이 만들 네임스페이스는 뒤 층에서도 「만들 예정」이다(적용할 층일 때만).
            if f is None or f.apply:
                for kind, name, _ in objs or []:
                    if kind == "Namespace" and name and name not in state.namespaces:
                        will_create_ns.setdefault(name, layer)
        results.append(r)
        by_layer[layer] = r
    consumer_verdicts(findings, by_layer)
    return results


def consumer_verdicts(findings, by_layer):
    """소비 층의 dry-run = 그 애드온의 최종 판정(설계 8-2) — plan 과 vcjob 모드가 같이 쓴다."""
    for addon, layer in CONSUMERS.items():
        if addon not in findings:
            continue
        f, r = findings[addon], by_layer.get(layer)
        # 판정 대상은 「이미 있는」 애드온이다(C·C!·U·D*) — 설치·채택할 것(A·P)이면 소비 층 실패는 그 층의 FAIL 로만 남긴다.
        if r is None or f.action == "stop" or f.code in ("A", "P"):
            continue
        if r.status == "FAIL":
            f.level = "FAIL"
            f.notes.append("최종 판정: %s server dry-run 실패 — %s" % (layer, r.detail))
        elif f.code == "U":
            # Traefik 의 U 는 「버전 모름」이 아니라 워크로드·Ready 를 못 본 것이다 — Ingress dry-run 은 Traefik 을 부르지 않는다(웹훅 없음).
            what = "Traefik 은 S8 ingress-probe 가 본다" if addon == "traefik" else "버전 모름"
            f.notes.append("최종 판정: %s server dry-run %s → %s" % (layer, r.status, "WARN(%s)" % what if r.status == "PASS" else "모름 유지(WARN)"))
        elif r.status == "PASS" and f.code in ("C", "C!"):
            f.notes.append("최종 판정: %s server dry-run 통과%s" % (layer, " — Traefik 경유 실측은 S8 ingress-probe" if addon == "traefik" else ""))


def _layer_result(layer, item, args, rc, err, will_create_ns, will_install, blocked=None, finding=None, context="<ctx>"):
    if rc == 0:
        return LayerResult(layer, item, "PASS", "", args)
    benign, real, deps = _classify(err, will_create_ns, will_install, layer, blocked)
    dep_notes = [_dep_note(a, blocked[a], sorted(kinds)) for a, kinds in sorted(deps.items())]
    if (benign or dep_notes) and not real:
        if benign == [MIGRATE_NOTE] and not dep_notes:
            # 건너뛴 것이 아니라 dry-run 이 된 것이다(그 Job 하나만 S11 몫) — PASS 로 보인다.
            return LayerResult(layer, item, "PASS", "%s — 그 밖의 객체는 dry-run 통과" % MIGRATE_NOTE, args)
        return LayerResult(layer, item, "SKIP", "skipped: %s — 그 밖의 객체는 dry-run 통과" % "; ".join(dep_notes + benign), args)
    detail = " | ".join(real[:3]) if real else ((err or "").strip().splitlines() or ["rc=%d" % rc])[-1]
    if dep_notes:
        detail += " (또 %s)" % "; ".join(dep_notes)
    if "failed calling webhook" in (err or ""):
        # 웹훅 이름(mcluster.cnpg.io · validatejob.volcano.sh)이나 층으로 어느 애드온인지 고른다.
        low = (err or "").lower()
        group = next((a for mark, a in (("cnpg", "cnpg"), ("volcano", "volcano")) if mark in low), None) \
            or {"platform/db-cluster": "cnpg", "platform/db-backup": "cnpg", "vcjob": "volcano"}.get(layer)
        if group in _WEBHOOK_HINT:
            detail += " — 힌트: " + _WEBHOOK_HINT[group]
    if "conflict" in (err or "").lower() and "--server-side" in (args or []) and "--force-conflicts" not in args:
        mgrs = conflict_managers(err)
        who = "다른 field manager(%s)" % ", ".join(mgrs) if mgrs else "다른 field manager"
        if finding is not None and finding.code == "P":
            detail += " — 힌트: %s 가 가진 필드다(남이 관리하는 객체 — 채택은 --adopt %s 로만)" % (who, item)
        else:
            # --adopt 는 감지 P 에만 듣는다(decide) — DMS 소유(D=·D<)·설치(A) 층의 충돌에 「--adopt 로만」이라 하면 운영자가 효과 없는 플래그를
            # 붙여 다시 돌린다(2026-11-03 T9). 충돌한 관리자 이름으로 손 수정인지 남의 도구인지 가른다.
            detail += (" — 힌트: %s 가 가진 필드다. 손 수정(관리자 kubectl-edit·kubectl-patch 등)이면 되돌리거나 DMS 값으로 덮어쓴다: kubectl --context %s apply "
                       "--server-side --field-manager=%s --force-conflicts -k %s · 다른 도구·컨트롤러(Helm·오퍼레이터)의 필드면 덮어쓰지 말고 "
                       "소유자와 정리한다(--adopt 는 감지 P 에만 듣는다)") % (who, context, FIELD_MANAGER, args[-1])
    return LayerResult(layer, item, "FAIL", detail, args)


# --- 출력 ------------------------------------------------------------------------------------------------------------------------

def overall(findings, layers=()):
    worst = "PASS"
    levels = [f.level for f in findings.values()] + [("FAIL" if r.status == "FAIL" else "PASS") for r in layers]
    for level in levels:
        if _RANK[level] > _RANK[worst]:
            worst = level
    return "PASS" if worst == "INFO" else worst


def _width(text):
    """터미널 표시 폭(한글 등 넓은 글자는 2칸) — 표 열을 맞춘다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def _pad(text, width):
    return str(text) + " " * max(0, width - _width(text))


def render_table(context, state, addon_install, findings, layers, result, mode):
    out = ["== install-detect %s (context %s · K8s %s · ADDON_INSTALL=%s · ns %s) ==" % (mode, context, state.k8s, addon_install, state.ns)]
    head = ("항목", "단계", "코드", "판정", "동작", "설치됨", "고정", "위치")
    rows = [head] + [(f.item, STAGES[f.item], f.code, f.level, f.action, f.installed or "-", f.pinned or "-", f.where or "-")
                     for f in (findings[i] for i in ITEMS)]
    widths = [max(_width(r[i]) for r in rows) for i in range(len(head))]
    for r in rows:
        out.append("  " + "  ".join(_pad(c, widths[i]) for i, c in enumerate(r)).rstrip())
    out.append("")
    for i in ITEMS:
        f = findings[i]
        out.append("  [%s] %s %s — %s" % (f.item, f.code, f.level, f.message))
        for w in f.warnings:
            out.append("      WARN %s" % w)
        for n in f.notes:
            out.append("      · %s" % n)
    if layers:
        out.append("")
        out.append("== 층별 server dry-run (SSA --field-manager=%s · 앱은 client-side) ==" % FIELD_MANAGER)
        lw = max(len(r.layer) for r in layers)
        for r in layers:
            out.append(("  %s  %-4s  %-13s %s" % (r.layer.ljust(lw), r.status, r.item, r.detail)).rstrip())
    out.append("")
    for i in ITEMS:
        f = findings[i]
        out.append("DETECT: %s %s %s %d %s" % (f.item, f.code, f.action, 1 if f.apply else 0, f.level))
    for r in layers:
        out.append("PLAN: %s %s %s" % (r.layer, r.status, r.item))
    out.append("RESULT: %s" % result)
    return "\n".join(out)


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="install-detect.py", description="애드온 감지 상태 기계(설계 v6 8-2) + 설치 계획(S3) — 판정만 한다")
    ap.add_argument("mode", choices=("detect", "plan", "vcjob"))
    ap.add_argument("--context", default=None, help="kube-context(필수 — 없으면 env KUBE_CONTEXT)")
    ap.add_argument("--rendered", help="렌더된 사이트 디렉터리(deploy/.rendered/<site> — render.env 가 있는 곳). plan 은 필수")
    ap.add_argument("--namespace", default=None, help="DMS 네임스페이스(기본 env DMS_NS 또는 dms)")
    ap.add_argument("--upgrade-addon", action="append", default=[], metavar="X", help="D< 를 올린다(D= 면 다시 적용). 여러 번·쉼표 가능")
    ap.add_argument("--adopt", action="append", default=[], metavar="X", help="P 를 채택한다(채택할 수 있는 모양일 때만). 여러 번·쉼표 가능")
    ap.add_argument("--versions-env", default=None, help="고정 버전 파일(기본 deploy/versions.env)")
    ap.add_argument("--json", action="store_true", help="표 대신 JSON 하나만 stdout 에")
    ap.add_argument("--json-out", default=None, help="JSON 을 이 파일에도 쓴다(표는 stdout)")
    return ap.parse_args(argv)


def _names(values):
    out = set()
    for v in values:
        for name in str(v).split(","):
            name = name.strip()
            if name:
                if name in RETIRED_ITEMS:
                    raise UsageError("항목 %r 은 2026-11-16 에 없앴다 — 입구는 클러스터의 Traefik(DMS 는 설치하지 않는다): 항목 traefik(판정만)·"
                                     "ingress(Ingress dms/%s)" % (name, INGRESS_NAME))
                if name not in ITEMS:
                    raise UsageError("모르는 항목 %r — %s 중 하나" % (name, ", ".join(ITEMS)))
                out.add(name)
    return out


def _render_context(rendered):
    for sub in ("app", "platform/priorityclass", "addons/cnpg"):
        path = os.path.join(rendered, sub, "kube-context")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    value = re.sub(r"\s", "", line.split("#", 1)[0])
                    if value:
                        return value
            return ""
    return None


def main(argv=None):
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        context = args.context or os.environ.get("KUBE_CONTEXT") or ""
        if not context:
            raise UsageError("--context <ctx> 가 필요하다(current-context 에 기대지 않는다)")
        addon_install = os.environ.get("ADDON_INSTALL", "auto") or "auto"
        if addon_install not in ("auto", "never"):
            raise UsageError("ADDON_INSTALL=%r — auto 또는 never" % addon_install)
        upgrade, adopt = _names(args.upgrade_addon), _names(args.adopt)
        namespace = args.namespace or os.environ.get("DMS_NS") or "dms"
        pins = load_pins(args.versions_env)
        render = None
        if args.rendered:
            env_path = os.path.join(args.rendered, "render.env")
            if not os.path.isfile(env_path):
                raise UsageError("render.env 없음: %s (sh deploy/render.sh <site> --context %s 먼저)" % (env_path, context))
            render = read_env_file(env_path)
            want = _render_context(args.rendered)
            if want is not None and want != context:
                raise UsageError("렌더의 kube-context(%s)와 --context %s 가 다르다 — 다른 클러스터의 렌더다" % (want or "빈 값", context))
        elif args.mode == "plan":
            raise UsageError("plan 은 --rendered deploy/.rendered/<site> 가 필요하다")
        if not Kube.context_exists(context):
            raise UsageError("컨텍스트 %r 가 kubeconfig 에 없다" % context)
        kube = Kube(context)
        state = State(kube, namespace)
    except UsageError as exc:
        print("install-detect: %s" % exc, file=sys.stderr)
        print("RESULT: UNKNOWN")
        return 2
    except (KubeError, ValueError) as exc:
        # 공통 읽기(CRD·Deployment·Namespace)가 실패하면(권한 등) 어느 항목도 판정할 수 없다 — 모름(4), 실패(1)와 섞지 않는다.
        print("install-detect: 클러스터 상태를 읽지 못했다: %s" % exc, file=sys.stderr)
        print("RESULT: UNKNOWN")
        return 4
    findings = detect_all(kube, state, pins, render)      # 항목별 읽기 실패는 그 항목만 「?」(detect_all)
    for f in findings.values():
        decide(f, addon_install, upgrade, adopt)
    if args.mode == "plan":
        layers = run_plan(kube, state, findings, args.rendered)
        result = overall(findings, layers)
    elif args.mode == "vcjob":
        # S9b(install.sh — Volcano 적용 뒤): Volcano 판정 + DMS 모양 vcjob server dry-run 만. 결과는 volcano 항목과 그 층으로만 낸다(2026-11-03 ⑦).
        r = vcjob_layer(kube, state, findings)
        layers = [r]
        consumer_verdicts(findings, {"vcjob": r})
        result = overall({"volcano": findings["volcano"]}, layers)
    else:
        layers = []
        result = overall(findings, layers)
    doc = {"mode": args.mode, "context": context, "k8s": state.k8s, "namespace": namespace, "addon_install": addon_install,
           "rendered": args.rendered, "items": [findings[i].to_dict() for i in ITEMS],
           "layers": [r.to_dict() for r in layers], "result": result}
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        print(render_table(context, state, addon_install, findings, layers, result, args.mode))
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
