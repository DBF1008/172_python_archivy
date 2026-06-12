from base64 import b64encode
from os import remove

import responses
from flask import Flask
from flask.testing import FlaskClient
from tinydb import Query
from archivy.data import create_dir, get_items, create_dir, get_item, delete_dir, rename_folder
from archivy.models import DataObj
from archivy.helpers import get_db


def test_bookmark_not_found(test_app, client: FlaskClient):
    response: Flask.response_class = client.get("/api/dataobjs/1")
    assert response.status_code == 404


def test_get_dataobj(test_app, client: FlaskClient, bookmark_fixture):
    response: Flask.response_class = client.get("/api/dataobjs/1")
    assert response.status_code == 200
    assert response.json["title"] == "Example"
    assert response.json["dataobj_id"] == 1
    assert response.json["content"].startswith("Lorem ipsum")


def test_create_bookmark(test_app, client: FlaskClient, mocked_responses):
    mocked_responses.add(responses.GET, "http://example.org", body="Example\n")
    response: Flask.response_class = client.post(
        "/api/bookmarks",
        json={
            "url": "http://example.org",
            "tags": ["test"],
            "path": "",
        },
    )
    assert response.status_code == 200

    response: Flask.response_class = client.get("/api/dataobjs/1")
    assert response.status_code == 200
    assert response.json["title"] == "http://example.org"
    assert response.json["dataobj_id"] == 1
    assert response.json["content"] == "Example"


def test_creating_bookmark_without_passing_path_saves_to_default_dir(
    test_app, client, mocked_responses
):
    mocked_responses.add(responses.GET, "http://example.org", body="Example\n")
    bookmarks_dir = "bookmarks"
    test_app.config["DEFAULT_BOOKMARKS_DIR"] = bookmarks_dir
    create_dir(bookmarks_dir)
    resp = client.post(
        "/api/bookmarks",
        json={
            "url": "http://example.org",
        },
    )
    bookmark = get_items(structured=False)[0]
    assert (
        "bookmarks" in bookmark["path"]
    )  # verify it was saved to default bookmark dir


def test_create_note(test_app, client: FlaskClient, mocked_responses):
    mocked_responses.add(responses.GET, "http://example.org", body="Example\n")
    response: Flask.response_class = client.post(
        "/api/notes",
        json={
            "title": "Test Note created with api",
            "content": "Example",
            "tags": ["test"],
            "path": "",
        },
    )
    assert response.status_code == 200

    response: Flask.response_class = client.get("/api/dataobjs/1")
    assert response.status_code == 200
    assert response.json["title"] == "Test Note created with api"
    assert response.json["dataobj_id"] == 1
    assert response.json["content"] == "Example"


def test_delete_bookmark_not_found(test_app, client: FlaskClient):
    response: Flask.response_class = client.delete("/api/dataobjs/1")
    assert response.status_code == 404


def test_delete_bookmark(test_app, client: FlaskClient, bookmark_fixture):
    response: Flask.response_class = client.delete("/api/dataobjs/1")
    assert response.status_code == 204


def test_get_bookmarks_with_empty_db(test_app, client: FlaskClient):
    response: Flask.response_class = client.get("/api/dataobjs")
    assert response.status_code == 200
    assert response.json == []


def test_get_dataobjs(test_app, client: FlaskClient, bookmark_fixture):
    note_dict = {
        "type": "note",
        "title": "Nested Test Note",
        "tags": ["testing", "archivy"],
        "path": "t",
    }

    create_dir("t")
    note = DataObj(**note_dict)
    note.insert()
    response: Flask.response_class = client.get("/api/dataobjs")
    print(response.data)
    assert response.status_code == 200
    assert isinstance(response.json, list)
    # check it correctly gets nested note
    assert len(response.json) == 2

    bookmark = response.json[0]
    assert bookmark["metadata"]["title"] == "Example"
    assert bookmark["metadata"]["id"] == 1


def test_update_dataobj(test_app, client: FlaskClient, note_fixture):
    lorem = "Updated note content"
    resp = client.put("/api/dataobjs/1", json={"content": lorem})

    assert resp.status_code == 200

    resp = client.get("/api/dataobjs/1")
    assert resp.json["content"] == lorem


def test_update_dataobj_frontmatter(test_app, client: FlaskClient, note_fixture):
    lorem = "Updated note title"
    resp = client.put("/api/dataobjs/frontmatter/1", json={"title": lorem})

    assert resp.status_code == 200

    resp = client.get("/api/dataobjs/1")
    assert resp.json["title"] == lorem


def test_updating_inexistent_dataobj_returns(test_app, client: FlaskClient):
    resp = client.put("/api/dataobjs/1", json={"content": "test"})

    assert resp.status_code == 404


