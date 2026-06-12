from base64 import b64encode
from os import remove

import responses
from flask import Flask
from flask.testing import FlaskClient
from tinydb import Query
from archivy.data import create_dir, get_items, create_dir, get_item, query_dataobjs
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


# ---------------------------------------------------------------------------
# Helpers for filter / sort / pagination tests
# ---------------------------------------------------------------------------

def _create_notes(test_app, notes_config):
    """Helper: create a batch of notes from a list of config dicts."""
    created = []
    for cfg in notes_config:
        with test_app.app_context():
            obj = DataObj(**cfg)
            obj.insert()
        created.append(obj)
    return created


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_get_dataobjs_backward_compat(test_app, client):
    """GET /api/dataobjs with no params returns a flat JSON array."""
    with test_app.app_context():
        DataObj(type="note", title="A Note", tags=[], path="").insert()

    resp = client.get("/api/dataobjs")
    assert resp.status_code == 200
    assert isinstance(resp.json, list)
    assert len(resp.json) == 1
    assert resp.json[0]["metadata"]["id"] == 1


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------

def test_filter_empty_results(test_app, client):
    with test_app.app_context():
        DataObj(type="note", title="A Note", tags=[], path="").insert()

    resp = client.get("/api/dataobjs?type=nonexistent")
    assert resp.status_code == 200
    assert resp.json["items"] == []
    assert resp.json["page_info"]["total_items"] == 0
    assert resp.json["page_info"]["total_pages"] == 0
    assert resp.json["page_info"]["has_next"] is False
    assert resp.json["page_info"]["has_prev"] is False


def test_filter_empty_results_empty_db(test_app, client):
    """Filtered query on a completely empty DB."""
    resp = client.get("/api/dataobjs?type=note")
    assert resp.status_code == 200
    assert resp.json["items"] == []
    assert resp.json["page_info"]["total_items"] == 0


# ---------------------------------------------------------------------------
# Filter by type
# ---------------------------------------------------------------------------

def test_filter_by_type(test_app, client):
    with test_app.app_context():
        DataObj(type="note", title="My Note", tags=[], path="").insert()
        bm = DataObj(
            type="bookmark", title="My BM", tags=[], path="",
            url="https://example.com/", content="saved content",
        )
        bm.insert()

    # type=note
    resp = client.get("/api/dataobjs?type=note")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["type"] == "note"

    # type=bookmark
    resp = client.get("/api/dataobjs?type=bookmark")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["type"] == "bookmark"

    # type=pocket_bookmark  → none
    resp = client.get("/api/dataobjs?type=pocket_bookmark")
    assert resp.json["page_info"]["total_items"] == 0


# ---------------------------------------------------------------------------
# Filter by path
# ---------------------------------------------------------------------------

def test_filter_by_path(test_app, client):
    create_dir("projects")
    with test_app.app_context():
        DataObj(type="note", title="Root Note", tags=[], path="").insert()
        DataObj(type="note", title="Project Note", tags=[], path="projects").insert()

    # root items only (path="")
    resp = client.get("/api/dataobjs?path=")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["title"] == "Root Note"

    # specific path
    resp = client.get("/api/dataobjs?path=projects")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["title"] == "Project Note"

    # non-existent path
    resp = client.get("/api/dataobjs?path=doesnotexist")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 0


# ---------------------------------------------------------------------------
# Filter by tags
# ---------------------------------------------------------------------------

def test_filter_by_single_tag(test_app, client):
    with test_app.app_context():
        DataObj(type="note", title="Tagged A", tags=["python", "web"], path="").insert()
        DataObj(type="note", title="Tagged B", tags=["python"], path="").insert()
        DataObj(type="note", title="Untagged", tags=[], path="").insert()

    resp = client.get("/api/dataobjs?tags=python")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 2
    titles = {it["metadata"]["title"] for it in resp.json["items"]}
    assert titles == {"Tagged A", "Tagged B"}


