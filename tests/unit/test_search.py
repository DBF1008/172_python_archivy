from unittest.mock import patch

from archivy.search import get_backlinks


def test_get_backlinks_returns_empty_when_search_disabled(test_app):
    test_app.config["SEARCH_CONF"]["enabled"] = 0
    with test_app.app_context():
        assert get_backlinks(1) == []


def test_get_backlinks_query_consistent_across_backends(test_app):
    """Both backends must resolve the same literal wikilink ``|id]]`` so that
    backlinks have stable semantics regardless of the configured engine.

    This guards against backend-specific drift (e.g. an extra ``)`` in the
    Elasticsearch query that would silently break ES backlinks).
    """
    test_app.config["SEARCH_CONF"]["enabled"] = 1
    captured = {}

    def fake_search(query, strict=False):
        captured["query"] = query
        captured["strict"] = strict
        return []

    with test_app.app_context():
        test_app.config["SEARCH_CONF"]["engine"] = "elasticsearch"
        with patch("archivy.search.search", side_effect=fake_search):
            get_backlinks(42)
        assert captured["query"] == "|42]]"  # no stray ")" — matches stored link
        assert captured["strict"] is True

        test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
        with patch("archivy.search.search", side_effect=fake_search):
            get_backlinks(42)
        assert captured["query"] == r"\|42]]"  # pipe escaped for the regex engine
        assert captured["strict"] is True

    test_app.config["SEARCH_CONF"]["enabled"] = 0
