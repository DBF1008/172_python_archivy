from base64 import b64encode
from datetime import datetime
from os import remove

import frontmatter
import responses
from flask import Flask
from flask.testing import FlaskClient
from tinydb import Query
from archivy.data import create_dir, get_items, get_item, get_by_id, move_item
from archivy.models import DataObj
from archivy.helpers import get_db


def _make_note(test_app, title, tags=None, path=""):
    """Create a note dataobj and return its id."""
    with test_app.app_context():
        if path:
            create_dir(path)
        note = DataObj(type="note", title=title, tags=tags or [], path=path)
        note.insert()
        return note.id


def _set_modified_at(test_app, dataobj_id, value):
    """Overwrite the ``modified_at`` frontmatter of a dataobj on disk.

    Used to give deterministic timestamps in sorting tests; the normal code
    path always writes the current time.
    """
    with test_app.app_context():
        path = get_by_id(dataobj_id)
        post = frontmatter.load(str(path))
        post["modified_at"] = value
        with open(path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))



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
    assert response.json["data"] == []
    pagination = response.json["pagination"]
    assert pagination["total_items"] == 0
    assert pagination["total_pages"] == 0
    assert pagination["has_next"] is False
    assert pagination["has_prev"] is False


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
    assert response.status_code == 200
    assert isinstance(response.json["data"], list)
    # check it correctly gets nested note alongside the root bookmark
    assert len(response.json["data"]) == 2
    assert response.json["pagination"]["total_items"] == 2
    assert response.json["pagination"]["total_pages"] == 1

    by_id = {obj["metadata"]["id"]: obj["metadata"] for obj in response.json["data"]}
    assert by_id[1]["title"] == "Example"


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


def test_dataobjs_empty_result_with_filter(test_app, client, note_fixture):
    # note_fixture inserts a single note; filtering by a type with no matches
    # must return an empty page with coherent metadata (not an error).
    resp = client.get("/api/dataobjs?type=bookmark")
    assert resp.status_code == 200
    assert resp.json["data"] == []
    pagination = resp.json["pagination"]
    assert pagination["total_items"] == 0
    assert pagination["total_pages"] == 0
    assert pagination["has_next"] is False
    assert pagination["has_prev"] is False


def test_dataobjs_combined_filters(test_app, client):
    _make_note(test_app, "Root AB", tags=["a", "b"])  # id 1, root
    _make_note(test_app, "Proj A", tags=["a"], path="proj")  # id 2
    _make_note(test_app, "Proj AB", tags=["a", "b"], path="proj")  # id 3
    _make_note(test_app, "Sub AB", tags=["a", "b"], path="proj/sub")  # id 4

    # directory (recursive) + tags (AND) + type, combined
    resp = client.get("/api/dataobjs?path=proj&tags=a&tags=b&type=note")
    assert resp.status_code == 200
    ids = sorted(o["metadata"]["id"] for o in resp.json["data"])
    # Proj AB (3) and Sub AB (4) match; Proj A (2) lacks tag b;
    # Root AB (1) is not under proj/.
    assert ids == [3, 4]
    assert resp.json["pagination"]["total_items"] == 2


def test_dataobjs_tag_filter_requires_all_tags(test_app, client):
    _make_note(test_app, "Has both", tags=["python", "flask"])  # id 1
    _make_note(test_app, "Has one", tags=["python"])  # id 2
    resp = client.get("/api/dataobjs?tags=python&tags=flask")
    assert resp.status_code == 200
    assert resp.json["pagination"]["total_items"] == 1
    assert resp.json["data"][0]["metadata"]["title"] == "Has both"


def test_dataobjs_sort_by_id(test_app, client):
    for title in ["one", "two", "three"]:
        _make_note(test_app, title)  # ids 1, 2, 3

    asc = client.get("/api/dataobjs?sort=id&order=asc").json["data"]
    assert [o["metadata"]["id"] for o in asc] == [1, 2, 3]
    desc = client.get("/api/dataobjs?sort=id&order=desc").json["data"]
    assert [o["metadata"]["id"] for o in desc] == [3, 2, 1]


def test_dataobjs_sort_by_title_is_case_insensitive(test_app, client):
    _make_note(test_app, "Banana")  # id 1
    _make_note(test_app, "apple")  # id 2
    _make_note(test_app, "Cherry")  # id 3

    asc = client.get("/api/dataobjs?sort=title&order=asc").json["data"]
    assert [o["metadata"]["title"] for o in asc] == ["apple", "Banana", "Cherry"]


def test_dataobjs_sort_by_modified_at_is_chronological(test_app, client):
    older = _make_note(test_app, "Older")  # id 1
    newer = _make_note(test_app, "Newer")  # id 2
    # Built with strftime so they parse under any locale. In the common
    # MM/DD/YY locale "01/.." sorts before "12/.." as a string, yet 2020 is
    # chronologically after 2019 -- a naive string sort gets this backwards.
    _set_modified_at(
        test_app, older, datetime(2019, 12, 31, 10, 0).strftime("%x %H:%M")
    )
    _set_modified_at(test_app, newer, datetime(2020, 1, 1, 9, 0).strftime("%x %H:%M"))

    asc = client.get("/api/dataobjs?sort=modified_at&order=asc").json["data"]
    assert [o["metadata"]["title"] for o in asc] == ["Older", "Newer"]
    desc = client.get("/api/dataobjs?sort=modified_at&order=desc").json["data"]
    assert [o["metadata"]["title"] for o in desc] == ["Newer", "Older"]


