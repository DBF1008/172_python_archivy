"""Regression tests for ripgrep search title parsing.

Covers:
- Hyphenated titles (multiple hyphens in filename)
- Underscore slug titles (underscores converted to spaces)
- Tag search returning correct titles
- Backlinks displaying correct titles for hyphenated-title notes
"""
import json

import pytest

from archivy.models import DataObj
from archivy.search import parse_ripgrep_line


# ---------------------------------------------------------------------------
# Unit test for parse_ripgrep_line
# ---------------------------------------------------------------------------


def _rg_begin_line(path_text):
    """Build a synthetic ripgrep JSON 'begin' event for the given file path."""
    return json.dumps(
        {
            "type": "begin",
            "data": {"path": {"text": path_text}},
        }
    )


def _rg_match_line(text):
    """Build a synthetic ripgrep JSON 'match' event."""
    return json.dumps(
        {
            "type": "match",
            "data": {"lines": {"text": text}},
        }
    )


def _rg_end_line():
    """Build a synthetic ripgrep JSON 'end' event (should be ignored)."""
    return json.dumps({"type": "end", "data": {}})


class TestParseRipgrepLine:
    """Unit-level tests for the title/id extraction logic."""

    def test_simple_title_no_hyphens(self):
        result, kind = parse_ripgrep_line(
            _rg_begin_line("/data/1-Simple_Title.md")
        )
        assert kind == "begin"
        assert result["id"] == 1
        assert result["title"] == "Simple Title"

    def test_title_with_multiple_hyphens(self):
        """Regression: filenames with multiple hyphens must not truncate the title."""
        result, kind = parse_ripgrep_line(
            _rg_begin_line("/data/42-My-Cool-Title.md")
        )
        assert kind == "begin"
        assert result["id"] == 42
        assert result["title"] == "My-Cool-Title"

    def test_title_with_hyphens_and_underscores(self):
        """Mixed hyphens and underscores: hyphens preserved, underscores become spaces."""
        result, kind = parse_ripgrep_line(
            _rg_begin_line("/data/7-Long-Note_With_Both.md")
        )
        assert kind == "begin"
        assert result["id"] == 7
        assert result["title"] == "Long-Note With Both"

    def test_title_with_many_hyphens(self):
        """Extreme case: title that is entirely hyphens."""
        result, kind = parse_ripgrep_line(
            _rg_begin_line("/data/100-a-b-c-d-e.md")
        )
        assert kind == "begin"
        assert result["id"] == 100
        assert result["title"] == "a-b-c-d-e"

    def test_title_underscore_slug(self):
        """Regression: underscore-only slug titles are converted to spaces."""
        result, kind = parse_ripgrep_line(
            _rg_begin_line("/data/5-my_underscore_slug.md")
        )
        assert kind == "begin"
        assert result["id"] == 5
        assert result["title"] == "my underscore slug"

    def test_match_line(self):
        result, kind = parse_ripgrep_line(_rg_match_line("some matched text\n"))
        assert kind == "match"
        assert result == "some matched text"

    def test_end_line_returns_none(self):
        assert parse_ripgrep_line(_rg_end_line()) is None

    def test_subdirectory_path(self):
        """File inside a subfolder must still parse correctly."""
        result, kind = parse_ripgrep_line(
            _rg_begin_line("/data/subfolder/99-Nested-Note.md")
        )
        assert kind == "begin"
        assert result["id"] == 99
        assert result["title"] == "Nested-Note"


# ---------------------------------------------------------------------------
# Integration / functional tests using real data objects + ripgrep
# ---------------------------------------------------------------------------


@pytest.fixture
def hyphenated_note(test_app):
    """A note whose title contains hyphens, producing a multi-hyphen filename."""
    with test_app.app_context():
        note = DataObj(
            type="note",
            title="Multi-Hyphen-Title",
            tags=["hyphen-test"],
            path="",
            content="Body mentioning hyphen-test keyword.",
        )
        note.insert()
    return note


@pytest.fixture
def underscore_note(test_app):
    """A note whose title uses underscores (slug-style)."""
    with test_app.app_context():
        note = DataObj(
            type="note",
            title="underscore_slug_note",
            tags=["slug-test"],
            path="",
            content="Body for underscore slug test.",
        )
        note.insert()
    return note


def _enable_ripgrep(app):
    app.config["SEARCH_CONF"]["enabled"] = 1
    app.config["SEARCH_CONF"]["engine"] = "ripgrep"


