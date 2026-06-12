import platform
import subprocess
import os
import shutil
from math import ceil
from pathlib import Path
from datetime import datetime

import frontmatter
from flask import current_app
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage

from archivy.search import remove_from_index


# FIXME: ugly hack to make sure the app path is evaluated at the right time
def get_data_dir():
    """Returns the directory where dataobjs are stored"""
    return Path(current_app.config["USER_DIR"]) / "data"


def is_relative_to(sub_path, parent):
    """Implement pathlib `is_relative_to` only available in python 3.9"""
    try:
        parent_path = Path(parent).resolve()
        sub_path.resolve().relative_to(parent_path)
        return True
    except ValueError:
        return False


class Directory:
    """Tree like file-structure used to build file navigation in Archiv"""

    def __init__(self, name):
        self.name = name
        self.child_files = []
        self.child_dirs = {}


FILE_GLOB = "[0-9]*-*.md"


def get_by_id(dataobj_id):
    """Returns filename of dataobj of given id"""
    results = list(get_data_dir().rglob(f"{dataobj_id}-*.md"))
    return results[0] if results else None


def load_frontmatter(filepath, load_content=False):
    if load_content:
        return frontmatter.load(filepath.open("r"))
    count = 0
    file = open(filepath, "r")
    data = ""
    line = "_"
    while count != 2 and line:
        line = file.readline()
        if line in ["---\n", "---"]:
            count += 1
        data += line
    data = frontmatter.loads(data)
    return data


def build_dir_tree(path, query_dir, load_content=False):
    """
    Builds a structured tree of directories and data objects.

    - **path**: name of the directory relative to the root directory.
    - **query_dir**: absolute path of the directory we're building the tree of.
    """
    datacont = Directory(path or "root")
    for filepath in query_dir.rglob("*"):
        current_path = filepath.relative_to(query_dir)
        current_dir = datacont

        # iterate through parent directories
        for segment in current_path.parts[:-1]:
            # directory has not been saved in tree yet
            if segment not in current_dir.child_dirs:
                current_dir.child_dirs[segment] = Directory(segment)
            current_dir = current_dir.child_dirs[segment]

        # handle last part of current_path
        last_seg = current_path.parts[-1]
        if filepath.is_dir():
            if last_seg not in current_dir.child_dirs:
                current_dir.child_dirs[last_seg] = Directory(last_seg)
            current_dir = current_dir.child_dirs[last_seg]
        elif last_seg.endswith(".md"):
            data = load_frontmatter(filepath, load_content=load_content)
            current_dir.child_files.append(data)
    return datacont


def get_items(
    collections=[], path="", structured=True, json_format=False, load_content=False
):
    """
    Gets all dataobjs.

    Parameters:

    - **collections** - filter dataobj by type, eg. bookmark / note
    - **path** - filter by path
    - **structured: if set to True, will return a Directory object, otherwise
      data will just be returned as a list of dataobjs
    - **json_format**: boolean value used internally to pre-process dataobjs
      to send back a json response.
    - **load_content**: internal value to disregard post content and not save them in memory if they won't be accessed.
    """
    data_dir = get_data_dir()
    query_dir = data_dir / path
    if not is_relative_to(query_dir, data_dir) or not query_dir.exists():
        raise FileNotFoundError
    if structured:
        return build_dir_tree(path, query_dir, load_content=load_content)
    else:
        datacont = []
        for filepath in query_dir.rglob("*.md"):
            data = load_frontmatter(filepath, load_content=load_content)
            data["fullpath"] = str(filepath.parent.relative_to(query_dir))
            if len(collections) == 0 or any(
                [collection == data["type"] for collection in collections]
            ):
                if json_format:
                    dict_dataobj = data.__dict__
                    # remove unnecessary yaml handler
                    dict_dataobj.pop("handler")
                    datacont.append(dict_dataobj)
                else:
                    datacont.append(data)
        return datacont


# Fields that `query_dataobjs` knows how to sort by.
SORTABLE_FIELDS = ("modified_at", "date", "title", "id")
# Upper bound on the page size a client may request.
MAX_PER_PAGE = 100


