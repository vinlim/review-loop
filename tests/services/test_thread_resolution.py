from review_loop.services.run_coordinator import step
from tests.services.test_coordinator_m3 import harness, to_publishing, verified


def test_a_finding_closed_by_the_rereview_has_its_thread_resolved(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    h.reviewer.reply(verified("R1-F1", "R1-F2"))

    run = step(h.deps, run)

    resolved = [write[1] for write in h.github.writes if write[0] == "resolve_thread"]
    findings = h.findings()
    assert sorted(resolved) == sorted(findings[fid].thread_id for fid in ("R1-F1", "R1-F2"))
    assert all(thread.startswith("PRRT_") for thread in resolved)


def test_an_open_finding_keeps_its_thread_open(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    h.reviewer.reply(verified("R1-F1"))
    h.reviewer.outputs[-1].value.data["resolved_prior"].append({"id": "R1-F2", "resolution": "disputed", "note": "still leaks"})

    step(h.deps, run)

    resolved = [write[1] for write in h.github.writes if write[0] == "resolve_thread"]
    assert resolved == [h.findings()["R1-F1"].thread_id]
