import pytest

from review_loop.engine.pull_url import PullUrlError, parse_pull_url


def test_a_github_pull_url_yields_owner_repo_and_number():
    ref = parse_pull_url("https://github.com/acme/webapp/pull/1004")

    assert (ref.owner, ref.repo, ref.number) == ("acme", "webapp", 1004)


def test_anything_else_is_refused_with_the_expected_shape_in_the_message():
    with pytest.raises(PullUrlError) as raised:
        parse_pull_url("https://github.com/acme/webapp/issues/12")

    assert "https://github.com/<owner>/<repo>/pull/<number>" in str(raised.value)
