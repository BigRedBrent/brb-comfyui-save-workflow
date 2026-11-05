import asyncio
import json
import os
import re
import threading
import time
import uuid
import zipfile

from . import save_workflow as archiving

WEB_DIRECTORY = "."

LOG_PREFIX = "[Save Workflow]"

# File name used when nothing usable is wired into path. It's saved in ComfyUI's output folder.
DEFAULT_NAME = "workflow"

# How many text inputs the node has, each shown in its own box and saved in the workflow file.
TEXT_INPUTS = 3

# Number of digits in the counter added to file names, like ComfyUI's own savers: "_00001".
COUNTER_DIGITS = 5

# Choices for the wildcard backup's compression: "none", then zip's levels from 1 (fastest) to 9 (smallest).
ZIP_COMPRESSION_CHOICES = ["none"] + [str(level) for level in range(1, 10)]


class AnyType(str):
    # A type that matches every other type, so any wire can be connected.
    def __ne__(self, other):
        return False


ANY = AnyType("*")


def reason(error):
    # Why something failed, like "Permission denied", without the file's path or name. Paths and names come from
    # the node's settings, which can be private, so they're never written to the log.
    return getattr(error, "strerror", None) or type(error).__name__


def output_folder():
    try:
        import folder_paths

        return folder_paths.get_output_directory()
    except Exception:
        return os.getcwd()


def last_text(value):
    # The last piece of text in a value, looking inside lists and tuples. Video Combine's file list is
    # (True, [preview.png, video.mp4]), and the finished video is always last.
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        for item in reversed(value):
            found = last_text(item)
            if found:
                return found
    return None


def to_text(value):
    # Turns any value into text, the same way Last Preview Text does.
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if value is None:
        return "None"
    try:
        return json.dumps(value, indent=4, ensure_ascii=False)
    except Exception:
        try:
            return str(value)
        except Exception:
            return "The input exists, but couldn't be turned into text."


def find_workflow_node(workflow, unique_id):
    # This node's entry in the saved workflow, including inside subgraphs (ids like "12:5").
    if not isinstance(workflow, dict):
        return None
    definitions = {}
    pending = [workflow]
    while pending:
        container = pending.pop()
        for definition in ((container.get("definitions") or {}).get("subgraphs") or []):
            if isinstance(definition, dict) and definition.get("id") and str(definition["id"]) not in definitions:
                definitions[str(definition["id"])] = definition
                pending.append(definition)
    nodes = workflow.get("nodes") or []
    ids = str(unique_id).split(":")
    for index, node_id in enumerate(ids):
        node = next((n for n in nodes if isinstance(n, dict) and str(n.get("id")) == node_id), None)
        if node is None:
            return None
        if index == len(ids) - 1:
            return node
        definition = definitions.get(str(node.get("type")))
        if definition is None:
            return None
        nodes = definition.get("nodes") or []
    return None


def has_extension(path):
    # The file name has an extension if it has a dot that isn't its first character. Everything after the
    # last such dot is the extension, whatever its length. Dots in folder names don't count.
    return bool(os.path.splitext(path)[1])


def numbered_path(base, extension, number):
    return f"{base}_{number:0{COUNTER_DIGITS}d}{extension}"


def next_number(base, extension):
    # One more than the highest number already used with this name, like ComfyUI's own savers.
    folder, name = os.path.split(base)
    pattern = re.compile(re.escape(name) + r"_(\d{" + str(COUNTER_DIGITS) + r"})" + re.escape(extension) + r"$")
    highest = 0
    try:
        for file_name in os.listdir(folder or "."):
            match = pattern.match(file_name)
            if match:
                highest = max(highest, int(match.group(1)))
    except OSError:
        pass
    return highest + 1


def comfyui_folder():
    try:
        import folder_paths

        return folder_paths.base_path
    except Exception:
        return os.getcwd()


def zip_path_for(json_path, ending):
    # The backup zip has the same name as the workflow file, with .zip in place of its ending.
    if ending and json_path.endswith(ending):
        return json_path[: -len(ending)] + ".zip"
    return json_path + ".zip"