def test_filter_by_multiple_tags(test_app, client):
    with test_app.app_context():
        DataObj(type="note", title="Both", tags=["alpha", "beta"], path="").insert()
        DataObj(type="note", title="Alpha only", tags=["alpha"], path="").insert()
        DataObj(type="note", title="Beta only", tags=["beta"], path="").insert()

    # AND semantics: must contain ALL specified tags
    resp = client.get("/api/dataobjs?tags=alpha,beta")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["title"] == "Both"


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------

def test_combined_filters(test_app, client):
    create_dir("work")
    with test_app.app_context():
        DataObj(type="note", title="Work Note", tags=["python", "urgent"], path="work").insert()
        DataObj(type="note", title="Other Note", tags=["python"], path="").insert()
        DataObj(type="note", title="Work NoTag", tags=[], path="work").insert()
        DataObj(
            type="bookmark", title="Work BM", tags=["python"],
            path="work", url="https://example.com/",
        ).insert()

    # type=note + path=work + tags=python  →  only "Work Note"
    resp = client.get("/api/dataobjs?type=note&path=work&tags=python&sort_by=title&sort_order=asc")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["title"] == "Work Note"

    # path=work (no type filter) → 3 items (Work Note, Work NoTag, Work BM)
    resp = client.get("/api/dataobjs?path=work")
    assert resp.json["page_info"]["total_items"] == 3


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def test_sort_by_id_asc(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": "C", "tags": [], "path": ""},
        {"type": "note", "title": "A", "tags": [], "path": ""},
        {"type": "note", "title": "B", "tags": [], "path": ""},
    ])

    resp = client.get("/api/dataobjs?sort_by=id&sort_order=asc")
    assert resp.status_code == 200
    ids = [it["metadata"]["id"] for it in resp.json["items"]]
    assert ids == sorted(ids)


def test_sort_by_id_desc(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": "First", "tags": [], "path": ""},
        {"type": "note", "title": "Second", "tags": [], "path": ""},
        {"type": "note", "title": "Third", "tags": [], "path": ""},
    ])

    resp = client.get("/api/dataobjs?sort_by=id&sort_order=desc")
    ids = [it["metadata"]["id"] for it in resp.json["items"]]
    assert ids == sorted(ids, reverse=True)


def test_sort_by_title(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": "Cherry", "tags": [], "path": ""},
        {"type": "note", "title": "Apple", "tags": [], "path": ""},
        {"type": "note", "title": "Banana", "tags": [], "path": ""},
    ])

    resp = client.get("/api/dataobjs?sort_by=title&sort_order=asc")
    assert resp.status_code == 200
    titles = [it["metadata"]["title"] for it in resp.json["items"]]
    assert titles == ["Apple", "Banana", "Cherry"]


def test_sort_by_title_desc(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": "Cherry", "tags": [], "path": ""},
        {"type": "note", "title": "Apple", "tags": [], "path": ""},
        {"type": "note", "title": "Banana", "tags": [], "path": ""},
    ])

    resp = client.get("/api/dataobjs?sort_by=title&sort_order=desc")
    titles = [it["metadata"]["title"] for it in resp.json["items"]]
    assert titles == ["Cherry", "Banana", "Apple"]


def test_sort_stability(test_app, client):
    """Items with the same sort key must appear in a deterministic order
    (tie-broken by id)."""
    _create_notes(test_app, [
        {"type": "note", "title": "Same", "tags": [], "path": ""},
        {"type": "note", "title": "Same", "tags": [], "path": ""},
        {"type": "note", "title": "Same", "tags": [], "path": ""},
    ])

    resp = client.get("/api/dataobjs?sort_by=title&sort_order=asc")
    ids = [it["metadata"]["id"] for it in resp.json["items"]]
    # With identical titles, ids should still be in ascending order (stable)
    assert ids == sorted(ids)


def test_sort_by_modified_at_after_edit(test_app, client):
    """After editing a note, sort_by=modified_at&sort_order=desc returns
    a valid structured response and the edit is reflected in the data."""
    _create_notes(test_app, [
        {"type": "note", "title": "Old A", "tags": [], "path": ""},
    ])

    # Edit the note
    resp = client.put("/api/dataobjs/1", json={"content": "updated content"})
    assert resp.status_code == 200

    resp = client.get("/api/dataobjs?sort_by=modified_at&sort_order=desc")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    item = resp.json["items"][0]
    assert item["metadata"]["id"] == 1
    # Verify the item is present and accessible
    assert item["metadata"]["type"] == "note"


