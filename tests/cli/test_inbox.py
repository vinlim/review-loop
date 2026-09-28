from review_loop.cli.main import main
from review_loop.repositories import inbox as inbox_repo
from tests.cli.test_main import container


def seeded(settings):
    box = container(settings)
    inbox_repo.add_item(box.conn, "webapp", {"title": "Cache key ignores the locale", "description": "d", "file": "app/X.php", "symbol": "",
                                              "evidence": "e", "impact": "medium", "next_step": "bound before encoding", "source_pr": 984,
                                              "source_commit": "abc", "source_run": "r1", "agent": "claude"}, "2026-09-27T10:00:00+00:00")
    return box


def test_inbox_list_and_show_render_the_items(settings, capsys):
    box = seeded(settings)

    assert main(["inbox", "list"], container=box) == 0
    out = capsys.readouterr().out
    assert "#1" in out and "Cache key ignores the locale" in out and "new" in out

    assert main(["inbox", "show", "1"], container=box) == 0
    assert "bound before encoding" in capsys.readouterr().out


def test_inbox_dismiss_schedule_and_resolve_change_the_status(settings, capsys):
    box = seeded(settings)

    assert main(["inbox", "dismiss", "1", "--reason", "by design"], container=box) == 0
    assert inbox_repo.get_item(box.conn, 1)["status"] == "dismissed"
    assert main(["inbox", "schedule", "1", "--reference", "acme/webapp#1010"], container=box) == 0
    assert inbox_repo.get_item(box.conn, 1)["reference"] == "acme/webapp#1010"
    assert main(["inbox", "resolve", "1", "--reference", "abc123"], container=box) == 0
    assert inbox_repo.get_item(box.conn, 1)["status"] == "resolved"
