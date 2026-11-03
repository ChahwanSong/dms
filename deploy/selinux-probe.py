#!/usr/bin/env python3
"""SELinux 노드 프로브 — S2b(2026-11-03 ⑦, 설계 v6 2-3 「SELinux 전제」·8-1 「S2b 세부」·#48).

  python3 deploy/selinux-probe.py --context <ctx> [--namespace dms] [--image <ref>] [--timeout 120] [--json] [--dry-run]

왜: RHEL 계열(RHEL·Rocky 등 EL) DMS 노드는 SELinux 가 **enforcing 이 아니어야** 한다(permissive 또는 disabled — 필수 설치 조건, #48).
enforcing 이면 CRI-O 가 컨테이너를 container_t + 파드별 MCS 로 띄우고 hostPath 는 relabel 되지 않아(설계 2-3 근거), agent 의 /proc/1 읽기·
잡의 등록 스토리지 접근·제어면의 <SHARED_DIR>/dms 접근이 EACCES 로 막히고 DMS 사유가 엉뚱하게 나온다. DMS 는 SELinux 를 위한 코드·라벨
작업을 하지 않는다 — 대신 설치기가 레지스트리·DB 를 띄우기 **전**에 이 프로브로 노드를 재고 멈춘다(설계 8-1 「순서의 이유」).

어떻게(설계 8-1 「S2b 세부」):
  - ns dms(S2 가 00-namespace.yaml 로 만든다 — PSA privileged. baseline 은 hostPath 를 막는다)에 **일회용 privileged DaemonSet** 을 띄운다.
    privileged 인 이유: CRI-O 는 privileged 컨테이너에 프로세스 라벨을 주지 않아(spc_t) 라벨 거부가 없다. 일반 컨테이너(container_t)면
    enforcing 노드에서 selinuxfs(security_t) 읽기가 거부될 수 있고 그러면 **거짓 PASS** 가 난다.
  - toleration Exists — 모든 노드에 뜬다(control-plane 포함). **판정은 DMS 노드만**(마스터 + 역할 NoSchedule taint 없는 워커 =
    dms-agent 가 뜨는 노드 — deploy/pg-path-probe.py 의 dms_nodes 를 그대로 불러 쓴다, 정의 하나). 그 밖의 노드(공유 클러스터의 다른 팀
    노드)는 「정보」로만 보인다.
  - 이미지는 레지스트리(S4)보다 앞이라 **pull 이 필요 없는** 이미지 — 모든 노드에 이미 있는 셸 포함 이미지를 imagePullPolicy Never 로 쓴다.
    기본은 kube-system 의 Cilium agent DaemonSet(k8s-app=cilium, 컨테이너 cilium-agent)의 이미지 ref 를 읽어 그대로 쓴다. --image 로 바꾼다
    (셸·ls·cat·sleep 이 있는, 모든 판정 대상 노드에 이미 있는 이미지).
  - hostPath /sys/fs·/etc 를 **읽기 전용**(recursiveReadOnly IfPossible)으로 붙인다. CRI-O 가 hostPath 를 rbind 로 붙여 하위의 selinuxfs·
    cgroup 도 보인다. /etc/selinux 를 직접 걸지 않는 이유: 없는 hostPath 소스는 CRI-O 가 MkdirAll 로 만들어 Ubuntu 호스트에 디렉터리를
    남긴다. 파드는 값을 찍기만 하고(쓰기 없음) 판정은 이 스크립트가 한다(judge — 순수 함수, 테스트가 행렬로 본다).
  - 끝나면(실패·Ctrl-C·SIGTERM 에도) DaemonSet 을 지운다. 시작할 때 남아 있는 옛 프로브 DaemonSet(라벨 app.kubernetes.io/name=
    dms-selinux-probe)도 지운다. 지우지 못하면 손 명령을 stderr 에 남긴다.

판정(노드별, 설계 8-1 — 모름과 실패를 섞지 않는다):
  /sys/fs/selinux/enforce == 1                                            → FAIL 「enforcing」
  0 인데 재부팅하면 enforcing(부팅 인자에 selinux=0 이 없고, 부팅 인자의 마지막 enforcing= 이 0 이 아닌 값이거나 — 없으면 /etc/selinux/config
    의 SELINUX=enforcing — libselinux selinux_init_load_policy 와 같은 우선순위: 부팅 인자 > config)        → FAIL 「재부팅하면 enforcing」
  0(그 밖)                                                                 → PASS(permissive)
  /sys/fs/selinux 가 없거나 비었고 /sys/fs/cgroup 은 보임(rbind 정상)        → PASS(SELinux 없음 또는 disabled — Ubuntu 등)
  읽기 오류(EACCES 등)·/sys/fs/cgroup 도 안 보임·파드 미기동·이미지가 노드에 없음 → 모름(UNKNOWN) — 설치자가 그 노드에서
    `getenforce` 와 `grep ^SELINUX= /etc/selinux/config` 로 확인한다.
  (부팅 인자 enforcing=1 은 설계 행렬에 없는 확장이다 — 「재부팅하면 enforcing 이 될 노드」라는 같은 뜻이라 같은 FAIL 로 본다. /proc/cmdline 은
  이번 부팅의 인자라, 다음 부팅을 위해 grubby 로 바꾼 인자는 보지 못한다.)

종료 코드: 0 PASS · 1 FAIL(DMS 노드 하나라도) · 4 모름(FAIL 없음) · 2 사용법·환경(클러스터에 아무것도 만들기 전). 마지막 줄은
`RESULT: <PASS|FAIL|UNKNOWN>`(--dry-run 은 `RESULT: DRY-RUN`, 0). --json 이면 stdout 은 JSON 하나, 사람용 줄은 stderr. --dry-run 은 읽기만
(노드·Cilium 이미지·ns) 하고 DaemonSet 매니페스트를 보인다.

kubectl 은 모든 클러스터 호출에 --context 를 붙인다(필수 — 두 테스트베드가 같은 네임스페이스, current-context 에 기대지 않는다). 설치 호스트의
python3(RHEL 9 = 3.9), 표준 라이브러리만. 비밀 값은 읽지도 찍지도 않는다(부팅 인자도 selinux=·enforcing= 낱말만 찍는다).
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
NAME_VALUE = "dms-selinux-probe"
NONCE_LABEL = "dms.io/probe-nonce"
EXIT = {"PASS": 0, "FAIL": 1, "UNKNOWN": 4}
_RANK = {"INFO": 0, "PASS": 1, "UNKNOWN": 2, "FAIL": 3}
POLL_SECONDS = 2.0       # 파드 상태를 다시 보는 간격
GRACE_SECONDS = 15.0     # 판정 대상이 다 정해진 뒤 판정 밖 노드의 결과를 더 기다리는 시간(정보용)
CILIUM_NAMESPACE = "kube-system"
CILIUM_SELECTOR = "k8s-app=cilium"
CILIUM_CONTAINER = "cilium-agent"
_IMAGE_REASONS = {"ErrImageNeverPull", "ErrImagePull", "ImagePullBackOff", "InvalidImageName"}
_START_REASONS = {"CreateContainerConfigError", "CreateContainerError", "RunContainerError", "CrashLoopBackOff", "StartError"}
_LINE = re.compile(r"^SEPROBE ([a-z_]+)=(.*)$")

# 파드 안 스크립트(POSIX sh — 셸·ls·cat·sleep 만 쓴다). 값을 찍기만 하고 판정하지 않는다. 쓰기 없음. set -f: 부팅 인자 낱말의 glob 확장 금지.
# 부팅 인자는 selinux=·enforcing= 낱말만 찍는다(나머지 인자는 로그에 남기지 않는다). SEPROBE_HOST·SEPROBE_CMDLINE 은 테스트가 가짜 호스트
# 트리로 이 스크립트를 실제로 돌리려는 것이다(파드는 설정하지 않는다 — 기본 /host·/proc/cmdline).
POD_SCRIPT = r"""set -f
H=${SEPROBE_HOST:-/host}
say() { printf 'SEPROBE %s\n' "$1"; }
say begin
if out=$(ls -A "$H/sys/fs/cgroup" 2>&1); then
  if [ -n "$out" ]; then say cgroup=visible; else say cgroup=empty; fi