def test_dataobjs_stable_sort_breaks_ties_by_id(test_app, client):
    ids = [_make_note(test_app, f"n{i}") for i in range(5)]  # ids 1..5
    # Give every object the exact same modified_at so the primary key always
    # ties; ordering must then fall back to ascending id, in both directions.
    same_time = datetime(2022, 6, 15, 12, 0).strftime("%x %H:%M")
    for dataobj_id in ids:
        _set_modified_at(test_app, dataobj_id, same_time)

    desc = client.get("/api/dataobjs?sort=modified_at&order=desc").json["data"]
    assert [o["metadata"]["id"] for o in desc] == [1, 2, 3, 4, 5]
    asc = client.get("/api/dataobjs?sort=modified_at&order=asc").json["data"]
    assert [o["metadata"]["id"] for o in asc] == [1, 2, 3, 4, 5]


def test_dataobjs_pagination(test_app, client):
    for i in range(5):
        _make_note(test_app, f"note{i}")  # ids 1..5

    p1 = client.get("/api/dataobjs?sort=id&order=asc&per_page=2&page=1").json
    assert [o["metadata"]["id"] for o in p1["data"]] == [1, 2]
    assert p1["pagination"]["page"] == 1
    assert p1["pagination"]["per_page"] == 2
    assert p1["pagination"]["total_items"] == 5
    assert p1["pagination"]["total_pages"] == 3
    assert p1["pagination"]["has_next"] is True
    assert p1["pagination"]["has_prev"] is False

    p2 = client.get("/api/dataobjs?sort=id&order=asc&per_page=2&page=2").json
    assert [o["metadata"]["id"] for o in p2["data"]] == [3, 4]
    assert p2["pagination"]["has_next"] is True
    assert p2["pagination"]["has_prev"] is True

    p3 = client.get("/api/dataobjs?sort=id&order=asc&per_page=2&page=3").json
    assert [o["metadata"]["id"] for o in p3["data"]] == [5]
    assert p3["pagination"]["has_next"] is False
    assert p3["pagination"]["has_prev"] is True


def test_dataobjs_page_beyond_range_returns_empty(test_app, client):
    for i in range(3):
        _make_note(test_app, f"n{i}")  # ids 1..3

    resp = client.get("/api/dataobjs?per_page=2&page=5").json
    assert resp["data"] == []
    assert resp["pagination"]["total_items"] == 3
    assert resp["pagination"]["total_pages"] == 2
    assert resp["pagination"]["has_next"] is False
    assert resp["pagination"]["has_prev"] is True


def test_dataobjs_default_returns_all_on_single_page(test_app, client):
    for i in range(3):
        _make_note(test_app, f"n{i}")

    resp = client.get("/api/dataobjs").json
    assert len(resp["data"]) == 3
    assert resp["pagination"]["total_items"] == 3
    assert resp["pagination"]["total_pages"] == 1
    assert resp["pagination"]["per_page"] == 3
    assert resp["pagination"]["has_next"] is False
    assert resp["pagination"]["has_prev"] is False


def test_dataobjs_invalid_params_return_400(test_app, client):
    bad_urls = [
        "/api/dataobjs?sort=bogus",
        "/api/dataobjs?order=sideways",
        "/api/dataobjs?page=0",
        "/api/dataobjs?page=abc",
        "/api/dataobjs?per_page=0",
        "/api/dataobjs?per_page=abc",
        "/api/dataobjs?per_page=1000",
    ]
    for url in bad_urls:
        resp = client.get(url)
        assert resp.status_code == 400, url


def test_dataobjs_reflects_create_move_and_edit(test_app, client):
    # create through the API
    resp = client.post(
        "/api/notes", json={"title": "Movable", "content": "hello", "path": ""}
    )
    assert resp.status_code == 200
    note_id = resp.json["note_id"]

    # listed at the root right after creation
    root_listing = client.get("/api/dataobjs?path=").json
    assert root_listing["pagination"]["total_items"] == 1
    assert note_id in [o["metadata"]["id"] for o in root_listing["data"]]

    # a different directory does not contain it yet
    with test_app.app_context():
        create_dir("archive")
    assert (
        client.get("/api/dataobjs?path=archive").json["pagination"]["total_items"] == 0
    )

    # move it and confirm the directory filter reflects the new location
    with test_app.app_context():
        move_item(note_id, "archive")
    in_archive = client.get("/api/dataobjs?path=archive").json
    assert [o["metadata"]["id"] for o in in_archive["data"]] == [note_id]
    # recursive root listing still sees it (archive is under root)
    assert client.get("/api/dataobjs").json["pagination"]["total_items"] == 1

    # edit content through the API; still listed exactly once
    resp = client.put(f"/api/dataobjs/{note_id}", json={"content": "updated"})
    assert resp.status_code == 200
    after_edit = client.get("/api/dataobjs?path=archive").json
    assert after_edit["pagination"]["total_items"] == 1
    assert after_edit["data"][0]["metadata"]["id"] == note_id
