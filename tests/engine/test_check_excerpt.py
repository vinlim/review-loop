from review_loop.engine.check_excerpt import failure_excerpt

PEST = """$ .claude/run-tests.sh full
exit 1
  ........................................................................⨯...
  .......drop_product_knowledge_items_table: case-only SKU drift detected, skipping PKI row id=1
  ────────────────────────────────────────────────────────────────────────────
   FAILED  Tests\\Feature\\ChannelWabaEventIngestionTest > a redelivered ACCOU…
  Expected [App\\Notifications\\WabaChannelOffboarded] to be sent 1 times, but was sent 0 times.
Failed asserting that 0 is identical to 1.
  Tests:    1 failed, 5 skipped, 22107 passed (79111 assertions)
  Tests:    220 passed (917 assertions)
"""
CHANGED = """$ .claude/run-tests.sh changed
exit 3

== changed: database/migrations/x.php affect every test, so no mapping covers this change. Nothing was run.
== changed: that takes '.claude/run-tests.sh full', which a session runs only when the user asks for it.
"""


def test_a_failed_block_gives_its_command_exit_and_the_lines_that_name_the_failure():
    excerpt = failure_excerpt([("nothing_selected", CHANGED), ("failed", PEST)])

    assert excerpt[:2] == ["$ .claude/run-tests.sh full", "exit 1"]
    assert "FAILED  Tests\\Feature\\ChannelWabaEventIngestionTest > a redelivered ACCOU…" in excerpt
    assert "Failed asserting that 0 is identical to 1." in excerpt
    assert "Tests:    1 failed, 5 skipped, 22107 passed (79111 assertions)" in excerpt
    assert not any("changed" in line for line in excerpt), "a check that handed over to the fallback is not the failure"
    assert not any("⨯" in line or "SKU drift" in line for line in excerpt)


def test_when_nothing_failed_outright_the_block_that_ran_nothing_is_the_explanation():
    excerpt = failure_excerpt([("passed", "$ vendor/bin/pint --dirty\nexit 0\n"), ("nothing_selected", CHANGED)])

    assert excerpt[0] == "$ .claude/run-tests.sh changed" and excerpt[1] == "exit 3"
    assert excerpt[-1].endswith("which a session runs only when the user asks for it.")


def test_a_block_without_any_failure_line_contributes_its_last_lines_and_each_block_is_capped():
    noisy = "$ make check\nexit 2\n" + "".join(f"line {n} failed\n" for n in range(30))
    quiet = "$ ./slow-suite\nexit -1 (timed out)\nstarted\nstill going\n"

    excerpt = failure_excerpt([("failed", noisy), ("unavailable", quiet)])

    assert len([line for line in excerpt if line.startswith("line ")]) == 8
    assert excerpt[-4:] == ["$ ./slow-suite", "exit -1 (timed out)", "started", "still going"]
