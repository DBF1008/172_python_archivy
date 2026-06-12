from flask import Response, jsonify, request, Blueprint, current_app
from werkzeug.security import check_password_hash
from flask_login import login_user
from tinydb import Query

from archivy import data, tags
from archivy.search import search
from archivy.models import DataObj, User
from archivy.helpers import get_db


api_bp = Blueprint("api", __name__)


@api_bp.route("/login", methods=["POST"])
def login():
    """
    Logs in the API client using
    [HTTP Basic Auth](https://en.wikipedia.org/wiki/Basic_access_authentication).
    Pass in the username and password of your account.
    """
    db = get_db()
    user = db.search(Query().username == request.authorization["username"])
    if user and check_password_hash(
        user[0]["hashed_password"], request.authorization["password"]
    ):
        # user is verified so we can log him in from the db
        user = User.from_db(user[0])
        login_user(user, remember=True)
        return Response(status=200)
    return Response(status=401)


@api_bp.route("/bookmarks", methods=["POST"])
def create_bookmark():
    """
    Creates a new bookmark

    **Parameters:**

    All parameters are sent through the JSON body.
    - **url** (required)
    - **tags**
    - **path**
    """
    json_data = request.get_json()
    bookmark = DataObj(
        url=json_data["url"],
        tags=json_data.get("tags", []),
        path=json_data.get("path", current_app.config["DEFAULT_BOOKMARKS_DIR"]),
        type="bookmark",
    )
    bookmark.process_bookmark_url()
    bookmark_id = bookmark.insert()
    if bookmark_id:
        return jsonify(
            bookmark_id=bookmark_id,
        )
    return Response(status=400)


@api_bp.route("/notes", methods=["POST"])
def create_note():
    """
    Creates a new note.

    **Parameters:**

    All parameters are sent through the JSON body.
    - **title** (required)
    - **content** (required)
    - **tags**
    - **path**
    """
    json_data = request.get_json()
    note = DataObj(
        title=json_data["title"],
        content=json_data["content"],
        path=json_data.get("path", ""),
        tags=json_data.get("tags", []),
        type="note",
    )

    note_id = note.insert()
    if note_id:
        return jsonify(note_id=note_id)
    return Response(status=400)


@api_bp.route("/dataobjs/<int:dataobj_id>")
def get_dataobj(dataobj_id):
    """Returns dataobj of given id"""
    dataobj = data.get_item(dataobj_id)

    return (
        jsonify(
            dataobj_id=dataobj_id,
            title=dataobj["title"],
            content=dataobj.content,
            md_path=dataobj["fullpath"],
        )
        if dataobj
        else Response(status=404)
    )


@api_bp.route("/dataobjs/<int:dataobj_id>", methods=["DELETE"])
def delete_dataobj(dataobj_id):
    """Deletes object of given id"""
    if not data.get_item(dataobj_id):
        return Response(status=404)
    data.delete_item(dataobj_id)
    return Response(status=204)


@api_bp.route("/dataobjs/<int:dataobj_id>", methods=["PUT"])
def update_dataobj(dataobj_id):
    """
    Updates object of given id.

    Paramter in JSON body:

    - **content**: markdown text of new dataobj.
    """
    if request.json.get("content"):
        try:
            data.update_item_md(dataobj_id, request.json.get("content"))
            return Response(status=200)
        except BaseException:
            return Response(status=404)
    return Response("Must provide content parameter", status=401)


@api_bp.route("/dataobjs/frontmatter/<int:dataobj_id>", methods=["PUT"])
def update_dataobj_frontmatter(dataobj_id):
    """
    Updates frontmatter of object of given id.

    Paramter in JSON body:

    - **title**: the new title of the dataobj.
    """

    new_frontmatter = {
        "title": request.json.get("title"),
    }

    try:
        data.update_item_frontmatter(dataobj_id, new_frontmatter)
        return Response(status=200)
    except BaseException:
        return Response(status=404)


