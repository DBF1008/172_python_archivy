from base64 import b64encode
from os import remove
from shutil import which

import responses
from flask import Flask
from flask.testing import FlaskClient
from tinydb import Query
from archivy.data import create_dir, get_items, get_item, get_by_id, get_data_dir
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


def test_update_frontmatter_renames_file_at_root(test_app, client, note_fixture):
    """Renaming a root note's title renames its file and updates the API path."""
    nid = note_fixture.id
    with test_app.app_context():
        old_path = get_by_id(nid)
    assert old_path.name == f"{nid}-Test_Note.md"

    resp = client.put(
        f"/api/dataobjs/frontmatter/{nid}", json={"title": "Renamed Note"}
    )
    assert resp.status_code == 200

    with test_app.app_context():
        new_path = get_by_id(nid)
    # the file on disk is renamed to match the new title; the old slug is gone
    assert new_path.name == f"{nid}-Renamed_Note.md"
    assert new_path.exists()
    assert not old_path.exists()

    # the API now exposes both the new title and the new file path
    resp = client.get(f"/api/dataobjs/{nid}")
    assert resp.json["title"] == "Renamed Note"
    assert resp.json["md_path"].endswith(f"{nid}-Renamed_Note.md")


def test_update_frontmatter_renames_file_in_subdir(test_app, client):
    """A renamed note stays inside its subdirectory and keeps its `{id}-` prefix."""
    with test_app.app_context():
        create_dir("folder")
        note = DataObj(type="note", title="Nested Note", path="folder")
        note.insert()
        old_path = get_by_id(note.id)
    assert old_path.parent.name == "folder"
    assert old_path.name == f"{note.id}-Nested_Note.md"

    resp = client.put(
        f"/api/dataobjs/frontmatter/{note.id}", json={"title": "Nested Renamed"}
    )
    assert resp.status_code == 200

    with test_app.app_context():
        new_path = get_by_id(note.id)
    assert new_path.parent.name == "folder"  # not moved out of its directory
    assert new_path.name == f"{note.id}-Nested_Renamed.md"
    assert not old_path.exists()


def test_repeated_frontmatter_renames_leave_no_stale_files(
    test_app, client, note_fixture
):
    """Renaming several times leaves exactly one file carrying the latest slug."""
    nid = note_fixture.id
    for new_title in ("First Rename", "Second Rename", "Third Rename"):
        resp = client.put(
            f"/api/dataobjs/frontmatter/{nid}", json={"title": new_title}
        )
        assert resp.status_code == 200

    with test_app.app_context():
        final_path = get_by_id(nid)
        md_files = list(get_data_dir().rglob("*.md"))
    assert final_path.name == f"{nid}-Third_Rename.md"
    # no leftover files from the intermediate renames
    assert len(md_files) == 1

    resp = client.get(f"/api/dataobjs/{nid}")
    assert resp.json["title"] == "Third Rename"
    assert resp.json["md_path"].endswith(f"{nid}-Third_Rename.md")


def test_edit_and_search_after_rename(test_app, client, note_fixture):
    """After a rename, content edits still resolve and search shows the new title."""
    nid = note_fixture.id
    resp = client.put(
        f"/api/dataobjs/frontmatter/{nid}", json={"title": "Searchable Title"}
    )
    assert resp.status_code == 200

    # editing the body still locates the (renamed) file by id
    resp = client.put(f"/api/dataobjs/{nid}", json={"content": "fresh body content"})
    assert resp.status_code == 200
    resp = client.get(f"/api/dataobjs/{nid}")
    assert resp.json["content"] == "fresh body content"
    assert resp.json["title"] == "Searchable Title"

    # ripgrep derives the displayed title from the filename, so the search result
    # must reflect the new title now that the file has been renamed
    if which("rg"):
        test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
        test_app.config["SEARCH_CONF"]["enabled"] = 1
        resp = client.get("/api/search?query=fresh")
        assert resp.status_code == 200
        assert resp.json[0]["id"] == nid
        assert resp.json[0]["title"] == "Searchable Title"
        test_app.config["SEARCH_CONF"]["enabled"] = 0


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
