"""Regression tests for file-rename-on-title-change.

When a dataobj's title is updated via the frontmatter API the file on
disk must be renamed so the slug in the filename stays in sync with the
frontmatter title.  This keeps ripgrep search results, the md_path
returned by the API, and local editing paths consistent.
"""

from pathlib import Path

from flask.testing import FlaskClient

from archivy.data import create_dir, get_by_id, get_data_dir, get_item
from archivy.models import DataObj


# ---------------------------------------------------------------------------
# Root directory rename
# ---------------------------------------------------------------------------

def test_rename_in_root_dir(test_app, client: FlaskClient, note_fixture):
    """Renaming a note in the root data dir renames the file on disk."""
    resp = client.put("/api/dataobjs/frontmatter/1", json={"title": "New Title"})
    assert resp.status_code == 200
    assert resp.json["title"] == "New Title"

    file = get_by_id(1)
    assert file is not None
    assert "New_Title" in file.name
    # The old slug must be gone
    assert "Test_Note" not in file.name


def test_rename_api_returns_updated_md_path(test_app, client: FlaskClient, note_fixture):
    """The frontmatter PUT endpoint returns the new md_path."""
    resp = client.put("/api/dataobjs/frontmatter/1", json={"title": "New Title"})
    assert resp.status_code == 200
    assert "New_Title" in resp.json["md_path"]


# ---------------------------------------------------------------------------
# Subdirectory rename
# ---------------------------------------------------------------------------

def test_rename_in_subdir(test_app, client: FlaskClient):
    """Renaming a note in a subdirectory keeps it in the same subdirectory."""
    create_dir("subdir")
    note = DataObj(type="note", title="Subdir Note", path="subdir")
    note.insert()

    resp = client.put(
        f"/api/dataobjs/frontmatter/{note.id}",
        json={"title": "Renamed Subdir Note"},
    )
    assert resp.status_code == 200

    file = get_by_id(note.id)
    assert file is not None
    assert "Renamed_Subdir_Note" in file.name
    assert file.parent.name == "subdir"

    item = get_item(note.id)
    assert item["dir"] == "subdir"


# ---------------------------------------------------------------------------
# Repeated rename
# ---------------------------------------------------------------------------

def test_repeated_rename(test_app, client: FlaskClient, note_fixture):
    """Renaming a note twice should work each time."""
    client.put("/api/dataobjs/frontmatter/1", json={"title": "First Rename"})

    file = get_by_id(1)
    assert "First_Rename" in file.name

    client.put("/api/dataobjs/frontmatter/1", json={"title": "Second Rename"})

    file = get_by_id(1)
    assert "Second_Rename" in file.name
    assert "First_Rename" not in file.name


def test_repeated_rename_three_times(test_app, client: FlaskClient, note_fixture):
    """Three consecutive renames should all succeed."""
    titles = ["Alpha Title", "Beta Title", "Gamma Title"]
    for title in titles:
        resp = client.put("/api/dataobjs/frontmatter/1", json={"title": title})
        assert resp.status_code == 200
        file = get_by_id(1)
        slug = title.replace(" ", "_")
        assert slug in file.name


# ---------------------------------------------------------------------------
# Rename then edit content
# ---------------------------------------------------------------------------

def test_rename_then_edit_content(test_app, client: FlaskClient, note_fixture):
    """After renaming, editing content should write to the renamed file."""
    client.put("/api/dataobjs/frontmatter/1", json={"title": "Renamed Note"})

    new_content = "Updated content after rename"
    resp = client.put("/api/dataobjs/1", json={"content": new_content})
    assert resp.status_code == 200

    resp = client.get("/api/dataobjs/1")
    assert resp.json["content"] == new_content
    assert resp.json["title"] == "Renamed Note"

    # The file on disk should be the renamed one
    file = get_by_id(1)
    assert "Renamed_Note" in file.name


# ---------------------------------------------------------------------------
# Rename then query (API)
# ---------------------------------------------------------------------------