def normalize_dir(dirpath):
    """Normalize a dataobj's directory.

    The root directory is represented as an empty string (``build_dir_tree``
    /``get_items`` return ``"."`` for it) and any surrounding slashes are
    stripped so directories can be compared reliably.
    """
    if dirpath in (".", "", None):
        return ""
    return str(dirpath).strip("/")


def _sort_key(metadata, sort_by):
    """Return a comparable value for ``sort_by`` from a dataobj's metadata.

    Date fields are parsed into ``datetime`` objects so they are ordered
    chronologically rather than lexicographically. Missing or malformed values
    fall back to a minimum value so a single bad file never breaks ordering.
    """
    if sort_by == "title":
        return str(metadata.get("title", "")).lower()
    if sort_by == "id":
        try:
            return int(metadata.get("id"))
        except (TypeError, ValueError):
            return -1
    raw = metadata.get(sort_by)
    if not raw:
        return datetime.min
    try:
        if sort_by == "modified_at":
            return datetime.strptime(str(raw), "%x %H:%M")
        # `date` is stored with '-' separators (see DataObj.insert).
        return datetime.strptime(str(raw).replace("-", "/"), "%x")
    except (ValueError, TypeError):
        return datetime.min


def query_dataobjs(
    types=None,
    tags=None,
    path="",
    sort_by="modified_at",
    order="desc",
    page=1,
    per_page=None,
):
    """
    Returns a filtered, sorted and paginated page of dataobjs together with
    pagination metadata.

    Parameters:

    - **types**: list of types to keep (eg. ``["note", "bookmark"]``). An
      object matches if its type is any of them. Empty/``None`` means no type
      filter.
    - **tags**: list of tags. An object matches only if it contains *all* of
      the given tags (logical AND). Empty/``None`` means no tag filter.
    - **path**: directory to restrict results to. The object's directory is
      resolved from its real location on disk, so objects in subdirectories are
      included and results stay correct after items are moved. Empty means no
      directory filter.
    - **sort_by**: one of :data:`SORTABLE_FIELDS`.
    - **order**: ``"asc"`` or ``"desc"``.
    - **page**: 1-indexed page number.
    - **per_page**: page size. If ``None``, every matching object is returned on
      a single page.

    Returns a dict with:

    - **data**: list of dataobjs for the requested page (same per-object shape
      as :func:`get_items` with ``json_format=True``).
    - **pagination**: dict with ``page``, ``per_page``, ``total_items``,
      ``total_pages``, ``has_next``, ``has_prev``, ``sort`` and ``order``.
    """
    types = types or []
    tags = tags or []
    path = normalize_dir(path)
    reverse = order == "desc"

    items = get_items(structured=False, json_format=True)

    filtered = []
    for item in items:
        metadata = item["metadata"]
        item_dir = normalize_dir(metadata.get("fullpath"))
        # recursive directory match: the object is in `path` or a subdir of it
        if path and item_dir != path and not item_dir.startswith(path + "/"):
            continue
        if types and metadata.get("type") not in types:
            continue
        if tags:
            item_tags = metadata.get("tags") or []
            if not all(tag in item_tags for tag in tags):
                continue
        filtered.append(item)

    # Stable, deterministic ordering: first sort by ascending id, then by the
    # requested key. Python's sort is stable, so objects that compare equal on
    # the requested key keep their id-ascending order in both asc and desc.
    filtered.sort(key=lambda it: _sort_key(it["metadata"], "id"))
    filtered.sort(key=lambda it: _sort_key(it["metadata"], sort_by), reverse=reverse)

    total_items = len(filtered)
    effective_per_page = per_page if per_page else (total_items or 1)
    total_pages = ceil(total_items / effective_per_page) if total_items else 0
    page = max(page, 1)
    start = (page - 1) * effective_per_page
    page_items = filtered[start : start + effective_per_page]

    return {
        "data": page_items,
        "pagination": {
            "page": page,
            "per_page": effective_per_page,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1 and total_items > 0,
            "sort": sort_by,
            "order": order,
        },
    }


