from review_loop.engine.placement import diff_lines, place

DIFF = """diff --git a/app/X.php b/app/X.php
index 1..2 100644
--- a/app/X.php
+++ b/app/X.php
@@ -10,4 +10,5 @@ class X
 context ten
-removed eleven
+added eleven
+added twelve
 context thirteen
 context fourteen
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+one
+two
"""


def test_diff_lines_are_the_right_side_lines_of_every_hunk():
    lines = diff_lines(DIFF)

    assert lines["app/X.php"] == {10, 11, 12, 13, 14}
    assert lines["new.txt"] == {1, 2}


def test_a_finding_inside_a_hunk_is_placed_inline_and_outside_it_in_the_body():
    lines = diff_lines(DIFF)

    assert place("app/X.php", 12, lines) == "inline"
    assert place("app/X.php", 40, lines) == "body"
    assert place("app/Other.php", 12, lines) == "body"
    assert place("", 0, lines) == "body"