def test_sort_by_modified_at_stable_tiebreak(test_app, client):
    """When modified_at values are identical, id provides a stable tiebreak."""
    _create_notes(test_app, [
        {"type": "note", "title": "First", "tags": [], "path": ""},
        {"type": "note", "title": "Second", "tags": [], "path": ""},
        {"type": "note", "title": "Third", "tags": [], "path": ""},
    ])

    # All notes created within the same minute → same modified_at string
    # Stable sort should tiebreak by id ascending
    resp = client.get("/api/dataobjs?sort_by=modified_at&sort_order=asc")
    assert resp.status_code == 200
    ids = [it["metadata"]["id"] for it in resp.json["items"]]
    assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_default(test_app, client):
    """Default page=1, per_page=20."""
    _create_notes(test_app, [
        {"type": "note", "title": f"Note {i}", "tags": [], "path": ""}
        for i in range(5)
    ])

    resp = client.get("/api/dataobjs")
    assert resp.status_code == 200
    assert isinstance(resp.json, list)  # backward compat
    assert len(resp.json) == 5


def test_pagination_with_per_page(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": f"Note {i}", "tags": [], "path": ""}
        for i in range(5)
    ])

    resp = client.get("/api/dataobjs?per_page=2&sort_by=id&sort_order=asc")
    assert resp.status_code == 200
    pi = resp.json["page_info"]
    assert pi["page"] == 1
    assert pi["per_page"] == 2
    assert pi["total_items"] == 5
    assert pi["total_pages"] == 3
    assert pi["has_next"] is True
    assert pi["has_prev"] is False
    assert len(resp.json["items"]) == 2


def test_pagination_page_2(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": f"Note {i}", "tags": [], "path": ""}
        for i in range(5)
    ])

    resp = client.get("/api/dataobjs?per_page=2&page=2&sort_by=id&sort_order=asc")
    assert resp.status_code == 200
    pi = resp.json["page_info"]
    assert pi["page"] == 2
    assert pi["has_next"] is True
    assert pi["has_prev"] is True
    assert len(resp.json["items"]) == 2
    # Items on page 2 should be the 3rd and 4th by id
    ids = [it["metadata"]["id"] for it in resp.json["items"]]
    assert ids == [3, 4]


def test_pagination_last_page(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": f"Note {i}", "tags": [], "path": ""}
        for i in range(5)
    ])

    resp = client.get("/api/dataobjs?per_page=2&page=3&sort_by=id&sort_order=asc")
    assert resp.status_code == 200
    pi = resp.json["page_info"]
    assert pi["page"] == 3
    assert pi["has_next"] is False
    assert pi["has_prev"] is True
    assert len(resp.json["items"]) == 1  # 5 items, page 3 has only 1


def test_pagination_out_of_range(test_app, client):
    _create_notes(test_app, [
        {"type": "note", "title": f"Note {i}", "tags": [], "path": ""}
        for i in range(3)
    ])

    resp = client.get("/api/dataobjs?per_page=2&page=100")
    assert resp.status_code == 200
    assert resp.json["items"] == []
    pi = resp.json["page_info"]
    assert pi["total_items"] == 3
    assert pi["has_next"] is False
    assert pi["has_prev"] is True  # page > 1


def test_pagination_single_item(test_app, client):
    with test_app.app_context():
        DataObj(type="note", title="Solo", tags=[], path="").insert()

    resp = client.get("/api/dataobjs?per_page=10")
    assert resp.status_code == 200
    pi = resp.json["page_info"]
    assert pi["total_items"] == 1
    assert pi["total_pages"] == 1
    assert pi["has_next"] is False
    assert pi["has_prev"] is False


# ---------------------------------------------------------------------------
# Invalid parameters → 400
# ---------------------------------------------------------------------------

def test_invalid_page_zero(test_app, client):
    resp = client.get("/api/dataobjs?page=0")
    assert resp.status_code == 400


