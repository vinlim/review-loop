"""Findings about control state and replay: pause from outside, stale PR state before an effect, receipts after crashes."""

from review_loop.config.settings import PublicationConfig
from review_loop.repositories import outbox as outbox_repo
from review_loop.repositories import runs as runs_repo
from review_loop.services import run_control
from review_loop.services.run_coordinator import run_loop, step
from review_loop.types.run import PauseReason, RunState
from tests.fakes.clock import FakeClock
from tests.services.test_coordinator_m3 import ASSESS_984, FIX, REJECTIONS, harness, to_publishing, verified
from tests.services.test_coordinator_phases import APPROVE, REVIEW_984, Harness


def with_publication(settings, **fields):
    repo = settings.repositories["webapp"]
    settings.repositories["webapp"] = type(repo)(**{**repo.__dict__, "publication": PublicationConfig(**{**repo.publication.__dict__, **fields})})
    return settings


# --- F1: inspect-only is a property of the run, enforced before every mutation ------------------------

def test_an_inspect_only_run_never_reaches_the_fix_phase_even_after_a_resume(settings, tmp_path):
    h = Harness(settings, tmp_path, inspect_only=True)
    h.reviewer.reply(REVIEW_984)
    h.author.reply(ASSESS_984)
    run = run_loop(h.deps, h.run)
    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.INSPECT_ONLY
    resumed = run_control.resume(h.conn, run, FakeClock()).value
    h.deps.inspect_only = False  # a later invocation without the flag must not turn a dry run live

    run = run_loop(h.deps, resumed)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.INSPECT_ONLY
    assert not [r for r in h.author.requests if r.phase == "fix"] and h.github.writes == []


# --- F2: the publication switches are honoured ---------------------------------------------------------

def test_post_reviews_false_keeps_the_review_on_disk_only(settings, tmp_path):
    h = harness(with_publication(settings, post_reviews=False), tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)

    run = step(h.deps, run)

    assert run.state == RunState.ASSESSING and h.github.writes == []
    assert (tmp_path / "runs" / run.id / "pass-1" / "review.md").exists()


def test_post_author_responses_false_publishes_no_replies_summary_or_mirror(settings, tmp_path):
    h = harness(with_publication(settings, post_author_responses=False), tmp_path)
    run = to_publishing(h)
    writes = len(h.github.writes)

    run = step(h.deps, run)

    assert run.state == RunState.REREVIEWING and len(h.github.writes) == writes


def test_push_verified_fixes_false_commits_locally_and_carries_on_without_a_push(settings, tmp_path):
    h = harness(with_publication(settings, push_verified_fixes=False), tmp_path)
    run = to_publishing(h)

    assert run.state == RunState.PUBLISHING and len(h.git.commits) == 1 and h.git.pushes == []
    assert h.findings()["R1-F1"].state == "fixed_pending_verification"
    assert run.extra["unpushed_commits"] == [h.git.heads[run.worktree_path]]


# --- F20: a pause or stop from another process stops the running loop ---------------------------------

