from dms.controller import build_loops
from dms.repositories import Repositories


def test_batch_orchestrator_loop_registered(db, settings):
    names = [l.name for l in build_loops(settings, Repositories(db))]
    assert "batch-orchestrator" in names
