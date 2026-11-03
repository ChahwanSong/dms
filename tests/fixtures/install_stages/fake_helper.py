#!/usr/bin/env python3
"""install.sh 단계 테스트용 도우미 가짜(2026-11-03 ⑦) — deploy/ 의 python·sh 도우미 자리에 놓인다.

  python3 fake_helper.py <도우미 이름> [인자…]
환경:
  FAKE_SCENARIO    JSON 파일 {"<이름>" 또는 "<이름> <모드>": {"rc": n, "out": "…"}} — 없으면 rc 0·빈 출력.
                   install-detect.py 의 detect·plan 은 "detect": {"<항목>": "<코드> <action> <apply> <판정>"} 로 DETECT 줄을 만든다.
  FAKE_HELPER_LOG  호출마다 JSON 한 줄 {"name", "argv", "applied"(그 순간 세계의 적용된 층), "dms_ns"(물려받은 env)}.
모드 = 인자 가운데 detect·plan·vcjob·ensure-app·init-secrets·check·compare-registry 같은 하위 명령 또는 --mode 값·--sysctl/--listen·--print/--run.
"""
import json
import os
import sys

name, args = sys.argv[1], sys.argv[2:]
# 그 순간 kubectl 심의 세계가 적용한 층 — 도우미와 SSA 의 상대 순서(예: S10 「사전 검사 → Cluster」)를 테스트가 본다(2026-11-03 ⑦ 리뷰 1).
applied = []
if os.environ.get("FAKE_WORLD") and os.path.exists(os.environ["FAKE_WORLD"]):
    with open(os.environ["FAKE_WORLD"], encoding="utf-8") as fh:
        applied = list(json.load(fh).get("applied") or [])
with open(os.environ["FAKE_HELPER_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"name": name, "argv": args, "applied": applied, "dms_ns": os.environ.get("DMS_NS")}) + "\n")
scenario = {}
if os.environ.get("FAKE_SCENARIO") and os.path.exists(os.environ["FAKE_SCENARIO"]):
    with open(os.environ["FAKE_SCENARIO"], encoding="utf-8") as fh:
        scenario = json.load(fh)

SUBCOMMANDS = ("detect", "plan", "vcjob", "ensure-app", "init-secrets", "check", "set-url", "compare-registry",
               "--sysctl", "--listen", "--print", "--run")
mode = next((a for a in args if a in SUBCOMMANDS), None)
if "--mode" in args:
    mode = args[args.index("--mode") + 1]
spec = scenario.get("%s %s" % (name, mode)) or scenario.get(name) or {}
rc = spec.get("rc", 0)
if isinstance(rc, list):                     # 호출마다 다음 값(마지막 값을 유지)
    counts = scenario.setdefault("_counts", {})
    k = "%s %s" % (name, mode)
    n = counts.get(k, 0)
    counts[k] = n + 1
    with open(os.environ["FAKE_SCENARIO"], "w", encoding="utf-8") as fh:
        json.dump(scenario, fh)
    rc = rc[min(n, len(rc) - 1)]
text = spec.get("out", "")
if spec.get("add_secrets"):                  # 예: pg-secrets.py init-secrets 가 dms-secrets 를 만든 것처럼(kubectl 심의 세계에)
    with open(os.environ["FAKE_WORLD"], encoding="utf-8") as fh:
        world = json.load(fh)
    for ref in spec["add_secrets"]:
        world.setdefault("secrets", {})[ref] = {"rv": "1"}
    with open(os.environ["FAKE_WORLD"], "w", encoding="utf-8") as fh:
        json.dump(world, fh)

if name == "install-detect.py" and mode in ("detect", "plan", "vcjob"):
    items = ("priorityclass", "registry", "gateway-api", "envoy-gateway", "gateway", "cnpg", "volcano", "db")
    default = {"priorityclass": "D= reapply 1 PASS", "registry": "D= reapply 1 PASS", "gateway-api": "D= none 0 PASS",
               "envoy-gateway": "D= none 0 PASS", "gateway": "D= reapply 1 PASS", "cnpg": "D= none 0 PASS",
               "volcano": "C reuse 0 PASS", "db": "D= reapply 1 PASS"}
    default.update(scenario.get("detect", {}))
    lines = ["== install-detect %s (fake) ==" % mode]
    if mode == "plan":
        lines.append("== 층별 server dry-run (fake) ==")
    lines += ["DETECT: %s %s" % (i, default[i]) for i in items]
    if mode == "plan":
        lines.append("PLAN: platform/priorityclass PASS priorityclass")
    if mode == "vcjob":
        lines.append("PLAN: vcjob PASS volcano")
    lines.append("RESULT: %s" % {0: "PASS", 3: "WARN", 1: "FAIL", 4: "UNKNOWN"}.get(rc, "UNKNOWN"))
    text = "\n".join(lines) + "\n" + text
elif name == "push-images.sh" and mode in (None, "--print"):
    reg = args[args.index("--registry") + 1] if "--registry" in args else "?"
    text = "# fake push-images --print\nskopeo copy --preserve-digests docker://src@sha256:x docker://%s/dest:tag\n%s" % (reg, text)
if text:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
sys.exit(rc)
