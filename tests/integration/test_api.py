from base64 import b64encode
from datetime import datetime
from os import remove

import frontmatter
import responses
from flask import Flask
from flask.testing import FlaskClient
from tinydb import Query
from archivy.data import (
    create_dir,
    get_items,
    get_item,
    get_by_id,
    move_item,
    rename_folder,
    delete_dir,
)
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


# --- /api/folders/tree -------------------------------------------------------


def _make_dataobj(type, title, path="", url=None):
    """Create a dataobj on disk without hitting the network and return its id."""
    obj = DataObj(type=type, title=title, path=path, url=url, content="content")
    obj.insert()
    return obj.id


def _find_child(node, name):
    for child in node["child_dirs"]:
        if child["name"] == name:
            return child
    raise AssertionError(f"no child directory named {name!r} in {node['path']!r}")


def _set_modified_at(dataobj_id, when: datetime):
    """Overwrite the modified_at frontmatter of a dataobj for deterministic tests."""
    path = get_by_id(dataobj_id)
    post = frontmatter.load(str(path))
    post["modified_at"] = when.strftime("%x %H:%M")
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))


def test_folder_tree_empty_root(test_app, client: FlaskClient):
    resp = client.get("/api/folders/tree")
    assert resp.status_code == 200
    tree = resp.json
    assert tree["name"] == "root"
    assert tree["path"] == ""
    assert tree["note_count"] == 0
    assert tree["bookmark_count"] == 0
    assert tree["total_note_count"] == 0
    assert tree["total_bookmark_count"] == 0
    assert tree["last_modified"] is None
    assert tree["child_dirs"] == []
    assert tree["most_recently_modified_path"] is None


def test_folder_tree_root_counts(
    test_app, client: FlaskClient, note_fixture, bookmark_fixture
):
    resp = client.get("/api/folders/tree")
    assert resp.status_code == 200
    tree = resp.json
    assert tree["note_count"] == 1
    assert tree["bookmark_count"] == 1
    assert tree["total_note_count"] == 1
    assert tree["total_bookmark_count"] == 1
    assert tree["child_dirs"] == []
    assert tree["last_modified"] is not None
    # most recent change lives directly in the root directory
    assert tree["most_recently_modified_path"] == ""


def test_folder_tree_nested_counts_and_paths(test_app, client: FlaskClient):
    create_dir("a")
    create_dir("a/b")
    _make_dataobj("note", "root note", path="")
    _make_dataobj("note", "nested note", path="a")
    _make_dataobj("bookmark", "deep bm", path="a/b", url="http://example.com")

    tree = client.get("/api/folders/tree").json

    # root: 1 direct note, recursive totals span the whole tree
    assert tree["note_count"] == 1
    assert tree["bookmark_count"] == 0
    assert tree["total_note_count"] == 2
    assert tree["total_bookmark_count"] == 1

    a = _find_child(tree, "a")
    assert a["path"] == "a"
    assert a["note_count"] == 1
    assert a["bookmark_count"] == 0
    assert a["total_note_count"] == 1
    assert a["total_bookmark_count"] == 1

    b = _find_child(a, "b")
    assert b["path"] == "a/b"
    assert b["note_count"] == 0
    assert b["bookmark_count"] == 1
    assert b["total_note_count"] == 0
    assert b["total_bookmark_count"] == 1
    assert b["child_dirs"] == []


def test_folder_tree_includes_empty_dirs(test_app, client: FlaskClient):
    _make_dataobj("note", "root note", path="")
    create_dir("empty")

    tree = client.get("/api/folders/tree").json

    empty = _find_child(tree, "empty")
    assert empty["path"] == "empty"
    assert empty["note_count"] == 0
    assert empty["bookmark_count"] == 0
    assert empty["total_note_count"] == 0
    assert empty["total_bookmark_count"] == 0
    assert empty["last_modified"] is None
    assert empty["child_dirs"] == []


def test_folder_tree_with_path_returns_subtree(test_app, client: FlaskClient):
    create_dir("parent")
    create_dir("parent/child")
    _make_dataobj("note", "n", path="parent")

    resp = client.get("/api/folders/tree?path=parent")
    assert resp.status_code == 200
    tree = resp.json
    assert tree["name"] == "parent"
    assert tree["path"] == "parent"
    assert tree["note_count"] == 1
    child = _find_child(tree, "child")
    assert child["path"] == "parent/child"


def test_folder_tree_nonexistent_path_returns_404(test_app, client: FlaskClient):
    resp = client.get("/api/folders/tree?path=does/not/exist")
    assert resp.status_code == 404


def test_folder_tree_most_recently_modified_path(test_app, client: FlaskClient):
    create_dir("a")
    create_dir("b")
    _make_dataobj("note", "in a", path="a")
    recent_id = _make_dataobj("note", "in b", path="b")
    future = datetime(2035, 1, 1, 12, 0)
    _set_modified_at(recent_id, future)

    tree = client.get("/api/folders/tree").json

    assert tree["most_recently_modified_path"] == "b"
    # the freshest timestamp bubbles up to the root summary
    assert tree["last_modified"] == future.strftime("%x %H:%M")


def test_folder_tree_reflects_rename(test_app, client: FlaskClient):
    create_dir("old")
    _make_dataobj("note", "kept note", path="old")

    tree = client.get("/api/folders/tree").json
    old = _find_child(tree, "old")
    assert old["note_count"] == 1

    rename_folder("old", "new")

    tree = client.get("/api/folders/tree").json
    assert all(c["name"] != "old" for c in tree["child_dirs"])
    new = _find_child(tree, "new")
    assert new["path"] == "new"
    assert new["note_count"] == 1


def test_folder_tree_reflects_move(test_app, client: FlaskClient):
    create_dir("dest")
    note_id = _make_dataobj("note", "movable", path="")

    tree = client.get("/api/folders/tree").json
    assert tree["note_count"] == 1
    assert _find_child(tree, "dest")["note_count"] == 0

    move_item(note_id, "dest")

    tree = client.get("/api/folders/tree").json
    assert tree["note_count"] == 0
    dest = _find_child(tree, "dest")
    assert dest["path"] == "dest"
    assert dest["note_count"] == 1


def test_folder_tree_reflects_deletion(test_app, client: FlaskClient):
    create_dir("temp")
    _make_dataobj("note", "doomed", path="temp")

    tree = client.get("/api/folders/tree").json
    assert _find_child(tree, "temp")["note_count"] == 1

    delete_dir("temp")

    tree = client.get("/api/folders/tree").json
    assert all(c["name"] != "temp" for c in tree["child_dirs"])
    assert tree["total_note_count"] == 0
