import json
import os
import time

from review_loop.adapters.claude_sessions import find_author_session


def session_file(projects, project, session_id, branch_lines, mtime):
    directory = projects / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    lines = [json.dumps({"type": "queue-operation", "sessionId": session_id})]
    lines += [json.dumps({"type": "user", "sessionId": session_id, "gitBranch": branch, "cwd": "/x"}) for branch in branch_lines]
    path.write_text("\n".join(lines) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def test_auto_picks_the_newest_session_that_worked_on_the_head_branch_across_project_dirs(tmp_path):
    now = time.time()
    session_file(tmp_path, "-Users-x-webapp", "old-session", ["claude/feature"], now - 3600)
    session_file(tmp_path, "-Users-x-webapp--claude-worktrees-a", "new-session", ["main", "claude/feature"], now - 60)
    session_file(tmp_path, "-Users-x-webapp--claude-worktrees-b", "other-branch", ["claude/other"], now)

    assert find_author_session(tmp_path, "claude/feature") == "new-session"


def test_auto_falls_back_to_empty_when_no_session_mentions_the_branch(tmp_path):
    session_file(tmp_path, "-Users-x-webapp", "s1", ["main"], time.time())

    assert find_author_session(tmp_path, "claude/feature") == ""


def test_a_branch_name_that_is_a_prefix_of_another_does_not_match(tmp_path):
    session_file(tmp_path, "-Users-x-webapp", "s1", ["claude/feature-2"], time.time())

    assert find_author_session(tmp_path, "claude/feature") == ""


def test_only_sessions_that_worked_inside_the_registered_checkout_are_candidates(tmp_path):
    projects = tmp_path / "projects"
    (projects / "-a").mkdir(parents=True)
    (projects / "-b").mkdir(parents=True)
    (projects / "-a" / "other-repo.jsonl").write_text(json.dumps({"type": "user", "gitBranch": "fix/tests", "cwd": "/Users/x/other"}) + "\n")
    (projects / "-b" / "ours.jsonl").write_text(json.dumps({"type": "user", "gitBranch": "fix/tests", "cwd": "/Users/x/webapp/.claude/worktrees/w1"}) + "\n")
    os.utime(projects / "-a" / "other-repo.jsonl", (time.time() + 100, time.time() + 100))

    assert find_author_session(projects, "fix/tests", local_path="/Users/x/webapp") == "ours"
