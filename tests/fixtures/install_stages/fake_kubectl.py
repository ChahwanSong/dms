#!/usr/bin/env python3
"""install.sh 단계 테스트용 kubectl 심(2026-11-03 ⑦) — 진짜 kubectl·클러스터를 부르지 않는다.

PATH 앞의 `kubectl` 셸 래퍼가 이 파일을 부른다. 환경:
  FAKE_WORLD     세계 JSON 파일(읽고 **고쳐 쓴다** — apply·create·annotate 가 상태를 바꿔 재실행 멱등을 흉내 낸다)
  FAKE_KUBE_LOG  호출마다 원래 인자를 한 줄로 덧붙인다(테스트가 --context·변경 동사를 본다)
stdin 을 읽는 호출(/dev/stdin·-f -)은 길이만 기록한다(값은 남기지 않는다 — 시크릿이 argv 에 없다는 것을 테스트가 본다).
"""
import json
import os
import sys
import time

argv = sys.argv[1:]
with open(os.environ["FAKE_KUBE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(argv) + "\n")

world_path = os.environ["FAKE_WORLD"]
with open(world_path, encoding="utf-8") as fh:
    W = json.load(fh)


def save():
    with open(world_path, "w", encoding="utf-8") as fh:
        json.dump(W, fh)


def out(text="", rc=0):
    sys.stdout.write(text)
    sys.exit(rc)


def err(text, rc=1):
    sys.stderr.write(text + "\n")
    sys.exit(rc)


# 강제 실패: {"부분 문자열": [rc, "stderr"]}
line = " ".join(argv)
for needle, (rc, msg) in (W.get("fail") or {}).items():
    if needle in line:
        err(msg, rc)

ctx, ns, rest = None, None, []
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--context":
        ctx = argv[i + 1]
        i += 2
        continue
    if a in ("-n", "--namespace"):
        ns = argv[i + 1]
        i += 2
        continue
    rest.append(a)
    i += 1
STDIN = ""
if any("/dev/stdin" in a for a in rest) or (len(rest) >= 2 and rest[-2:] == ["-f", "-"]) or "-f" in rest and "-" in rest:
    STDIN = sys.stdin.read()
    W.setdefault("stdin_lengths", []).append(len(STDIN))

if rest[:2] == ["config", "get-contexts"]:
    sys.exit(0 if rest[2] in W.get("contexts", ["known"]) else 1)
if ctx is not None and ctx not in W.get("contexts", ["known"]):
    err('error: context "%s" does not exist' % ctx)
verb = rest[0] if rest else ""
jp = next((a.split("=", 1)[1] for a in rest if a.startswith("jsonpath=")), None)
if jp is None and "-o" in rest:
    nxt = rest[rest.index("-o") + 1]
    jp = nxt.split("=", 1)[1] if nxt.startswith("jsonpath=") else nxt
ns = ns or "default"


def key(kind, name):
    return "%s/%s/%s" % (ns, kind, name)


if verb == "version":
    out('{"serverVersion": {"gitVersion": "v1.34.6", "minor": "34"}}' if "-o" in rest else "")
if verb == "kustomize":
    path = rest[1]
    if path.endswith("/app"):
        imgs = W.get("app_images", [])
        out("".join("        image: %s\n" % i for i in imgs if "mpifileutils" not in i)
            + "".join("  DMS_JOB_IMAGE: %s\n" % i for i in imgs if "mpifileutils" in i))
    out("")
if verb == "port-forward":
    if W.get("pf_fail"):
        err("error: unable to forward port because pod is not running")
    with open(world_path + ".pf-pids", "a", encoding="utf-8") as fh:      # 설치기가 끝난 뒤 이 프로세스가 남지 않았는지 테스트가 본다
        fh.write("%d\n" % os.getpid())
    sys.stdout.write("Forwarding from 127.0.0.1:41234 -> 5000\nForwarding from [::1]:41234 -> 5000\n")
    sys.stdout.flush()
    time.sleep(30)
    sys.exit(0)
if verb in ("wait",):
    sys.exit(0)
if verb == "rollout":
    if rest[1] == "restart":
        W.setdefault("restarts", []).extend(rest[2:])
        save()
    sys.exit(0)
if verb == "annotate":
    target = rest[1]
    k, v = rest[2].split("=", 1)
    W.setdefault("annotations", {})[target] = v
    save()
    sys.exit(0)
if verb == "logs":
    out((W.get("logs") or {}).get(rest[1], ""))
if verb == "delete":
    if rest[1] == "job":
        W.get("jobs", {}).pop(rest[2], None)
        save()
    sys.exit(0)
if verb == "create":
    if "--dry-run=server" in rest:
        sys.exit(0)
    if rest[1] == "namespace":
        W.setdefault("ns", []).append(rest[2])
        save()
        sys.exit(0)
    if rest[1] == "secret":
        name = rest[3] if rest[2] in ("generic", "tls") else rest[2]
        W.setdefault("secrets", {})["%s/%s" % (ns, name)] = {"rv": "1"}
        save()
        sys.exit(0)
    if rest[1] == "job":
        name = rest[-1]
        W.setdefault("jobs", {})[name] = W.get("new_job_state", "Complete")
        W.setdefault("created_jobs", []).append(name)
        save()
        sys.exit(0)
    sys.exit(0)
if verb == "apply":
    if "--dry-run=server" in rest:
        sys.exit(0)
    if "-f" in rest and rest[rest.index("-f") + 1].endswith("00-namespace.yaml"):
        if "dms" not in W.setdefault("ns", []):
            W["ns"].append("dms")
        save()
        sys.exit(0)
    if "-f" in rest and rest[rest.index("-f") + 1] == "-" and "kind: Namespace" in STDIN:
        # install.sh own_ns — DMS 라벨을 붙인 Namespace SSA(2026-11-03 ⑦ 리뷰 1). 세계에 ns 와 그 라벨을 남긴다.
        import re as _re
        name = _re.search(r"(?m)^  name: (\S+)", STDIN).group(1)
        if name not in W.setdefault("ns", []):
            W["ns"].append(name)
        W.setdefault("ns_labels", {})[name] = dict(_re.findall(r"(?m)^    (\S+): (\S+)", STDIN))
        W.setdefault("ns_applied", []).append(name)
        save()
        sys.exit(0)
    path = rest[rest.index("-k") + 1] if "-k" in rest else ""
    W.setdefault("applied", []).append(path)
    if path.endswith("platform/db-cluster") and not W.get("cluster"):
        W["cluster"] = dict(W.get("cluster_after_apply") or {"instances": "3", "ready": "3", "primary": "dms-pg-1"})
    if path.endswith("addons/envoy-gateway"):
        W.setdefault("secrets", {}).update({"envoy-gateway-system/envoy-gateway": {}, "envoy-gateway-system/envoy": {}})
    if path.endswith("/app"):
        W.setdefault("jobs", {})["dms-migrate"] = W.get("migrate_state", "Complete")
        gen = W.setdefault("gen", {})
        for w in ("deploy/dms-api", "deploy/dms-controller", "ds/dms-agent"):
            gen[w] = str(int(gen.get(w, "0")) + (1 if W.get("app_bumps") or w not in gen else 0))
        if W.get("app_changes_config"):
            W["cm_rv"] = str(int(W.get("cm_rv", "1")) + 1)
    save()
    sys.exit(0)
if verb != "get":
    err("fake kubectl: 모르는 호출 %s" % line, 97)

# --- get --------------------------------------------------------------------------------------------------------------------
kind = rest[1]
names = [a for a in rest[2:] if not a.startswith("-") and not a.startswith("jsonpath") and a not in ("json", "name")]
if "-o" in rest:
    o = rest.index("-o")
    names = [a for a in rest[2:o] if not a.startswith("-")]
if kind.startswith(("deploy/", "ds/")):
    target = kind
    if jp and "generation" in jp:
        out(W.get("gen", {}).get(target, ""), 0 if target in W.get("gen", {}) else 1)
    if jp and "install-config-rev" in jp:
        out(W.get("annotations", {}).get(target, ""))
    out("")
if kind == "ns":
    if all(n in W.get("ns", []) for n in names):
        out("")
    err('Error from server (NotFound): namespaces "%s" not found' % names[0])
if kind == "secret":
    found = all("%s/%s" % (ns, n) in W.get("secrets", {}) for n in names)
    if not found:
        err('Error from server (NotFound): secrets "%s" not found' % names[0])
    s = W["secrets"]["%s/%s" % (ns, names[0])]
    if "go-template" in line:
        out(W.get("secret_data", "DMS_DATABASE_URL=x\nDMS_SHARED_TOKEN=x\nDMS_ADMIN_TOKEN=x\nDMS_SESSION_SECRET=x\n"))
    if jp == "{.type}":
        out(s.get("type", "Opaque"))
    if jp and "resourceVersion" in jp:
        out(s.get("rv", "1"))
    out("")
if kind == "configmap":
    if jp and "resourceVersion" in jp:
        out(W.get("cm_rv", "1"))
    out("")
if kind == "nodes":
    out(W.get("masters", "cp1") + ",")
if kind == "deploy" and names == ["dms-api"]:
    out(W.get("api_image", ""))
if kind == "clusters.postgresql.cnpg.io":
    c = W.get("cluster")
    if not c:
        err('Error from server (NotFound): clusters.postgresql.cnpg.io "dms-pg" not found')
    if not jp:
        out("")
    if "spec.instances" in jp:
        out(c.get("instances", "3"))
    if "readyInstances" in jp:
        out(c.get("ready", "3"))
    if "currentPrimary" in jp:
        out(c.get("primary", "dms-pg-1"))
    if "phase" in jp:
        out(c.get("phase", "Cluster in healthy state"))
    out("")
if kind == "endpoints":
    out("10.0.0.9" if W.get("cluster") else "")
if kind == "pod":
    if "-l" in rest:                                   # 레지스트리 파드의 노드(S4 실패 사유)
        out(W.get("pod_nodes", ""))
    out("True")
if kind == "pods":                                      # S8 hostPort 충돌 감지(2026-11-14) — 「ns/이름\t노드\thostNetwork\thostPort들」 줄
    out(W.get("hostport_pods", ""))
if kind == "cronjob":
    out(W.get("cronjob_last", ""))
if kind == "job":
    st = W.get("jobs", {}).get(names[0])
    if st is None:
        err('Error from server (NotFound): jobs.batch "%s" not found' % names[0])
    out(st + " ")
if kind == "events":
    out(W.get("events", ""))
out("")
