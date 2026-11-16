#!/usr/bin/env python3
"""deploy/ingress-probe.sh 테스트용 kubectl 심(2026-11-16 — 옛 fake_kubectl_hostnet.py 자리) — 진짜 kubectl·클러스터를 부르지 않는다. 읽기만.

환경:
  FAKE_WORLD     세계 JSON(읽기만): {"pods": "<get pods -A -o jsonpath 의 출력 — 노드\\thostIP\\tphase\\t이미지들 줄>",
                                     "nodes": "<get nodes -o jsonpath 의 출력 — 이름 InternalIP 줄>", "fail": ["pods"|"nodes"]}
  FAKE_KUBE_LOG  호출마다 원래 인자를 한 줄로 덧붙인다(테스트가 --context·읽기 전용을 본다)
"""
import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["FAKE_KUBE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(argv) + "\n")
with open(os.environ["FAKE_WORLD"], encoding="utf-8") as fh:
    W = json.load(fh)
rest, i = [], 0
while i < len(argv):
    if argv[i] == "--context":
        i += 2
        continue
    rest.append(argv[i])
    i += 1
if rest[:2] == ["config", "get-contexts"]:
    sys.exit(0 if rest[2] == "known" else 1)
if rest[:2] == ["get", "pods"] and "-A" in rest:
    if "pods" in W.get("fail", []):
        sys.stderr.write("Error from server (Forbidden): pods is forbidden\n")
        sys.exit(1)
    sys.stdout.write(W.get("pods", ""))
    sys.exit(0)
if rest[:2] == ["get", "nodes"]:
    if "nodes" in W.get("fail", []):
        sys.stderr.write("Error from server (Forbidden): nodes is forbidden\n")
        sys.exit(1)
    sys.stdout.write(W.get("nodes", ""))
    sys.exit(0)
sys.stderr.write("fake ingress kubectl: 읽기 밖의 호출 %s\n" % " ".join(argv))     # ingress-probe 는 클러스터를 바꾸지 않는다
sys.exit(97)
