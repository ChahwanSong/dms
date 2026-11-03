#!/usr/bin/env python3
"""install-detect 테스트용 kubectl 심(2026-11-03 ⑦) — 진짜 kubectl·클러스터를 부르지 않는다.

PATH 앞의 `kubectl` 셸 래퍼가 이 파일을 부른다. 환경:
  FAKE_KUBE_WORLD  세계 JSON — {"contexts": [...], "k8s": "v1.34.6",
                     "objects": {<자원 키>: [객체…]},               자원 키 = 아래 ALIASES 의 값
                     "errors": {<자원 키>: "Error from server (Forbidden): …"},
                     "dry_run": [{"match": "<인자·stdin 의 부분 문자열>", "rc": 1, "stderr": "…"}],
                     "kustomize": {"<층 경로 끝>": "<kubectl kustomize 출력>"}}
  FAKE_KUBE_LOG    호출마다 JSON 한 줄 {"ctx", "args", "stdin"} 을 덧붙인다(테스트가 --context·apply 호출을 본다).
알려진 동사만 흉내 낸다: config get-contexts · version · get · kustomize · apply/create(--dry-run=server 만 — 그 밖이면 98).
"""
import json
import os
import re
import sys

ALIASES = {
    "deploy": "deployments", "deployment": "deployments", "deployments": "deployments",
    "crd": "crds", "crds": "crds", "customresourcedefinition": "crds", "customresourcedefinitions": "crds",
    "ns": "namespaces", "namespace": "namespaces", "namespaces": "namespaces",
    "pc": "priorityclasses", "priorityclass": "priorityclasses", "priorityclasses": "priorityclasses",
    "sc": "storageclasses", "storageclass": "storageclasses", "storageclasses": "storageclasses",
    "pv": "persistentvolumes", "persistentvolume": "persistentvolumes", "persistentvolumes": "persistentvolumes",
    "svc": "services", "service": "services", "services": "services",
    "cm": "configmaps", "configmap": "configmaps", "configmaps": "configmaps",
    "secret": "secrets", "secrets": "secrets",
    "cronjob": "cronjobs", "cronjobs": "cronjobs",
    "clusters.postgresql.cnpg.io": "clusters",
    "gatewayclasses.gateway.networking.k8s.io": "gatewayclasses", "gatewayclass": "gatewayclasses",
    "gateways.gateway.networking.k8s.io": "gateways", "gateway": "gateways",
    "queues.scheduling.volcano.sh": "queues", "queue": "queues",
}
CLUSTER_SCOPED = {"crds", "namespaces", "priorityclasses", "storageclasses", "persistentvolumes", "gatewayclasses", "queues"}


def _name(obj):
    return (obj.get("metadata") or {}).get("name")


def _ns(obj):
    return (obj.get("metadata") or {}).get("namespace")


def do_get(world, rest):
    resource, names, ns, all_ns, ignore, output = rest[0], [], None, False, False, None
    i = 1
    while i < len(rest):
        a = rest[i]
        if a in ("-n", "--namespace"):
            ns = rest[i + 1]
            i += 2
            continue
        if a == "-o":
            output = rest[i + 1]
            i += 2
            continue
        if a in ("-A", "--all-namespaces"):
            all_ns = True
        elif a == "--ignore-not-found":
            ignore = True
        elif a.startswith("-o"):
            output = a[2:].lstrip("=")
        elif not a.startswith("-"):
            names.append(a)
        i += 1
    key = ALIASES.get(resource, resource)
    err = (world.get("errors") or {}).get(key)
    if err:
        sys.stderr.write(err + "\n")
        return 1
    objs = (world.get("objects") or {}).get(key) or []
    if key not in CLUSTER_SCOPED and not all_ns:
        objs = [o for o in objs if _ns(o) == (ns or "default")]
    if names:
        found = [o for n in names for o in objs if _name(o) == n]
        missing = [n for n in names if not any(_name(o) == n for o in objs)]
        if missing and not ignore:
            sys.stderr.write('Error from server (NotFound): %s "%s" not found\n' % (resource, missing[0]))
            return 1
    else:
        found = objs
    if output and output.startswith("jsonpath="):
        m = re.match(r"^\{\.data\.([A-Za-z0-9_]+)\}$", output[len("jsonpath="):])
        if not m:
            sys.stderr.write("심: 모르는 jsonpath %s\n" % output)
            return 97
        if found:
            sys.stdout.write(str((found[0].get("data") or {}).get(m.group(1), "")))
        return 0
    if output != "json":
        sys.stderr.write("심: -o json·jsonpath 만 흉내 낸다(%s)\n" % output)
        return 97
    if names and len(names) == 1:
        if found:
            print(json.dumps(found[0]))
        return 0
    if names and not found:
        return 0
    print(json.dumps({"apiVersion": "v1", "kind": "List", "items": found}))
    return 0


def main(argv):
    with open(os.environ["FAKE_KUBE_WORLD"], encoding="utf-8") as fh:
        world = json.load(fh)
    args, ctx, stdin = list(argv), None, None
    if args[:1] == ["--context"]:
        ctx, args = args[1], args[2:]
    if "-f" in args and args[args.index("-f") + 1] == "-":
        stdin = sys.stdin.read()
    with open(os.environ["FAKE_KUBE_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ctx": ctx, "args": args, "stdin": stdin}) + "\n")
    if args[:2] == ["config", "get-contexts"]:
        return 0 if args[2] in world.get("contexts", []) else 1
    if ctx is None:
        sys.stderr.write("심: --context 없는 클러스터 호출: %s\n" % " ".join(args))
        return 99
    if ctx not in world.get("contexts", []):
        sys.stderr.write("error: context %s not found\n" % ctx)
        return 1
    verb = args[0]
    if verb == "version":
        v = world.get("k8s", "v1.34.6")
        m = re.match(r"v(\d+)\.(\d+)", v)
        print(json.dumps({"serverVersion": {"gitVersion": v, "major": m.group(1), "minor": m.group(2)}}))
        return 0
    if verb == "get":
        return do_get(world, args[1:])
    if verb == "kustomize":
        for suffix, text in (world.get("kustomize") or {}).items():
            if args[1].rstrip("/").endswith(suffix):
                sys.stdout.write(text)
                return 0
        return 0
    if verb in ("apply", "create"):
        if "--dry-run=server" not in args:
            sys.stderr.write("심: dry-run 없는 %s 는 거절한다\n" % verb)
            return 98
        joined = " ".join(args) + "\n" + (stdin or "")
        for rule in world.get("dry_run") or []:
            if rule["match"] in joined:
                sys.stdout.write(rule.get("stdout", ""))
                sys.stderr.write(rule.get("stderr", ""))
                return rule.get("rc", 1)
        print("objects serverside-applied (server dry run)")
        return 0
    sys.stderr.write("심: 모르는 동사 %s\n" % verb)
    return 97


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