def test_a_pause_recorded_by_another_process_stops_the_loop_before_the_next_phase(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.reviewer.reply(REVIEW_984)
    other_view = None

    def pause_from_outside(request):
        nonlocal other_view
        other_view = runs_repo.get_run(h.conn, h.run.id)
        run_control.pause(h.conn, other_view, PauseReason.MANUAL, FakeClock())
        return ASSESS_984

    h.author.outputs = []
    h.author.reply(ASSESS_984)
    original_run = h.author.run

    def run_and_pause(req):
        result = original_run(req)
        pause_from_outside(req)
        return result

    h.author.run = run_and_pause

    run = run_loop(h.deps, h.run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.MANUAL
    assert runs_repo.get_run(h.conn, run.id).state == RunState.PAUSED
    assert not [r for r in h.author.requests if r.phase == "fix"]


def test_a_stop_recorded_by_another_process_is_never_overwritten(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    outside = runs_repo.get_run(h.conn, run.id)
    run_control.stop(h.conn, outside, FakeClock())
    h.reviewer.reply(REVIEW_984)

    run = step(h.deps, run)

    assert run.state == RunState.CANCELLED and runs_repo.get_run(h.conn, run.id).state == RunState.CANCELLED
    assert h.github.writes == []


# --- F21: every effect re-checks the PR first ----------------------------------------------------------

def test_publish_on_a_closed_pr_cancels_without_writing(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    writes = len(h.github.writes)
    h.github.add_pull(1004, state="closed", merged=True, head_sha=run.head_sha, base_sha="b" * 40)

    run = step(h.deps, run)

    assert run.state == RunState.CANCELLED and len(h.github.writes) == writes


def test_publish_after_the_head_moved_pauses_before_writing(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    writes = len(h.github.writes)
    h.github.add_pull(1004, head_sha="9" * 40, base_sha="b" * 40)
    h.git.remote_heads["claude/change"] = "9" * 40  # someone else pushed: git and the API both show it

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.HEAD_CHANGED and len(h.github.writes) == writes


def test_completion_on_a_closed_pr_writes_no_final_comment(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    h.reviewer.reply(verified("R1-F1", "R1-F2"))
    original = h.github.fetch_pull

    def close_after_review(ref):
        pull = original(ref)
        if h.reviewer.outputs == []:
            h.github.add_pull(1004, state="closed", merged=True, head_sha=run.head_sha, base_sha="b" * 40)
            return h.github.pulls[1004]
        return pull

    h.github.fetch_pull = close_after_review
    writes = len(h.github.writes)

    run = step(h.deps, run)

    assert run.state == RunState.CANCELLED
    assert not [w for w in h.github.writes[writes:] if w[0] == "post_comment"]


# --- F24, F25, F26, F27: replay safety -------------------------------------------------------------------

def test_an_uncertain_post_that_did_land_is_reconciled_and_not_sent_again(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    from review_loop.engine.discussion import Role, make_marker
    marker = make_marker(Role.AUTHOR, run.id, 1, "summary")
    entry = outbox_repo.record_intent(h.conn, run.id, "summary", {"pass": 1}, marker, "t")
    outbox_repo.mark_uncertain(h.conn, entry)
    landed = h.github.add_comment(1004, "## Review response, pass 1\n" + marker)

    run = step(h.deps, run)

    summaries = [w for w in h.github.writes if w[0] == "post_comment" and "Review response" in w[2]]
    assert summaries == []
    reconciled = next(e for e in outbox_repo.list_entries(h.conn, run.id) if e["marker"] == marker)
    assert reconciled["state"] == "done" and reconciled["receipt"]["id"] == landed


def test_a_review_pass_that_crashed_after_posting_is_not_posted_again_on_resume(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    original_save = runs_repo.save_run_unless_controlled  # the write every phase transition goes through
    calls = {"n": 0}

    def crash_after_publish(conn, run_object):
        if [w for w in h.github.writes if w[0] == "post_review"] and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("simulated crash after publication, before the transition was saved")
        return original_save(conn, run_object)

    import review_loop.services.phase_support as support
    support.runs_repo.save_run_unless_controlled = crash_after_publish
    try:
        try:
            step(h.deps, run)
        except RuntimeError:
            pass
    finally:
        support.runs_repo.save_run_unless_controlled = original_save

    persisted = runs_repo.get_run(h.conn, run.id)
    assert persisted.pass_no == 1 and len(h.findings()) == 2
    run = step(h.deps, persisted)

    assert run.state == RunState.ASSESSING
    assert len([w for w in h.github.writes if w[0] == "post_review"]) == 1
    assert len(h.reviewer.requests) == 1


def test_a_lost_inbox_comment_id_is_recovered_from_the_receipt(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    first_id = run.extra.pop("inbox_comment_id")
    runs_repo.save_run(h.conn, run)
    h.reviewer.reply({**REVIEW_984, "findings": REVIEW_984["findings"][:1], "resolved_prior": [
        {"id": "R1-F1", "resolution": "verified", "note": "ok"}, {"id": "R1-F2", "resolution": "verified", "note": "ok"}]})
    run = step(h.deps, run)
    h.author.reply({"dispositions": [{"finding_id": "R2-F1", "disposition": "reject", "reply": "No.", "evidence": "e", "intended_fix": ""}],
                    "adjacent_findings": [{"title": "Another thing", "description": "d", "file": "f", "symbol": "", "evidence": "e", "impact": "low", "next_step": "n"}],
                    "summary": "s"})
    run = step(h.deps, run)

    run = step(h.deps, run)

    mirrors = [w for w in h.github.writes if w[0] == "post_comment" and w[2].startswith("## Adjacent findings")]
    assert len(mirrors) == 1
    assert [w for w in h.github.writes if w[0] == "edit_comment"][-1][1] == first_id


def test_a_push_that_succeeded_before_a_crash_is_recognised_on_resume(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    pushed = run.head_sha
    parent = h.git.commit_infos[pushed][0]
    run.head_sha = parent
    run.state = RunState.VERIFYING
    runs_repo.save_run(h.conn, run)
    h.git.working_changed = []

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING and run.head_sha == pushed and len(h.git.pushes) == 1


def test_a_phase_pause_after_an_external_stop_keeps_the_stop(settings, tmp_path):
    from review_loop.types.agents import AgentError, AgentFailure

    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.on_run = lambda request: run_control.stop(h.conn, runs_repo.get_run(h.conn, run.id), FakeClock())
    h.reviewer.fail(AgentError(AgentFailure.TIMEOUT, "slow"))
    h.reviewer.fail(AgentError(AgentFailure.TIMEOUT, "slow again"))

    final = step(h.deps, run)

    assert final.state == RunState.CANCELLED and runs_repo.get_run(h.conn, run.id).state == RunState.CANCELLED


# --- the API can lag a push git already confirmed; git decides whether the branch moved -------------------------

def test_publishing_proceeds_when_the_api_still_shows_the_head_before_the_runs_own_push(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    pushed = run.extra["remote_head"]
    h.github.move_head(1004, "a" * 40)  # GitHub answering from before the push it has not caught up with
    assert h.git.remote_heads["claude/change"] == pushed

    run = step(h.deps, run)

    assert run.state == RunState.REREVIEWING and run.pause_reason is None


def test_publishing_pauses_when_git_confirms_the_branch_moved(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    h.github.move_head(1004, "f" * 40)
    h.git.remote_heads["claude/change"] = "f" * 40

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.HEAD_CHANGED


def test_a_verified_fix_is_pushed_when_the_api_lags_the_runs_previous_push(settings, tmp_path):
    from tests.services.test_coordinator_m3 import to_verifying

    h = harness(settings, tmp_path)
    run = to_verifying(h)
    run.extra["remote_head"] = "p" * 40  # a push this run made earlier, which git shows and the API does not yet
    h.git.remote_heads["claude/change"] = "p" * 40

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING
    assert h.git.pushes[-1][4] == "p" * 40


def test_publishing_pauses_when_git_shows_a_move_the_api_has_not_caught_up_with(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    writes = len(h.github.writes)
    h.git.remote_heads["claude/change"] = "x" * 40  # someone else pushed; the API still answers with the expected head

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.HEAD_CHANGED and len(h.github.writes) == writes


def test_a_run_completes_while_the_api_still_shows_the_head_before_its_own_push(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    h.github.move_head(1004, "a" * 40)  # stale for the rest of the run
    run = step(h.deps, run)
    assert run.state == RunState.REREVIEWING
    h.reviewer.reply(verified("R1-F1", "R1-F2"))

    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and run.pause_reason is None


# --- a pause or stop that lands during a review holds; the review is kept, not posted, and published on resume -------

def test_a_manual_pause_during_the_review_holds_and_the_review_is_published_on_resume_without_asking_again(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.on_run = lambda request: run_control.pause(h.conn, runs_repo.get_run(h.conn, run.id), PauseReason.MANUAL, FakeClock())
    h.reviewer.reply(REVIEW_984)

    paused = step(h.deps, run)

    assert (paused.state, paused.pause_reason, paused.resume_state) == (RunState.PAUSED, PauseReason.MANUAL, RunState.REVIEWING)
    assert paused.pass_no == 1 and paused.extra["pending_review_pass"] == 1 and len(h.findings()) == 2
    assert not [w for w in h.github.writes if w[0] == "post_review"], "nothing is posted under a pause"
    h.reviewer.on_run = None
    resumed = run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), FakeClock()).value

    run = step(h.deps, resumed)

    assert run.state == RunState.ASSESSING
    assert len([w for w in h.github.writes if w[0] == "post_review"]) == 1 and len(h.reviewer.requests) == 1


def test_a_stop_during_the_review_holds_and_keeps_the_review_as_a_record_without_posting_it(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.on_run = lambda request: run_control.stop(h.conn, runs_repo.get_run(h.conn, run.id), FakeClock())
    h.reviewer.reply(REVIEW_984)

    final = step(h.deps, run)

    assert final.state == RunState.CANCELLED and runs_repo.get_run(h.conn, run.id).state == RunState.CANCELLED
    assert runs_repo.get_run(h.conn, run.id).pass_no == 1 and len(h.findings()) == 2
    assert not [w for w in h.github.writes if w[0] == "post_review"]


# --- a control that lands after the checkpoint read and before the post is honoured: no post starts ------------------

def land_while_the_publisher_reconciles(monkeypatch, control):
    """Model a pause or stop that waited behind the checkpoint transaction: it lands after the coordinator's own
    control read, while the publisher is reconciling its outbox right before the post."""
    from review_loop.services.publication import Publisher

    original = Publisher.reconcile

    def reconcile_then_land(self, run_id, ref):
        outcomes = original(self, run_id, ref)
        control(runs_repo.get_run(self.conn, run_id))
        return outcomes

    monkeypatch.setattr(Publisher, "reconcile", reconcile_then_land)


def test_a_pause_that_lands_between_the_checkpoint_and_the_post_withholds_the_post_until_resume(settings, tmp_path, monkeypatch):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    land_while_the_publisher_reconciles(monkeypatch, lambda current: run_control.pause(h.conn, current, PauseReason.MANUAL, FakeClock()))

    paused = step(h.deps, run)

    assert (paused.state, paused.pause_reason) == (RunState.PAUSED, PauseReason.MANUAL)
    assert not [w for w in h.github.writes if w[0] == "post_review"], "no post starts once the pause has landed"
    assert runs_repo.get_run(h.conn, run.id).extra["pending_review_pass"] == 1
    monkeypatch.undo()
    resumed = run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), FakeClock()).value

    run = step(h.deps, resumed)

    assert run.state == RunState.ASSESSING
    assert len([w for w in h.github.writes if w[0] == "post_review"]) == 1 and len(h.reviewer.requests) == 1


def test_a_stop_that_lands_between_the_checkpoint_and_the_post_withholds_the_post_for_good(settings, tmp_path, monkeypatch):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    land_while_the_publisher_reconciles(monkeypatch, lambda current: run_control.stop(h.conn, current, FakeClock()))

    final = step(h.deps, run)

    assert final.state == RunState.CANCELLED and runs_repo.get_run(h.conn, run.id).state == RunState.CANCELLED
    assert not [w for w in h.github.writes if w[0] == "post_review"]


def test_a_stop_that_lands_during_verification_withholds_the_push(settings, tmp_path):
    from tests.services.test_coordinator_m3 import to_verifying

    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.process.on_run = lambda call: run_control.stop(h.conn, runs_repo.get_run(h.conn, run.id), FakeClock())

    final = step(h.deps, run)

    assert final.state == RunState.CANCELLED and h.git.pushes == []


def test_a_pause_that_lands_at_the_reservation_itself_withholds_the_post(settings, tmp_path, monkeypatch):
    """The reviewer's interleaving: control commits after every earlier read and right before intent is reserved."""
    from review_loop.repositories import outbox as outbox_repo

    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    original = outbox_repo.reserve_unless_controlled

    def land_then_reserve(conn, run_id, *args, **kwargs):
        run_control.pause(conn, runs_repo.get_run(conn, run_id), PauseReason.MANUAL, FakeClock())
        return original(conn, run_id, *args, **kwargs)

    monkeypatch.setattr(outbox_repo, "reserve_unless_controlled", land_then_reserve)

    paused = step(h.deps, run)

    assert (paused.state, paused.pause_reason) == (RunState.PAUSED, PauseReason.MANUAL)
    assert not [w for w in h.github.writes if w[0] == "post_review"]
    assert outbox_repo.list_entries(h.conn, run.id) == [], "no intent is recorded for an effect that never started"


def test_a_stop_that_lands_after_the_commit_and_before_the_push_withholds_the_push(settings, tmp_path, monkeypatch):
    from tests.services.test_coordinator_m3 import to_verifying

    h = harness(settings, tmp_path)
    run = to_verifying(h)
    original_commit = h.git.commit

    def commit_then_stop(path, message):
        sha = original_commit(path, message)
        run_control.stop(h.conn, runs_repo.get_run(h.conn, run.id), FakeClock())
        return sha

    monkeypatch.setattr(h.git, "commit", commit_then_stop)

    final = step(h.deps, run)

    assert final.state == RunState.CANCELLED and h.git.pushes == [] and len(h.git.commits) == 1


# --- the run's own push is recognised from git on resume, whatever kept it from being recorded ---------------------

def test_a_pause_that_lands_during_the_push_keeps_the_push_recorded_and_the_resume_carries_on_despite_a_stale_api(settings, tmp_path, monkeypatch):
    from tests.services.test_coordinator_m3 import to_verifying

    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.on_push = None  # the API keeps answering with the head from before the push
    original_push = h.git.push_guarded

    def push_then_pause(*args, **kwargs):
        result = original_push(*args, **kwargs)
        run_control.pause(h.conn, runs_repo.get_run(h.conn, run.id), PauseReason.MANUAL, FakeClock())
        return result

    monkeypatch.setattr(h.git, "push_guarded", push_then_pause)

    paused = step(h.deps, run)

    assert (paused.state, paused.pause_reason) == (RunState.PAUSED, PauseReason.MANUAL) and len(h.git.pushes) == 1
    pushed = h.git.pushes[0][2]
    assert paused.extra["remote_head"] == pushed and paused.head_sha == pushed, "the push that happened is on the record, pause or not"
    resumed = run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), FakeClock()).value

    run = step(h.deps, resumed)

    assert run.state == RunState.PUBLISHING and len(h.git.pushes) == 1 and run.extra["remote_head"] == pushed


def test_a_crash_after_the_push_and_before_the_record_is_recognised_from_git_on_resume_despite_a_stale_api(settings, tmp_path, monkeypatch):
    import pytest

    from tests.services.test_coordinator_m3 import to_verifying

    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.on_push = None
    original_push = h.git.push_guarded

    def push_then_die(*args, **kwargs):
        original_push(*args, **kwargs)
        raise RuntimeError("the coordinator lost power right after the remote accepted the push")

    monkeypatch.setattr(h.git, "push_guarded", push_then_die)
    with pytest.raises(RuntimeError):
        step(h.deps, run)
    monkeypatch.undo()
    stranded = runs_repo.get_run(h.conn, run.id)
    assert stranded.state == RunState.VERIFYING and stranded.extra.get("remote_head") != h.git.pushes[0][2]
    resumed = run_control.resume(h.conn, stranded, FakeClock(), unattended=True).value

    run = step(h.deps, resumed)

    assert run.state == RunState.PUBLISHING and len(h.git.pushes) == 1 and run.extra["remote_head"] == h.git.pushes[0][2]


def test_a_crash_while_recording_the_push_leaves_findings_and_run_untouched_together_and_the_resume_records_it(settings, tmp_path, monkeypatch):
    """The fixed events and the run's progress are one transaction: a crash between them cannot leave a pushed fix
    looking like no change, which the resume would then record as unfixed."""
    import pytest

    from tests.services.test_coordinator_m3 import to_verifying

    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.on_push = None  # the API stays at the pre-push head throughout
    original = runs_repo.save_run_keeping_control

    def die_after_the_checkpoint_write(conn, current):
        original(conn, current)  # the statement itself lands
        raise RuntimeError("the coordinator lost power right after writing the run's progress")

    monkeypatch.setattr(runs_repo, "save_run_keeping_control", die_after_the_checkpoint_write)
    with pytest.raises(RuntimeError):
        step(h.deps, run)
    monkeypatch.undo()
    assert all(f.state == "accepted" for f in h.findings().values()), "the events rolled back with the progress"
    stranded = runs_repo.get_run(h.conn, run.id)
    assert stranded.state == RunState.VERIFYING and stranded.head_sha != h.git.pushes[0][2]
    resumed = run_control.resume(h.conn, stranded, FakeClock(), unattended=True).value

    run = step(h.deps, resumed)

    assert run.state == RunState.PUBLISHING and len(h.git.pushes) == 1 and run.extra["remote_head"] == h.git.pushes[0][2]
    assert all(f.state == "fixed_pending_verification" for f in h.findings().values())
