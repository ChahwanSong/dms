"""Agent DaemonSet rollout helper (restart RM+DM agents + report status).

Exercises restart_agents / agent_rollout_status with a fake AppsV1Api so no real
cluster is needed. The k8s client resolution (_apps_api) is monkeypatched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dms.api._helpers import agent_rollout


class FakeApps:
    def __init__(self, statuses: dict, fail: set[str] | None = None):
        self._statuses = statuses
        self._fail = fail or set()
        self.patched: list[tuple[str, str]] = []

    def patch_namespaced_daemon_set(self, *, name, namespace, body):
        if name in self._fail:
            raise RuntimeError(f"forbidden: {name}")
        self.patched.append((name, body["spec"]["template"]["metadata"]["annotations"][
            "kubectl.kubernetes.io/restartedAt"]))

    def read_namespaced_daemon_set(self, *, name, namespace):
        if name in self._fail:
            raise RuntimeError(f"not found: {name}")
        s = self._statuses[name]
        return SimpleNamespace(
            status=SimpleNamespace(
                desired_number_scheduled=s["desired"],
                updated_number_scheduled=s["updated"],
                number_ready=s["ready"],
                number_available=s["available"],
                number_unavailable=s.get("unavailable", 0),
                observed_generation=s.get("observed", 1),
            ),
            metadata=SimpleNamespace(generation=s.get("generation", 1)),
            spec=SimpleNamespace(template=SimpleNamespace(
                metadata=SimpleNamespace(annotations={
                    "kubectl.kubernetes.io/restartedAt": s.get("restarted_at", "2026-07-02T00:00:00Z"),
                }))),
        )


def _patch(monkeypatch, apps):
    monkeypatch.setattr(agent_rollout, "_apps_api", lambda settings: (apps, "dms"))


def test_restart_agents_patches_both(monkeypatch):
    apps = FakeApps(statuses={})
    _patch(monkeypatch, apps)
    res = agent_rollout.restart_agents(SimpleNamespace(), restarted_at="2026-07-02T12:00:00Z")
    assert res["namespace"] == "dms"
    assert set(res["restarted"]) == {"dms-rm-agent", "dms-dm-agent"}
    assert res["errors"] == {}
    # both DaemonSets stamped with the same restartedAt
    assert {n for n, _ in apps.patched} == {"dms-rm-agent", "dms-dm-agent"}
    assert all(ts == "2026-07-02T12:00:00Z" for _, ts in apps.patched)


def test_restart_agents_reports_per_daemonset_error(monkeypatch):
    apps = FakeApps(statuses={}, fail={"dms-dm-agent"})
    _patch(monkeypatch, apps)
    res = agent_rollout.restart_agents(SimpleNamespace(), restarted_at="t")
    assert res["restarted"] == ["dms-rm-agent"]
    assert "dms-dm-agent" in res["errors"]


def test_rollout_status_flags_rolling_vs_converged(monkeypatch):
    apps = FakeApps(statuses={
        # converged: observed>=gen, updated==desired, available==desired, unavailable 0
        "dms-rm-agent": {"desired": 3, "updated": 3, "ready": 3, "available": 3,
                          "unavailable": 0, "observed": 2, "generation": 2},
        # rolling: not all updated/available yet
        "dms-dm-agent": {"desired": 3, "updated": 1, "ready": 1, "available": 1,
                          "unavailable": 2, "observed": 2, "generation": 2},
    })
    _patch(monkeypatch, apps)
    out = agent_rollout.agent_rollout_status(SimpleNamespace())
    by = {d["name"]: d for d in out["daemonsets"]}
    assert by["dms-rm-agent"]["rolling"] is False
    assert by["dms-rm-agent"]["ready"] == 3 and by["dms-rm-agent"]["desired"] == 3
    assert by["dms-dm-agent"]["rolling"] is True
    assert by["dms-dm-agent"]["updated"] == 1


def test_rollout_status_surfaces_read_error(monkeypatch):
    apps = FakeApps(statuses={
        "dms-rm-agent": {"desired": 1, "updated": 1, "ready": 1, "available": 1},
    }, fail={"dms-dm-agent"})
    _patch(monkeypatch, apps)
    out = agent_rollout.agent_rollout_status(SimpleNamespace())
    by = {d["name"]: d for d in out["daemonsets"]}
    assert "error" in by["dms-dm-agent"]
    assert by["dms-rm-agent"]["rolling"] is False


def test_kubernetes_unavailable_raises(monkeypatch):
    def boom(settings):
        raise agent_rollout.KubernetesUnavailable("no k8s")
    monkeypatch.setattr(agent_rollout, "_apps_api", boom)
    with pytest.raises(agent_rollout.KubernetesUnavailable):
        agent_rollout.restart_agents(SimpleNamespace())
