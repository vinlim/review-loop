"""Before a run is driven, each agent answers one structured probe with the model and effort a phase would use."""

import json

from review_loop.services.preflight import probe_agents
from review_loop.types.agents import AgentError, AgentFailure
from tests.fakes.agent import FakeAgent
from tests.services.test_coordinator_phases import with_review

STALE_CLI = AgentError(AgentFailure.PROCESS_FAILED, "claude exited 1: API Error: 400 Claude Code 2.1.278 does not support this model; "
                                                    "version 2.1.280 or newer is required. Run 'claude update'")


def agents(reviewer_ok=True, author_ok=True):
    reviewer, author = FakeAgent(), FakeAgent()
    reviewer.reply({"ok": True}) if reviewer_ok else reviewer.fail(STALE_CLI)
    author.reply({"ok": True}) if author_ok else author.fail(STALE_CLI)
    return {"codex": reviewer, "claude": author}


def probe(settings, tmp_path, adapters):
    return probe_agents(adapters, settings.repositories["webapp"], state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                        output_dir=tmp_path / "state" / "preflight")


def test_each_role_gets_one_read_only_structured_probe_with_its_model_and_effort_in_the_agent_environment(settings, tmp_path):
    adapters = agents()

    probes = probe(settings, tmp_path, adapters)

    assert [(p.role, p.agent, p.ok) for p in probes] == [("reviewer", "codex", True), ("author", "claude", True)]
    request = adapters["claude"].requests[0]
    assert request.phase == "preflight" and request.tools_policy == "read-only" and request.resume_session_id == ""
    assert (request.model, request.effort) == ("claude-opus-5-5", "xhigh")
    assert request.cwd == str(tmp_path / "state") and request.output_dir == str(tmp_path / "state" / "preflight" / "author")
    assert json.loads(open(request.schema_path).read())["required"] == ["ok"]
    assert request.env["PATH"].startswith(str(tmp_path / "state" / "shims"))


def test_a_failed_probe_names_the_role_the_agent_the_model_and_the_agents_own_error(settings, tmp_path):
    probes = probe(settings, tmp_path, agents(author_ok=False))

    author = probes[1]
    assert not author.ok
    assert author.detail == "author claude cannot serve claude-opus-5-5 at xhigh: " + STALE_CLI.detail


def test_two_roles_on_the_same_agent_model_and_effort_share_one_probe(settings, tmp_path):
    claude = FakeAgent()
    claude.reply({"ok": True})
    both = with_review(settings, reviewer="claude", reviewer_model="claude-opus-5-5", reviewer_effort="xhigh")

    probes = probe(both, tmp_path, {"claude": claude})

    assert len(claude.requests) == 1 and [p.role for p in probes] == ["reviewer", "author"] and all(p.ok for p in probes)
