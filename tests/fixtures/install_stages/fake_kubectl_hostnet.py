#!/usr/bin/env python3
"""deploy/host-net-probe.sh 테스트용 kubectl 심(2026-11-03 ⑦ 리뷰 1) — 진짜 kubectl·클러스터를 부르지 않는다.

환경:
  FAKE_WORLD     세계 JSON(읽고 고쳐 쓴다 — create 한 프로브 파드를 기억한다):
                   {"nodes": "cp1,cp2", "values": {"<노드>": "<VALUE= 뒤 값 — 없으면 null(로그 빈 줄)>"},
                    "phase": {"<노드>": "Succeeded"}, "reason": {"<노드>": "ErrImagePull"}, "pods": [...]}
  FAKE_KUBE_LOG  호출마다 원래 인자를 한 줄로 덧붙인다
"""
import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["FAKE_KUBE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(argv) + "\n")
path = os.environ["FAKE_WORLD"]
with open(path, encoding="utf-8") as fh:
    W = json.load(fh)
rest, i = [], 0
while i < len(argv):
    if argv[i] in ("--context", "-n"):
        i += 2
        continue
    rest.append(argv[i])
    i += 1
if rest[:2] == ["config", "get-contexts"]:
    sys.exit(0 if rest[2] == "known" else 1)
pods = W.setdefault("pods", [])
if rest[0] == "get" and rest[1] == "nodes":
    sys.stdout.write("".join(n + "," for n in W.get("nodes", "").split(",") if n))
    sys.exit(0)
if rest[0] == "delete":
    sys.exit(0)
if rest[:3] == ["create", "-f", "-"]:
    spec = json.loads(sys.stdin.read())
    script = spec["spec"]["containers"][0]["command"][2]
    pods.append({"name": spec["metadata"]["name"], "node": spec["spec"]["nodeName"], "script": script})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(W, fh)
    sys.exit(0)
if rest[0] == "get" and rest[1] == "pod":
    jp = rest[-1]
    if "metadata.name" in jp:
        sys.stdout.write("".join("%s %s %s %s\n" % (p["name"], p["node"], W.get("phase", {}).get(p["node"], "Succeeded"),
                                                    W.get("reason", {}).get(p["node"], "")) for p in pods))
    else:
        sys.stdout.write("".join("%s/%s " % (W.get("phase", {}).get(p["node"], "Succeeded"), W.get("reason", {}).get(p["node"], ""))
                                 for p in pods))
    sys.exit(0)
if rest[0] == "logs":
    pod = next(p for p in pods if p["name"] == rest[1])
    v = W.get("values", {}).get(pod["node"])
    sys.stdout.write("" if v is None else "VALUE=%s\n" % v)
    sys.exit(0)
sys.stderr.write("fake hostnet kubectl: 모르는 호출 %s\n" % " ".join(argv))
sys.exit(97)
