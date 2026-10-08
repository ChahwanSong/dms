"""요청 삭제 정리 테스트용 가짜 k8s(execution_volcano.K8sClient 계약 + list_vcjob_briefs). 테스트 전용이라 shutil 을 쓴다
(src/dms 에는 삭제 코드를 두지 않는다 -- purge 파드가 하는 일을 run_purge_pods 가 흉내 낸다)."""
import itertools
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

_AUTO = object()
_UIDS = itertools.count(1)


def _new_uid():
    return f"uid-{next(_UIDS)}"


class AlreadyExists(Exception):
    status = 409


def _match(labels, selector):
    m = re.fullmatch(r"(\S+) in \((.*)\)", selector)       # set 기반(launcher 조회) -- 단일 항만 쓴다
    if m:
        return labels.get(m.group(1)) in m.group(2).split(",")
    for term in selector.split(","):
        key, _, value = term.partition("=")
        if labels.get(key) != value:
            return False
    return True


class FakeK8s:
    def __init__(self):
        self.pods = {}        # name -> {"labels", "phase", "deleting", "spec", "created", "waiting_reason"}
        self.vcjobs = {}      # name -> {"labels", "deleting"}
        self.deleted = []     # [(kind, name)] -- delete 호출 순서(없는 것 포함 -- 404 삼킴)
        self.created = []     # 생성한 매니페스트
        self.linger = set()   # 지우면 바로 사라지지 않고 Terminating 으로 남는 (kind, name)
        self.fail = {}        # 메서드 이름 -> 던질 예외
        self.calls = []

    # ---- 시드 ----
    def add_pod(self, name, labels, *, phase="Running", deleting=False, spec=None, created="2026-10-08T00:00:00Z",
                waiting_reason=None, owner=_AUTO):
        """owner = 소유 vcjob (name, uid). 기본(_AUTO)은 Volcano 흉내: `volcano.sh/job-name` 라벨이 있으면 그 vcjob 이
        지금 있으면 그 uid, 없으면 이미 사라진 주인(새 uid)을 가리킨다. None = ownerReferences 없음."""
        if owner is _AUTO:
            vc = labels.get("volcano.sh/job-name")
            owner = None if vc is None else (vc, self.vcjobs[vc]["uid"] if vc in self.vcjobs else _new_uid())
        self.pods[name] = {"labels": dict(labels), "phase": phase, "deleting": deleting, "spec": spec or {},
                           "created": created, "waiting_reason": waiting_reason, "owner": owner,
                           "uid": _new_uid()}

    def add_vcjob(self, name, labels, *, deleting=False, uid=None):
        self.vcjobs[name] = {"labels": dict(labels), "deleting": deleting, "uid": uid or _new_uid()}

    def release(self):
        """Terminating 이던 객체가 실제로 사라진다(다음 관찰 시점)."""
        self.linger.clear()
        for name in [n for n, p in self.pods.items() if p["deleting"]]:
            del self.pods[name]
        for name in [n for n, v in self.vcjobs.items() if v["deleting"]]:
            del self.vcjobs[name]

    def _maybe_fail(self, method):
        self.calls.append(method)
        exc = self.fail.get(method)
        if exc is not None:
            raise exc

    # ---- K8sClient ----
    def create(self, manifest):
        self._maybe_fail("create")
        name = manifest["metadata"]["name"]
        if manifest["kind"] != "Pod":
            raise AssertionError("purge only creates pods")
        if name in self.pods:
            raise AlreadyExists(name)
        self.created.append(manifest)
        self.add_pod(name, manifest["metadata"].get("labels") or {}, phase="Pending", spec=manifest["spec"],
                     created=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def get(self, kind, name, namespace):
        self._maybe_fail("get")
        if kind == "Pod":
            p = self.pods.get(name)
            if p is None:
                return None
            meta = {"name": name, "labels": p["labels"], "creationTimestamp": p["created"], "uid": p["uid"]}
            if p["owner"] is not None:
                meta["ownerReferences"] = [{"apiVersion": "batch.volcano.sh/v1alpha1", "kind": "Job",
                                            "name": p["owner"][0], "uid": p["owner"][1], "controller": True}]
            return {"metadata": meta, "spec": p["spec"], "status": {"phase": p["phase"]}}
        v = self.vcjobs.get(name)
        return None if v is None else {"metadata": {"name": name, "labels": v["labels"], "uid": v["uid"]}}

    def delete(self, kind, name, namespace):
        self._maybe_fail("delete")
        self.deleted.append((kind, name))
        store = self.pods if kind == "Pod" else self.vcjobs
        if name not in store:
            return                                  # 404 삼킴(멱등)
        if (kind, name) in self.linger:
            store[name]["deleting"] = True
            return
        del store[name]
        if kind == "Job":                           # Volcano 가 소유 파드를 지운다(종료 대기 없이 흉내)
            for pname in [n for n, p in self.pods.items()
                          if p["labels"].get("volcano.sh/job-name") == name and ("Pod", n) not in self.linger]:
                del self.pods[pname]

    def list_pod_briefs(self, namespace, label_selector):
        self._maybe_fail("list_pod_briefs")
        return [{"name": n, "node": "dms-w1", "images": {}, "phase": p["phase"],
                 "waiting_reason": p["waiting_reason"], "deleting": p["deleting"]}
                for n, p in sorted(self.pods.items()) if _match(p["labels"], label_selector)]

    def list_vcjob_briefs(self, namespace, label_selector):
        self._maybe_fail("list_vcjob_briefs")
        return [{"name": n, "deleting": v["deleting"], "uid": v["uid"]}
                for n, v in sorted(self.vcjobs.items()) if _match(v["labels"], label_selector)]

    # ---- purge 파드 흉내 ----
    def purge_pods(self):
        return {n: p for n, p in self.pods.items() if p["labels"].get("dms.io/purge") == "1"}

    def run_purge_pods(self, base, *, succeed=True, remove=True):
        """Pending/Running purge 파드를 「실행」한다: command 의 이름들을 <base>/.dms-trash 에서 지우고(remove) phase 를
        Succeeded(또는 Failed)로."""
        for name, p in self.purge_pods().items():
            if p["phase"] not in ("Pending", "Running"):
                continue
            names = p["spec"]["containers"][0]["command"][4:]
            if remove:
                for n in names:
                    target = Path(base) / ".dms-trash" / n
                    if target.is_symlink() or target.is_file():
                        target.unlink()
                    elif target.exists():
                        shutil.rmtree(target)
            p["phase"] = "Succeeded" if succeed else "Failed"
