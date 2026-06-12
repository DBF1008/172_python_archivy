"""
Regression tests for path metadata and search index consistency
after move, rename, and delete operations on dataobjs and directories.
"""

from flask import Flask
from flask.testing import FlaskClient

from archivy.data import (
    create_dir,
    move_item,
    rename_folder,
    delete_dir,
    get_items,
    get_item,
)
from archivy.models import DataObj
from archivy.search import search


def _create_note(test_app, title, path="", content="Test content for search"):
    """Helper to create a note dataobj and return it."""
    with test_app.app_context():
        note = DataObj(type="note", title=title, content=content, path=path)
        note.insert()
    return note


class TestMoveItemConsistency:
    """Tests for move_item: front matter path and search index stay consistent."""

    def test_move_item_updates_frontmatter_path(self, test_app, client: FlaskClient):
        """After moving, the 'path' field in front matter must reflect the new directory."""
        create_dir("old_dir")
        create_dir("new_dir")
        note = _create_note(test_app, "Movable Note", path="old_dir")

        with test_app.app_context():
            move_item(note.id, "new_dir")

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 1

        moved = resp.json[0]
        assert moved["metadata"]["path"] == "new_dir", (
            f"Expected path 'new_dir', got '{moved['metadata']['path']}'"
        )

    def test_move_item_searchable_at_new_location(self, test_app, client: FlaskClient):
        """After moving, the dataobj must be findable via search."""
        test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
        test_app.config["SEARCH_CONF"]["enabled"] = 1

        create_dir("old_dir")
        create_dir("new_dir")
        note = _create_note(
            test_app, "Searchable Note", path="old_dir", content="unique_search_token"
        )

        with test_app.app_context():
            move_item(note.id, "new_dir")

        resp = client.get("/api/search?query=unique_search_token")
        assert resp.status_code == 200
        assert len(resp.json) >= 1
        assert any(
            r["id"] == note.id for r in resp.json
        ), f"Note {note.id} not found in search results after move"

        test_app.config["SEARCH_CONF"]["enabled"] = 0

    def test_move_item_get_item_reflects_new_dir(self, test_app, client: FlaskClient):
        """get_item should report the new directory after a move."""
        create_dir("src_dir")
        create_dir("dst_dir")
        note = _create_note(test_app, "Dir Check Note", path="src_dir")

        with test_app.app_context():
            move_item(note.id, "dst_dir")
            item = get_item(note.id)

        assert item is not None
        assert item["dir"] == "dst_dir"

    def test_move_item_to_root(self, test_app, client: FlaskClient):
        """Moving a dataobj to root ('') should set path to empty string."""
        create_dir("sub_dir")
        note = _create_note(test_app, "Rootward Note", path="sub_dir")

        with test_app.app_context():
            move_item(note.id, "")

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        moved = resp.json[0]
        assert moved["metadata"]["path"] == ""


class TestRenameFolderConsistency:
    """Tests for rename_folder: all contained paths and indexes stay consistent."""

    def test_rename_folder_updates_contained_paths(self, test_app, client: FlaskClient):
        """After renaming a folder, all contained dataobjs must have updated path."""
        create_dir("old_folder")
        note1 = _create_note(test_app, "Note One", path="old_folder")
        note2 = _create_note(test_app, "Note Two", path="old_folder")

        with test_app.app_context():
            result = rename_folder("old_folder", "new_folder")

        assert result == "new_folder"

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 2

        for item in resp.json:
            assert item["metadata"]["path"] == "new_folder", (
                f"Note '{item['metadata']['title']}' still has path '{item['metadata']['path']}'"
            )

    def test_rename_folder_preserves_nested_structure(self, test_app, client: FlaskClient):
        """Renaming a parent folder must correctly update nested dataobj paths."""
        create_dir("parent/child")
        note = _create_note(test_app, "Nested Note", path="parent/child")

        with test_app.app_context():
            rename_folder("parent", "renamed_parent")

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 1

        nested = resp.json[0]
        expected_path = "renamed_parent/child"
        assert nested["metadata"]["path"] == expected_path, (
            f"Expected path '{expected_path}', got '{nested['metadata']['path']}'"
        )

    def test_rename_folder_search_still_works(self, test_app, client: FlaskClient):
        """After renaming, dataobjs must remain searchable."""
        test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
        test_app.config["SEARCH_CONF"]["enabled"] = 1

        create_dir("before")
        note = _create_note(
            test_app, "Find Me", path="before", content="rename_search_target"
        )

        with test_app.app_context():
            rename_folder("before", "after")

        resp = client.get("/api/search?query=rename_search_target")
        assert resp.status_code == 200
        assert any(r["id"] == note.id for r in resp.json)

        test_app.config["SEARCH_CONF"]["enabled"] = 0