def write_wildcard_backup(zip_path, folder, compression, overwrite=False):
    # Saves every file in the folder and its subfolders into a zip, sorted by name, each with its own date.
    # Sorting and keeping the dates means an unchanged file is stored exactly the same in every backup.
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        for file_name in filenames:
            full_path = os.path.join(dirpath, file_name)
            files.append((os.path.relpath(full_path, folder).replace(os.sep, "/"), full_path))
    files.sort()
    if not files:
        print(f"{LOG_PREFIX} The wildcards folder is empty, so no backup was saved")
        return

    uncompressed = compression == "none"
    method = zipfile.ZIP_STORED if uncompressed else zipfile.ZIP_DEFLATED
    try:
        # "xb" never overwrites an existing file; "wb" replaces it, only when overwrite is on.
        with open(zip_path, "wb" if overwrite else "xb") as zip_file, zipfile.ZipFile(zip_file, "w") as archive:
            for name, full_path in files:
                modified = time.localtime(os.stat(full_path).st_mtime)
                date = max(tuple(modified[:6]), (1980, 1, 1, 0, 0, 0))
                info = zipfile.ZipInfo(name, date_time=date)
                info.compress_type = method
                info.external_attr = 0o644 << 16
                with open(full_path, "rb") as source:
                    if uncompressed:
                        archive.writestr(info, source.read())
                    else:
                        archive.writestr(info, source.read(), compresslevel=int(compression))
        print(f"{LOG_PREFIX} Saved the wildcard backup")
    except OSError as error:
        print(f"{LOG_PREFIX} Couldn't save the wildcard backup: {reason(error)}")


def wildcards_source(wildcards_folder):
    source = str(wildcards_folder or "").strip().strip('"').strip().replace("\\", "/")
    if not os.path.isabs(source):
        source = os.path.join(comfyui_folder(), source)
    return source


# Archiving.

def archive_folder_for(archive_path):
    # A name or relative path is inside the output folder; a full path is used as is.
    path = str(archive_path or "").strip().strip('"').strip().replace("\\", "/") or ".workflows"
    if not os.path.isabs(path):
        path = os.path.join(output_folder(), path)
    return os.path.normpath(path)


def failed_list_file():
    # The list of folders holding files from failed saves, in ComfyUI's user folder.
    try:
        import folder_paths

        user = folder_paths.get_user_directory()
    except Exception:
        user = os.path.join(comfyui_folder(), "user")
    return os.path.join(user, "brb_save_workflow", "failed_archiving.json")


def send_to_page(event, data):
    # Messages for the page, shown in pop-ups there. They may name paths, since nothing is kept: they're never
    # written to the log or the browser's console.
    try:
        from server import PromptServer

        PromptServer.instance.send_sync(event, data)
    except Exception:
        pass


class NodeProgress:
    # The node's progress bar, with a line of text saying what it's doing where ComfyUI supports it. Each step
    # starts a new bar.
    STEPS = 1000

    def __init__(self, node_id):
        self.node_id = node_id
        self.bar = None
        self.text = None

    def show(self, text, fraction):
        # fraction is None for steps with no real progress to show, like compressing, which Git doesn't report:
        # then only the text is shown, with no bar.
        try:
            import comfy.utils

            if text != self.text:
                if fraction is not None and (
                    self.bar is None or text.split(" ")[0] != (self.text or "").split(" ")[0]
                ):
                    self.bar = comfy.utils.ProgressBar(self.STEPS, node_id=self.node_id)
                self.text = text
                try:
                    from server import PromptServer

                    if hasattr(PromptServer.instance, "send_progress_text"):
                        PromptServer.instance.send_progress_text(text, self.node_id)
                except Exception:
                    pass
            if fraction is None:
                self.bar = None
            else:
                self.bar.update_absolute(int(max(0.0, min(fraction, 1.0)) * self.STEPS), self.STEPS)
        except Exception:
            # Including a canceled job: archiving is always finished, so the archive is never left half done.
            pass


# Folders already checked for files left from failed saves since ComfyUI started.
_checked_folders = set()
_checked_lock = threading.Lock()

# The Workflow Extractor, copied next to each archive when it's created, and updated when the node loads.
EXTRACTOR_NAME = ".workflow_extractor.pyw"
EXTRACTOR_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), EXTRACTOR_NAME)


