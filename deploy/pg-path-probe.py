#!/usr/bin/env python3
"""PG 볼륨 경로 프로브 — S10 사전 검사·경로 대조(2026-11-03 ⑤, 설계 v6 8-1 「S10 세부」·6-2·#31).

  python3 deploy/pg-path-probe.py --context <ctx> --rendered deploy/.rendered/<site>/platform/db-probe --mode pv-check
  python3 deploy/pg-path-probe.py --context <ctx> --rendered deploy/.rendered/<site>/platform/db-probe --mode precheck
  python3 deploy/pg-path-probe.py --context <ctx> --rendered deploy/.rendered/<site>/platform/db-probe --mode contrast
      [--namespace dms] [--timeout 300] [--keep] [--dry-run] [--render-env <렌더>/render.env] [--nodes a,b]

PG 데이터는 정적 hostPath PV(<SHARED_DIR>/dms/pgdata/dms-pgdata-01..05, type Directory)에 있다. hostPath 는 노드의 경로를 그대로 붙이므로,
공용 스토리지가 빠진 노드에 같은 이름의 로컬 디렉터리가 있으면 PG 가 조용히 로컬 디스크에 쓴다(#31 의 조용한 위반, 위험 #29). 이 프로브가
그것을 동작으로 잡는다 — 렌더된 일회용 DaemonSet(deploy/platform/db/probe — uid 26, 읽기 전용, 모든 노드)이 노드마다 PV 다섯 디렉터리의
모양(uid:gid:mode)·센티널(.dms-pv-id)·pgdata/PG_VERSION 을 찍고, 이 스크립트가 모아 판정한 뒤 DaemonSet 을 지운다.

  pv-check(db-volumes 를 적용하기 **전** — S10 「SC·PV 가 이미 있는데 path·SC·용량이 다르면 FAIL」): 프로브를 띄우지 않는다. 이미 있는 PV 의
    hostPath 경로·type·capacity·storageClassName 과 SC 의 provisioner·reclaimPolicy·volumeBindingMode 를 렌더 값(render.env 의 SHARED_DIR·
    PG_STORAGE_SIZE)과 대조한다. 적용 뒤에 보면 늦다(2026-11-03 ⑤ 리뷰 — k8s 1.34 ValidatePersistentVolumeUpdate): PV 의 hostPath·volumeMode 는
    apiserver 가 거절하지만(시끄럽다) **capacity·storageClassName 은 가변이라 SSA 가 Bound PV 까지 조용히 바꾼다** — 그 뒤 CNPG 웹훅이 storage.size
    축소를 거절하면 PV 용량 < Cluster size 가 되어 다음 재생성 PVC 가 Pending 이다. 없는 PV·SC 는 INFO(적용이 만든다).
  판정 대상 노드 = **DMS 노드**(설계 2-3 — S2b 와 같은 정의: 마스터 + NoSchedule taint 없는 워커, 곧 dms-agent 가 뜨는 노드. cordon 같은
    node.kubernetes.io/* 상태 taint 는 세지 않는다). 프로브 파드는 모든 노드에 뜨지만(toleration Exists) 그 밖의 노드(다른 팀의 taint 노드 —
    공용 스토리지가 없을 수 있다)는 판정하지 않고 목록만 보인다(2026-11-03 ⑤ 리뷰 2: 공유 클러스터에서 늘 FAIL 했다). `--nodes a,b` 로 바꾼다.
  precheck(Cluster 를 만들기 전): PV 다섯 개가 있고, **판정 대상 노드마다** 각 디렉터리가 26:26 0700 이고 .dms-pv-id == PV 이름이며, Available PV 에
    옛 PGDATA(pgdata/)가 없다 — 있으면 FAIL(새 인스턴스가 붙으면 CNPG 1.30 은 실패하지 않고 옛 PGDATA 를 같은 PV 안 pgdata_<UTC> 로
    옮긴 뒤 진행한다 — 옛 데이터가 조용히 남는다(2026-11-03 T6 실측; 재활용 런북 deploy/README §2f-5). 어느 단계든 PV 안에 그 잔재
    (pgdata_*)가 보이면 WARN.
    Released PV 는 건드리지 않고 WARN(S10), Bound 는 재실행으로 보고 INFO.
  contrast(Cluster Ready 뒤 — 「경로 대조」): Bound PV(claim 이 이 네임스페이스) 마다 **판정 대상 노드 모두**가 같은 pgdata/PG_VERSION 을 본다 —
    모두 보면 인스턴스가 아닌 노드도 본 것이다(PG 인스턴스는 DMS 노드에만 뜬다 — 마스터, 테스트베드 all-nodes 는 taint 없는 노드).
    한 노드라도 못 보면 FAIL 「PG 가 공용 경로가 아닌 곳에 씀」. Bound 가 없으면 모름. 단 Cluster
    dms-pg 의 status.initializingPVC 에 있는 PVC(initdb·join — pg_basebackup 중이라 PG_VERSION 이 아직 없을 수 있다)는 UNKNOWN 「초기화 중 — Ready
    뒤 다시」(2026-11-03 ⑤ 리뷰: readyInstances ≥ 1 직후 대조하면 join 중인 인스턴스를 FAIL 로 오판했다).
  공통: 판정 대상 노드의 파드가 PV 경로가 없어 뜨지 못하면(FailedMount — hostPath type 검사) FAIL 「마운트 없음·S0 미실행」, 이미지 pull 실패·시간 초과는
    모름. Available 여분 PV < 1 이면 WARN(인스턴스 재생성이 Pending 이 된다 — 데이터 위험은 없다). precheck·contrast 도 pv-check 의 값 대조를
    같이 한다(어긋남만 FAIL 로 보인다 — 손 patch 등 적용 뒤에 생긴 어긋남).
교차 노드 원칙(S7 과 같다): 파드는 PG_VERSION 을 stat 이 아니라 open 으로 읽고 60초(NFS 기본 acdirmax)까지 재시도한 뒤 「없음」을 찍는다.

판정·종료 코드(S7 과 같은 규약 — 모름 ≠ 실패): 0 PASS · 3 WARN 만 · 1 FAIL 하나라도 · 4 모름(FAIL 없음) · 2 사용법·환경(클러스터를 건드리기
전). 마지막 줄은 `RESULT: <PASS|WARN|FAIL|UNKNOWN>`. 시작할 때 남아 있는 프로브 DaemonSet 을 먼저 지운다(옛 결과를 읽지 않게), 끝나면(실패·
중단에도) 지운다(--keep 이면 남김). kubectl 은 모든 호출에 --context 를 붙인다(필수 — 두 테스트베드가 같은 네임스페이스). 렌더 디렉터리에
kube-context 파일이 있으면 그 값과 --context 가 같아야 한다. 렌더 값은 render.env(기본 <--rendered>/../../render.env — render.sh 가 늘 쓴다)에서
읽는다 — 없거나 SHARED_DIR·PG_STORAGE_SIZE 가 비면 2. 설치 호스트의 python3(RHEL 9 = 3.9), 표준 라이브러리만.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

DS_NAME = "dms-pg-path-probe"
LABEL = "app.kubernetes.io/name=dms-pg-path-probe"
CLUSTER = "dms-pg"
PVS = tuple("dms-pgdata-0%d" % n for n in range(1, 6))
WANT_SHAPE = "26:26:700"
SC_NAME = "dms-pg-shared"
# SC 의 바꿀 수 없는 필드(ValidateStorageClassUpdate — 다르면 SSA 가 거절한다; 여기서는 이유를 먼저 보인다). 값은 platform/db/volumes 그대로.
WANT_SC = {"provisioner": "kubernetes.io/no-provisioner", "reclaimPolicy": "Retain", "volumeBindingMode": "Immediate"}
_QTY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E|)$")
_QTY_MULT = {"": 1, "k": 10 ** 3, "M": 10 ** 6, "G": 10 ** 9, "T": 10 ** 12, "P": 10 ** 15, "E": 10 ** 18,
             "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60}
CP_LABEL = "node-role.kubernetes.io/control-plane"
EXIT = {"PASS": 0, "FAIL": 1, "WARN": 3, "UNKNOWN": 4}
_RANK = {"INFO": 0, "PASS": 1, "WARN": 2, "UNKNOWN": 3, "FAIL": 4}
_LINE = re.compile(r"^PGPROBE node=(\S+) (.*)$")
_FIELD = re.compile(r"(\w+)=(\S*)")
_PULL_REASONS = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull"}


class UsageError(Exception):
    pass



# --- 판정(순수 함수 — 테스트가 직접 부른다) ------------------------------------------------------------------------------------

def parse_logs(text: str) -> dict:
    """한 파드의 로그 → {"pv": {pv: {"shape","id","pgdata","stale"}}, "done": bool, "node": 이름 또는 None}. 모르는 줄은 무시."""
    out = {"pv": {}, "done": False, "node": None}
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        out["node"] = m.group(1)
        rest = m.group(2)
        if rest == "done":
            out["done"] = True
            continue
        fields = dict(_FIELD.findall(rest))
        pv = fields.get("pv")
        if pv not in PVS:
            continue
        entry = out["pv"].setdefault(pv, {})
        for key in ("shape", "id", "pgdata", "stale"):
            if key in fields:
                entry[key] = fields[key]
    return out


def judge(mode: str, nodes: list, results: dict, pvs: dict, node_problems: dict, namespace: str = "dms",
          initializing=None) -> list:
    """[(LEVEL, 메시지)].

    nodes          판정 대상 노드 이름(dms_nodes 의 DMS 노드 또는 --nodes — DaemonSet 은 toleration Exists 라 모두에 뜨지만 판정은 이 노드만)
    results        {노드: parse_logs 결과(done 인 것만)}
    pvs            {PV 이름: {"phase": …, "claim": "ns/name" 또는 None, …}} — 이름 dms-pgdata-01..05 인 PV
    node_problems  {노드: ("mount"|"pull"|"timeout", 상세)} — 결과가 없는 노드의 사유
    initializing   contrast: Cluster dms-pg 의 status.initializingPVC(PVC 이름 집합) — 그 PV 는 대조하지 않고 UNKNOWN. None = 모름(대조함)
    """
    out = []
    for node in nodes:
        if node in results:
            continue
        kind, detail = node_problems.get(node, ("timeout", "결과 없음"))
        if kind == "mount":
            out.append(("FAIL", f"{node}: PV 경로가 없어 프로브 파드가 뜨지 못했다(FailedMount — 공용 스토리지 마운트 없음·S0 미실행): {detail}"))
        elif kind == "pull":
            out.append(("UNKNOWN", f"{node}: 프로브 이미지를 받지 못했다({detail}) — 레지스트리 신뢰(S0-4)·S5 push 를 확인"))
        else:
            out.append(("UNKNOWN", f"{node}: 결과를 받지 못했다({detail})"))
    seen = [n for n in nodes if n in results]
    for pv in PVS:
        if pv not in pvs:
            out.append(("FAIL", f"{pv}: PV 가 없다 — platform/db-volumes 를 먼저 적용"))
    for node in seen:
        per = results[node]["pv"]
        for pv in PVS:
            e = per.get(pv)
            if e is None or "shape" not in e:
                out.append(("UNKNOWN", f"{node} {pv}: 모양 줄이 없다(프로브 출력이 잘렸다)"))
                continue
            if e["shape"] != WANT_SHAPE:
                out.append(("FAIL", f"{node} {pv}: 모양 {e['shape']} — 기대 {WANT_SHAPE}(S0 가 uid/gid 26 0700 으로 만든다 — hostPath 는 fsGroup "
                                    f"을 적용하지 않는다; 공용 경로라 손으로 고치기 전에 S0 --check)"))
            if e.get("id") != pv:
                out.append(("FAIL", f"{node} {pv}: 센티널 .dms-pv-id = {e.get('id', '<없음>')} — 기대 {pv}(마운트 빠진 노드의 로컬 디렉터리이거나 "
                                    f"S0 를 거치지 않은 디렉터리)"))
    available = [pv for pv in PVS if pvs.get(pv, {}).get("phase") == "Available"]
    for pv in PVS:
        state = pvs.get(pv)
        if state is None:
            continue
        phase, claim = state.get("phase"), state.get("claim")
        seen_values = {node: results[node]["pv"].get(pv, {}).get("pgdata") for node in seen}
        has_data = sorted(n for n, v in seen_values.items() if v not in (None, "none"))
        stale = max((int(results[n]["pv"].get(pv, {}).get("stale") or 0) for n in seen
                     if str(results[n]["pv"].get(pv, {}).get("stale") or "0").isdigit()), default=0)
        if stale:
            out.append(("WARN", f"{pv}: 옛 PGDATA 잔재 pgdata_* {stale}개 — CNPG 가 옛 데이터를 옮겨 두고 진행한 흔적(CNPG 1.30 동작 — 테스트베드 실측). 내용을 확인한 뒤 "
                                f"같은 FS 의 pgdata/.retired 로 옮긴다(재활용 런북 deploy/README §2f-5)"))
        if phase == "Released":
            out.append(("WARN", f"{pv}: Released(옛 claim {claim}) — 건드리지 않는다; 재활용은 런북(deploy/README §2f-5)"))
            continue
        if phase == "Available":
            if has_data:
                level = "FAIL" if mode == "precheck" else "WARN"
                out.append((level, f"{pv}: Available 인데 옛 PGDATA(pgdata/)가 보인다({', '.join(has_data)}) — 새 인스턴스가 붙으면 CNPG 가 "
                                   f"실패 없이 옛 데이터를 pgdata_<UTC> 로 옮겨 두고 새로 시작한다(옛 데이터가 조용히 남음). 재활용 런북(.retired 로 "
                                   f"옮기고 빈 디렉터리 + .dms-pv-id)"))
            continue
        if phase != "Bound":
            out.append(("UNKNOWN", f"{pv}: 상태 {phase}"))
            continue
        if mode == "precheck":
            out.append(("INFO", f"{pv}: 이미 Bound({claim}) — 사전 검사는 Cluster 전 단계다(재실행이면 정상)"))
            continue
        if not claim or not claim.startswith(namespace + "/"):
            out.append(("WARN", f"{pv}: 다른 네임스페이스의 PVC 가 가져갔다({claim}) — 전용 SC 를 다른 곳이 쓰고 있다(위험 #24)"))
            continue
        if initializing is not None and claim.split("/", 1)[1] in initializing:
            # initdb·join(pg_basebackup) 중 — PG_VERSION 이 아직 없을 수 있다. 데이터는 아직 없으니 대조를 미룬다(모름 ≠ 실패).
            out.append(("UNKNOWN", f"{pv}({claim}): 인스턴스가 초기화 중(Cluster status.initializingPVC — initdb·join) — Ready(readyInstances 3) "
                                   f"뒤 다시 대조"))
            continue
        missing = sorted(n for n, v in seen_values.items() if not v or v in ("none",) or not v.isdigit())
        values = sorted({v for v in seen_values.values() if v and v.isdigit()})
        if missing:
            out.append(("FAIL", f"{pv}({claim}): {', '.join(missing)} 에서 pgdata/PG_VERSION 이 안 보인다 — PG 가 공용 경로가 아닌 곳에 썼다"
                                f"(그 인스턴스 노드의 마운트·로컬 디렉터리 확인; 데이터 위험 — 운영 규칙 6-6 을 지키며 조사)"))
        elif len(values) > 1:
            out.append(("FAIL", f"{pv}({claim}): 노드마다 PG_VERSION 이 다르다({values}) — 같은 스토리지가 아니다"))
        elif seen:
            out.append(("PASS", f"{pv}({claim}): 모든 노드({len(seen)})가 같은 PGDATA 를 본다(PG {values[0]})"))
    if mode == "contrast" and not any(pvs.get(pv, {}).get("phase") == "Bound" for pv in PVS):
        out.append(("UNKNOWN", "Bound PV 가 없다 — Cluster dms-pg 가 아직 PVC 를 만들지 않았다(경로 대조는 Ready 뒤에)"))
    if pvs and len(available) < 1:
        out.append(("WARN", "Available 여분 PV 가 없다 — 인스턴스를 볼륨째 다시 만들면 새 PVC 가 Pending 이 된다(데이터 위험은 없다; "
                            "Released 를 런북으로 되돌린다)"))
    if not any(level in ("FAIL", "WARN", "UNKNOWN") for level, _ in out) and seen:
        out.append(("PASS", f"{mode}: {len(seen)}개 노드 모두 PV 다섯 디렉터리가 {WANT_SHAPE}·센티널 일치"))
    return out


def dms_nodes(items: list) -> tuple:
    """`get nodes -o json` 의 items → (판정 대상, 판정 밖) 이름 목록(정렬).

    DMS 노드(설계 2-3 — S2b 와 같은 정의) = control-plane 라벨이 있는 노드 + 역할 NoSchedule taint 가 없는 노드(dms-agent DS 는 NoExecute 만 견디고
    nodeSelector 가 없다 — 50-agent-daemonset.yaml). node.kubernetes.io/*(unschedulable·not-ready·압박 등)는 노드 **상태** taint 라 세지 않는다 —
    cordon 한 워커도 DMS 노드다(빠지면 유지보수 중인 노드의 로컬 쓰기를 놓친다).
    """
    inside, outside = [], []
    for item in items:
        meta = item.get("metadata") or {}
        taints = (item.get("spec") or {}).get("taints") or []
        role = [t for t in taints if t.get("effect") == "NoSchedule" and not str(t.get("key") or "").startswith("node.kubernetes.io/")]
        (inside if CP_LABEL in (meta.get("labels") or {}) or not role else outside).append(meta["name"])
    return sorted(inside), sorted(outside)


def _quantity(text):
    """k8s 수량 → 바이트(Fraction) — 「20Gi」 와 apiserver 가 정규화한 「21474836480」 을 같게 본다. 모르는 모양이면 None."""
    from fractions import Fraction
    m = _QTY.match(text or "")
    return Fraction(m.group(1)) * _QTY_MULT[m.group(2)] if m else None


def check_pvs(pvs: dict, sc, expected: dict, verbose: bool = False) -> list:
    """이미 있는 SC·PV 의 값 대조(S10) → [(LEVEL, 메시지)]. 어긋남은 FAIL, verbose(pv-check)면 없음·같음도 INFO 로.

    pvs       {PV 이름: {"phase","claim","path","type","capacity","sc"}} — 없는 PV 는 키가 없다
    sc        SC dms-pg-shared 의 {"provisioner","reclaimPolicy","volumeBindingMode"} 또는 None(없음)
    expected  {"shared_dir": render.env SHARED_DIR, "size": render.env PG_STORAGE_SIZE}
    """
    out = []
    root = expected["shared_dir"].rstrip("/") + "/dms/pgdata/"
    if sc is None:
        if verbose:
            out.append(("INFO", f"SC {SC_NAME}: 없음 — db-volumes 적용이 만든다"))
    else:
        bad = [f"{k} {sc.get(k)!r}(렌더 {v!r})" for k, v in WANT_SC.items() if sc.get(k) != v]
        if bad:
            out.append(("FAIL", f"SC {SC_NAME} 가 렌더와 다르다: {', '.join(bad)} — 이 필드는 만든 뒤 바꿀 수 없어 SSA 가 거절한다(조용히 다시 "
                                f"만들지 않는다 — 누가 만든 SC 인지 확인)"))
        elif verbose:
            out.append(("INFO", f"SC {SC_NAME}: 렌더와 같다"))
    for pv in PVS:
        st = pvs.get(pv)
        if st is None:
            if verbose:
                out.append(("INFO", f"{pv}: 없음 — db-volumes 적용이 만든다"))
            continue
        want_path = root + pv
        bad = []
        if st.get("path") != want_path:
            where = "" if (st.get("path") or "").startswith(root) else f" — <SHARED_DIR>/dms/pgdata/ 밖"
            bad.append(f"hostPath {st.get('path')!r}(렌더 {want_path!r}{where}; 만든 뒤 바꿀 수 없다 — apiserver 가 거절)")
        if st.get("type") != "Directory":
            bad.append(f"hostPath type {st.get('type')!r}(렌더 'Directory'; 바꿀 수 없다)")
        live_q, want_q = _quantity(st.get("capacity")), _quantity(expected["size"])
        if live_q is not None and want_q is not None:
            cap_differs = live_q != want_q
        else:
            cap_differs = st.get("capacity") != expected["size"]
        if cap_differs:
            bad.append(f"capacity {st.get('capacity')!r}(렌더 PG_STORAGE_SIZE {expected['size']!r}; SSA 가 조용히 바꾼다 — CNPG 는 storage.size "
                       f"축소를 거절하므로 둘이 어긋나 재생성 PVC 가 Pending 이 된다: values 의 PG_STORAGE_SIZE 를 라이브 값으로)")
        if st.get("sc") != SC_NAME:
            bad.append(f"storageClassName {st.get('sc')!r}(렌더 {SC_NAME!r}; SSA 가 조용히 바꾼다 — 다른 SC 로 묶인 PVC 와 어긋난다)")
        if bad:
            out.append(("FAIL", f"{pv}: 이미 있는 PV 가 렌더와 다르다 — {'; '.join(bad)}. 조용히 다시 만들지 않는다(사이트 결정)"))
        elif verbose:
            out.append(("INFO", f"{pv}: 렌더와 같다({st.get('phase')}{' ' + st['claim'] if st.get('claim') else ''})"))
    if verbose and not any(level == "FAIL" for level, _ in out):
        out.append(("PASS", "pv-check: 이미 있는 SC·PV 가 렌더(경로·type·용량·SC)와 같다 — 없는 것은 적용이 만든다"))
    return out


def verdict(findings: list) -> str:
    worst = "PASS"
    for level, _ in findings:
        if level != "INFO" and _RANK[level] > _RANK[worst]:
            worst = level
    return worst


# --- kubectl -----------------------------------------------------------------------------------------------------------

class Kube:
    def __init__(self, context: str, namespace: str):
        self.context = context
        self.namespace = namespace

    def run(self, *args, timeout: float = 60, check: bool = True, namespaced: bool = True) -> str:
        cmd = ["kubectl", "--context", self.context]
        if namespaced:
            cmd += ["-n", self.namespace]
        cmd += list(args)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def context_exists(self) -> bool:
        r = subprocess.run(["kubectl", "config", "get-contexts", self.context], capture_output=True, text=True, timeout=30)
        return r.returncode == 0


def _select_nodes(kube, wanted_arg):
    """판정 대상 노드 → (대상, 판정 밖, 어떻게 골랐나). --nodes 면 그 목록(없는 이름은 UsageError — 클러스터를 건드리기 전), 아니면 DMS 노드."""
    items = json.loads(kube.run("get", "nodes", "-o", "json", namespaced=False)).get("items", [])
    names = sorted(i["metadata"]["name"] for i in items)
    if wanted_arg:
        wanted = sorted({n.strip() for n in wanted_arg.split(",") if n.strip()})
        unknown = [n for n in wanted if n not in names]
        if unknown or not wanted:
            raise UsageError(f"--nodes 에 클러스터에 없는 노드: {unknown or '(빈 목록)'} — 있는 노드: {', '.join(names)}")
        return wanted, [n for n in names if n not in wanted], "--nodes"
    inside, outside = dms_nodes(items)
    if not inside:
        raise UsageError(f"DMS 노드가 없다(control-plane 도 NoSchedule taint 없는 노드도 없음 — 노드 {', '.join(names) or '0'}) — --nodes 로 고른다")
    return inside, outside, "DMS 노드 — 마스터 + NoSchedule taint 없는 워커, 설계 2-3"


def _items(text: str) -> list:
    """`get <이름…> --ignore-not-found -o json` 출력 → 객체 목록. 하나도 없으면 빈 출력이고, 이름 하나면 List 가 아니라 객체 하나다."""
    if not text.strip():
        return []
    data = json.loads(text)
    return data.get("items", []) if "items" in data else [data]


def _pv_states(kube) -> dict:
    # 이름으로 읽는다(라벨이 아니라) — 라벨 없는 같은 이름 PV 도 대조에 들어와야 한다(SSA 가 그 PV 를 덮는다).
    out = {}
    for item in _items(kube.run("get", "pv", *PVS, "--ignore-not-found", "-o", "json", namespaced=False)):
        spec = item.get("spec") or {}
        ref = spec.get("claimRef") or {}
        claim = f"{ref.get('namespace')}/{ref.get('name')}" if ref.get("name") else None
        host = spec.get("hostPath") or {}
        out[item["metadata"]["name"]] = {"phase": (item.get("status") or {}).get("phase"), "claim": claim,
                                         "path": host.get("path"), "type": host.get("type"),
                                         "capacity": (spec.get("capacity") or {}).get("storage"), "sc": spec.get("storageClassName")}
    return out


def _sc_state(kube):
    items = _items(kube.run("get", "sc", SC_NAME, "--ignore-not-found", "-o", "json", namespaced=False))
    return {k: items[0].get(k) for k in WANT_SC} if items else None


def _initializing_pvcs(kube):
    """Cluster dms-pg 의 status.initializingPVC 이름 집합. Cluster 가 없거나 못 읽으면 (None, 사유)."""
    try:
        items = _items(kube.run("get", "clusters.postgresql.cnpg.io", CLUSTER, "--ignore-not-found", "-o", "json"))
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return None, f"Cluster {CLUSTER} 상태를 읽지 못했다({exc})"
    if not items:
        return None, f"Cluster {CLUSTER} 가 없다"
    return set((items[0].get("status") or {}).get("initializingPVC") or []), None


def _pods(kube) -> list:
    return json.loads(kube.run("get", "pods", "-l", LABEL, "-o", "json")).get("items", [])


def _pod_problem(kube, pod: dict):
    """뜨지 못한 파드의 사유 — ("mount"|"pull"|"timeout", 상세)."""
    name = pod["metadata"]["name"]
    for st in (pod.get("status") or {}).get("containerStatuses") or []:
        waiting = (st.get("state") or {}).get("waiting") or {}
        if waiting.get("reason") in _PULL_REASONS:
            return ("pull", waiting.get("reason"))
    try:
        ev = json.loads(kube.run("get", "events", "--field-selector", f"involvedObject.name={name}", "-o", "json"))
    except (RuntimeError, ValueError):
        ev = {}
    for item in ev.get("items", []):
        if item.get("reason") == "FailedMount":
            return ("mount", (item.get("message") or "").splitlines()[0][:200])
    return ("timeout", f"파드 {name} 상태 {(pod.get('status') or {}).get('phase')}")


def _wait_gone(kube, deadline: float):
    while time.monotonic() < deadline:
        if not _pods(kube):
            return True
        time.sleep(2)
    return False


def collect(kube, nodes: list, timeout: float, log=print):
    """프로브 파드가 노드마다 `done` 을 찍을 때까지 모은다 → (results, node_problems)."""
    deadline = time.monotonic() + timeout
    results, last = {}, {}
    while True:
        pods = _pods(kube)
        by_node = {}
        for pod in pods:
            node = (pod.get("spec") or {}).get("nodeName")
            if node and not (pod.get("metadata") or {}).get("deletionTimestamp"):
                by_node[node] = pod
        for node, pod in by_node.items():
            if node in results:
                continue
            last[node] = pod
            if (pod.get("status") or {}).get("phase") != "Running":
                continue
            text = kube.run("logs", pod["metadata"]["name"], "-c", "probe", check=False)
            parsed = parse_logs(text)
            if parsed["done"]:
                results[node] = parsed
                log(f"  ...   {node}: 결과 받음")
        if all(n in results for n in nodes) or time.monotonic() >= deadline:
            break
        time.sleep(3)
    problems = {}
    for node in nodes:
        if node in results:
            continue
        pod = last.get(node)
        problems[node] = _pod_problem(kube, pod) if pod else ("timeout", "이 노드에 프로브 파드가 없다(노드 NotReady?)")
    return results, problems


# --- main --------------------------------------------------------------------------------------------------------------

def parse_args(argv):
    ap = argparse.ArgumentParser(description="PG 볼륨 경로 프로브(S10 사전 검사·경로 대조)")
    ap.add_argument("--context", required=True, help="kube-context(필수)")
    ap.add_argument("--rendered", required=True, help="렌더된 프로브 디렉터리(deploy/.rendered/<site>/platform/db-probe)")
    ap.add_argument("--mode", required=True, choices=("pv-check", "precheck", "contrast"))
    ap.add_argument("--render-env", help="렌더 값(SHARED_DIR·PG_STORAGE_SIZE) — 기본 <--rendered>/../../render.env")
    ap.add_argument("--namespace", default="dms")
    ap.add_argument("--timeout", type=float, default=300.0, help="프로브 결과를 기다리는 시간(초, 기본 300 — 파드 안에서 60초 재시도)")
    ap.add_argument("--keep", action="store_true", help="끝나도 DaemonSet 을 지우지 않는다(조사용)")
    ap.add_argument("--dry-run", action="store_true", help="렌더 결과·노드·PV 상태만 보이고 아무것도 만들지 않는다")
    ap.add_argument("--nodes", help="판정할 노드 쉼표 목록(기본: DMS 노드 = control-plane + NoSchedule taint 없는 노드, 설계 2-3 — 그 밖은 정보만)")
    return ap.parse_args(argv)


def _expected_context(rendered: str):
    path = os.path.join(rendered, "kube-context")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            value = re.sub(r"\s", "", line.split("#", 1)[0])
            if value:
                return value
    return ""


def _render_values(path: str):
    """render.env → {"shared_dir","size"}, 없거나 비면 None. `KEY=값`·`KEY="값"` 줄만 본다(sh 로 . 하지 않는다)."""
    if not os.path.isfile(path):
        return None
    values = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r'^([A-Z_]+)="?([^"\n]*)"?\s*$', line)
            if m:
                values[m.group(1)] = m.group(2)
    if not values.get("SHARED_DIR", "").startswith("/") or not values.get("PG_STORAGE_SIZE"):
        return None
    return {"shared_dir": values["SHARED_DIR"], "size": values["PG_STORAGE_SIZE"]}


def _probe(kube, args, expected, state):
    """precheck·contrast — 프로브 DaemonSet 을 띄워 모으고 판정한다. dry-run 이면 None(아무것도 만들지 않음)."""
    nodes, outside, how = _select_nodes(kube, args.nodes)
    print(f"  판정 대상 노드 {len(nodes)}({how}): {', '.join(nodes)}")
    if outside:
        # 프로브 파드는 거기에도 뜨지만(toleration Exists) 결과·FailedMount 로 판정하지 않는다 — 넣으려면 --nodes.
        print(f"  판정 밖 노드 {len(outside)}(정보만 — 넣으려면 --nodes): {', '.join(outside)}")
    if args.dry_run:
        pvs = _pv_states(kube)
        for pv in PVS:
            st = pvs.get(pv)
            print(f"  PV {pv}: " + (f"{st['phase']} {st['claim'] or ''} {st['path']} {st['capacity']} {st['sc']}" if st else "없음"))
        print(kube.run("kustomize", args.rendered, namespaced=False, timeout=120))
        print("(dry-run — 프로브를 띄우지 않았다)")
        return None
    # 남은 프로브(옛 결과)를 먼저 지운다 — 같은 파드 템플릿이면 SSA 가 파드를 다시 띄우지 않아 옛 로그를 읽게 된다.
    kube.run("delete", "ds", DS_NAME, "--ignore-not-found", "--wait=false")
    if not _wait_gone(kube, time.monotonic() + 90):
        raise RuntimeError("옛 프로브 파드가 90초 안에 사라지지 않았다")
    kube.run("apply", "--server-side", "--field-manager=dms-install", "-k", args.rendered, namespaced=False, timeout=120)
    state["applied"] = True
    results, problems = collect(kube, nodes, args.timeout)
    pvs = _pv_states(kube)
    findings, initializing = [], None
    if args.mode == "contrast":
        initializing, why = _initializing_pvcs(kube)
        if why:
            findings.append(("UNKNOWN", f"{why} — 초기화 중(initdb·join) PVC 를 가리지 못한다(대조는 그대로 한다)"))
    findings += judge(args.mode, nodes, results, pvs, problems, args.namespace, initializing)
    findings += check_pvs(pvs, _sc_state(kube), expected)
    if outside:
        findings.append(("INFO", f"판정 밖 노드: {', '.join(outside)} — 결과를 보지 않았다(판정 대상 = {how})"))
    return findings


def main(argv=None, *, kube_factory=Kube) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not os.path.isfile(os.path.join(args.rendered, "kustomization.yaml")):
        print(f"FAIL: {args.rendered}/kustomization.yaml 이 없다 — sh deploy/render.sh <site> 로 만든 platform/db-probe 를 준다", file=sys.stderr)
        return 2
    expect = _expected_context(args.rendered)
    if expect is not None and expect != args.context:
        print(f"FAIL: {args.rendered} 는 컨텍스트 '{expect}' 전용인데 --context {args.context} 를 겨눴다(kube-context)", file=sys.stderr)
        return 2
    env_path = args.render_env or os.path.normpath(os.path.join(args.rendered, os.pardir, os.pardir, "render.env"))
    expected = _render_values(env_path)
    if expected is None:
        print(f"FAIL: {env_path} 에 SHARED_DIR(절대 경로)·PG_STORAGE_SIZE 가 없다 — sh deploy/render.sh <site> 가 쓴 render.env(또는 --render-env)",
              file=sys.stderr)
        return 2
    kube = kube_factory(args.context, args.namespace)
    if not kube.context_exists():
        print(f"FAIL: kubeconfig 에 컨텍스트 '{args.context}' 가 없다", file=sys.stderr)
        return 2
    print(f"== PG 볼륨 경로 프로브({args.mode}) — context {args.context}, {args.rendered} ==")
    findings = []

    def _term(signum, frame):
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGTERM, _term)
    state = {"applied": False}
    try:
        print(f"  렌더 값: SHARED_DIR={expected['shared_dir']} PG_STORAGE_SIZE={expected['size']} SC={SC_NAME} ({env_path})")
        if args.mode == "pv-check":
            # 적용 전 값 대조만 — 프로브를 띄우지 않는다(노드·DaemonSet 무관).
            findings = check_pvs(_pv_states(kube), _sc_state(kube), expected, verbose=True)
        else:
            findings = _probe(kube, args, expected, state)
            if findings is None:
                return 0
    except UsageError as exc:
        # 노드 선택 문제(--nodes 의 없는 이름·DMS 노드 0) — 프로브를 띄우기 전이다.
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        findings.append(("UNKNOWN", "중단됨"))
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        findings.append(("UNKNOWN", f"kubectl 실패: {exc}"))
    finally:
        signal.signal(signal.SIGTERM, old)
        if state["applied"] and not args.keep:
            try:
                kube.run("delete", "-k", args.rendered, "--ignore-not-found", "--wait=false", namespaced=False, check=False)
            except (subprocess.TimeoutExpired, OSError) as exc:
                findings.append(("INFO", f"프로브 정리 실패 — 손으로: kubectl --context {args.context} -n {args.namespace} delete ds {DS_NAME} ({exc})"))
    for level, msg in findings:
        print(f"  {level:<7} {msg}")
    result = verdict(findings)
    print(f"RESULT: {result}")
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
