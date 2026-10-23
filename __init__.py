import json
import os
import re

LOG_PREFIX = "[Save Workflow]"

# File name used when nothing usable is wired into path. It's saved in ComfyUI's output folder.
DEFAULT_NAME = "workflow"

# Number of digits in the counter added to file names, like ComfyUI's own savers: "_00001".
COUNTER_DIGITS = 5


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
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("passthrough",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "utils"
    DESCRIPTION = "Saves the workflow as a .json file, such as next to a video, so it can be opened again later."

    def save(self, extension, always_add_counter, api_format_only, format,
             passthrough=None, path=None, prompt=None, extra_pnginfo=None):
        result = {"result": (passthrough,)}

        if api_format_only:
            data = prompt
        else:
            data = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
        if data is None:
            print(f"{LOG_PREFIX} Nothing to save (the job wasn't queued from the ComfyUI page)")
            return result

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

        candidate = None if always_add_counter else base + ending
        number = None
        while True:
            if candidate is None:
                number = next_number(base, ending) if number is None else number + 1
                candidate = numbered_path(base, ending, number)
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
        return result


NODE_CLASS_MAPPINGS = {
    "BRBSaveWorkflow": SaveWorkflow,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BRBSaveWorkflow": "Save Workflow",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