def numbered_name(name, ending, number):
    return f"{name}_{number:0{COUNTER_DIGITS}d}{ending}"


def pick_archive_name(base, ending, taken, always_add_counter, with_zip):
    # The file name for a save into the archive. A name counts as taken if it's already in the archive or waiting
    # in the folder to be stored, and a number like _00001 is added the same way as on disk: one more than the
    # highest number used with that name.
    name = os.path.basename(base)

    def free(file_name):
        return file_name not in taken and (not with_zip or zip_path_for(file_name, ending) not in taken)

    if not always_add_counter and free(name + ending):
        return name + ending
    pattern = re.compile(re.escape(name) + r"_(\d{" + str(COUNTER_DIGITS) + r"})" + re.escape(ending) + r"$")
    highest = 0
    for file_name in taken:
        match = pattern.match(file_name)
        if match:
            highest = max(highest, int(match.group(1)))
    number = highest + 1
    while not free(numbered_name(name, ending, number)):
        number += 1
    return numbered_name(name, ending, number)


def store_leftovers(archive, folder, output, progress):
    # Stores files left from failed saves (or renamed to end in .archive) from every folder on the list for this
    # archive, and from this save's folder on its first save since ComfyUI started.
    list_file = failed_list_file()
    folders = archiving.failed_folders(list_file, archive, output)
    key = os.path.normcase(os.path.realpath(folder))
    with _checked_lock:
        if key not in _checked_folders:
            _checked_folders.add(key)
            folders.append(folder)
    found = []
    seen = set()
    for each in folders:
        each_key = os.path.normcase(os.path.realpath(each))
        if each_key in seen:
            continue
        seen.add(each_key)
        try:
            found += archiving.leftovers_in(each)
        except OSError:
            pass
    if found:
        archiving.store_files(
            archive, output, found, "Stored files left from failed saves",
            lambda text, fraction: progress.show(text, fraction), "Storing left over",
        )
        print(f"{LOG_PREFIX} Stored {len(found)} file(s) left from failed saves")
    for each in folders:
        archiving.update_failed_folder(list_file, each, archive, output)


def place_extractor(archive, only_update=False):
    # Copies the Workflow Extractor next to the archive, replacing a copy that isn't exactly the same as this
    # add-on's. With only_update, used when the node loads, a missing copy isn't added.
    target = os.path.join(os.path.dirname(archive), EXTRACTOR_NAME)
    temp = None
    try:
        if only_update and not os.path.isfile(target):
            return
        with open(EXTRACTOR_SOURCE, "rb") as file:
            data = file.read()
        if os.path.isfile(target):
            with open(target, "rb") as file:
                if file.read() == data:
                    return
        # Written under a temporary name first, so a copy is never left half written.
        temp = f"{target}.{uuid.uuid4().hex}.tmp"
        with open(temp, "xb") as file:
            file.write(data)
        os.replace(temp, target)
        temp = None
        print(f"{LOG_PREFIX} Copied the Workflow Extractor next to the archive")
        return True
    except Exception:
        print(f"{LOG_PREFIX} Couldn't copy the Workflow Extractor next to the archive")
    finally:
        if temp is not None:
            try:
                os.remove(temp)
            except OSError:
                pass


def save_failed(started, folder, json_name, text, zip_data, archive, output, error):
    # Archiving failed, so the files are saved to their folder instead, named the way they'd be stored, with
    # .archive added, like "2026-11-03_10-21-05 my_clip_00001.json.archive". They're stored later by retry_failed or
    # the Retry Failed Archive button.
    day, clock = archiving.stamp_parts(started)
    ending = os.path.splitext(json_name)[1]
    saved = False
    try:
        os.makedirs(folder, exist_ok=True)
        name = json_name
        stem = os.path.splitext(json_name)[0]
        while True:
            json_path = os.path.join(folder, f"{day}_{clock} {name}{archiving.LEFTOVER_ENDING}")
            zip_name = zip_path_for(name, ending)
            zip_path = os.path.join(folder, f"{day}_{clock} {zip_name}{archiving.LEFTOVER_ENDING}")
            if not os.path.exists(json_path) and (zip_data is None or not os.path.exists(zip_path)):
                break
            # Saved in the same second with the same name: try the next number.
            match = re.match(r"(.*)_(\d{" + str(COUNTER_DIGITS) + r"})$", stem)
            stem = f"{match.group(1)}_{int(match.group(2)) + 1:0{COUNTER_DIGITS}d}" if match else f"{stem}_00001"
            name = stem + ending
        with open(json_path, "xb") as file:
            file.write(text.encode("utf-8"))
        if zip_data is not None:
            with open(zip_path, "xb") as file:
                file.write(zip_data)
        saved = True
        archiving.add_failed_folder(failed_list_file(), folder, archive, output)
    except OSError as write_error:
        print(f"{LOG_PREFIX} Couldn't save the workflow to its folder either: {reason(write_error)}")
    if saved:
        print(f"{LOG_PREFIX} Archiving failed, so the workflow was saved to its folder instead, ending in .archive")
    send_to_page("brb.save_workflow.archive_failed", {
        "reason": str(error) if isinstance(error, archiving.ArchiveError) else f"{type(error).__name__}: {error}",
        "folder": folder if saved else None,
    })