def create(contents, title, path=""):
    """
    Helper method to save a new dataobj onto the filesystem.

    Parameters:

    - **contents**: md file contents
    - **title** - title used for filename
    - **path**
    """
    filename = secure_filename(title)
    data_dir = get_data_dir()
    max_filename_length = 255
    if len(filename + ".md") > max_filename_length:
        filename = filename[0 : max_filename_length - 3]
    if not is_relative_to(data_dir / path, data_dir):
        path = ""
    path_to_md_file = data_dir / path / f"{filename}.md"
    with open(path_to_md_file, "w", encoding="utf-8") as file:
        file.write(contents)

    return path_to_md_file


def get_item(dataobj_id):
    """Returns a Post object with the given dataobjs' attributes"""
    file = get_by_id(dataobj_id)
    if file:
        data = frontmatter.load(file)
        data["fullpath"] = str(file)
        data["dir"] = str(file.parent.relative_to(get_data_dir()))
        # replace . for root items to ''
        if data["dir"] == ".":
            data["dir"] = ""
        return data
    return None


def move_item(dataobj_id, new_path):
    """Move dataobj of given id to new_path"""
    file = get_by_id(dataobj_id)
    data_dir = get_data_dir()
    out_dir = (data_dir / new_path).resolve()
    if not file:
        raise FileNotFoundError
    if (out_dir / file.parts[-1]).exists():
        raise FileExistsError
    elif is_relative_to(out_dir, data_dir) and out_dir.exists():  # check file isn't
        return shutil.move(str(file), f"{get_data_dir()}/{new_path}/")
    return False


def rename_folder(old_path, new_name):
    data_dir = get_data_dir()
    curr_dir = (data_dir / old_path).resolve()
    suggested_renaming = (curr_dir.parent / new_name).resolve()
    if (
        not is_relative_to(suggested_renaming, data_dir)
        or curr_dir == data_dir
        or suggested_renaming == data_dir
        or not is_relative_to(curr_dir, data_dir)
        or not suggested_renaming.parent.is_dir()
    ):
        return False  # invalid inputs
    if not curr_dir.is_dir():
        raise FileNotFoundError
    if suggested_renaming.exists():
        raise FileExistsError
    curr_dir.rename(suggested_renaming)
    return str(suggested_renaming.relative_to(data_dir))


def delete_item(dataobj_id):
    """Delete dataobj of given id"""
    file = get_by_id(dataobj_id)
    remove_from_index(dataobj_id)
    if file:
        Path(file).unlink()


def update_item_md(dataobj_id, new_content):
    """
    Given an object id, this method overwrites the inner
    content of the post with `new_content`.

    This means that it won't change the frontmatter (eg tags, id, title)
    but it can change the file content.

    For example:

    If we have a dataobj like this:

    ```md
    ---
    id: 1
    title: Note
    ---

    # This is random
    ```

    Calling `update_item(1, "# This is specific")` will turn it into:


    ```md
    ---
    id: 1 # unchanged
    title: Note
    ---

    # This is specific
    ```
    """

    from archivy.models import DataObj

    filename = get_by_id(dataobj_id)
    dataobj = frontmatter.load(filename)
    dataobj["modified_at"] = datetime.now().strftime("%x %H:%M")
    dataobj.content = new_content
    md = frontmatter.dumps(dataobj)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(md)

    converted_dataobj = DataObj.from_md(md)
    converted_dataobj.fullpath = str(
        filename.relative_to(current_app.config["USER_DIR"])
    )
    converted_dataobj.index()
    current_app.config["HOOKS"].on_edit(converted_dataobj)


def update_item_frontmatter(dataobj_id, new_frontmatter):
    """
    Given an object id, this method overwrites the front matter
    of the post with `new_frontmatter`.

    ---
    date: Str
    id: Str
    path: Str
    tags: List[Str]
    title: Str
    type: note/bookmark
    ---
    """

    from archivy.models import DataObj

    filename = get_by_id(dataobj_id)
    dataobj = frontmatter.load(filename)
    for key in list(new_frontmatter):
        dataobj[key] = new_frontmatter[key]
    dataobj["modified_at"] = datetime.now().strftime("%x %H:%M")
    md = frontmatter.dumps(dataobj)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(md)

    converted_dataobj = DataObj.from_md(md)
    converted_dataobj.fullpath = str(
        filename.relative_to(current_app.config["USER_DIR"])
    )
    converted_dataobj.index()
    current_app.config["HOOKS"].on_edit(converted_dataobj)


