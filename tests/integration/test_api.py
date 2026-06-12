from base64 import b64encode
from os import remove
from types import SimpleNamespace
import json

import responses
from flask import Flask
from flask.testing import FlaskClient
from tinydb import Query
from archivy.data import create_dir, get_items, create_dir, get_item
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


def _fake_rg_output(entries):
    """Build canned `rg --json` stdout bytes for the given referencing objects.

    `entries` is a list of ``(id, title, [match_lines])`` tuples. Each tuple is
    rendered as one matched file (a ``begin`` event) followed by one ``match``
    event per line, mirroring ripgrep's JSON output. This lets the real
    ``query_ripgrep`` parsing / deduplication logic run without the ``rg``
    binary being installed.
    """
    lines = []
    for obj_id, title, match_lines in entries:
        path = f"/tmp/data/{obj_id}-{title}.md"
        lines.append(json.dumps({"type": "begin", "data": {"path": {"text": path}}}))
        for text in match_lines:
            lines.append(
                json.dumps({"type": "match", "data": {"lines": {"text": text + "\n"}}})
            )
        lines.append(json.dumps({"type": "end", "data": {}}))
    return "\n".join(lines).encode()


def _patch_ripgrep(monkeypatch, output):
    monkeypatch.setattr("archivy.search.which", lambda _: "rg")
    monkeypatch.setattr("archivy.search.run", lambda *a, **k: SimpleNamespace(stdout=output))


def test_get_backlinks_with_no_references(test_app, client, note_fixture, monkeypatch):
    test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
    test_app.config["SEARCH_CONF"]["enabled"] = 1
    _patch_ripgrep(monkeypatch, b"")  # ripgrep finds nothing

    resp = client.get(f"/api/dataobjs/{note_fixture.id}/backlinks")
    assert resp.status_code == 200
    assert resp.json == []
    test_app.config["SEARCH_CONF"]["enabled"] = 0


def test_get_backlinks_from_multiple_sources(test_app, client, note_fixture, monkeypatch):
    test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
    test_app.config["SEARCH_CONF"]["enabled"] = 1
    target = note_fixture.id
    output = _fake_rg_output(
        [
            (3, "Source A", [f"see [[Source A|{target}]] here"]),
            (4, "Source B", [f"ref [[Source B|{target}]]"]),
        ]
    )
    _patch_ripgrep(monkeypatch, output)

    resp = client.get(f"/api/dataobjs/{target}/backlinks")
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json}
    assert ids == {3, 4}  # both referencing objects are returned
    for item in resp.json:
        assert item["matches"]  # each backlink carries its snippet
    test_app.config["SEARCH_CONF"]["enabled"] = 0


def test_get_backlinks_deduplicates_per_source(test_app, client, note_fixture, monkeypatch):
    test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
    test_app.config["SEARCH_CONF"]["enabled"] = 1
    target = note_fixture.id
    # a single source file references the target twice
    output = _fake_rg_output(
        [
            (
                3,
                "Source A",
                [
                    f"first [[Source A|{target}]] mention",
                    f"second [[Source A|{target}]] mention",
                ],
            ),
        ]
    )
    _patch_ripgrep(monkeypatch, output)

    resp = client.get(f"/api/dataobjs/{target}/backlinks")
    assert resp.status_code == 200
    assert len(resp.json) == 1  # deduplicated to a single backlink entry
    assert resp.json[0]["id"] == 3
    assert len(resp.json[0]["matches"]) == 2  # both snippets retained
    test_app.config["SEARCH_CONF"]["enabled"] = 0


def test_get_backlinks_when_search_disabled(test_app, client, note_fixture):
    test_app.config["SEARCH_CONF"]["enabled"] = 0
    resp = client.get(f"/api/dataobjs/{note_fixture.id}/backlinks")
    assert resp.status_code == 401
    assert b"Search is disabled" in resp.data


def test_get_backlinks_for_nonexistent_dataobj(test_app, client):
    test_app.config["SEARCH_CONF"]["enabled"] = 1
    resp = client.get("/api/dataobjs/999/backlinks")
    assert resp.status_code == 404
    test_app.config["SEARCH_CONF"]["enabled"] = 0
