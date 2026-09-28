from review_loop.engine.scope import scope_drift


def test_files_no_accepted_finding_or_declared_change_names_are_drift():
    changed = ["app/X.php", "tests/XTest.php", "app/Unrelated.php", "resources/js/other.ts"]

    drift = scope_drift(changed, finding_files=["app/X.php"], declared_files=["app/X.php", "tests/XTest.php"])

    assert drift == ["app/Unrelated.php", "resources/js/other.ts"]


def test_a_test_file_for_a_touched_class_is_not_drift():
    drift = scope_drift(["app/Domain/Guard.php", "tests/Unit/Domain/GuardTest.php"], finding_files=["app/Domain/Guard.php"], declared_files=[])

    assert drift == []
