#!/usr/bin/env python3
"""deploy/verify.sh(S12) 테스트용 kubectl 심(2026-11-03 ⑦ 리뷰 1) — 진짜 kubectl·클러스터를 부르지 않는다. 읽기만 흉내 낸다.

PATH 앞의 `kubectl` 셸 래퍼가 이 파일을 부른다. 환경:
  FAKE_WORLD     세계 JSON(읽기만) — 키는 tests/test_install_verify.py 의 good_world() 참고
  FAKE_KUBE_LOG  호출마다 원래 인자를 한 줄로 덧붙인다
"""
import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["FAKE_KUBE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(argv) + "\n")
with open(os.environ["FAKE_WORLD"], encoding="utf-8") as fh:
    W = json.load(fh)


def out(text="", rc=0):
    sys.stdout.write(text)
    sys.exit(rc)


def err(text, rc=1):
    sys.stderr.write(text + "\n")
    sys.exit(rc)


rest, i = [], 0
while i < len(argv):
    if argv[i] in ("--context", "-n", "--namespace"):
        if argv[i] == "--context" and argv[i + 1] not in W.get("contexts", ["known"]):
            err('error: context "%s" does not exist' % argv[i + 1])
        i += 2
        continue
    rest.append(argv[i])
    i += 1
if rest[:2] == ["config", "get-contexts"]:
    sys.exit(0 if rest[2] in W.get("contexts", ["known"]) else 1)
line = " ".join(rest)
jp = next((a.split("=", 1)[1] for a in rest if a.startswith("jsonpath=")), "")
if rest and rest[0] != "get" and rest[0] != "logs":
    err("fake verify kubectl: 읽기 밖의 호출 %s" % line, 97)        # verify 는 클러스터를 바꾸지 않는다
if rest[0] == "logs":
    out(W.get("logs", {}).get(rest[1], ""))
if rest[1] == "--raw":
    path = rest[2]
    if path.endswith("/proxy/readyz"):
        body = W.get("readyz")
        if body is None:
            err('Error from server (ServiceUnavailable): the server is currently unable to handle the request (get services http:dms-api:http)')
        out(body)
    if "dms-registry" in path and path.endswith("/tags/list"):
        tags = W.get("tags")
        if tags is None:
            err("Error from server (ServiceUnavailable): no endpoints available for service \"http:dms-registry:http\"")
        out(tags)
    err("fake: 모르는 raw %s" % path)
kind, name = rest[1], (rest[2] if len(rest) > 2 and not rest[2].startswith("-") else "")
if "/" in kind:
    kind, name = kind.split("/", 1)
    kind = {"daemonset": "ds"}.get(kind, kind)


def obj(table, key):
    d = (W.get(table) or {}).get(key)
    if d is None:
        err('Error from server (NotFound): %s "%s" not found' % (kind, key))
    return d


if kind == "job":
    out(W.get("migrate", "1"))
if kind in ("deploy", "ds"):
    d = obj("workloads", name)
    if "readyReplicas" in jp or "numberReady" in jp:
        out(d.get("ready", ""))
    if "spec.replicas" in jp or "desiredNumberScheduled" in jp:
        out(d.get("want", ""))
    if "image" in jp:
        out(d.get("image", ""))
    out("")
if kind == "clusters.postgresql.cnpg.io":
    c = W.get("cluster")
    if not c:
        err('Error from server (NotFound): clusters.postgresql.cnpg.io "dms-pg" not found')
    for k, field in (("spec.instances", "instances"), ("readyInstances", "ready"), ("phase", "phase")):
        if k in jp:
            out(c.get(field, ""))
    out("")
if kind == "endpoints":
    out(W.get("eps", ""))
if kind == "pv":
    out(W.get("pvs", ""))
if kind == "cronjob":
    if W.get("cronjob") is None:
        err('Error from server (NotFound): cronjobs.batch "dms-pg-dump" not found')
    out(W["cronjob"] if jp else "")
if kind == "svc":
    s = W.get("svc_registry")
    if s is None:
        err('Error from server (NotFound): services "dms-registry" not found')
    out(s.get("clusterIP", "") if jp else "")
if kind == "gateway":
    g = W.get("gateway")
    if g is None:
        err('Error from server (NotFound): gateways.gateway.networking.k8s.io "dms-portal" not found')
    out(g)
out("")