def _disable_ripgrep(app):
    app.config["SEARCH_CONF"]["enabled"] = 0


class TestRipgrepSearchResults:
    """Integration tests that exercise the full ripgrep search pipeline."""

    def test_search_returns_full_hyphenated_title(
        self, test_app, hyphenated_note
    ):
        """query_ripgrep must return the complete title for hyphenated notes."""
        from archivy.search import query_ripgrep

        _enable_ripgrep(test_app)
        try:
            with test_app.app_context():
                hits = query_ripgrep("hyphen-test")
            assert len(hits) >= 1
            hit = next(h for h in hits if h["id"] == hyphenated_note.id)
            assert hit["title"] == "Multi-Hyphen-Title"
        finally:
            _disable_ripgrep(test_app)

    def test_search_returns_underscore_slug_as_spaces(
        self, test_app, underscore_note
    ):
        """Underscore slugs must be rendered with spaces in search results."""
        from archivy.search import query_ripgrep

        _enable_ripgrep(test_app)
        try:
            with test_app.app_context():
                hits = query_ripgrep("underscore slug test")
            assert len(hits) >= 1
            hit = next(h for h in hits if h["id"] == underscore_note.id)
            assert hit["title"] == "underscore slug note"
        finally:
            _disable_ripgrep(test_app)

    def test_tag_search_hyphenated_title(
        self, test_app, hyphenated_note
    ):
        """search_frontmatter_tags must preserve full hyphenated titles."""
        from archivy.search import search_frontmatter_tags

        _enable_ripgrep(test_app)
        try:
            with test_app.app_context():
                hits = search_frontmatter_tags(tag="hyphen-test")
            assert len(hits) >= 1
            hit = next(h for h in hits if h["id"] == hyphenated_note.id)
            assert hit["title"] == "Multi-Hyphen-Title"
            assert "hyphen-test" in hit["tags"]
        finally:
            _disable_ripgrep(test_app)

    def test_backlinks_show_full_hyphenated_title(
        self, test_app, hyphenated_note
    ):
        """Backlinks search must return the full title of the linking note,
        even when that title contains hyphens."""
        from archivy.search import query_ripgrep

        _enable_ripgrep(test_app)
        try:
            # Create a note whose title has hyphens and that links to
            # the hyphenated_note via a wiki-link.
            with test_app.app_context():
                linking_note = DataObj(
                    type="note",
                    title="Source-With-Hyphens",
                    tags=[],
                    path="",
                    content=f"See [[Multi-Hyphen-Title|{hyphenated_note.id}]]",
                )
                linking_note.insert()

            # Use the same search pattern that routes.py uses for backlinks
            with test_app.app_context():
                query = f"\\|{hyphenated_note.id}]]"
                backlinks = query_ripgrep(query)

            assert len(backlinks) >= 1
            bl = next(b for b in backlinks if b["id"] == linking_note.id)
            # Must be the full "Source-With-Hyphens", not just "Hyphens"
            assert bl["title"] == "Source-With-Hyphens"
        finally:
            _disable_ripgrep(test_app)

    def test_backlinks_hyphenated_note_as_source(
        self, test_app, hyphenated_note
    ):
        """When a hyphenated-title note links to another, the backlink search
        result for the target must carry the full hyphenated title."""
        from archivy.search import query_ripgrep

        _enable_ripgrep(test_app)
        try:
            with test_app.app_context():
                target_note = DataObj(
                    type="note",
                    title="Target Note",
                    tags=[],
                    path="",
                    content="",
                )
                target_note.insert()

            # Append a wiki-link to the target inside the hyphenated note's file
            with test_app.app_context():
                from archivy.data import get_data_dir, get_by_id
                import frontmatter as fm

                md_path = get_by_id(hyphenated_note.id)
                post = fm.load(str(md_path))
                post.content += f"\n[[Target Note|{target_note.id}]]"
                with open(str(md_path), "w") as f:
                    f.write(fm.dumps(post))

            # Search for backlinks of the target note
            with test_app.app_context():
                query = f"\\|{target_note.id}]]"
                backlinks = query_ripgrep(query)

            assert len(backlinks) >= 1
            bl = next(b for b in backlinks if b["id"] == hyphenated_note.id)
            # The full hyphenated title must be preserved
            assert bl["title"] == "Multi-Hyphen-Title"
        finally:
            _disable_ripgrep(test_app)
