"""
Regression tests for path / metadata / search-index consistency after a
dataobj is moved, a directory is renamed, or a directory is deleted.

Before the fix, ``move_item`` / ``rename_folder`` left the ``path`` frontmatter
stale (so ``/api/dataobjs`` kept returning the old path) and ``delete_dir`` never
removed the contained objects from the search index (leaving "ghost" records).
"""

from flask.testing import FlaskClient

from archivy.data import (
    create_dir,
    delete_dir,
    get_data_dir,
    get_item,
    move_item,
    rename_folder,
)
from archivy.models import DataObj


def _api_metadata(client, dataobj_id):
    """Returns the ``metadata`` dict that /api/dataobjs reports for the given id.

    /api/dataobjs serializes each dataobj as ``{"metadata": {...}, "content": ...}``
    where ``metadata`` holds both the frontmatter ``path`` and the freshly computed
    ``fullpath``. Returns ``None`` if the id is not listed anymore.
    """
    resp = client.get("/api/dataobjs")
    assert resp.status_code == 200
    for entry in resp.json:
        if entry["metadata"]["id"] == dataobj_id:
            return entry["metadata"]
    return None


def test_move_item_syncs_path_metadata_and_api(test_app, client: FlaskClient, note_fixture):
    """Moving a single dataobj must update its path metadata everywhere, and a
    subsequent re-query must reflect the new location (not the stale one)."""
    obj_id = note_fixture.id

    with test_app.app_context():
        create_dir("moved")
        assert move_item(obj_id, "moved")

        item = get_item(obj_id)
        # frontmatter path + computed dir both point at the new location
        assert item["path"] == "moved"
        assert item["dir"] == "moved"

    # re-query through the API: the leaked stale path "" must be gone
    meta = _api_metadata(client, obj_id)
    assert meta is not None
    assert meta["path"] == "moved"
    assert meta["fullpath"] == "moved"

    # move it back to the root and re-query again: still consistent
    with test_app.app_context():
        assert move_item(obj_id, "")
        assert get_item(obj_id)["path"] == ""

    meta = _api_metadata(client, obj_id)
    assert meta["path"] == ""
    assert meta["fullpath"] == "."


def test_rename_folder_syncs_nested_path_metadata(test_app, client: FlaskClient):
    """Renaming a directory must rewrite the path metadata of every contained
    dataobj, including those in nested sub-directories."""
    with test_app.app_context():
        create_dir("orig")
        create_dir("orig/sub")
        top_id = DataObj(type="note", title="Top", path="orig").insert()
        nested_id = DataObj(type="note", title="Nested", path="orig/sub").insert()

        assert rename_folder("orig", "renamed") == "renamed"

        assert get_item(top_id)["path"] == "renamed"
        assert get_item(nested_id)["path"] == "renamed/sub"

    # re-query through the API reflects the renamed directory
    assert _api_metadata(client, top_id)["path"] == "renamed"
    assert _api_metadata(client, nested_id)["path"] == "renamed/sub"


def test_delete_dir_removes_contained_objs_from_index_and_api(
    test_app, client: FlaskClient, monkeypatch
):
    """Deleting a directory must remove every contained dataobj from the search
    index (no ghost records) and they must no longer be returned by the API."""
    removed_ids = []
    monkeypatch.setattr(
        "archivy.data.remove_from_index", lambda did: removed_ids.append(did)
    )

    with test_app.app_context():
        create_dir("trash")
        create_dir("trash/deep")
        top_id = DataObj(type="note", title="Trash Top", path="trash").insert()
        nested_id = DataObj(type="note", title="Trash Nested", path="trash/deep").insert()

        assert delete_dir("trash") is True
        # gone from disk
        assert not (get_data_dir() / "trash").exists()
        assert get_item(top_id) is None
        assert get_item(nested_id) is None

    # every contained object was purged from the search index
    assert sorted(removed_ids) == sorted([top_id, nested_id])

    # re-query through the API: deleted objects are no longer listed
    assert _api_metadata(client, top_id) is None
    assert _api_metadata(client, nested_id) is None
