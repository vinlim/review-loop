from review_loop.engine.redaction import host_paths, redact_paths, shown_command

PATHS = host_paths("/Users/vin/.review-loop", "/Users/vin")


def test_the_state_directory_and_the_home_directory_are_shown_as_placeholders():
    text = "rg -n leak /Users/vin/.review-loop/runs/r/pass-2/verify-1.log: one hit; see /Users/vin/notes.md"

    assert redact_paths(text, PATHS) == "rg -n leak <review-loop>/runs/r/pass-2/verify-1.log: one hit; see ~/notes.md"


def test_a_match_ends_at_a_path_boundary():
    assert redact_paths("/Users/vinlim/x and /Users/vin.bak and /Users/vin", PATHS) == "/Users/vinlim/x and /Users/vin.bak and ~"


def test_a_state_directory_outside_the_home_directory_is_still_rewritten():
    paths = host_paths("/srv/loop", "/Users/vin")

    assert redact_paths("/srv/loop/runs/r under /Users/vin/code", paths) == "<review-loop>/runs/r under ~/code"


def test_a_check_command_is_shown_relative_to_the_checkout_and_the_home_directory():
    argv = ["/Users/vin/Projects/webapp/.venv/bin/python", "-m", "pytest", "-q", "/Users/vin/bin/extra", "/usr/bin/true"]

    assert shown_command(argv, "/Users/vin/Projects/webapp", "/Users/vin") == ".venv/bin/python -m pytest -q ~/bin/extra /usr/bin/true"


def test_a_relative_command_is_shown_as_it_is():
    assert shown_command([".claude/run-tests.sh", "changed"], "/Users/vin/Projects/webapp") == ".claude/run-tests.sh changed"


def test_a_root_followed_by_a_full_stop_is_rewritten_and_a_sibling_name_is_kept():
    paths = host_paths("/srv/loop", "/Users/vin")

    assert redact_paths("The home is /Users/vin.", paths) == "The home is ~."
    assert redact_paths("The home is /Users/vin.\nNext line.", paths) == "The home is ~.\nNext line."
    assert redact_paths("(under /Users/vin.)", paths) == "(under ~.)"
    assert redact_paths("State lives in /srv/loop.", paths) == "State lives in <review-loop>."
    assert redact_paths("Copied to /Users/vin.bak and /srv/loop.old", paths) == "Copied to /Users/vin.bak and /srv/loop.old"