def test_api_login(test_app, client: FlaskClient):
    # logout
    client.delete("/logout")
    # requires converting to base64 for http basic auth
    authorization = "Basic " + b64encode("halcyon:password".encode("ascii")).decode(
        "ascii"
    )
    resp = client.post("/api/login", headers={"Authorization": authorization})

    assert resp.status_code == 200
    resp = client.get("/api/dataobjs")
    assert resp.status_code == 200


def test_unlogged_in_api_fails(test_app, client: FlaskClient):
    client.delete("/logout")
    resp = client.get("/api/dataobjs")
    assert resp.status_code == 302


def test_deleting_unrelated_user_dir_fails(test_app, client: FlaskClient):
    resp = client.delete("/api/folders/delete", json={"path": "/dev/null"})
    assert resp.status_code == 400


def test_path_injection_fails(test_app, client: FlaskClient):
    resp = client.post("/api/folders/new", json={"path": "../../"})
    assert resp.status_code == 400


def test_search_using_ripgrep(test_app, client: FlaskClient, note_fixture):
    test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
    test_app.config["SEARCH_CONF"]["enabled"] = 1

    resp = client.get("/api/search?query=test")
    assert resp.status_code == 200
    assert resp.json[0]["id"] == note_fixture.id
    assert "matches" in resp.json[0]
    assert "test" in resp.json[0]["matches"][0]
    test_app.config["SEARCH_CONF"]["enabled"] = 0


def test_searching_on_disabled(test_app, client):
    test_app.config["SEARCH_CONF"]["enabled"] = 0
    resp = client.get("/api/search?query=shouldn't-work")
    assert resp.status_code == 401
    assert b"Search is disabled" in resp.data