@api_bp.route("/dataobjs", methods=["GET"])
def get_dataobjs():
    """
    Returns a filtered, sorted and paginated list of dataobjs.

    All parameters are optional URL query parameters. With no parameters, every
    dataobj is returned on a single page, sorted by `modified_at` descending.

    **Filtering**

    - **path**: only return dataobjs located in this directory or any of its
      subdirectories. The directory is resolved from each object's real
      location on disk, so results stay correct after objects are moved.
    - **type**: filter by type (eg. `note`, `bookmark`). May be repeated
      (`?type=note&type=bookmark`); an object matches if its type is any of
      the given ones.
    - **tags**: filter by tag. May be repeated (`?tags=a&tags=b`); an object
      matches only if it contains *all* of the requested tags.

    **Sorting**

    - **sort**: field to sort by, one of `modified_at` (default), `date`,
      `title`, `id`.
    - **order**: `desc` (default) or `asc`. Ties are always broken by ascending
      id, so ordering is stable across calls.

    **Pagination**

    - **page**: 1-indexed page number (default `1`).
    - **per_page**: number of items per page (1-100). If omitted, every matching
      item is returned on a single page.

    The response is a JSON object with two keys:

    - **data**: list of dataobjs for the requested page.
    - **pagination**: metadata with `page`, `per_page`, `total_items`,
      `total_pages`, `has_next`, `has_prev`, `sort` and `order`.

    Returns `400` if any parameter is invalid.
    """
    types = request.args.getlist("type")
    tags = request.args.getlist("tags")
    path = request.args.get("path", "")

    sort_by = request.args.get("sort", "modified_at")
    if sort_by not in data.SORTABLE_FIELDS:
        return Response(
            f"Invalid sort field. Must be one of: {', '.join(data.SORTABLE_FIELDS)}.",
            status=400,
        )

    order = request.args.get("order", "desc").lower()
    if order not in ("asc", "desc"):
        return Response("Invalid order. Must be 'asc' or 'desc'.", status=400)

    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        return Response("page must be an integer.", status=400)
    if page < 1:
        return Response("page must be >= 1.", status=400)

    per_page = request.args.get("per_page")
    if per_page is not None:
        try:
            per_page = int(per_page)
        except ValueError:
            return Response("per_page must be an integer.", status=400)
        if per_page < 1 or per_page > data.MAX_PER_PAGE:
            return Response(
                f"per_page must be between 1 and {data.MAX_PER_PAGE}.", status=400
            )

    result = data.query_dataobjs(
        types=types,
        tags=tags,
        path=path,
        sort_by=sort_by,
        order=order,
        page=page,
        per_page=per_page,
    )
    return jsonify(result)


@api_bp.route("/tags/add_to_index", methods=["PUT"])
def add_tag_to_index():
    """Add a tag to the database."""
    tag = request.json.get("tag", False)
    if tag and type(tag) is str and tags.is_tag_format(tag):
        if tags.add_tag_to_index(tag):
            return Response(status=200)
        else:
            return Response(status=404)

    return Response("Must provide valid tag name.", status=401)


@api_bp.route("/dataobj/local_edit/<dataobj_id>", methods=["GET"])
def local_edit(dataobj_id):
    dataobj = data.get_item(dataobj_id)
    if dataobj:
        data.open_file(dataobj["fullpath"])
        return Response(status=200)
    return Response(status=404)


@api_bp.route("/folders/new", methods=["POST"])
def create_folder():
    """
    Creates new directory

    Parameter in JSON body:
    - **path** (required) - path of newdir
    """
    directory = request.json.get("path")
    try:
        sanitized_name = data.create_dir(directory)
        if not sanitized_name:
            return Response("Invalid dirname", status=400)
    except FileExistsError:
        return Response("Directory already exists", status=400)
    return Response(sanitized_name, status=200)


@api_bp.route("/folders/delete", methods=["DELETE"])
def delete_folder():
    """
    Deletes directory.

    Parameter in JSON body:
    - **path** of dir to delete
    """
    directory = request.json.get("path")
    if directory == "":
        return Response("Cannot delete root dir", status=401)
    if data.delete_dir(directory):
        return Response("Successfully deleted", status=200)
    return Response("Could not delete directory", status=400)


@api_bp.route("/search", methods=["GET"])
def search_endpoint():
    """
    Searches the instance.

    Request URL Parameter:
    - **query**
    """
    if not current_app.config["SEARCH_CONF"]["enabled"]:
        return Response("Search is disabled", status=401)
    query = request.args.get("query")
    search_results = search(query)
    return jsonify(search_results)


@api_bp.route("/images", methods=["POST"])
def image_upload():
    CONTENT_TYPES = ["image/jpeg", "image/png", "image/gif"]
    if "image" not in request.files:
        return jsonify({"error": "400"}), 400
    image = request.files["image"]
    if (
        data.valid_image_filename(image.filename)
        and image.headers["Content-Type"].strip() in CONTENT_TYPES
    ):
        saved_to = data.save_image(image)
        return jsonify({"data": {"filePath": f"/images/{saved_to}"}}), 200
    return jsonify({"error": "415"}), 415