def get_dirs():
    """Gets all dir names where dataobjs are stored"""
    # join glob matchers
    dirnames = [
        str(dir_path.relative_to(get_data_dir()))
        for dir_path in get_data_dir().rglob("*")
        if dir_path.is_dir()
    ]

    return dirnames


def create_dir(name):
    """Create dir of given name"""
    root_dir = get_data_dir()
    new_path = root_dir / name.strip("/")
    if is_relative_to(new_path, root_dir):
        new_path.mkdir(parents=True, exist_ok=True)
        return str(new_path.relative_to(root_dir))
    return False


def delete_dir(name):
    """Deletes dir of given name"""
    root_dir = get_data_dir()
    target_dir = root_dir / name
    if not is_relative_to(target_dir, root_dir) or target_dir == root_dir:
        return False
    try:
        shutil.rmtree(target_dir)
        return True
    except FileNotFoundError:
        return False


def format_file(path: str):
    """
    Converts normal md of file at `path` to formatted archivy markdown file, with yaml front matter
    and a filename of format "{id}-{old_filename}.md"
    """

    from archivy.models import DataObj

    data_dir = get_data_dir()
    path = Path(path)
    if not path.exists():
        return

    if path.is_dir():
        for filename in path.iterdir():
            format_file(filename)

    else:
        new_file = path.open("r", encoding="utf-8")
        file_contents = new_file.read()
        new_file.close()
        try:
            # get relative path of object in `data` dir
            datapath = path.parent.resolve().relative_to(data_dir)
        except ValueError:
            datapath = Path()

        note_dataobj = {
            "title": path.name.replace(".md", ""),
            "content": file_contents,
            "type": "note",
            "path": str(datapath),
        }

        dataobj = DataObj(**note_dataobj)
        dataobj.insert()

        path.unlink()
        current_app.logger.info(
            f"Formatted and moved {str(datapath / path.name)} to {dataobj.fullpath}"
        )


def unformat_file(path: str, out_dir: str):
    """
    Converts normal md of file at `path` to formatted archivy markdown file, with yaml front matter
    and a filename of format "{id}-{old_filename}.md"
    """

    data_dir = get_data_dir()
    path = Path(path)
    out_dir = Path(out_dir)
    if not path.exists() and out_dir.exists() and out_dir.is_dir():
        return

    if path.is_dir():
        path.mkdir(exist_ok=True)
        for filename in path.iterdir():
            unformat_file(filename, str(out_dir))

    else:
        dataobj = frontmatter.load(str(path))

        try:
            # get relative path of object in `data` dir
            datapath = path.parent.resolve().relative_to(data_dir)
        except ValueError:
            datapath = Path()

        # create subdir if doesn't exist
        (out_dir / datapath).mkdir(exist_ok=True)
        new_path = out_dir / datapath / f"{dataobj.metadata['title']}.md"
        with new_path.open("w") as f:
            f.write(dataobj.content)

        current_app.logger.info(
            f"Unformatted and moved {str(path)} to {str(new_path.resolve())}"
        )
        path.unlink()


def open_file(path):
    """Cross platform way of opening file on user's computer"""
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def valid_image_filename(filename):
    ALLOWED_EXTENSIONS = ["jpg", "png", "gif", "jpeg"]
    return "." in filename and filename.rsplit(".", 1)[1] in ALLOWED_EXTENSIONS


def save_image(image: FileStorage):
    """
    Saves image to USER_DATA_DIR

    Returns: filename where image has been saved.
    """
    base_path = Path(current_app.config["USER_DIR"]) / "images"
    fileparts = image.filename.rsplit(".", 1)
    sanitized_filename = secure_filename(fileparts[0])
    dest_path = base_path / f"{sanitized_filename}.{fileparts[1]}"
    i = 1
    while dest_path.exists():
        dest_path = base_path / f"{sanitized_filename}-{i}.{fileparts[1]}"
        i += 1
    image.save(str(dest_path))
    return dest_path.parts[-1]


def image_exists(filename: str):
    sanitized = secure_filename(filename)
    images_dir = Path(current_app.config["USER_DIR"]) / "images"
    image_path = images_dir / sanitized
    if image_path.exists() and is_relative_to(image_path, images_dir):
        return str(image_path)
    return 0