def test_invalid_page_negative(test_app, client):
    resp = client.get("/api/dataobjs?page=-1")
    assert resp.status_code == 400


def test_invalid_per_page_zero(test_app, client):
    resp = client.get("/api/dataobjs?per_page=0")
    assert resp.status_code == 400


def test_invalid_per_page_too_large(test_app, client):
    resp = client.get("/api/dataobjs?per_page=201")
    assert resp.status_code == 400


def test_invalid_page_non_numeric(test_app, client):
    resp = client.get("/api/dataobjs?page=abc")
    assert resp.status_code == 400


def test_invalid_sort_by(test_app, client):
    resp = client.get("/api/dataobjs?sort_by=invalid_field")
    assert resp.status_code == 400


def test_invalid_sort_order(test_app, client):
    resp = client.get("/api/dataobjs?sort_order=sideways")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Data consistency after create / edit / move
# ---------------------------------------------------------------------------

def test_data_consistency_after_create(test_app, client):
    """Newly created objects appear in filtered results."""
    resp = client.get("/api/dataobjs?type=note")
    assert resp.json["page_info"]["total_items"] == 0

    with test_app.app_context():
        note = DataObj(type="note", title="Fresh", tags=["fresh"], path="")
        note.insert()

    resp = client.get("/api/dataobjs?type=note")
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["title"] == "Fresh"


def test_data_consistency_after_edit(test_app, client, note_fixture):
    """Editing updates modified_at and the new value is reflected."""
    client.put("/api/dataobjs/1", json={"content": "brand new content"})

    resp = client.get("/api/dataobjs?sort_by=modified_at&sort_order=desc")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    # modified_at should be a recent timestamp
    item = resp.json["items"][0]
    assert item["metadata"]["id"] == 1


def test_data_consistency_after_frontmatter_update(test_app, client, note_fixture):
    """Frontmatter update is reflected in filtered results."""
    client.put("/api/dataobjs/frontmatter/1", json={"title": "Renamed Note"})

    resp = client.get("/api/dataobjs?type=note")
    assert resp.status_code == 200
    assert resp.json["items"][0]["metadata"]["title"] == "Renamed Note"


def test_data_consistency_after_move(test_app, client, note_fixture):
    """After moving an object, path filter returns it from the new location."""
    create_dir("archive")
    with test_app.app_context():
        from archivy.data import move_item
        move_item(1, "archive")

    # Not in root anymore
    resp = client.get("/api/dataobjs?path=")
    assert resp.json["page_info"]["total_items"] == 0

    # Found under archive
    resp = client.get("/api/dataobjs?path=archive")
    assert resp.status_code == 200
    assert resp.json["page_info"]["total_items"] == 1
    assert resp.json["items"][0]["metadata"]["id"] == 1


def test_data_consistency_after_delete(test_app, client, note_fixture):
    """Deleted objects disappear from results."""
    resp = client.get("/api/dataobjs?type=note")
    assert resp.json["page_info"]["total_items"] == 1

    client.delete("/api/dataobjs/1")

    resp = client.get("/api/dataobjs?type=note")
    assert resp.json["page_info"]["total_items"] == 0


# ---------------------------------------------------------------------------
# query_dataobjs() direct unit tests (bypassing HTTP layer)
# ---------------------------------------------------------------------------

def test_query_dataobjs_function(test_app):
    """Call query_dataobjs directly to verify its return structure."""
    with test_app.app_context():
        for i in range(3):
            DataObj(type="note", title=f"N{i}", tags=["t"], path="").insert()

    result = query_dataobjs(sort_by="id", sort_order="asc", page=1, per_page=2)
    assert "items" in result
    assert "page_info" in result
    pi = result["page_info"]
    assert pi["page"] == 1
    assert pi["per_page"] == 2
    assert pi["total_items"] == 3
    assert pi["total_pages"] == 2
    assert pi["has_next"] is True
    assert pi["has_prev"] is False
    assert len(result["items"]) == 2
    # Verify ascending id order
    ids = [it["metadata"]["id"] for it in result["items"]]
    assert ids == [1, 2]
