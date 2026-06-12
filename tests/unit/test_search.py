import json
from shutil import which

import pytest

from archivy.models import DataObj
from archivy.search import (
    parse_ripgrep_line,
    query_ripgrep,
    search,
    search_frontmatter_tags,
)


def _begin_line(path):
    """Build a ripgrep --json 'begin' event for the given file path."""
    return json.dumps({"type": "begin", "data": {"path": {"text": str(path)}}})


def test_parse_ripgrep_line_keeps_full_hyphenated_title(test_app):
    # Filenames look like "{id}-{secure_filename(title)}.md" and secure_filename
    # keeps hyphens, so a multi-hyphen title used to be truncated to its last
    # segment ("Notes"). The title must now match the dataobj exactly.
    title = "Distributed-Systems-Reading-Notes"
    with test_app.app_context():
        note = DataObj(type="note", title=title, path="")
        note.insert()
        parsed, event = parse_ripgrep_line(_begin_line(note.fullpath))

    assert event == "begin"
    assert parsed["title"] == title
    assert parsed["id"] == note.id
    assert parsed["matches"] == []


def test_parse_ripgrep_line_preserves_underscores_in_title(test_app):
    # Spaces and underscores both collapse to "_" in the filename slug, so the
    # original title can only be recovered by reading the frontmatter, not by
    # reconstructing it from the filename.
    title = "my_private project notes"
    with test_app.app_context():
        note = DataObj(type="note", title=title, path="")
        note.insert()
        parsed, _ = parse_ripgrep_line(_begin_line(note.fullpath))

    assert parsed["title"] == title
    assert parsed["id"] == note.id


@pytest.mark.skipif(not which("rg"), reason="ripgrep needs to be installed")
def test_query_ripgrep_returns_full_title_and_preserves_matches(test_app):
    title = "Edge-Case-Hyphen-Title"
    with test_app.app_context():
        note = DataObj(
            type="note",
            title=title,
            content="a unique_token_xyz appears in the body",
            path="",
        )
        note.insert()
        results = query_ripgrep("unique_token_xyz")

    hit = next(r for r in results if r["id"] == note.id)
    assert hit["title"] == title
    # highlighting / match collection behavior is unchanged
    assert any("unique_token_xyz" in match for match in hit["matches"])


@pytest.mark.skipif(not which("rg"), reason="ripgrep needs to be installed")
def test_search_frontmatter_tags_returns_full_title(test_app):
    title = "Project-Plan-With-Hyphens"
    with test_app.app_context():
        note = DataObj(type="note", title=title, tags=["planningtag"], path="")
        note.insert()
        results = search_frontmatter_tags("planningtag")

    match = next(r for r in results if r["id"] == note.id)
    assert match["title"] == title
    assert "planningtag" in match["tags"]


@pytest.mark.skipif(not which("rg"), reason="ripgrep needs to be installed")
def test_tag_search_dedupes_and_keeps_full_title(test_app):
    # Replicates the /tags/<tag> route: an embedded-tag search is merged with a
    # frontmatter-tag search, deduplicated by id. A note carrying the tag both
    # ways must appear exactly once, with its full (non-truncated) title.
    title = "Meeting-Notes-2026-Q2"
    with test_app.app_context():
        test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
        note = DataObj(
            type="note",
            title=title,
            tags=["sprinttag"],
            content="recap #sprinttag# follow-ups",
            path="",
        )
        note.insert()

        results = search("#sprinttag#", strict=True)
        res_ids = {item["id"] for item in results}
        for res in search_frontmatter_tags("sprinttag"):
            if res["id"] not in res_ids:
                results.append(res)

    titles = [r["title"] for r in results if r["id"] == note.id]
    assert titles == [title]


@pytest.mark.skipif(not which("rg"), reason="ripgrep needs to be installed")
def test_backlink_search_returns_full_source_title(test_app):
    with test_app.app_context():
        test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
        target = DataObj(type="note", title="Target", path="")
        target.insert()
        source_title = "Linking-Note-With-Hyphens"
        source = DataObj(
            type="note",
            title=source_title,
            content=f"refer to [[Target|{target.id}]]",
            path="",
        )
        source.insert()

        # same query the backlinks route builds for the ripgrep engine
        backlinks = search(f"\\|{target.id}]]", strict=True)

    titles = [b["title"] for b in backlinks if b["id"] == source.id]
    assert titles == [source_title]