else
  say "cgroup_error=$out"
fi
d=$H/sys/fs/selinux
if [ ! -e "$d" ] && [ ! -L "$d" ]; then
  say selinuxfs=absent
elif out=$(ls -A "$d" 2>&1); then
  if [ -z "$out" ]; then
    say selinuxfs=empty
  else
    say selinuxfs=present
    if v=$(cat "$d/enforce" 2>&1); then say "enforce=$v"; else say "enforce_error=$v"; fi
  fi
else
  say "selinuxfs_error=$out"
fi
if [ -e "$H/etc/passwd" ] || [ -e "$H/etc/hostname" ] || [ -e "$H/etc/fstab" ]; then say etc=visible; else say etc=missing; fi
c=$H/etc/selinux/config
if [ ! -e "$c" ] && [ ! -L "$c" ]; then
  say config=absent
elif [ -r "$c" ] && [ -f "$c" ]; then
  say config=present
  while IFS= read -r l || [ -n "$l" ]; do
    case "$l" in SELINUX=*) say "config_selinux=${l#SELINUX=}" ;; esac
  done < "$c"
else
  say "config_error=읽을 수 없다(일반 파일이 아니거나 권한 — 심링크면 호스트 경로로 따라가지 못한다)"
fi
if v=$(cat "${SEPROBE_CMDLINE:-/proc/cmdline}" 2>&1); then
  t=
  for w in $v; do case "$w" in selinux=*|enforcing=*) t="$t $w" ;; esac; done
  say "cmdline=${t# }"