def archive_save(started, base, ending, text, always_add_counter, backup_wildcards, wildcards_folder, archive_path,
                 compress_every, retry_failed, compress_in_background, unique_id):
    output = output_folder()
    archive = archive_folder_for(archive_path)
    folder = os.path.dirname(base)
    progress = NodeProgress(unique_id)
    progress.show("Archiving", 0.0)
    zip_data = None
    if backup_wildcards:
        source = wildcards_source(wildcards_folder)
        if os.path.isdir(source):
            try:
                zip_data = archiving.wildcard_zip(source, "none")
            except OSError as error:
                print(f"{LOG_PREFIX} Couldn't read the wildcards folder: {reason(error)}")
            if zip_data is None:
                print(f"{LOG_PREFIX} The wildcards folder is empty, so no backup was saved")
        else:
            print(f"{LOG_PREFIX} The wildcards folder doesn't exist, so no backup was saved")

    json_name = os.path.basename(base) + ending
    try:
        created = not archiving.is_marked_archive(archive)
        archiving.open_archive(archive)
        if created:
            place_extractor(archive)
        if retry_failed:
            store_leftovers(archive, folder, output, progress)
        parts = archiving.folder_parts(folder, output)
        day, clock = archiving.stamp_parts(started)
        reader = archiving.Reader(archive)
        try:
            taken = archiving.names_in_folder(archive, parts, reader) | archiving.leftover_names(folder)
            json_name = pick_archive_name(base, ending, taken, always_add_counter, zip_data is not None)
            items = [archiving.Item(parts, day, clock, json_name, text.encode("utf-8"))]
            if zip_data is not None:
                items.append(archiving.Item(parts, day, clock, zip_path_for(json_name, ending), zip_data))
            progress.show("Archiving", 0.5)
            count = archiving.store(archive, items, reader=reader)
        finally:
            reader.close()
        progress.show("Archiving", 1.0)
    except Exception as error:
        if not isinstance(error, archiving.ArchiveError):
            print(f"{LOG_PREFIX} Archiving failed: {type(error).__name__}")
        save_failed(started, folder, json_name, text, zip_data, archive, output, error)
        return
    print(f"{LOG_PREFIX} Archived the workflow")

    if compress_every and count >= compress_every:
        if compress_in_background:
            threading.Thread(target=compress_archive, args=(archive, None), daemon=True).start()
        else:
            compress_archive(archive, progress)


def compress_archive(archive, progress):
    started = time.time()
    try:
        ran = archiving.compress(
            archive, (lambda text, fraction: progress.show(text, fraction)) if progress else None
        )
    except Exception as error:
        print(f"{LOG_PREFIX} Compressing the archive failed. It'll be tried again on the next save")
        send_to_page("brb.save_workflow.archive_problem", {
            "text": f"Compressing the archive failed, so it'll be tried again on the next save.\n\n{error}",
        })
        return
    if ran:
        print(f"{LOG_PREFIX} Compressed the archive in {time.time() - started:.0f} second(s)")


# The node's buttons, which run on the server, in the background, while the page shows their progress.

_tasks = {}
_tasks_lock = threading.Lock()