def test_upload_image(test_app, client: FlaskClient):
    open("image.png", "a").close()
    data = {"image": open("image.png", "r")}
    resp = client.post("/api/images", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert open(test_app.config["USER_DIR"] + "/images/image.png", "r")
    remove("image.png")


def test_uploading_image_with_invalid_ext_fails(test_app, client: FlaskClient):
    open("video.mp4", "a").close()
    data = {"image": open("video.mp4", "r")}
    resp = client.post("/api/images", data=data, content_type="multipart/form-data")
    assert resp.status_code == 415
    try:
        open(test_app.config["USER_DIR"] + "/images/video.mp4", "r")
        assert False
    except FileNotFoundError:
        pass
    remove("video.mp4")


def test_calling_upload_images_without_image_fails(test_app, client):
    resp = client.post("/api/images", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_uploading_image_with_same_name_doesnt_collide(test_app, client):
    open("image.png", "a").close()
    resp = client.post(
        "/api/images",
        data={"image": open("image.png", "r")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert open(test_app.config["USER_DIR"] + "/images/image.png", "r")

    resp = client.post(
        "/api/images",
        data={"image": open("image.png", "r")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert open(test_app.config["USER_DIR"] + resp.json["data"]["filePath"], "r")
    remove("image.png")


def test_add_tag_to_index(test_app, client):
    resp = client.put("/api/tags/add_to_index", json={"tag": "new-tag"})
    assert resp.status_code == 200
    db = get_db()
    tag_list = db.search(Query().name == "tag_list")[0]["val"]
    assert "new-tag" in tag_list


def test_adding_invalid_tag_name_fails(test_app, client):
    tag_names = ["", "_#dsd;", "sd!!"]
    for tag in tag_names:
        resp = client.put("/api/tags/add_to_index", json={"tag": tag})
        assert b"Must provide valid tag name" in resp.data
        assert resp.status_code == 401


# --- Directory tree API tests ---


def test_get_tree_empty(test_app, client):
    """Empty data dir returns root node with no children or files."""
    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    tree = resp.json
    assert tree["name"] == "root"
    assert tree["path"] == ""
    assert tree["note_count"] == 0
    assert tree["bookmark_count"] == 0
    assert tree["last_modified"] is None
    assert tree["children"] == []
    assert tree["files"] == []


def test_get_tree_root_with_items(test_app, client):
    """Notes and bookmarks at root have correct counts."""
    # Create a note
    note = DataObj(type="note", title="Root Note", tags=["t"], path="")
    note.insert()
    # Create a bookmark directly (skip HTTP fetching to avoid mock issues)
    bm = DataObj(
        type="bookmark",
        title="Root BM",
        content="bookmark content",
        tags=["t"],
        path="",
        url="https://example.com/",
    )
    bm.insert()

    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    tree = resp.json
    assert tree["path"] == ""
    assert tree["note_count"] == 1
    assert tree["bookmark_count"] == 1
    assert len(tree["files"]) == 2
    assert tree["last_modified"] is not None
    # bookmark file should have a url field
    bm_file = next(f for f in tree["files"] if f["type"] == "bookmark")
    assert "url" in bm_file


def test_get_tree_nested(test_app, client):
    """Multi-level directories produce proper hierarchy with correct counts."""
    create_dir("level1/level2")
    # Note at root
    DataObj(type="note", title="Root Note", tags=[], path="").insert()
    # Note in level1
    DataObj(type="note", title="L1 Note", tags=[], path="level1").insert()
    # Two notes in level1/level2
    DataObj(type="note", title="L2 Note A", tags=[], path="level1/level2").insert()
    DataObj(type="note", title="L2 Note B", tags=[], path="level1/level2").insert()

    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    tree = resp.json

    # Root level
    assert tree["note_count"] == 1
    assert len(tree["children"]) == 1

    # Level 1
    l1 = tree["children"][0]
    assert l1["name"] == "level1"
    assert l1["path"] == "level1"
    assert l1["note_count"] == 1
    assert len(l1["children"]) == 1

    # Level 2
    l2 = l1["children"][0]
    assert l2["name"] == "level2"
    assert l2["path"] == "level1/level2"
    assert l2["note_count"] == 2
    assert l2["children"] == []
    assert len(l2["files"]) == 2


def test_get_tree_empty_dir(test_app, client):
    """Empty directory appears in children with zero counts."""
    create_dir("empty_folder")
    # Also create a non-empty dir so tree has items
    create_dir("has_content")
    DataObj(type="note", title="A Note", tags=[], path="has_content").insert()

    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    tree = resp.json
    child_names = [c["name"] for c in tree["children"]]
    assert "empty_folder" in child_names

    empty = next(c for c in tree["children"] if c["name"] == "empty_folder")
    assert empty["note_count"] == 0
    assert empty["bookmark_count"] == 0
    assert empty["files"] == []
    assert empty["children"] == []
    assert empty["last_modified"] is None


def test_get_tree_path_param(test_app, client):
    """?path=subdir returns subtree scoped to that directory."""
    create_dir("alpha/beta")
    DataObj(type="note", title="In Alpha", tags=[], path="alpha").insert()
    DataObj(type="note", title="In Beta", tags=[], path="alpha/beta").insert()

    resp = client.get("/api/dataobjs/tree?path=alpha")
    assert resp.status_code == 200
    tree = resp.json
    assert tree["name"] == "alpha"
    assert tree["path"] == "alpha"
    assert tree["note_count"] == 1
    assert len(tree["children"]) == 1
    assert tree["children"][0]["name"] == "beta"
    assert tree["children"][0]["path"] == "alpha/beta"


def test_get_tree_invalid_path(test_app, client):
    """Nonexistent path returns 404."""
    resp = client.get("/api/dataobjs/tree?path=does_not_exist")
    assert resp.status_code == 404


def test_get_tree_path_injection(test_app, client):
    """Path traversal attempt returns 404."""
    resp = client.get("/api/dataobjs/tree?path=../../etc")
    assert resp.status_code == 404


def test_get_tree_after_create_folder(test_app, client):
    """Newly created folder appears in tree."""
    resp = client.get("/api/dataobjs/tree")
    assert resp.json["children"] == []

    create_dir("new_folder")

    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    child_names = [c["name"] for c in resp.json["children"]]
    assert "new_folder" in child_names


def test_get_tree_after_delete_folder(test_app, client):
    """Deleted folder disappears from tree."""
    create_dir("to_delete")
    resp = client.get("/api/dataobjs/tree")
    assert any(c["name"] == "to_delete" for c in resp.json["children"])

    delete_dir("to_delete")

    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    assert not any(c["name"] == "to_delete" for c in resp.json["children"])


def test_get_tree_after_rename_folder(test_app, client):
    """Renamed folder: old name gone, new name present."""
    create_dir("old_name")
    DataObj(type="note", title="Inside", tags=[], path="old_name").insert()

    rename_folder("old_name", "new_name")

    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    child_names = [c["name"] for c in resp.json["children"]]
    assert "old_name" not in child_names
    assert "new_name" in child_names
    # The note should still be inside the renamed folder
    renamed = next(c for c in resp.json["children"] if c["name"] == "new_name")
    assert renamed["note_count"] == 1


def test_get_tree_after_move_item(test_app, client):
    """Moved dataobj: removed from source dir, appears in target dir."""
    create_dir("source")
    create_dir("target")
    note = DataObj(type="note", title="Movable", tags=[], path="source")
    note.insert()

    # Verify initial state
    resp = client.get("/api/dataobjs/tree")
    source = next(c for c in resp.json["children"] if c["name"] == "source")
    target = next(c for c in resp.json["children"] if c["name"] == "target")
    assert source["note_count"] == 1
    assert target["note_count"] == 0

    # Move the note
    from archivy.data import move_item
    move_item(note.id, "target")

    # Re-query tree
    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 200
    source = next(c for c in resp.json["children"] if c["name"] == "source")
    target = next(c for c in resp.json["children"] if c["name"] == "target")
    assert source["note_count"] == 0
    assert target["note_count"] == 1


def test_get_tree_unauthenticated(test_app, client):
    """Unauthenticated request returns 302."""
    client.delete("/logout")
    resp = client.get("/api/dataobjs/tree")
    assert resp.status_code == 302