else
  say "cmdline_error=$v"
fi
say done
exec sleep 86400
"""

FAIL_GUIDE = ("RHEL 계열 DMS 노드는 SELinux 를 enforcing 이 아니게 둘 것: /etc/selinux/config 의 SELINUX=permissive 로 고정하고 "
              "setenforce 0(권장). 완전 비활성은 grubby --update-kernel ALL --args selinux=0 후 재부팅(Red Hat 은 프로덕션에서 selinux=0 을 "
              "권하지 않고, disabled 에서 되돌릴 때는 전체 relabel 이 일어난다). setenforce 0 만 하면 재부팅 때 되돌아간다. 부팅 인자에 "
              "enforcing=1 이 있으면 grubby --update-kernel ALL --remove-args enforcing 으로 지운다(설계 v6 2-3).")
UNKNOWN_GUIDE = ("모름 — 그 노드에서 직접 확인한다: getenforce 와 grep ^SELINUX= /etc/selinux/config (Enforcing 이 아니고 config 가 "
                 "enforcing 이 아니면 통과 조건이다). 확인한 뒤 이 프로브를 다시 돌린다(설계 v6 8-1 S2b — 모름과 실패를 섞지 않는다).")


class UsageError(Exception):
    pass


# --- 판정(순수 함수 — 테스트가 행렬로 직접 부른다) -----------------------------------------------------------------------------

def parse_logs(text: str) -> dict:
    """파드 로그 → 값 사전. 마지막 `SEPROBE begin` 이후만 본다(컨테이너가 다시 떴으면 마지막 회차). `done` 이 있어야 완결이다."""
    out = {"done": False, "config_selinux": []}
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        if line == "SEPROBE begin":
            out = {"done": False, "config_selinux": []}
        elif line == "SEPROBE done":
            out["done"] = True
        else:
            m = _LINE.match(line)
            if m:
                key, value = m.group(1), m.group(2)[:300]
                if key == "config_selinux":
                    out["config_selinux"].append(value)
                else:
                    out[key] = value
    return out


def config_mode(values: list):
    """/etc/selinux/config 의 SELINUX= 값들 → 'enforcing'|'permissive'|'disabled'|None.

    libselinux selinux_getenforcemode 와 같은 규칙: 위에서부터 SELINUX= 줄의 값이 enforcing·permissive·disabled 로 **시작**하면(대소문자
    무시) 그 줄에서 정하고, 어느 것도 아니면 다음 줄을 본다. 따옴표는 libselinux 도 벗기지 않는다(그런 줄은 무시된다).
    """
    for value in values:
        low = value.lower()
        for mode in ("enforcing", "permissive", "disabled"):
            if low.startswith(mode):
                return mode
    return None


def _atoi(text: str) -> int:
    """C atoi 처럼 앞쪽 숫자만(없으면 0) — 커널·libselinux 가 enforcing= 값을 읽는 방식."""
    m = re.match(r"^\s*([+-]?\d+)", text)
    return int(m.group(1)) if m else 0


def cmdline_args(text: str) -> dict:
    """찍힌 부팅 인자 낱말(selinux=·enforcing=) → {'selinux': 마지막 값|None, 'enforcing': 마지막 값|None} (atoi)."""
    out = {"selinux": None, "enforcing": None}
    for word in (text or "").split():
        key, _, value = word.partition("=")
        if key in out:
            out[key] = _atoi(value)
    return out


def judge(raw: dict) -> dict:
    """노드 하나의 값 → {'status': PASS|FAIL|UNKNOWN, 'code': ..., 'message': ...} (설계 8-1 「S2b 세부」 판정)."""
    def res(status, code, message):
        return {"status": status, "code": code, "message": message}

    if not raw.get("done"):
        return res("UNKNOWN", "incomplete", "프로브 출력이 끝나지 않았다(파드가 값을 다 찍지 못했다)")
    fs = raw.get("selinuxfs")
    if fs is None:
        return res("UNKNOWN", "read-error", f"/sys/fs/selinux 를 읽지 못했다({raw.get('selinuxfs_error') or '값 없음'})")
    if fs in ("absent", "empty"):
        if raw.get("cgroup") != "visible":
            why = raw.get("cgroup_error") or ("비었다" if raw.get("cgroup") == "empty" else "값 없음")
            return res("UNKNOWN", "rbind-unverified",
                       f"/sys/fs/selinux 가 {'없지만' if fs == 'absent' else '비었지만'} /sys/fs/cgroup 도 보이지 않는다({why}) — hostPath rbind 를 "
                       "확인할 수 없어 「SELinux 없음」으로 볼 수 없다")
        return res("PASS", "no-selinux", f"SELinux 없음 또는 disabled(/sys/fs/selinux {'없음' if fs == 'absent' else '비었음'}, /sys/fs/cgroup 은 보임)")
    if fs != "present":
        return res("UNKNOWN", "read-error", f"/sys/fs/selinux 상태를 알 수 없다({fs})")
    enforce = raw.get("enforce")
    if enforce is None:
        return res("UNKNOWN", "read-error", f"/sys/fs/selinux/enforce 를 읽지 못했다({raw.get('enforce_error') or '값 없음'})")
    enforce = enforce.strip()
    if enforce == "1":
        return res("FAIL", "enforcing", "enforcing — /sys/fs/selinux/enforce = 1")
    if enforce != "0":
        return res("UNKNOWN", "read-error", f"/sys/fs/selinux/enforce 값이 0·1 이 아니다({enforce!r})")
    # 지금 permissive — 재부팅 뒤를 본다(libselinux selinux_init_load_policy 우선순위: 부팅 인자 enforcing= > config).
    if "cmdline" not in raw:
        return res("UNKNOWN", "read-error", f"지금은 permissive 인데 부팅 인자(/proc/cmdline)를 읽지 못해 재부팅 뒤를 모른다"
                                            f"({raw.get('cmdline_error') or '값 없음'})")
    args = cmdline_args(raw["cmdline"])
    if args["selinux"] == 0:
        return res("PASS", "permissive", "permissive(enforce 0) — 부팅 인자 selinux=0 이라 재부팅하면 꺼진다")
    if args["enforcing"] is not None:
        if args["enforcing"] != 0:
            return res("FAIL", "reboot-enforcing",
                       "재부팅하면 enforcing — 지금은 enforce 0(setenforce 0 만 한 상태)이지만 부팅 인자에 enforcing=1 이 있다")
        return res("PASS", "permissive", "permissive(enforce 0) — 부팅 인자 enforcing=0 이 config 보다 우선한다")
    if raw.get("etc") != "visible":
        return res("UNKNOWN", "read-error", "지금은 permissive 인데 호스트 /etc 가 보이지 않아 /etc/selinux/config 를 확인하지 못했다")
    config = raw.get("config")
    if config is None:
        return res("UNKNOWN", "read-error", f"지금은 permissive 인데 /etc/selinux/config 를 읽지 못했다({raw.get('config_error') or '값 없음'})")
    mode = config_mode(raw.get("config_selinux") or []) if config == "present" else None
    if mode == "enforcing":
        return res("FAIL", "reboot-enforcing",
                   "재부팅하면 enforcing — 지금은 enforce 0(setenforce 0 만 한 상태)인데 /etc/selinux/config 가 SELINUX=enforcing 이고 "
                   "부팅 인자에 selinux=0·enforcing=0 이 없다")
    where = f"config SELINUX={mode}" if mode else ("config 없음" if config == "absent" else "config 에 유효한 SELINUX= 줄 없음")
    return res("PASS", "permissive", f"permissive(enforce 0) — {where}")


def verdict(results: list) -> str:
    """판정 대상(DMS 노드) 결과만 센다. INFO(판정 밖)는 빼고, 아무것도 못 쟀으면 모름."""
    judged = [r["status"] for r in results if r["status"] != "INFO"]
    if not judged:
        return "UNKNOWN"
    return max(judged, key=lambda s: _RANK[s])


def dms_nodes(items: list) -> tuple:
    """DMS 노드(판정 대상), 판정 밖 — deploy/pg-path-probe.py 의 dms_nodes 를 그대로 쓴다(설계 2-3 — S10·S2b 가 같은 정의, BACKLOG ⑤)."""
    return _pg_path_probe().dms_nodes(items)


_PG_PATH_PROBE = None


def _pg_path_probe():
    global _PG_PATH_PROBE
    if _PG_PATH_PROBE is None:
        spec = importlib.util.spec_from_file_location("dms_pg_path_probe", os.path.join(HERE, "pg-path-probe.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PG_PATH_PROBE = mod
    return _PG_PATH_PROBE


def ds_manifest(*, name: str, nonce: str, image: str, namespace: str) -> dict:
    labels = {NAME_KEY: NAME_VALUE, "app.kubernetes.io/part-of": "dms", NONCE_LABEL: nonce}
    ro = {"readOnly": True, "recursiveReadOnly": "IfPossible"}
    return {
        "apiVersion": "apps/v1", "kind": "DaemonSet",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "selector": {"matchLabels": {NAME_KEY: NAME_VALUE, NONCE_LABEL: nonce}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False, "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": 1,
                    "tolerations": [{"operator": "Exists"}],
                    "containers": [{
                        "name": "probe", "image": image, "imagePullPolicy": "Never",
                        "command": ["/bin/sh", "-c", POD_SCRIPT],
                        # privileged: CRI-O 가 프로세스 라벨을 주지 않는다(spc_t) — enforcing 노드에서 selinuxfs 읽기가 막혀 거짓 PASS 가 나지 않게.
                        "securityContext": {"privileged": True, "runAsUser": 0, "runAsGroup": 0, "readOnlyRootFilesystem": True},
                        "resources": {"requests": {"cpu": "1m", "memory": "8Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
                        "volumeMounts": [dict(name="host-sys-fs", mountPath="/host/sys/fs", **ro),
                                         dict(name="host-etc", mountPath="/host/etc", **ro)],
                    }],
                    # /etc/selinux 를 직접 걸지 않는다 — 없는 hostPath 소스는 CRI-O 가 만들어 Ubuntu 호스트에 디렉터리를 남긴다(설계 8-1).
                    "volumes": [{"name": "host-sys-fs", "hostPath": {"path": "/sys/fs", "type": "Directory"}},
                                {"name": "host-etc", "hostPath": {"path": "/etc", "type": "Directory"}}],
                },
            },
        },
    }


def cilium_image(ds_list: dict):
    """kube-system 의 `get ds -l k8s-app=cilium -o json` → Cilium agent 이미지 ref(없으면 None). DS 이름 cilium·컨테이너 cilium-agent 를 먼저."""
    items = sorted(ds_list.get("items") or [], key=lambda d: (d.get("metadata", {}).get("name") != "cilium",
                                                              d.get("metadata", {}).get("name", "")))
    for ds in items:
        containers = (((ds.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
        named = [c for c in containers if c.get("name") == CILIUM_CONTAINER]
        for c in named or containers[:1]:
            if c.get("image"):
                return c["image"]
    return None


# --- kubectl -----------------------------------------------------------------------------------------------------------

class Kube:
    """모든 클러스터 호출에 --context 를 붙인다. config get-contexts(존재 확인)만 로컬 호출이다."""

    def __init__(self, context: str, namespace: str, kubectl: str = "kubectl"):
        self.context, self.namespace, self.kubectl = context, namespace, kubectl

    def _run(self, args, *, input_text=None, timeout=60, cluster=True):
        cmd = [self.kubectl] + (["--context", self.context] if cluster else []) + list(args)
        # subprocess.run 은 예외(KeyboardInterrupt 포함) 때 자식을 죽이고 다시 올린다 — 중단이 kubectl 을 기다리지 않는다.
        return subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)

    def _json(self, args, what):
        r = self._run(args)
        if r.returncode != 0:
            raise RuntimeError(f"{what} 실패: {(r.stderr or r.stdout).strip()[:300]}")
        return json.loads(r.stdout or "{}")

    def context_exists(self) -> bool:
        try:
            return self._run(["config", "get-contexts", self.context], cluster=False, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def nodes(self) -> list:
        return self._json(["get", "nodes", "-o", "json"], "kubectl get nodes").get("items", [])

    def namespace_labels(self):
        """ns 라벨 사전, 없으면 None."""
        r = self._run(["get", "namespace", self.namespace, "--ignore-not-found", "-o", "json"])
        if r.returncode != 0:
            raise RuntimeError(f"kubectl get namespace {self.namespace} 실패: {(r.stderr or r.stdout).strip()[:300]}")
        if not r.stdout.strip():
            return None
        return (json.loads(r.stdout).get("metadata") or {}).get("labels") or {}

    def cilium_daemonsets(self) -> dict:
        return self._json(["-n", CILIUM_NAMESPACE, "get", "daemonsets", "-l", CILIUM_SELECTOR, "-o", "json"],
                          f"kubectl -n {CILIUM_NAMESPACE} get daemonsets -l {CILIUM_SELECTOR}")

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

    def logs(self, pod: str) -> str:
        r = self._run(["-n", self.namespace, "logs", pod, "-c", "probe"])
        return r.stdout if r.returncode == 0 else ""

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

def _pod_state(pod: dict):
    """('image'|'start'|'running'|'pending', 상세)."""
    status = pod.get("status") or {}
    for st in status.get("containerStatuses") or []:
        state = st.get("state") or {}
        waiting = state.get("waiting") or {}
        reason = waiting.get("reason")
        if reason in _IMAGE_REASONS:
            return "image", f"{reason}: {(waiting.get('message') or '').strip()[:200]}"
        if reason in _START_REASONS:
            return "start", f"{reason}: {(waiting.get('message') or '').strip()[:200]}"
        if "running" in state or "terminated" in state:
            return "running", ""
    return "pending", status.get("phase") or "Pending"


def collect(kube, *, nonce: str, ds_name: str, judged: list, others: list, not_ready: set, timeout: float,
            poll=None, grace=None, clock=None, sleep=None) -> dict:
    """{node: {'raw': 값|None, 'problem': 사유|None}} — 판정 대상 노드가 모두 정해지면(다른 노드는 grace 초까지 더 본다) 또는 timeout."""
    decided = {n: {"raw": None, "problem": "노드가 NotReady — 프로브 파드가 뜰 수 없다"} for n in not_ready}
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
            kind, detail = _pod_state(pod)
            if kind == "image":
                decided[node] = {"raw": None, "problem": f"이미지가 이 노드에 없다(imagePullPolicy Never — {detail}) — 모든 노드에 있는 "
                                                         "셸 포함 이미지를 --image 로 준다"}
            elif kind == "start":
                # 값을 다 찍고 나서 죽었으면(이미지에 sleep 이 없어 CrashLoopBackOff 등) 그 로그로 판정한다.
                raw = parse_logs(kube.logs(pod["metadata"]["name"]))
                decided[node] = {"raw": raw, "problem": None} if raw.get("done") else \
                    {"raw": None, "problem": f"프로브 컨테이너가 시작하지 못했다({detail})"}
            elif kind == "running":
                raw = parse_logs(kube.logs(pod["metadata"]["name"]))
                if raw.get("done"):
                    decided[node] = {"raw": raw, "problem": None}
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
            why = _ds_problem(kube, ds_name)
            decided[node] = {"raw": None, "problem": f"{int(timeout)}초 안에 이 노드에 프로브 파드가 생기지 않았다{why}"}
        else:
            decided[node] = {"raw": None, "problem": _pod_timeout_problem(kube, pod, timeout)}
    return decided


def _ds_problem(kube, ds_name: str) -> str:
    try:
        events = kube.events("DaemonSet", ds_name)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return ""
    for ev in events:
        if ev.get("reason") == "FailedCreate":
            return f" — {(ev.get('message') or '').strip()[:300]}(ns 의 PSA 가 privileged 인지 — S2 의 00-namespace.yaml)"
    return ""


def _pod_timeout_problem(kube, pod: dict, timeout: float) -> str:
    name = pod["metadata"]["name"]
    kind, detail = _pod_state(pod)
    try:
        events = kube.events("Pod", name)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        events = []
    for ev in events:
        if ev.get("reason") in ("FailedMount", "FailedScheduling", "Failed"):
            return f"{int(timeout)}초 안에 결과가 없다 — {ev.get('reason')}: {(ev.get('message') or '').strip()[:200]}"
    if kind == "running":
        return f"{int(timeout)}초 안에 프로브 출력이 끝나지 않았다(로그에 SEPROBE done 없음)"
    return f"{int(timeout)}초 안에 프로브 파드가 뜨지 않았다(파드 {name}: {detail})"


# --- main --------------------------------------------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(prog="selinux-probe.py", description="SELinux 노드 프로브(설계 v6 S2b — DMS 노드는 enforcing 금지)")
    p.add_argument("--context", default=os.environ.get("KUBE_CONTEXT") or None,
                   help="kubeconfig 컨텍스트(필수, 또는 env KUBE_CONTEXT — 플래그가 이긴다)")
    p.add_argument("--namespace", default="dms", help="프로브를 띄울 ns(PSA privileged — S2 의 00-namespace.yaml, 기본 dms)")
    p.add_argument("--image", help="모든 노드에 이미 있는 셸 포함 이미지 ref(기본: kube-system Cilium agent DaemonSet 의 이미지)")
    p.add_argument("--timeout", type=float, default=120.0, help="결과를 기다리는 시간(초, 기본 120)")
    p.add_argument("--json", action="store_true", help="stdout 에 JSON 결과만(사람용 줄은 stderr)")
    p.add_argument("--dry-run", action="store_true", help="읽기만 하고 DaemonSet 매니페스트를 보인다(아무것도 만들지 않는다)")
    return p.parse_args(argv)


def _usage(msg: str) -> int:
    sys.stderr.write(f"usage: {msg}\n")
    return 2


def _node_info(items: list) -> dict:
    out = {}
    for item in items:
        conds = {c.get("type"): c.get("status") for c in (item.get("status") or {}).get("conditions") or []}
        out[item["metadata"]["name"]] = {"ready": conds.get("Ready") == "True",
                                         "os": ((item.get("status") or {}).get("nodeInfo") or {}).get("osImage") or ""}
    return out


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
        return _usage("--context <ctx> 가 필요하다(또는 KUBE_CONTEXT) — privileged DaemonSet 을 만드는 스크립트라 current-context 에 기대지 않는다")
    if args.timeout <= 0:
        return _usage("--timeout > 0")
    human = sys.stderr if args.json else sys.stdout
    kube = kube_factory(args.context, args.namespace)
    if not kube.context_exists():
        sys.stderr.write(f"컨텍스트 '{args.context}' 가 kubeconfig 에 없습니다\n")
        return 2
    try:
        items = kube.nodes()
        if not items:
            raise UsageError("노드가 없다")
        judged, others = dms_nodes(items)
        if not judged:
            raise UsageError("DMS 노드가 없다(control-plane 도 역할 NoSchedule taint 없는 노드도 없음)")
        image = args.image
        if not image:
            image = cilium_image(kube.cilium_daemonsets())
            if not image:
                raise UsageError(f"{CILIUM_NAMESPACE} 에서 Cilium agent DaemonSet({CILIUM_SELECTOR})을 찾지 못했다 — 모든 노드에 이미 있는 "
                                 "셸 포함 이미지를 --image 로 준다(imagePullPolicy Never — 레지스트리 전이라 pull 하지 않는다)")
        labels = kube.namespace_labels()
        if labels is None:
            raise UsageError(f"ns {args.namespace} 가 없다 — S2 에서 deploy/k8s/00-namespace.yaml 을 먼저 적용한다(PSA privileged)")
        enforce = labels.get("pod-security.kubernetes.io/enforce")
        if enforce not in (None, "privileged"):
            raise UsageError(f"ns {args.namespace} 의 PSA enforce 가 '{enforce}' 다 — privileged 여야 프로브(privileged·hostPath)가 뜬다"
                             "(deploy/k8s/00-namespace.yaml)")
    except UsageError as exc:
        return _usage(str(exc))
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    info = _node_info(items)
    nonce = secrets.token_hex(8)
    ds_name = f"{NAME_VALUE}-{nonce[:8]}"
    manifest = ds_manifest(name=ds_name, nonce=nonce, image=image, namespace=args.namespace)
    human.write(f"[S2b] SELinux 노드 프로브 — context {args.context} · ns {args.namespace} · 이미지 {image}(Never) · DS {ds_name}\n")
    human.write(f"  판정 대상(DMS 노드 — 마스터 + 역할 NoSchedule taint 없는 워커, 설계 2-3) {len(judged)}: {', '.join(judged)}\n")
    if others:
        human.write(f"  판정 밖(정보만) {len(others)}: {', '.join(others)}\n")
    if args.dry_run:
        human.write("  (dry-run) privileged DaemonSet 을 만들어 노드마다 /sys/fs/selinux·/etc/selinux/config·부팅 인자를 읽고 지운다\n")
        if args.json:
            sys.stdout.write(json.dumps({"result": "DRY-RUN", "image": image, "judged": judged, "others": others,
                                         "manifest": manifest}, ensure_ascii=False) + "\n")
        else:
            human.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            human.write("RESULT: DRY-RUN\n")
        return 0

    def _terminate(signum, _frame):
        # 첫 신호만 중단으로 바꾸고 이후 신호는 무시한다 — 두 번째 Ctrl-C·SIGTERM 이 DaemonSet 삭제를 끊지 않게.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise KeyboardInterrupt(f"signal {signum}")

    previous = {sig: signal.signal(sig, _terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    results, created, aborted = [], False, None
    try:
        for ref in kube.leftovers():
            # 중단된 옛 실행이 남긴 프로브 — privileged DaemonSet 을 남겨 두지 않는다.
            human.write(f"  INFO    옛 프로브 {ref} 를 지운다\n")
            kube.delete(ref)
        created = True                     # create 가 실패해도(시간 초과 등) 서버에는 생겼을 수 있다 — 지우기는 늘 시도한다
        kube.create(manifest)
        not_ready = {n for n in judged + others if not info.get(n, {}).get("ready", True)}
        collected = collect(kube, nonce=nonce, ds_name=ds_name, judged=judged, others=others, not_ready=not_ready,
                            timeout=args.timeout)
        for node in judged + others:
            got = collected.get(node) or {"raw": None, "problem": "결과 없음"}
            res = judge(got["raw"]) if got["problem"] is None else {"status": "UNKNOWN", "code": "not-measured", "message": got["problem"]}
            entry = {"node": node, "dms": node in judged, "os": info.get(node, {}).get("os", ""), **res}
            if node not in judged:
                entry["status"], entry["measured"] = "INFO", res["status"]
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
        results = [{"node": "-", "dms": True, "os": "", "status": "UNKNOWN", "code": "aborted", "message": aborted}] + results
    for r in results:
        tag = "DMS" if r["dms"] else f"판정 밖 — 재면 {r.get('measured')}"
        human.write(f"  {r['status']:<7} {r['node']:<20} ({tag}{' · ' + r['os'] if r['os'] else ''}) {r['message']}\n")
    result = verdict(results)
    failed = [r["node"] for r in results if r["status"] == "FAIL"]
    unknown = [r["node"] for r in results if r["status"] == "UNKNOWN" and r["node"] != "-"]
    if failed:
        human.write(f"  FAIL 노드 {', '.join(failed)}: {FAIL_GUIDE}\n")
    if unknown:
        human.write(f"  모름 노드 {', '.join(unknown)}: {UNKNOWN_GUIDE}\n")
    if args.json:
        sys.stdout.write(json.dumps({"result": result, "image": image, "daemonset": ds_name, "nodes": results,
                                     "fail_guide": FAIL_GUIDE if failed else None,
                                     "unknown_guide": UNKNOWN_GUIDE if (unknown or aborted) else None}, ensure_ascii=False) + "\n")
    else:
        human.write(f"RESULT: {result}\n")
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