def run_task(action, archive):
    output = output_folder()
    task = {
        "text": "Starting...", "fraction": None, "done": False, "error": None, "result": None, "archive": archive,
        "abort": False, "aborted": False, "status": None, "thread": None,
    }
    should_stop = lambda: task["abort"]

    def progress(text, fraction):
        task["text"] = text
        task["fraction"] = fraction

    def watch():
        # Once a second, checks whether the task's own work (storing files, looking for them) is using the
        # processor: "working", or "not responding" after a few seconds without. Git's own steps report theirs in
        # their message instead.
        try:
            import psutil

            me = psutil.Process()
        except Exception:
            return
        last_cpu = None
        last_active = time.time()
        while not task["done"]:
            try:
                thread = next((each for each in me.threads() if each.id == task["thread"]), None)
                cpu = thread.user_time + thread.system_time if thread else None
            except Exception:
                cpu = None
            now = time.time()
            if cpu is not None:
                if last_cpu is None or cpu > last_cpu + 0.01:
                    last_active = now
                last_cpu = cpu
                task["status"] = "working" if now - last_active < archiving.IDLE_AFTER else "not responding"
            time.sleep(1.0)

    def work():
        task["thread"] = threading.get_native_id()
        try:
            if action == "rebuild":
                size_before = archiving.archive_size(archive)
                rebuild_started = time.time()
                if not archiving.compress(archive, progress, rebuild=True, should_stop=should_stop):
                    raise archiving.ArchiveError(
                        "The archive is being compressed right now. Try again once that's done."
                    )
                size_after = archiving.archive_size(archive)
                task["result"] = (
                    "The archive was rebuilt and checked, and everything in it is intact.\n\n"
                    f"Time to rebuild: {archiving.duration(time.time() - rebuild_started)}\n\n"
                    f"Archive size before rebuild: {archiving.size_text(size_before)}\n"
                    f"Archive size after rebuild: {archiving.size_text(size_after)}"
                )
                return
            list_file = failed_list_file()
            listed = archiving.failed_folders(list_file, archive, output)
            progress("Looking for files...", None)
            found = archiving.find_all(output, archive, include_regular=(action == "archive_all"), extra_folders=listed)
            if not found:
                task["result"] = (
                    "No workflows or wildcard backups were found to archive." if action == "archive_all"
                    else "No files ending in .archive were found, so there was nothing to retry."
                )
                return
            message = "Archived existing files" if action == "archive_all" else "Stored files left from failed saves"
            stored, identical, unreadable = archiving.store_files(
                archive, output, found, message,
                progress,
                "Archiving" if action == "archive_all" else "Storing",
                should_stop,
            )
            for folder in listed:
                archiving.update_failed_folder(list_file, folder, archive, output)
            lines = [f"Stored {stored:,} file(s) in the archive."]
            if identical:
                lines.append(f"{identical:,} were already stored exactly the same, so they were just removed from "
                             "their folders.")
            if unreadable:
                lines.append(f"{unreadable:,} couldn't be read, so they were left where they are.")
            if action == "archive_all":
                archiving.compress(archive, progress, should_stop=should_stop)
                lines.append("The archive was compressed afterward.")
            task["result"] = "\n".join(lines)
            print(f"{LOG_PREFIX} Stored {stored + identical} file(s) in the archive from the node's button")
        except archiving.Stopped:
            task["aborted"] = True
        except Exception as error:
            task["error"] = str(error) if isinstance(error, archiving.ArchiveError) else f"{type(error).__name__}: {error}"
        finally:
            task["fraction"] = 1.0
            task["done"] = True

    task_id = uuid.uuid4().hex
    with _tasks_lock:
        _tasks[task_id] = task
    threading.Thread(target=work, daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    return task_id


def create_archive_now(archive):
    # The Create Archive button: creates a new, empty archive, only where there isn't one or anything else already.
    try:
        archiving.check_git()
        if archiving.is_marked_archive(archive):
            # Puts the Workflow Extractor back if it was deleted, or updates it.
            if place_extractor(archive):
                return {"result": (
                    f"An archive already exists at:\n{archive}\n\n"
                    "The Workflow Extractor was copied next to it."
                )}
            return {"result": f"An archive already exists at:\n{archive}\n\nNothing was changed."}
        if os.path.exists(archive) and (not os.path.isdir(archive) or os.listdir(archive)):
            return {"error": (
                f"That folder already has other files in it, so an archive can't be created there:\n{archive}\n\n"
                "Either remove the existing folder, or choose a different location in the archive_path setting."
            )}
        archiving.create_archive(archive)
    except archiving.ArchiveError as error:
        return {"error": str(error)}
    except OSError as error:
        return {"error": f"Couldn't create the archive ({error.strerror or error}):\n{archive}"}
    print(f"{LOG_PREFIX} Created a new archive")
    place_extractor(archive)
    return {"result": f"Created a new archive at:\n{archive}"}


def add_routes():
    try:
        from aiohttp import web
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception:
        return

    @routes.post("/brb_save_workflow/task")
    async def start_task(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        action = data.get("action")
        if action not in ("create", "retry_failed", "archive_all", "rebuild"):
            return web.json_response({"error": "Unknown action."})
        archive = archive_folder_for(data.get("archive_path"))
        if action == "create":
            return web.json_response(create_archive_now(archive))
        with _tasks_lock:
            busy = any(
                not task["done"] and archiving.archive_key(task["archive"]) == archiving.archive_key(archive)
                for task in _tasks.values()
            )
        if busy:
            return web.json_response({"error": "Another task is still running on this archive. Try again once it's done."})
        try:
            archiving.open_archive(archive, create=False)
        except archiving.ArchiveError as error:
            return web.json_response({"error": str(error)})
        return web.json_response({"task": run_task(action, archive)})

    @routes.post("/brb_save_workflow/update_extractor")
    async def update_extractor(request):
        # Sent once by the page when the node loads: updates the Workflow Extractor next to a valid archive, if it's
        # already there.
        try:
            data = await request.json()
        except Exception:
            data = {}
        archive = archive_folder_for(data.get("archive_path"))

        def update():
            try:
                if archiving.is_marked_archive(archive):
                    place_extractor(archive, only_update=True)
            except Exception:
                pass

        await asyncio.to_thread(update)
        return web.json_response({"ok": True})

    @routes.get("/brb_save_workflow/task/{task_id}")
    async def task_status(request):
        with _tasks_lock:
            task = _tasks.get(request.match_info["task_id"])
            if task is None:
                return web.json_response({"error": "This task is no longer known.", "done": True})
            if task["done"]:
                # Each finished task is only reported once.
                _tasks.pop(request.match_info["task_id"], None)
        return web.json_response(
            {key: task[key] for key in ("text", "fraction", "done", "error", "result", "aborted", "status")}
        )

    @routes.post("/brb_save_workflow/task/{task_id}/abort")
    async def abort_task(request):
        # The task stops as soon as it safely can; the page waits for it to report that it's done.
        with _tasks_lock:
            task = _tasks.get(request.match_info["task_id"])
            if task is not None:
                task["abort"] = True
        return web.json_response({"ok": task is not None})


add_routes()


class SaveWorkflow:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "extension": (["replace", "append", "none"], {
                    "default": "replace",
                    "tooltip": "replace: my_clip.mp4 is saved as my_clip.json. "
                               "append: my_clip.mp4 is saved as my_clip.mp4.json. "
                               "none: the name is used as it is.",
                }),
                "always_add_counter": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Off: a number like _00001 is only added if a file with that name already exists. "
                               "On: a number is always added. Existing files are never overwritten either way.",
                }),
                "api_format_only": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Off: saves the full workflow, with its layout, groups and notes. "
                               "On: saves only the node settings used to run the job (API format).",
                }),
                "format": (["compact", "readable"], {
                    "default": "compact",
                    "tooltip": "compact: smallest file. readable: indented, for reading in a text editor.",
                }),
                "backup_wildcards": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Also saves a zip of the wildcards folder next to the workflow file, with the same "
                               "name, so you know exactly which wildcard files a result was made with.",
                }),
                "zip_compression": (ZIP_COMPRESSION_CHOICES, {
                    "default": "9",
                    "tooltip": "How much the wildcard backup is compressed: 1 is fastest, 9 is smallest. \"none\" "
                               "stores the files without compression. Each zip is larger, but if you keep many backups "
                               "in an archive that stores only differences, such as Git, uncompressed zips take up "
                               "far less space overall.",
                }),
                "wildcards_folder": ("STRING", {
                    "default": "wildcards",
                    "tooltip": "The folder to back up. A name like \"wildcards\" is inside the ComfyUI folder, "
                               "which is where Dynamic Prompts keeps its wildcards. A full path works too.",
                }),
                # Added last, so workflows saved with earlier versions keep their other settings in place.
                "overwrite": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Saves to the exact same name every time, replacing the file (and wildcard backup) "
                               "if it already exists. No number is ever added. Turning this on turns off "
                               "always_add_counter, and the other way around. Off: existing files are never replaced.",
                }),
                # Added last, so workflows saved with earlier versions keep their other settings in place.
                "keep_text": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "On: the text shown in the text boxes is kept with this workflow, so it survives "
                               "undo and comes back when the workflow is opened again. Off: the text is only "
                               "shown, not kept, so it's lost on undo or reopening. The saved workflow file always "
                               "gets the text either way.",
                }),
                # The archive settings, added last, so workflows saved with earlier versions keep their other
                # settings in place.
                "archive": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Saves the workflow (and wildcard backup) into an archive instead of the folder, where "
                               "similar files take up far less space. Find and extract them with the Workflow "
                               "Extractor, from this add-on's GitHub page. While on, zip_compression and overwrite "
                               "are ignored: wildcard backups are stored uncompressed, and files are never "
                               "overwritten. Requires Git.",
                }),
                "archive_path": ("STRING", {
                    "default": ".workflows",
                    "tooltip": "Where the archive is. A name or relative path, like .workflows, is inside ComfyUI's "
                               "output folder. A full path is used as is. It's created on the first save if it doesn't "
                               "exist. An existing folder is only used if it's empty or already an archive.",
                }),
                "compress_every": ("INT", {
                    "default": 1000,
                    "min": 0,
                    "max": 1000000,
                    "step": 1,
                    "tooltip": "How many saves between compressions of the archive. Until then, new saves take up "
                               "more space. 0 means never: the archive is never compressed and keeps growing.",
                }),
                "retry_failed": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "If archiving ever fails, the files are saved to their folder instead, with .archive "
                               "added to their names. With this on, they're stored in the archive on later saves. "
                               "Files you rename yourself to end in .archive, like my_clip.json.archive, are picked "
                               "up too, on the first save to their folder after ComfyUI starts.",
                }),
                "compress_in_background": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Off: when it's time to compress the archive, the job pauses until it's done, so the "
                               "compression doesn't compete with jobs for memory. On: it compresses alongside your "
                               "jobs instead.",
                }),
            },
            "optional": {
                "passthrough": (ANY, {
                    "tooltip": "Any value, sent out unchanged through the passthrough output. The node waits for it before "
                               "saving, so wire it from a saver to save after the file is saved.",
                }),
                "path": (ANY, {
                    "tooltip": "Where to save. Wire in the path or name of what you're saving, such as a video "
                               "saver's file path, so the names match. A full path saves exactly there. A name or "
                               "prefix, like \"videos/my_clip\", saves in ComfyUI's output folder. A file list, like "
                               "Video Combine's Filenames output, uses the last file in it. If path isn't connected, "
                               "the file is saved as \"workflow\" in the output folder.",
                }),
                **{
                    f"text_{number}": (ANY, {
                        "tooltip": "Any value, turned into text, such as a positive or negative prompt. It's shown "
                                   "on this node and saved in the workflow file, so opening that file in ComfyUI "
                                   "shows it here again. Right-click the input to give it a label, like "
                                   "\"positive\". Left out when nothing is connected, or when api_format_only is on.",
                    })
                    for number in range(1, TEXT_INPUTS + 1)
                },
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("passthrough",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "utils"
    DESCRIPTION = "Saves the workflow as a .json file, such as next to a video, so it can be opened again later."

    def save(self, extension, always_add_counter, api_format_only, format,
             backup_wildcards=False, zip_compression="9", wildcards_folder="wildcards", overwrite=False,
             keep_text=False, archive=False, archive_path=".workflows", compress_every=1000, retry_failed=True,
             compress_in_background=False,
             passthrough=None, path=None, prompt=None, extra_pnginfo=None, unique_id=None, **texts):
        # The time of this save, taken first, before anything else is done.
        started = time.time()
        result = {"result": (passthrough,)}
        # The text inputs that are connected, turned into text: {"text_1": "...", ...}
        saved_text = {}
        for number in range(1, TEXT_INPUTS + 1):
            name = f"text_{number}"
            if texts.get(name) is not None:
                saved_text[name] = to_text(texts[name])
        if saved_text:
            # Shown on the node (see save_workflow.js).
            result["ui"] = {"saved_text": [saved_text]}

        if api_format_only:
            data = prompt
        else:
            data = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
        if data is None:
            print(f"{LOG_PREFIX} Nothing to save (the job wasn't queued from the ComfyUI page)")
            return result

        if saved_text and not api_format_only:
            # Saved in this node's entry in the file only. The job's own copy of the workflow is shared with
            # every other node in the job, so it's copied first and left unchanged.
            data = json.loads(json.dumps(data))
            entry = find_workflow_node(data, unique_id or "")
            if entry is not None:
                properties = entry.setdefault("properties", {})
                if isinstance(properties, dict):
                    properties["saved_text"] = saved_text

        # The name comes only from path. If path isn't connected or has no name in it, use the default name.
        target = last_text(path)
        target = str(target or "").strip().strip('"').strip()
        if not target:
            target = DEFAULT_NAME
        # Backslashes separate folders only on Windows. Use forward slashes, which work everywhere, so a path
        # like "videos\my_clip" from a Windows workflow saves in a "videos" folder on Linux and Mac too.
        target = target.replace("\\", "/")
        if not os.path.isabs(target):
            target = os.path.join(output_folder(), target)
        if not os.path.basename(target):
            # A folder was given without a name.
            target = os.path.join(target, DEFAULT_NAME)

        if extension == "replace":
            base = os.path.splitext(target)[0] if has_extension(target) else target
            ending = ".json"
        elif extension == "append":
            base = target
            ending = ".json"
        else:
            base = target
            ending = ""

        folder = os.path.dirname(base)

        if format == "readable":
            text = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

        if archive:
            # zip_compression and overwrite are ignored while archiving: zips are stored uncompressed, and nothing
            # is ever overwritten.
            archive_save(
                started, base, ending, text, always_add_counter, backup_wildcards, wildcards_folder, archive_path,
                compress_every, retry_failed, compress_in_background, unique_id,
            )
            return result

        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as error:
            print(f"{LOG_PREFIX} Couldn't create the folder to save in: {reason(error)}")
            return result

        if overwrite:
            # Always the exact name, replacing any file already there.
            candidate = base + ending
            try:
                with open(candidate, "w", encoding="utf-8") as json_file:
                    json_file.write(text)
            except OSError as error:
                print(f"{LOG_PREFIX} Couldn't save the workflow: {reason(error)}")
                return result
        else:
            candidate = None if always_add_counter else base + ending
        number = None
        while not overwrite:
            if candidate is None:
                number = next_number(base, ending) if number is None else number + 1
                candidate = numbered_path(base, ending, number)
            if backup_wildcards and os.path.exists(zip_path_for(candidate, ending)):
                # The backup must get the same name, so a name whose zip is taken counts as taken too.
                candidate = None
                continue
            try:
                # "x" never overwrites: it fails if the file already exists, and a number is tried instead.
                with open(candidate, "x", encoding="utf-8") as json_file:
                    json_file.write(text)
                break
            except FileExistsError:
                candidate = None
            except OSError as error:
                print(f"{LOG_PREFIX} Couldn't save the workflow: {reason(error)}")
                return result

        print(f"{LOG_PREFIX} Saved the workflow")

        if backup_wildcards:
            source = wildcards_source(wildcards_folder)
            if os.path.isdir(source):
                write_wildcard_backup(zip_path_for(candidate, ending), source, zip_compression, overwrite)
            else:
                print(f"{LOG_PREFIX} The wildcards folder doesn't exist, so no backup was saved")
        return result


NODE_CLASS_MAPPINGS = {
    "BRBSaveWorkflow": SaveWorkflow,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BRBSaveWorkflow": "Save Workflow",
}

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
