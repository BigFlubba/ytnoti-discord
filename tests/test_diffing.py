from ytnoti_discord.diffing import (describe_changes, description_change, tags_change,
                                    title_change)


def test_title_before_and_after():
    out = title_change("Old", "New")
    assert "Title changed" in out and "~~Old~~" in out and "**New**" in out
    assert title_change("Same", "Same") == "" and title_change("", "New") == ""


def test_description_shows_only_changed_lines_as_diff_block():
    old = "Intro\nSponsor: OldCo\nLinks below\nhttps://a.example"
    new = "Intro\nSponsor: NewCo\nLinks below\nhttps://a.example\nChapters: 0:00 start"
    out = description_change(old, new)
    assert out.startswith("Description changed:\n```diff\n") and out.endswith("\n```")
    assert "-Sponsor: OldCo" in out and "+Sponsor: NewCo" in out
    assert "+Chapters: 0:00 start" in out
    assert "Intro" not in out and "Links below" not in out     # unchanged lines omitted


def test_description_unknown_or_cosmetic_is_not_a_change():
    assert description_change(None, "x") == "" and description_change("x", None) == ""
    assert description_change("a\nb  \n", "a\r\nb") == ""      # whitespace/newline style only


def test_description_cannot_break_out_of_the_code_fence():
    out = description_change("a", "b ``` @everyone")
    assert out.count("```") == 2                               # only our own fence


def test_long_description_diff_is_capped():
    old = "\n".join(f"old line {i}" for i in range(500))
    new = "\n".join(f"new line {i}" for i in range(500))
    out = description_change(old, new)
    assert len(out) < 1800 and "more changed lines" in out


def test_tags_added_and_removed():
    out = tags_change(["a", "b"], ["b", "c"])
    assert "Before (removed): ~~a~~" in out and "After (added): **c**" in out
    assert tags_change(["a"], ["a"]) == "" and tags_change(None, ["a"]) == ""
    assert "Before" not in tags_change([], ["x"])


def test_describe_combines_sections_and_stays_within_embed_limit():
    out = describe_changes(
        old_title="A" * 100, new_title="B" * 100,
        old_description="\n".join(str(i) for i in range(300)),
        new_description="\n".join(f"n{i}" for i in range(300)),
        old_tags=[f"t{i}" for i in range(100)], new_tags=[f"u{i}" for i in range(100)])
    assert out.count("changed:") == 3 and len(out) < 4096


def test_describe_nothing_known_is_empty():
    assert describe_changes(old_title="x", new_title="x") == ""
