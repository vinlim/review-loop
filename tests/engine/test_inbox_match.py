from review_loop.engine.inbox_match import match_inbox_item


def item(title, file="app/X.php", id=1):
    return {"id": id, "title": title, "file": file}


def test_the_same_title_and_file_is_a_clear_match():
    clear, ambiguous = match_inbox_item({"title": "Cache key ignores the locale", "file": "app/X.php"},
                                        [item("Cache key ignores the locale")])

    assert (clear, ambiguous) == (1, [])


def test_the_same_file_with_a_different_title_is_only_a_suggestion():
    clear, ambiguous = match_inbox_item({"title": "Something else here", "file": "app/X.php"},
                                        [item("Cache key ignores the locale")])

    assert (clear, ambiguous) == (None, [1])


def test_an_unrelated_item_matches_nothing():
    clear, ambiguous = match_inbox_item({"title": "Other", "file": "app/Y.php"}, [item("Cache key ignores the locale")])

    assert (clear, ambiguous) == (None, [])