def test_rename_then_query_api(test_app, client: FlaskClient, note_fixture):
    """GET /api/dataobjs/<id> returns the new title and md_path after rename."""
    client.put("/api/dataobjs/frontmatter/1", json={"title": "Query Title"})

    resp = client.get("/api/dataobjs/1")
    assert resp.status_code == 200
    assert resp.json["title"] == "Query Title"
    assert "Query_Title" in resp.json["md_path"]


# ---------------------------------------------------------------------------
# Rename then search (ripgrep)
# ---------------------------------------------------------------------------

def test_rename_then_search_ripgrep(test_app, client: FlaskClient, note_fixture):
    """Ripgrep search returns the new title parsed from the renamed file."""
    test_app.config["SEARCH_CONF"]["engine"] = "ripgrep"
    test_app.config["SEARCH_CONF"]["enabled"] = 1

    # Put searchable content in the note
    client.put("/api/dataobjs/1", json={"content": "unique_searchable_token_xyz"})

    client.put("/api/dataobjs/frontmatter/1", json={"title": "Fresh Title"})

    resp = client.get("/api/search?query=unique_searchable_token_xyz")
    assert resp.status_code == 200
    assert len(resp.json) >= 1

    hit = resp.json[0]
    assert hit["id"] == 1
    # Title should come from the NEW filename, not the old one
    assert "Fresh" in hit["title"]
    assert "Test" not in hit["title"]

    test_app.config["SEARCH_CONF"]["enabled"] = 0


# ---------------------------------------------------------------------------
# Rename to same title (no-op)
# ---------------------------------------------------------------------------

def test_rename_to_same_title_is_noop(test_app, client: FlaskClient, note_fixture):
    """Renaming to the same title should not change the file path."""
    file_before = get_by_id(1)
    path_before = file_before

    resp = client.put("/api/dataobjs/frontmatter/1", json={"title": "Test Note"})
    assert resp.status_code == 200

    file_after = get_by_id(1)
    assert file_after.name == path_before.name


# ---------------------------------------------------------------------------
# md_path consistency across operations
# ---------------------------------------------------------------------------

def test_md_path_consistent_after_rename_and_edit(
    test_app, client: FlaskClient, note_fixture
):
    """md_path stays consistent through rename, content edit, and re-query."""
    # Rename
    rename_resp = client.put(
        "/api/dataobjs/frontmatter/1", json={"title": "Consistent Title"}
    )
    assert rename_resp.status_code == 200
    rename_md_path = rename_resp.json["md_path"]

    # Edit content
    client.put("/api/dataobjs/1", json={"content": "some new content"})

    # Query
    get_resp = client.get("/api/dataobjs/1")
    assert get_resp.json["md_path"] == rename_md_path
    assert get_resp.json["title"] == "Consistent Title"

    # File on disk should match
    file = get_by_id(1)
    assert str(file).endswith(rename_md_path.split("/")[-1])


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_rename_with_special_characters(test_app, client: FlaskClient, note_fixture):
    """Titles with special characters are sanitized by secure_filename."""
    resp = client.put(
        "/api/dataobjs/frontmatter/1",
        json={"title": "Hello/World: Test!"},
    )
    assert resp.status_code == 200

    file = get_by_id(1)
    assert file is not None
    # secure_filename should strip dangerous chars
    assert "/" not in file.stem.split("-", 1)[-1]
    assert ":" not in file.name


def test_rename_preserves_content_and_tags(test_app, client: FlaskClient, note_fixture):
    """Renaming should not lose content or tags."""
    # Set some content first
    client.put("/api/dataobjs/1", json={"content": "Preserved content body"})

    # Rename
    client.put("/api/dataobjs/frontmatter/1", json={"title": "Preserved Title"})

    # Verify content and tags survived
    resp = client.get("/api/dataobjs/1")
    assert resp.json["content"] == "Preserved content body"
    assert resp.json["title"] == "Preserved Title"


def test_rename_does_not_move_to_different_dir(
    test_app, client: FlaskClient, note_fixture
):
    """Renaming should keep the file in its original directory."""
    item_before = get_item(1)
    dir_before = item_before["dir"]

    client.put("/api/dataobjs/frontmatter/1", json={"title": "Still Here"})

    item_after = get_item(1)
    assert item_after["dir"] == dir_before
