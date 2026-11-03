#!/usr/bin/env python3
"""노드 pull 프로브 — S6(2026-11-03 ⑦, 설계 v6 7-4 「노드 신뢰 설정」·8-1 S6).

  python3 deploy/pull-probe.py --context <ctx> --render-env deploy/.rendered/<site>/render.env [--lock deploy/images.lock]
  python3 deploy/pull-probe.py --context <ctx> --image <ref>
      [--namespace dms] [--nodes a,b] [--timeout 120] [--json] [--dry-run]

왜: 노드(kubelet → CRI-O)는 레지스트리를 **호스트 netns 에서 ClusterIP 로**(kube-proxy 경로) 평문 HTTP 로 받는다. 그 길에는 노드마다 어긋날
수 있는 것이 넷 있다 — 신뢰 설정(/etc/containers/registries.conf.d 의 insecure 항목 + crio reload), 서명 정책(/etc/crio/policy.json 이
reject 기본이면 허용 항목), 런타임 프록시(env 가 있으면 NO_PROXY 에 ClusterIP), ClusterIP 경로 자체(kube-proxy·방화벽). 이 프로브가
노드마다 **실제 pull** 로 그것을 실증하는 게이트다(설계 7-4 「S6 pull 프로브가 노드마다 이것을 실증하는 게이트」). 인터넷 노드의 push 도
같은 경로라 함께 증명된다. 설치기는 S5(push·lock 대조) 뒤에 부르고, 운영자가 따로 돌려도 된다.

어떻게:
  - 일회용 DaemonSet(ns dms, toleration Exists — control-plane 포함 모든 노드)이 레지스트리 이미지 `<레지스트리>/registry:<태그>` 를
    **imagePullPolicy Always** 로 띄운다(캐시가 있어도 kubelet 이 레지스트리에 간다 — 마스터에 고정된 같은 이미지가 있어도 길을 잰다).
    컨테이너는 `sleep` 만 한다(레지스트리를 띄우지 않는다). 비 root(65534)·cap 없음·읽기 전용 루트 — PSA restricted 모양.
  - 레지스트리 = render.env 의 ADDON_REGISTRY(REGISTRY_CLUSTER_IP 가 있으면 <IP>:<PORT> — S5 가 lock 이미지를 push 한 곳; 없으면 REGISTRY).
    태그 = images.lock 의 `registry:<태그>` 줄(레지스트리 자기 이미지 — 늘 lock 에 있고 S5 가 push 한다). --image 로 바꾼다(예: S11 의
    `dms` 이미지 사전 pull 을 --nodes <마스터> 와 함께 — 이미지에 sleep 이 없어도 pull 성공은 판정된다).
  - 노드별 결과: 컨테이너가 뜨거나(또는 pull 뒤 시작만 실패) → PASS, ErrImagePull·ImagePullBackOff 등 → FAIL(사유 + 힌트), --timeout(기본
    120초) 안에 pull 이 끝나지 않음 → FAIL, 파드가 그 노드에 생기지 않음·노드 NotReady → 모름. 끝나면(실패·Ctrl-C·SIGTERM 에도) DaemonSet 을
    지운다. 시작할 때 남은 옛 프로브(라벨 app.kubernetes.io/name=dms-pull-probe)도 지운다.
  - 판정 대상 = DMS 노드(마스터 + 역할 NoSchedule taint 없는 워커 — deploy/pg-path-probe.py dms_nodes 를 그대로, S2b·S10 과 같은 정의).
    DMS 파드(agent·잡·빌드·api·controller·PG)는 그 노드에만 뜨므로 공유 클러스터의 다른 팀 노드는 pull 은 해 보되 「정보」로만 보인다
    (2026-11-03 ⑦ — pg-path-probe 리뷰 2 와 같은 이유: 그 노드에 신뢰 설정이 없어도 DMS 와 무관하다). --nodes 를 주면 그 노드에만 띄우고
    그 노드만 판정한다(nodeAffinity).
  - FAIL 힌트: 오류 문구로 원인을 짚고(HTTP 응답·x509 → 신뢰 설정, policy·signature → 서명 정책, proxyconnect → 프록시 NO_PROXY,
    i/o timeout·connection refused·no route → ClusterIP 경로, manifest unknown → S5 push), 늘 「신뢰 설정(registries.conf.d)·서명 정책·
    프록시 NO_PROXY·ClusterIP 경로」 네 가지를 함께 보인다.

종료 코드: 0 PASS · 1 FAIL(판정 대상 하나라도) · 4 모름(FAIL 없음) · 2 사용법·환경(클러스터에 아무것도 만들기 전). 마지막 줄은
`RESULT: <PASS|FAIL|UNKNOWN>`(--dry-run 은 `RESULT: DRY-RUN`, 0). --json 이면 stdout 은 JSON 하나, 사람용 줄은 stderr.
--render-env 를 주면 같은 렌더 디렉터리의 app/kube-context 와 --context 가 같아야 한다(다른 클러스터를 겨누지 않게).

kubectl 은 모든 클러스터 호출에 --context 를 붙인다(필수 — current-context 에 기대지 않는다). 설치 호스트의 python3(RHEL 9 = 3.9),
표준 라이브러리만. 비밀 값은 다루지 않는다(레지스트리는 익명 pull).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NAME_KEY = "app.kubernetes.io/name"
NAME_VALUE = "dms-pull-probe"
NONCE_LABEL = "dms.io/probe-nonce"
EXIT = {"PASS": 0, "FAIL": 1, "UNKNOWN": 4}
_RANK = {"INFO": 0, "PASS": 1, "UNKNOWN": 2, "FAIL": 3}
POLL_SECONDS = 2.0       # 파드 상태를 다시 보는 간격
GRACE_SECONDS = 15.0     # 판정 대상이 다 정해진 뒤 판정 밖 노드의 결과를 더 기다리는 시간(정보용)
_PULL_FAIL = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull", "RegistryUnavailable",
              "SignatureValidationFailed"}
# pull 뒤의 실패(이미지는 받았다) — sleep 이 없는 이미지(--image)도 pull 판정은 된다.
_AFTER_PULL = {"CreateContainerConfigError", "CreateContainerError", "RunContainerError", "CrashLoopBackOff", "StartError"}
GENERIC_HINT = "확인할 것: 신뢰 설정(registries.conf.d)·서명 정책·프록시 NO_PROXY·ClusterIP 경로(설계 v6 7-4)"


def hints(message: str, registry: str) -> list:
    """pull 오류 문구 → 원인 힌트(구체적인 것 먼저, 마지막은 늘 네 가지 목록)."""
    low = (message or "").lower()
    host = registry.split(":")[0] if registry else "<레지스트리 IP>"
    out = []
    if "http response to https client" in low or "x509" in low or "certificate" in low or "tls" in low:
        out.append(f"신뢰 설정 — /etc/containers/registries.conf.d/50-dms-registry.conf 에 [[registry]] location=\"{registry}\" "
                   "insecure=true 를 쓰고 systemctl reload crio(평문 HTTP 레지스트리, 설계 7-4)")
    if "signature" in low or "policy" in low or "rejected" in low:
        out.append(f"서명 정책 — /etc/crio/policy.json(패키지 CRI-O 가 보는 파일)이 reject 기본이면 {registry} 를 허용한다")
    if "proxyconnect" in low or "proxy" in low:
        out.append(f"런타임 프록시 — CRI-O 의 프록시 env(systemd drop-in)의 NO_PROXY 에 {host} 를 넣고 crio 를 다시 띄운다")
    if any(k in low for k in ("i/o timeout", "connection refused", "no route to host", "network is unreachable",
                              "context deadline exceeded", "connection reset", "dial tcp")):
        out.append(f"ClusterIP 경로 — 그 노드 호스트에서 curl -sS http://{registry}/v2/ 가 되는지(kube-proxy·방화벽 — KPR 도 kube-proxy 도 "
                   "없으면 안 된다), 레지스트리 파드 Ready(S4)")
    if "manifest unknown" in low or "not found" in low or "name unknown" in low:
        out.append("레지스트리에 그 태그가 없다 — S5(인터넷 노드 push → install.sh --stage S5 대조)를 먼저")
    if "unauthorized" in low or "authentication required" in low or "denied" in low:
        out.append("인증 거부 — 레지스트리는 익명 pull 이어야 한다(사이트 레지스트리면 노드 인증 설정)")
    out.append(GENERIC_HINT)
    return out


def pod_pull_state(pod: dict):
    """파드 상태 → ('ok'|'fail'|'pending', 상세). 컨테이너가 떴거나(지금·전에) 이미지 ID 가 있거나 pull 뒤 단계에서 멈췄으면 ok."""
    status = pod.get("status") or {}
    for st in status.get("containerStatuses") or []:
        state, last = st.get("state") or {}, st.get("lastState") or {}
        waiting = state.get("waiting") or {}
        reason = waiting.get("reason")
        if reason in _PULL_FAIL:
            return "fail", f"{reason}: {(waiting.get('message') or '').strip()[:400]}"
        if "running" in state or "terminated" in state or "terminated" in last or st.get("imageID"):
            return "ok", st.get("imageID") or ""
        if reason in _AFTER_PULL:
            return "ok", f"pull 됨(컨테이너 시작은 {reason} — pull 판정과 무관)"
    conds = {c.get("type"): c for c in status.get("conditions") or []}
    sched = conds.get("PodScheduled") or {}
    if sched.get("status") == "False":
        return "pending", f"스케줄 안 됨({sched.get('reason') or ''}: {(sched.get('message') or '').strip()[:200]})"
    return "pending", status.get("phase") or "Pending"


def judge_events(events: list, timeout: float):
    """시간 초과한 노드의 파드 이벤트 → (status, 사유). Failed 문구가 있으면 FAIL(그 문구), Pulling 만 있으면 FAIL(시간 초과),
    Pulled 가 있으면 PASS, 아무 흔적도 없으면 모름."""
    by_reason = {}
    for ev in sorted(events, key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or ""):
        by_reason[ev.get("reason")] = (ev.get("message") or "").strip()
    if "Pulled" in by_reason:
        return "PASS", f"pull 됨({by_reason['Pulled'][:200]})"
    if "Failed" in by_reason:
        return "FAIL", by_reason["Failed"][:400]
    if "Pulling" in by_reason:
        return "FAIL", f"pull 이 {int(timeout)}초 안에 끝나지 않았다(연결이 매달린 경우가 흔하다 — ClusterIP 경로·프록시)"
    if "FailedScheduling" in by_reason:
        return "UNKNOWN", f"스케줄 안 됨: {by_reason['FailedScheduling'][:200]}"
    return "UNKNOWN", f"{int(timeout)}초 안에 pull 흔적이 없다(파드 미기동)"


def verdict(results: list) -> str:
    judged = [r["status"] for r in results if r["status"] != "INFO"]
    if not judged:
        return "UNKNOWN"
    return max(judged, key=lambda s: _RANK[s])


_LOADED = {}


def _load(filename: str):
    if filename not in _LOADED:
        spec = importlib.util.spec_from_file_location("dms_" + re.sub(r"\W", "_", filename), os.path.join(HERE, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LOADED[filename] = mod
    return _LOADED[filename]


def dms_nodes(items: list) -> tuple:
    """DMS 노드(판정 대상), 판정 밖 — deploy/pg-path-probe.py 의 dms_nodes 그대로(정의 하나)."""
    return _load("pg-path-probe.py").dms_nodes(items)


def registry_of(image: str) -> str:
    """이미지 ref 의 레지스트리 호스트(첫 조각에 . 이나 : 가 있거나 localhost 일 때 — docker 참조 규칙). 없으면 ''."""
    first = image.split("/", 1)[0] if "/" in image else ""
    return first if ("." in first or ":" in first or first == "localhost") else ""


def render_values(path: str) -> dict:
    """render.env 의 `KEY=값`·`KEY="값"` 줄(sh 로 . 하지 않는다)."""
    values = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r'^([A-Z_][A-Z0-9_]*)="?([^"\n]*)"?\s*$', line)
            if m:
                values[m.group(1)] = m.group(2)
    return values


def registry_tag(lock_path: str) -> str:
    """images.lock 의 `registry:<태그>` 줄의 태그(images-lock.py 의 parse_lock — sh 와 같은 규칙). 없거나 lock 이 깨졌으면 ValueError."""
    with open(lock_path, encoding="utf-8") as fh:
        entries, problems = _load("images-lock.py").parse_lock(fh.read())
    if problems:
        raise ValueError(f"{lock_path} 형식 오류 {len(problems)}건(첫: {problems[0][0]}번째 줄 — {problems[0][1]}) — "
                         "python3 deploy/images-lock.py check")
    tags = [dest.split(":", 1)[1] for _no, dest, _src, _dg in entries if dest.split(":", 1)[0] == "registry"]
    if len(tags) != 1:
        raise ValueError(f"{lock_path} 에 registry:<태그> 줄이 {len(tags)}개다(하나여야 한다)")
    return tags[0]


def image_from_render(render_env: str, lock_path: str) -> str:
    values = render_values(render_env)
    registry = values.get("ADDON_REGISTRY") or values.get("REGISTRY") or ""
    if not re.match(r"^[A-Za-z0-9.-]+(:[0-9]+)?$", registry):
        raise ValueError(f"{render_env} 의 ADDON_REGISTRY·REGISTRY 가 host[:port] 가 아니다({registry!r}) — sh deploy/render.sh <site> 로 다시 렌더")
    return f"{registry}/registry:{registry_tag(lock_path)}"


def expected_context(render_env: str):
    """render.env 옆 app/kube-context 의 첫 비주석 값(파일이 없으면 None)."""
    path = os.path.join(os.path.dirname(os.path.abspath(render_env)), "app", "kube-context")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            value = re.sub(r"\s", "", line.split("#", 1)[0])
            if value:
                return value
    return ""


def ds_manifest(*, name: str, nonce: str, image: str, namespace: str, nodes=None) -> dict:
    labels = {NAME_KEY: NAME_VALUE, "app.kubernetes.io/part-of": "dms", NONCE_LABEL: nonce}
    spec = {
        "automountServiceAccountToken": False, "enableServiceLinks": False,
        "terminationGracePeriodSeconds": 1,
        "tolerations": [{"operator": "Exists"}],
        "securityContext": {"runAsNonRoot": True, "runAsUser": 65534, "runAsGroup": 65534,
                            "seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [{
            "name": "pull", "image": image, "imagePullPolicy": "Always",
            "command": ["sleep", "86400"],
            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]}},
            "resources": {"requests": {"cpu": "1m", "memory": "8Mi"}, "limits": {"cpu": "50m", "memory": "32Mi"}},
        }],
    }
    if nodes:
        spec["affinity"] = {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
            {"matchFields": [{"key": "metadata.name", "operator": "In", "values": sorted(nodes)}]}]}}}
    return {
        "apiVersion": "apps/v1", "kind": "DaemonSet",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {"selector": {"matchLabels": {NAME_KEY: NAME_VALUE, NONCE_LABEL: nonce}},
                 "template": {"metadata": {"labels": labels}, "spec": spec}},
    }


# --- kubectl -----------------------------------------------------------------------------------------------------------

class Kube:
    """모든 클러스터 호출에 --context 를 붙인다. config get-contexts(존재 확인)만 로컬 호출이다."""

    def __init__(self, context: str, namespace: str, kubectl: str = "kubectl"):
        self.context, self.namespace, self.kubectl = context, namespace, kubectl

    def _run(self, args, *, input_text=None, timeout=60, cluster=True):
        cmd = [self.kubectl] + (["--context", self.context] if cluster else []) + list(args)
        return subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)

    def context_exists(self) -> bool:
        try:
            return self._run(["config", "get-contexts", self.context], cluster=False, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def nodes(self) -> list:
        r = self._run(["get", "nodes", "-o", "json"])
        if r.returncode != 0:
            raise RuntimeError(f"kubectl get nodes 실패: {(r.stderr or r.stdout).strip()[:300]}")
        return json.loads(r.stdout or "{}").get("items", [])

    def namespace_exists(self) -> bool:
        r = self._run(["get", "namespace", self.namespace, "--ignore-not-found", "-o", "name"])
        if r.returncode != 0:
            raise RuntimeError(f"kubectl get namespace {self.namespace} 실패: {(r.stderr or r.stdout).strip()[:300]}")
        return bool(r.stdout.strip())

    def leftovers(self) -> list:
        r = self._run(["-n", self.namespace, "get", "daemonsets", "-l", f"{NAME_KEY}={NAME_VALUE}", "-o", "name"])
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []

    def create(self, manifest: dict) -> None:
        r = self._run(["-n", self.namespace, "create", "-f", "-"], input_text=json.dumps(manifest), timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"프로브 DaemonSet 생성 실패: {(r.stderr or r.stdout).strip()[:500]}")

    def pods(self, nonce: str) -> list:
        r = self._run(["-n", self.namespace, "get", "pods", "-l", f"{NONCE_LABEL}={nonce}", "-o", "json"])
        return json.loads(r.stdout).get("items", []) if r.returncode == 0 and r.stdout.strip() else []

    def events(self, kind: str, name: str) -> list:
        r = self._run(["-n", self.namespace, "get", "events", "--field-selector",
                       f"involvedObject.kind={kind},involvedObject.name={name}", "-o", "json"])
        return json.loads(r.stdout).get("items", []) if r.returncode == 0 and r.stdout.strip() else []

    def delete(self, ref: str) -> bool:
        try:
            r = self._run(["-n", self.namespace, "delete", ref, "--ignore-not-found", "--wait=false"], timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0


# --- 수집 --------------------------------------------------------------------------------------------------------------

def collect(kube, *, nonce: str, ds_name: str, judged: list, others: list, not_ready: set, timeout: float,
            poll=None, grace=None, clock=None, sleep=None) -> dict:
    """{node: (status, 사유)} — 판정 대상이 모두 정해지면(다른 노드는 grace 초까지 더 본다) 또는 timeout. pull 오류 문구는 처음 본
    ErrImagePull 의 것을 쓴다(ImagePullBackOff 의 문구는 원인을 감출 수 있다)."""
    decided = {n: ("UNKNOWN", "노드가 NotReady — 프로브 파드가 뜰 수 없다") for n in not_ready}
    poll = POLL_SECONDS if poll is None else poll
    grace = GRACE_SECONDS if grace is None else grace
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    wanted = set(judged) | set(others)
    deadline, judged_done_at, last_pod = clock() + timeout, None, {}
    while True:
        for pod in kube.pods(nonce):
            node = (pod.get("spec") or {}).get("nodeName")
            if not node or node in decided or (pod.get("metadata") or {}).get("deletionTimestamp"):
                continue
            last_pod[node] = pod
            kind, detail = pod_pull_state(pod)
            if kind == "ok":
                decided[node] = ("PASS", f"pull 됨{(' — ' + detail) if detail else ''}")
            elif kind == "fail":
                decided[node] = ("FAIL", detail)
        now = clock()
        if all(n in decided for n in judged):
            judged_done_at = judged_done_at if judged_done_at is not None else now
            if all(n in decided for n in wanted) or now - judged_done_at >= grace:
                break
        if now >= deadline:
            break
        sleep(poll)
    for node in sorted(wanted - set(decided)):
        pod = last_pod.get(node)
        if pod is None:
            decided[node] = ("UNKNOWN", f"{int(timeout)}초 안에 이 노드에 프로브 파드가 생기지 않았다{_ds_problem(kube, ds_name)}")
            continue
        try:
            events = kube.events("Pod", pod["metadata"]["name"])
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            events = []
        decided[node] = judge_events(events, timeout)
    # ImagePullBackOff 로 정해진 노드는 이벤트의 Failed 문구가 원인을 더 잘 말한다.
    for node, (status, detail) in list(decided.items()):
        if status == "FAIL" and detail.startswith("ImagePullBackOff") and node in last_pod:
            try:
                events = kube.events("Pod", last_pod[node]["metadata"]["name"])
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                events = []
            failed = [e for e in events if e.get("reason") == "Failed" and "ImagePullBackOff" not in (e.get("message") or "")]
            if failed:
                decided[node] = ("FAIL", (failed[-1].get("message") or "").strip()[:400])
    return decided


def _ds_problem(kube, ds_name: str) -> str:
    try:
        events = kube.events("DaemonSet", ds_name)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return ""
    for ev in events:
        if ev.get("reason") == "FailedCreate":
            return f" — {(ev.get('message') or '').strip()[:300]}"
    return ""


# --- main --------------------------------------------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(prog="pull-probe.py", description="노드 pull 프로브(설계 v6 S6 — 노드 → 레지스트리 ClusterIP pull)")
    p.add_argument("--context", default=os.environ.get("KUBE_CONTEXT") or None,
                   help="kubeconfig 컨텍스트(필수, 또는 env KUBE_CONTEXT — 플래그가 이긴다)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--image", help="pull 할 이미지 ref(기본: render.env 의 레지스트리 + images.lock 의 registry:<태그>)")
    src.add_argument("--render-env", help="deploy/.rendered/<site>/render.env — ADDON_REGISTRY(없으면 REGISTRY)")
    p.add_argument("--lock", default=os.path.join(HERE, "images.lock"), help="images.lock(registry:<태그> 줄, 기본 deploy/images.lock)")
    p.add_argument("--namespace", default="dms")
    p.add_argument("--nodes", help="쉼표 목록 — 그 노드에만 띄우고 그 노드만 판정(기본: 모든 노드에 띄우고 DMS 노드만 판정)")
    p.add_argument("--timeout", type=float, default=120.0, help="pull 을 기다리는 시간(초, 기본 120)")
    p.add_argument("--json", action="store_true", help="stdout 에 JSON 결과만(사람용 줄은 stderr)")
    p.add_argument("--dry-run", action="store_true", help="읽기만 하고 DaemonSet 매니페스트를 보인다(아무것도 만들지 않는다)")
    return p.parse_args(argv)


def _usage(msg: str) -> int:
    sys.stderr.write(f"usage: {msg}\n")
    return 2


def _wait_pods_gone(kube, nonce: str, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if not kube.pods(nonce):
                return True
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
            return False
        time.sleep(POLL_SECONDS)
    return False


def main(argv=None, *, kube_factory=Kube) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not args.context:
        return _usage("--context <ctx> 가 필요하다(또는 KUBE_CONTEXT) — DaemonSet 을 만드는 스크립트라 current-context 에 기대지 않는다")
    if not args.image and not args.render_env:
        return _usage("--image <ref> 또는 --render-env deploy/.rendered/<site>/render.env 가 필요하다")
    if args.timeout <= 0:
        return _usage("--timeout > 0")
    image = args.image
    if args.render_env:
        if not os.path.isfile(args.render_env):
            return _usage(f"{args.render_env} 가 없다 — sh deploy/render.sh <site> 가 쓴다")
        expect = expected_context(args.render_env)
        if expect is not None and expect != args.context:
            return _usage(f"{args.render_env} 는 컨텍스트 '{expect}' 전용인데 --context {args.context} 를 겨눴다(app/kube-context)")
        try:
            image = image_from_render(args.render_env, args.lock)
        except (OSError, ValueError) as exc:
            return _usage(str(exc))
    wanted = sorted({n.strip() for n in (args.nodes or "").split(",") if n.strip()})
    if args.nodes is not None and not wanted:
        return _usage("--nodes 가 비었다")
    human = sys.stderr if args.json else sys.stdout
    kube = kube_factory(args.context, args.namespace)
    if not kube.context_exists():
        sys.stderr.write(f"컨텍스트 '{args.context}' 가 kubeconfig 에 없습니다\n")
        return 2
    try:
        items = kube.nodes()
        names = sorted(i["metadata"]["name"] for i in items)
        if wanted:
            unknown = [n for n in wanted if n not in names]
            if unknown:
                return _usage(f"--nodes 에 클러스터에 없는 노드: {unknown} — 있는 노드: {', '.join(names)}")
            judged, others, how = wanted, [], "--nodes"
        else:
            judged, others = dms_nodes(items)
            how = "DMS 노드 — 마스터 + 역할 NoSchedule taint 없는 워커, 설계 2-3"
        if not judged:
            return _usage("판정 대상 노드가 없다(DMS 노드 0 — --nodes 로 고른다)")
        if not kube.namespace_exists():
            return _usage(f"ns {args.namespace} 가 없다 — S2 에서 deploy/k8s/00-namespace.yaml 을 먼저 적용한다")
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    ready = {}
    for item in items:
        conds = {c.get("type"): c.get("status") for c in (item.get("status") or {}).get("conditions") or []}
        ready[item["metadata"]["name"]] = conds.get("Ready") == "True"
    registry = registry_of(image)
    nonce = secrets.token_hex(8)
    ds_name = f"{NAME_VALUE}-{nonce[:8]}"
    manifest = ds_manifest(name=ds_name, nonce=nonce, image=image, namespace=args.namespace, nodes=wanted or None)
    human.write(f"[S6] 노드 pull 프로브 — context {args.context} · ns {args.namespace} · 이미지 {image}(Always) · DS {ds_name} · "
                f"{int(args.timeout)}초\n")
    human.write(f"  판정 대상({how}) {len(judged)}: {', '.join(judged)}\n")
    if others:
        human.write(f"  판정 밖(pull 은 해 보되 정보만) {len(others)}: {', '.join(others)}\n")
    if args.dry_run:
        human.write("  (dry-run) DaemonSet 을 만들어 노드마다 이미지를 pull(Always)하고 지운다\n")
        if args.json:
            sys.stdout.write(json.dumps({"result": "DRY-RUN", "image": image, "judged": judged, "others": others,
                                         "manifest": manifest}, ensure_ascii=False) + "\n")
        else:
            human.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            human.write("RESULT: DRY-RUN\n")
        return 0

    def _terminate(signum, _frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise KeyboardInterrupt(f"signal {signum}")

    previous = {sig: signal.signal(sig, _terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    results, created, aborted = [], False, None
    try:
        for ref in kube.leftovers():
            human.write(f"  INFO    옛 프로브 {ref} 를 지운다\n")
            kube.delete(ref)
        created = True                     # create 가 실패해도(시간 초과 등) 서버에는 생겼을 수 있다 — 지우기는 늘 시도한다
        kube.create(manifest)
        not_ready = {n for n in judged + others if not ready.get(n, True)}
        decided = collect(kube, nonce=nonce, ds_name=ds_name, judged=judged, others=others, not_ready=not_ready,
                          timeout=args.timeout)
        for node in judged + others:
            status, detail = decided.get(node, ("UNKNOWN", "결과 없음"))
            entry = {"node": node, "judged": node in judged, "status": status, "message": detail,
                     "hints": hints(detail, registry) if status == "FAIL" else []}
            if node not in judged:
                entry["status"], entry["measured"] = "INFO", status
            results.append(entry)
    except KeyboardInterrupt:
        aborted = "중단됨 — DaemonSet 을 지우고 끝낸다"
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        aborted = f"실행 실패: {exc}"
    finally:
        if created:
            ok = kube.delete(f"daemonset/{ds_name}") and _wait_pods_gone(kube, nonce, 30)
            if not ok:
                sys.stderr.write(f"주의: 프로브를 다 지우지 못했다 — kubectl --context {args.context} -n {args.namespace} delete daemonset "
                                 f"{ds_name} --ignore-not-found\n")
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if aborted:
        results = [{"node": "-", "judged": True, "status": "UNKNOWN", "message": aborted, "hints": []}] + results
    for r in results:
        tag = "" if r["judged"] else f"(판정 밖 — 재면 {r.get('measured')}) "
        human.write(f"  {r['status']:<7} {r['node']:<20} {tag}{r['message']}\n")
        for h in r["hints"]:
            human.write(f"          - {h}\n")
    result = verdict(results)
    if args.json:
        sys.stdout.write(json.dumps({"result": result, "image": image, "daemonset": ds_name, "nodes": results},
                                    ensure_ascii=False) + "\n")
    else:
        human.write(f"RESULT: {result}\n")
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
