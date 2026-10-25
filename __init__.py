import json
import os
import re
import time
import zipfile

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
        print(f"{LOG_PREFIX} The wildcards folder is empty, so no backup was saved: {folder}")
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
        print(f"{LOG_PREFIX} Saved wildcard backup: {zip_path}")
    except OSError as error:
        print(f"{LOG_PREFIX} Couldn't save the wildcard backup {zip_path}: {error}")


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
             keep_text=False,
             passthrough=None, path=None, prompt=None, extra_pnginfo=None, unique_id=None, **texts):
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
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as error:
            print(f"{LOG_PREFIX} Couldn't create the folder {folder}: {error}")
            return result

        if format == "readable":
            text = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

        if overwrite:
            # Always the exact name, replacing any file already there.
            candidate = base + ending
            try:
                with open(candidate, "w", encoding="utf-8") as json_file:
                    json_file.write(text)
            except OSError as error:
                print(f"{LOG_PREFIX} Couldn't save {candidate}: {error}")
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
                print(f"{LOG_PREFIX} Couldn't save {candidate}: {error}")
                return result

        print(f"{LOG_PREFIX} Saved: {candidate}")

        if backup_wildcards:
            source = str(wildcards_folder or "").strip().strip('"').strip().replace("\\", "/")
            if not os.path.isabs(source):
                source = os.path.join(comfyui_folder(), source)
            if os.path.isdir(source):
                write_wildcard_backup(zip_path_for(candidate, ending), source, zip_compression, overwrite)
            else:
                print(f"{LOG_PREFIX} The wildcards folder doesn't exist, so no backup was saved: {source}")
        return result


NODE_CLASS_MAPPINGS = {
    "BRBSaveWorkflow": SaveWorkflow,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BRBSaveWorkflow": "Save Workflow",
}

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