class TestDeleteDirConsistency:
    """Tests for delete_dir: search index is cleaned up when directories are removed."""

    def test_delete_dir_removes_from_search_index(self, test_app, client: FlaskClient):
        """Deleting a directory must remove its contents from the search index."""
        test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
        test_app.config["SEARCH_CONF"]["enabled"] = 1

        create_dir("doomed")
        note = _create_note(
            test_app, "Doomed Note", path="doomed", content="ephemeral_content_xyz"
        )

        # Verify the note is searchable before deletion
        resp = client.get("/api/search?query=ephemeral_content_xyz")
        assert resp.status_code == 200
        assert len(resp.json) >= 1

        with test_app.app_context():
            delete_dir("doomed")

        # After deletion, search must return nothing
        resp = client.get("/api/search?query=ephemeral_content_xyz")
        assert resp.status_code == 200
        assert len(resp.json) == 0, (
            f"Search still returned results after directory deletion: {resp.json}"
        )

        test_app.config["SEARCH_CONF"]["enabled"] = 0

    def test_delete_dir_dataobjs_no_longer_returned(self, test_app, client: FlaskClient):
        """After deleting a directory, /api/dataobjs must not include its contents."""
        create_dir("to_delete")
        note = _create_note(test_app, "Soon Gone", path="to_delete")

        # Verify before deletion
        resp = client.get("/api/dataobjs")
        assert len(resp.json) == 1

        with test_app.app_context():
            delete_dir("to_delete")

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 0, (
            f"API still returned dataobjs after directory deletion: {resp.json}"
        )

    def test_delete_dir_preserves_sibling_data(self, test_app, client: FlaskClient):
        """Deleting one directory must not affect dataobjs in other directories."""
        create_dir("keep_me")
        create_dir("remove_me")
        keep_note = _create_note(test_app, "Keeper", path="keep_me")
        _create_note(test_app, "Removable", path="remove_me")

        with test_app.app_context():
            delete_dir("remove_me")

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 1
        assert resp.json[0]["metadata"]["title"] == "Keeper"


class TestMoveThenQuery:
    """End-to-end tests: move/rename then re-query API to confirm consistency."""

    def test_move_then_query_api(self, test_app, client: FlaskClient):
        """Move a note and verify the API reflects the new path immediately."""
        create_dir("origin")
        create_dir("destination")
        note = _create_note(test_app, "Journey Note", path="origin")

        # Verify initial state
        resp = client.get("/api/dataobjs")
        assert resp.json[0]["metadata"]["path"] == "origin"

        # Move
        with test_app.app_context():
            move_item(note.id, "destination")

        # Re-query and verify
        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 1
        assert resp.json[0]["metadata"]["path"] == "destination"

    def test_rename_then_query_api(self, test_app, client: FlaskClient):
        """Rename a folder and verify the API reflects new paths immediately."""
        create_dir("alpha")
        note = _create_note(test_app, "Alpha Note", path="alpha")

        resp = client.get("/api/dataobjs")
        assert resp.json[0]["metadata"]["path"] == "alpha"

        with test_app.app_context():
            rename_folder("alpha", "beta")

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 1
        assert resp.json[0]["metadata"]["path"] == "beta"

    def test_delete_then_query_api(self, test_app, client: FlaskClient):
        """Delete a directory and verify the API returns no ghost entries."""
        create_dir("ghost_town")
        _create_note(test_app, "Ghost", path="ghost_town")

        resp = client.get("/api/dataobjs")
        assert len(resp.json) == 1

        with test_app.app_context():
            delete_dir("ghost_town")

        resp = client.get("/api/dataobjs")
        assert resp.status_code == 200
        assert len(resp.json) == 0
