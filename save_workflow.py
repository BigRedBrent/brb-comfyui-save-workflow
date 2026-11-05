# The archive that Save Workflow can save into: a Git archive where each saved file is kept as its own entry,
# in a folder for the day it was stored, with the time in front of its name:
#   Text2Img/2026-11-03/10-21-05 my_clip_00001.json
# A counter like "10-21-05_2" is added only when the same name was already stored in the same second.
#
# Files are stored straight from memory, writing Git's objects directly, so each save starts Git only twice: once to
# look things up and once to update the archive. The update only goes through if nothing else changed the archive in
# the meantime (like the Workflow Extractor removing files), and is otherwise done again.
#
# Nothing here writes names, paths or file contents to the log. Error messages can name paths, since they're only
# shown in pop-ups on the page.

import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
import zlib
import hashlib

BRANCH_REF = "refs/heads/main"

# Marks an archive made for Save Workflow. Archives without it are never used.
MARKER_KEY = "workflowarchive.format"
MARKER_VALUE = "1"

# Moved to the archive's newest entry after each compression, the same as the converter and the Workflow Extractor.
BOOKMARK_REF = "refs/workflowarchive/compressed"

# Compression settings, chosen from timing tests: a wide search for similar files, on one thread, which uses less
# memory and, for a rebuild, compresses better than several threads.
COMPRESSION_LEVEL = 9
SIMILAR_FILE_SEARCH = 1000
DIFFERENCE_CHAIN_LENGTH = 4095
THREADS = 1

# Data nothing refers to anymore is only deleted once it's this old, so files being saved at the same moment are
# never touched. Long enough to stay safe even on a network drive whose clock is set to the wrong time zone, which
# can be off by up to 26 hours.
PRUNE_AFTER = "27.hours.ago"

# Each save's entry in the archive's history notes how many saves there have been since the last compression.
COUNT_LINE = "Saves-Since-Compression"

# Git 2.36 or newer is needed, for the way files are looked up.
MINIMUM_GIT = (2, 36)

DAY_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H-%M-%S"
DAY_FOLDER = re.compile(r"\d{4}-\d{2}-\d{2}$")
TIMED_NAME = re.compile(r"(\d{2}-\d{2}-\d{2})(?:_(\d+))? (.+)$", re.DOTALL)

# Files left in a folder for the archive: "2026-11-03_10-21-05 my_clip_00001.json.archive" from a failed save, or
# "my_clip.json.archive" renamed by hand, which is dated by when it was last modified.
LEFTOVER_ENDING = ".archive"
DATED_LEFTOVER = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2}) (.+\.(?:json|zip))\.archive$", re.IGNORECASE)
PLAIN_LEFTOVER = re.compile(r"(.+\.(?:json|zip))\.archive$", re.IGNORECASE)

# How many files the buttons store in each step.
BATCH_SIZE = 200

# Keeps Git from opening a console window for each command on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

IDENTITY = "Workflow Archive <workflow-archive@localhost>"
GIT_ENVIRONMENT = {
    "GIT_AUTHOR_NAME": "Workflow Archive",
    "GIT_AUTHOR_EMAIL": "workflow-archive@localhost",
    "GIT_COMMITTER_NAME": "Workflow Archive",
    "GIT_COMMITTER_EMAIL": "workflow-archive@localhost",
    # Never ask anything; there's no one to answer.
    "GIT_TERMINAL_PROMPT": "0",
}

ZERO_ID = "0" * 40


class ArchiveError(Exception):
    # A problem with the archive. The message may name paths, since it's only shown in pop-ups on the page.
    pass


class Stopped(ArchiveError):
    # Stopped by the Abort button. Nothing is damaged: anything already stored stays stored, and a compression that
    # was stopped is simply done again later.
    pass


class ArchiveChanged(Exception):
    # Something else changed the archive while storing, so storing is done again.
    pass


# Storing and compressing are each done one at a time per archive.
_store_locks = {}
_compress_locks = {}
_locks_lock = threading.Lock()


def archive_key(archive):
    return os.path.normcase(os.path.realpath(archive))


def store_lock(archive):
    with _locks_lock:
        return _store_locks.setdefault(archive_key(archive), threading.RLock())


def compress_lock(archive):
    with _locks_lock:
        return _compress_locks.setdefault(archive_key(archive), threading.Lock())


def git_environment():
    env = dict(os.environ)
    env.update(GIT_ENVIRONMENT)
    return env


def run_git(archive, args, input_bytes=None):
    result = subprocess.run(
        ["git", "--git-dir", str(archive)] + args,
        input=input_bytes,
        capture_output=True,
        env=git_environment(),
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise ArchiveError(f"Git couldn't {args[0]} the archive: {error}")
    return result.stdout


_git_checked = None


def check_git():
    # Raises ArchiveError if Git is missing or too old.
    global _git_checked
    if _git_checked:
        return
    if shutil.which("git") is None:
        raise ArchiveError("Git wasn't found. Install Git (Git for Windows on Windows), then restart ComfyUI.")
    try:
        output = subprocess.run(
            ["git", "--version"], capture_output=True, creationflags=NO_WINDOW
        ).stdout.decode("utf-8", errors="replace")
        version = tuple(int(part) for part in re.search(r"(\d+)\.(\d+)", output).groups())
    except Exception:
        raise ArchiveError("Couldn't tell which version of Git is installed.")
    if version < MINIMUM_GIT:
        raise ArchiveError(
            f"Git {MINIMUM_GIT[0]}.{MINIMUM_GIT[1]} or newer is needed for archiving. Update Git, then restart ComfyUI."
        )
    _git_checked = True


def is_marked_archive(folder):
    if not os.path.isfile(os.path.join(folder, "HEAD")) or not os.path.isfile(os.path.join(folder, "config")):
        return False
    # Read straight from the archive's settings file when it's laid out the usual way, which saves starting Git.
    try:
        with open(os.path.join(folder, "config"), "r", encoding="utf-8", errors="replace") as file:
            settings = file.read()
        if re.search(r"^\s*\[workflowarchive\]\s*$(?:\n(?!\s*\[).*)*?\n\s*format\s*=\s*1\s*$", settings,
                     re.MULTILINE | re.IGNORECASE):
            return True
    except OSError:
        pass
    try:
        value = run_git(folder, ["config", "--get", MARKER_KEY]).decode("utf-8").strip()
    except ArchiveError:
        return False
    return value == MARKER_VALUE


def create_archive(folder):
    os.makedirs(folder, exist_ok=True)
    result = subprocess.run(
        ["git", "init", "--bare", "--quiet", str(folder)],
        capture_output=True,
        env=git_environment(),
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise ArchiveError(f"Git couldn't create the archive at:\n{folder}")
    run_git(folder, ["symbolic-ref", "HEAD", BRANCH_REF])
    for key, value in (
        ("user.name", "Workflow Archive"),
        ("user.email", "workflow-archive@localhost"),
        ("gc.auto", "0"),
        ("core.loosecompression", str(COMPRESSION_LEVEL)),
        ("pack.compression", str(COMPRESSION_LEVEL)),
        ("pack.window", str(SIMILAR_FILE_SEARCH)),
        ("pack.depth", str(DIFFERENCE_CHAIN_LENGTH)),
        ("pack.threads", str(THREADS)),
    ):
        run_git(folder, ["config", key, value])
    # Marked last, so an archive that wasn't finished being set up is never used.
    run_git(folder, ["config", MARKER_KEY, MARKER_VALUE])


def open_archive(folder, create=True):
    # Makes sure the folder is an archive Save Workflow can use: one with the marker, or, with create on, a new one
    # in a missing or empty folder. Anything else is refused, so a folder of other files, or an old-format archive,
    # is never written into.
    check_git()
    if os.path.isdir(folder):
        if is_marked_archive(folder):
            return
        try:
            empty = not os.listdir(folder)
        except OSError as error:
            raise ArchiveError(f"Couldn't open the archive folder ({error.strerror or error}):\n{folder}")
        if not empty:
            raise ArchiveError(
                f"This folder isn't a workflow archive, so nothing was stored in it:\n{folder}\n\n"
                "Check the archive_path setting. An archive made by an older archive script has to be converted first."
            )
        if not create:
            raise ArchiveError(f"No workflow archive was found at:\n{folder}\n\nCheck the archive_path setting.")
    elif not create:
        raise ArchiveError(f"No workflow archive was found at:\n{folder}\n\nCheck the archive_path setting.")
    create_archive(folder)


# Git's own objects, written directly.

def object_id(kind, data):
    return hashlib.sha1(f"{kind} {len(data)}\0".encode("ascii") + data).hexdigest()


def object_path(archive, sha):
    return os.path.join(archive, "objects", sha[:2], sha[2:])


def write_object(archive, kind, data):
    # Writes one object the way Git does: compressed into a temporary file, then renamed into place. An object
    # that's already there as its own file has its date refreshed instead, the same as Git does, so cleanups that
    # only delete unused data older than a while never delete it while it's being stored again.
    sha = object_id(kind, data)
    path = object_path(archive, sha)
    if os.path.exists(path):
        try:
            os.utime(path)
        except OSError:
            pass
        return sha
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    temporary = os.path.join(folder, f"tmp_obj_{os.getpid()}_{threading.get_ident()}")
    with open(temporary, "wb") as file:
        file.write(zlib.compress(f"{kind} {len(data)}\0".encode("ascii") + data, COMPRESSION_LEVEL))
        file.flush()
        os.fsync(file.fileno())
    try:
        os.chmod(temporary, 0o444)
    except OSError:
        pass
    try:
        os.replace(temporary, path)
    except OSError:
        # Written at the same moment by something else.
        try:
            os.chmod(temporary, 0o644)
            os.remove(temporary)
        except OSError:
            pass
        if not os.path.exists(path):
            raise
    return sha


def object_file_is_intact(archive, sha):
    # Reads an object's file back and checks its contents match its name.
    try:
        with open(object_path(archive, sha), "rb") as file:
            data = zlib.decompress(file.read())
    except (OSError, zlib.error):
        return False
    return hashlib.sha1(data).hexdigest() == sha


def tree_bytes(entries):
    # entries: {name: (mode, id)}, mode "100644" for files and "40000" for folders. Sorted the way Git sorts them,
    # where a folder sorts as if its name ended in "/".
    def sort_key(item):
        name, (mode, _) = item
        encoded = name.encode("utf-8")
        return encoded + b"/" if mode == "40000" else encoded

    data = bytearray()
    for name, (mode, sha) in sorted(entries.items(), key=sort_key):
        data += f"{mode} {name}".encode("utf-8") + b"\0" + bytes.fromhex(sha)
    return bytes(data)


def parse_tree(data):
    entries = {}
    position = 0
    while position < len(data):
        space = data.index(b" ", position)
        end = data.index(b"\0", space)
        mode = data[position:space].decode("ascii")
        name = data[space + 1:end].decode("utf-8", errors="surrogateescape")
        sha = data[end + 1:end + 21].hex()
        entries[name] = ("40000" if mode == "40000" else mode, sha)
        position = end + 21
    return entries


def time_zone(timestamp):
    offset = time.localtime(timestamp).tm_gmtoff
    sign = "+" if offset >= 0 else "-"
    offset = abs(offset) // 60
    return f"{sign}{offset // 60:02d}{offset % 60:02d}"


def commit_bytes(tree, parent, message):
    now = int(time.time())
    stamp = f"{IDENTITY} {now} {time_zone(now)}"
    lines = [f"tree {tree}"]
    if parent:
        lines.append(f"parent {parent}")
    lines += [f"author {stamp}", f"committer {stamp}", "", message]
    return ("\n".join(lines) + "\n").encode("utf-8")


def count_in(message):
    match = re.search(rf"^{COUNT_LINE}: (\d+)$", message, re.MULTILINE)
    return int(match.group(1)) if match else 0


class Reader:
    # One Git process for looking up anything in the archive while storing.
    def __init__(self, archive):
        self.archive = archive
        self.process = subprocess.Popen(
            ["git", "--git-dir", str(archive), "cat-file", "--batch-command"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_environment(),
            creationflags=NO_WINDOW,
        )

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def _ask(self, command):
        self.process.stdin.write(command.encode("utf-8") + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().decode("utf-8", errors="replace").split()
        if len(header) != 3:
            return None
        return header

    def info(self, name):
        header = self._ask(f"info {name}")
        return None if header is None else (header[0], header[1], int(header[2]))

    def contents(self, name):
        header = self._ask(f"contents {name}")
        if header is None:
            return None
        data = self.process.stdout.read(int(header[2]))
        self.process.stdout.read(1)
        return header[1], data

    def exists(self, sha):
        return os.path.exists(object_path(self.archive, sha)) or self.info(sha) is not None

    def head(self):
        # (commit id or None, tree id or None, saves since compression)
        found = self.contents(BRANCH_REF)
        if found is None:
            return None, None, 0
        kind, data = found
        if kind != "commit":
            raise ArchiveError("The archive's newest entry can't be read.")
        text = data.decode("utf-8", errors="replace")
        header, _, message = text.partition("\n\n")
        tree = re.search(r"^tree ([0-9a-f]{40})$", header, re.MULTILINE).group(1)
        commit = object_id("commit", data)
        return commit, tree, count_in(message)

    def tree(self, sha):
        if not sha:
            return {}
        found = self.contents(sha)
        if found is None or found[0] != "tree":
            raise ArchiveError("A folder list in the archive can't be read.")
        return parse_tree(found[1])


def update_head(archive, new, old):
    # Only changes the archive if its newest entry is still old, so nothing someone else stored is lost.
    result = subprocess.run(
        ["git", "--git-dir", str(archive), "update-ref", BRANCH_REF, new, old or ZERO_ID],
        capture_output=True,
        env=git_environment(),
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise ArchiveChanged()


# Paths inside the archive.

def folder_parts(folder, output_folder):
    # The folders a save's files are stored under: relative to the output folder when inside it, otherwise the full
    # path with its drive kept, like ("D:", "Images"), or ("\\server\share", "Images") for a network path.
    folder = os.path.realpath(folder)
    output = os.path.realpath(output_folder)
    if os.path.normcase(folder) == os.path.normcase(output):
        return ()
    try:
        relative = os.path.relpath(folder, output)
    except ValueError:
        relative = None
    if relative is not None and not relative.startswith(".."):
        return tuple(part for part in relative.replace("\\", "/").split("/") if part and part != ".")
    drive, rest = os.path.splitdrive(folder)
    parts = [part for part in rest.replace("\\", "/").split("/") if part]
    return ((drive,) if drive else ()) + tuple(parts)


def stamp_parts(timestamp):
    moment = time.localtime(timestamp)
    return time.strftime(DAY_FORMAT, moment), time.strftime(TIME_FORMAT, moment)


class Item:
    # One file to store.
    __slots__ = ("folder", "day", "clock", "name", "data", "source", "stored", "identical")

    def __init__(self, folder, day, clock, name, data, source=None):
        self.folder = tuple(folder)
        self.day = day
        self.clock = clock
        self.name = name
        self.data = data
        # The file on disk this came from, deleted once it's safely stored.
        self.source = source
        self.stored = None
        self.identical = False


def store(archive, items, count_step=1, message="Stored a save", skip_identical=False, reader=None):
    # Stores the items as one new entry in the archive's history. Returns the number of saves since the last
    # compression. With skip_identical on, an item whose exact name, time and contents are already stored is
    # marked identical and not stored again.
    with store_lock(archive):
        for _ in range(20):
            own_reader = reader is None
            current = Reader(archive) if own_reader else reader
            try:
                return _store_once(archive, current, items, count_step, message, skip_identical)
            except ArchiveChanged:
                time.sleep(0.05)
                continue
            finally:
                if own_reader:
                    current.close()
        raise ArchiveError("The archive kept changing while storing, so nothing was stored.")


def _store_once(archive, reader, items, count_step, message, skip_identical):
    parent, root, count = reader.head()
    trees = {}

    def load(path):
        # A folder's list in the archive, read once per attempt; missing folders start empty.
        if path in trees:
            return trees[path]
        if not path:
            entries = reader.tree(root)
        else:
            above = load(path[:-1])
            entry = above.get(path[-1])
            entries = reader.tree(entry[1]) if entry and entry[0] == "40000" else {}
        trees[path] = dict(entries)
        return trees[path]

    # Every object written by this save, to check afterward: {id: (kind, data)}.
    written = {}
    # Files the archive's newest entry already holds, in the folders read for this save. Only these are reused
    # without writing them again: anything else could be removed and scrubbed at this very moment, so it's written
    # as its own file, which no cleanup deletes while it's new.
    def held(entries):
        return {sha for mode, sha in entries.values() if mode != "40000"}

    for item in items:
        item.identical = False
        blob = object_id("blob", item.data)
        day_path = item.folder + (item.day,)
        entries = load(day_path)
        pattern = re.compile(re.escape(item.clock) + r"(?:_(\d+))? " + re.escape(item.name) + r"$", re.DOTALL)
        highest = 0
        duplicate = None
        for name, (mode, sha) in entries.items():
            match = pattern.match(name)
            if match:
                highest = max(highest, int(match.group(1) or 1))
                if sha == blob:
                    duplicate = name
        if skip_identical and duplicate is not None:
            item.identical = True
            item.stored = "/".join(day_path + (duplicate,))
            continue
        stored_name = f"{item.clock} {item.name}" if highest == 0 else f"{item.clock}_{highest + 1} {item.name}"
        if blob not in held(entries):
            write_object(archive, "blob", item.data)
            written[blob] = ("blob", item.data)
        entries[stored_name] = ("100644", blob)
        item.stored = "/".join(day_path + (stored_name,))

    if not any(not item.identical for item in items):
        return count

    # Write the changed folder lists, deepest first, each into the one above it.
    for path in sorted(trees, key=len, reverse=True):
        data = tree_bytes(trees[path])
        sha = write_object(archive, "tree", data)
        written[sha] = ("tree", data)
        if path:
            load(path[:-1])[path[-1]] = ("40000", sha)
        else:
            new_root = sha
    new_count = count + count_step
    data = commit_bytes(new_root, parent, f"{message}\n\n{COUNT_LINE}: {new_count}")
    commit = write_object(archive, "commit", data)
    written[commit] = ("commit", data)
    update_head(archive, commit, parent)

    # Check every file just written. One deleted at that exact moment, like by the Workflow Extractor scrubbing
    # removed files, is written again.
    for sha, (kind, data) in written.items():
        if object_file_is_intact(archive, sha) or reader.exists(sha):
            continue
        write_object(archive, kind, data)
        if not object_file_is_intact(archive, sha):
            raise ArchiveError("A file stored in the archive couldn't be read back.")
    return new_count


def names_in_folder(archive, folder, reader=None):
    # The real names of everything stored in a folder of the archive (without day folders and times).
    names = set()
    own_reader = reader is None
    if own_reader:
        reader = Reader(archive)
    try:
        commit, root, _ = reader.head()
        if commit is None:
            return names
        entries = reader.tree(root)
        for part in folder:
            entry = entries.get(part)
            if not entry or entry[0] != "40000":
                return names
            entries = reader.tree(entry[1])
        for day, (mode, sha) in entries.items():
            if mode != "40000" or not DAY_FOLDER.match(day):
                continue
            for name in reader.tree(sha):
                match = TIMED_NAME.match(name)
                if match:
                    names.add(match.group(3))
    finally:
        if own_reader:
            reader.close()
    return names


def leftover_names(folder):
    # The real names of files waiting in a folder to be stored, without their date and .archive ending.
    names = set()
    try:
        listing = os.listdir(folder)
    except OSError:
        return names
    for file_name in listing:
        if is_hidden(file_name):
            continue
        match = DATED_LEFTOVER.match(file_name)
        if match:
            names.add(match.group(3))
            continue
        match = PLAIN_LEFTOVER.match(file_name)
        if match:
            names.add(match.group(1))
    return names


# Files waiting to be stored, from failed saves or renamed by hand.

def is_hidden(name):
    # Files and folders whose names start with a dot are treated as hidden, and are never picked up for the archive.
    return name.startswith(".")


def leftovers_in(folder):
    # The .archive files directly in a folder, as (path, day, clock, real name). Hidden files are skipped.
    found = []
    for file_name in sorted(os.listdir(folder)):
        if is_hidden(file_name):
            continue
        path = os.path.join(folder, file_name)
        match = DATED_LEFTOVER.match(file_name)
        if match:
            day, clock, name = match.groups()
        else:
            match = PLAIN_LEFTOVER.match(file_name)
            if not match:
                continue
            name = match.group(1)
            try:
                day, clock = stamp_parts(os.path.getmtime(path))
            except OSError:
                continue
        if os.path.isfile(path):
            found.append((path, day, clock, name))
    return found


def store_files(archive, output_folder, found, message, progress=None, total_text="Storing", should_stop=None):
    # Stores files from disk in batches, deleting each one once it's safely stored (or was already stored exactly
    # the same). found: [(path, day, clock, real name)]. Returns (stored, already there, couldn't be read).
    stored = identical = unreadable = 0
    total = len(found)
    done = 0
    for start in range(0, total, BATCH_SIZE):
        # Stopping between batches: everything stored so far stays stored, and the rest stays in its folders.
        if should_stop and should_stop():
            raise Stopped("Stopped.")
        items = []
        for path, day, clock, name in found[start:start + BATCH_SIZE]:
            try:
                with open(path, "rb") as file:
                    data = file.read()
            except OSError:
                unreadable += 1
                continue
            items.append(Item(folder_parts(os.path.dirname(path), output_folder), day, clock, name, data, path))
        if items:
            store(archive, items, count_step=0, message=message, skip_identical=True)
            for item in items:
                if item.identical:
                    identical += 1
                else:
                    stored += 1
                try:
                    os.chmod(item.source, 0o644)
                    os.remove(item.source)
                except OSError:
                    pass
        done = min(start + BATCH_SIZE, total)
        if progress:
            progress(f"{total_text} {done:,} of {total:,} file(s)...", done / total if total else 1)
    return stored, identical, unreadable


# The list of folders with files left from failed saves, kept in ComfyUI's user folder.

_list_lock = threading.Lock()


def read_failed_list(list_file):
    try:
        with open(list_file, "r", encoding="utf-8") as file:
            data = json.load(file)
        entries = data.get("folders", [])
        return [entry for entry in entries if isinstance(entry, dict) and "folder" in entry and "archive" in entry]
    except (OSError, ValueError, AttributeError):
        return []


def write_failed_list(list_file, entries):
    os.makedirs(os.path.dirname(list_file), exist_ok=True)
    temporary = list_file + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump({"folders": entries}, file, indent=2)
    os.replace(temporary, list_file)


def stored_folder(folder, output_folder):
    # Folders inside the output folder are kept relative to it.
    try:
        relative = os.path.relpath(folder, output_folder)
        if not relative.startswith(".."):
            return relative.replace("\\", "/")
    except ValueError:
        pass
    return folder


def listed_folder(entry_folder, output_folder):
    return entry_folder if os.path.isabs(entry_folder) else os.path.join(output_folder, entry_folder)


def add_failed_folder(list_file, folder, archive, output_folder):
    with _list_lock:
        entries = read_failed_list(list_file)
        record = {"folder": stored_folder(folder, output_folder), "archive": os.path.realpath(archive)}
        if record not in entries:
            entries.append(record)
            write_failed_list(list_file, entries)


def failed_folders(list_file, archive, output_folder):
    key = archive_key(archive)
    with _list_lock:
        return [
            listed_folder(entry["folder"], output_folder)
            for entry in read_failed_list(list_file)
            if archive_key(entry["archive"]) == key
        ]


def update_failed_folder(list_file, folder, archive, output_folder):
    # Takes a folder off the list once it has no files left to store, or no longer exists. A folder that can't be
    # reached right now, like an offline network drive, stays on the list.
    try:
        clean = not any(DATED_LEFTOVER.match(name) or PLAIN_LEFTOVER.match(name) for name in os.listdir(folder))
    except FileNotFoundError:
        clean = True
    except OSError:
        clean = False
    if not clean:
        return
    with _list_lock:
        entries = read_failed_list(list_file)
        key = archive_key(archive)
        kept = [
            entry for entry in entries
            if not (
                archive_key(entry["archive"]) == key
                and os.path.normcase(os.path.realpath(listed_folder(entry["folder"], output_folder)))
                == os.path.normcase(os.path.realpath(folder))
            )
        ]
        if kept != entries:
            write_failed_list(list_file, kept)


# Compression.

# How long Git can go without using the processor before it's shown as not responding.
IDLE_AFTER = 5.0


def process_tree_cpu(pid):
    # The processor time a program and the programs it started have used so far, or None if it can't be told.
    try:
        import psutil

        process = psutil.Process(pid)
        total = 0.0
        for each in [process] + process.children(recursive=True):
            try:
                times = each.cpu_times()
                total += times.user + times.system
            except psutil.Error:
                pass
        return total
    except Exception:
        return None


def stop_process(process):
    # Stops Git and the programs it started, and waits until they've actually ended.
    try:
        import psutil

        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.Error:
                pass
        parent.kill()
    except Exception:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True,
                           creationflags=NO_WINDOW)
        else:
            try:
                process.kill()
            except OSError:
                pass
    process.wait()


def run_watched(archive, args, text, progress, should_stop, started):
    # Runs a long Git command, showing the time so far and whether Git is working or not responding (not using the
    # processor for a while), and stopping it if asked.
    process = subprocess.Popen(
        ["git", "--git-dir", str(archive)] + args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=git_environment(),
        creationflags=NO_WINDOW,
    )
    error_output = []
    reader = threading.Thread(target=lambda: error_output.append(process.stderr.read()), daemon=True)
    reader.start()
    last_cpu = None
    last_active = time.time()
    # Abort is checked four times a second, so it responds right away. Whether Git has finished and the status, like
    # "Rebuilding the archive... [Status: working] (Elapsed Time: 1 min 23 sec)", are checked once a second.
    while process.poll() is None:
        now = time.time()
        if progress:
            cpu = process_tree_cpu(process.pid)
            status = ""
            if cpu is not None:
                if last_cpu is None or cpu > last_cpu + 0.01:
                    last_active = now
                last_cpu = cpu
                status = " [Status: working]" if now - last_active < IDLE_AFTER else " [Status: not responding]"
            progress(f"{text}{status} (Elapsed Time: {duration(now - started)})", None)
        for _ in range(4):
            if should_stop and should_stop():
                stop_process(process)
                reader.join(timeout=5)
                raise Stopped("Stopped.")
            time.sleep(0.25)
    reader.join(timeout=5)
    if process.returncode != 0:
        detail = b"".join(error_output).decode("utf-8", errors="replace").strip()
        raise ArchiveError(f"Git stopped with an error: {detail}")


def compress(archive, progress=None, rebuild=False, should_stop=None):
    # Compresses the archive: clears its history, so nothing old is kept for it, then packs everything into one
    # storage file. A rebuild redoes every match from scratch, for the smallest archive, then checks everything is
    # intact. Saves made meanwhile are safe: new files are only deleted by a later compression once they're two
    # weeks old and still unused, and the history is only replaced if nothing else changed it meanwhile.
    # Returns True if it ran, or False if another compression of this archive was already running.
    lock = compress_lock(archive)
    if not lock.acquire(blocking=False):
        return False
    try:
        started = time.time()
        # A fresh start for the history, keeping the count, so an interrupted compression is tried again.
        compressed_count = _restart_history(archive, None, "Compressing")
        run_git(archive, ["reflog", "expire", "--expire=now", "--all"])

        args = [
            "-c", f"pack.window={SIMILAR_FILE_SEARCH}",
            "-c", f"pack.depth={DIFFERENCE_CHAIN_LENGTH}",
            "-c", f"pack.compression={COMPRESSION_LEVEL}",
            "-c", f"pack.threads={THREADS}",
            "repack", "-a", "-d", "-q",
        ] + (["-f"] if rebuild else [])
        # Git only shows its own progress in a console window, so there's no real progress to show. Instead, whether
        # Git is working and the time so far are shown. The fraction is None: no bar.
        text = "Rebuilding the archive..." if rebuild else "Compressing the archive..."
        run_watched(archive, args, text, progress, should_stop, started)

        run_git(archive, ["prune", f"--expire={PRUNE_AFTER}"])
        # Saves stored while compressing still count toward the next one.
        _restart_history(archive, compressed_count, "Compressed")
        if rebuild:
            run_watched(archive, ["fsck", "--no-dangling", "--no-progress"], "Checking the archive...", progress,
                        should_stop, time.time())
        if progress:
            progress("Rebuilt" if rebuild else "Compressed", 1.0)
        return True
    finally:
        lock.release()


def archive_size(archive):
    # The total size of everything in the archive folder, in bytes.
    total = 0
    for dirpath, dirnames, filenames in os.walk(archive):
        for file_name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, file_name))
            except OSError:
                pass
    return total


def size_text(size):
    # Like "4.47 MB", or "812 KB" for under a megabyte.
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):,.2f} MB"
    return f"{max(size, 1) / 1024:,.0f} KB"


def duration(seconds):
    # Like "1 hr 1 min 23 sec".
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hr")
    if hours or minutes:
        parts.append(f"{minutes} min")
    parts.append(f"{seconds} sec")
    return " ".join(parts)


def _restart_history(archive, subtract, message):
    # Replaces the archive's newest entry with one holding the same files but no history, and moves the bookmark to
    # it. With subtract None, the count is kept; otherwise that many saves are taken off it. Returns the count before.
    for _ in range(20):
        reader = Reader(archive)
        try:
            commit, tree, count = reader.head()
        finally:
            reader.close()
        if commit is None:
            return 0
        new_count = count if subtract is None else max(count - subtract, 0)
        new = write_object(archive, "commit", commit_bytes(tree, None, f"{message}\n\n{COUNT_LINE}: {new_count}"))
        try:
            update_head(archive, new, commit)
        except ArchiveChanged:
            time.sleep(0.05)
            continue
        run_git(archive, ["update-ref", BOOKMARK_REF, new])
        return count
    raise ArchiveError("The archive kept changing, so it couldn't be compressed.")


# The wildcard backup, built in memory.

def wildcard_zip(folder, compression):
    # Every file in the folder and its subfolders, sorted by name, each with its own date, so an unchanged folder
    # always gives exactly the same zip. Returns None if the folder is empty.
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        for file_name in filenames:
            full_path = os.path.join(dirpath, file_name)
            files.append((os.path.relpath(full_path, folder).replace(os.sep, "/"), full_path))
    files.sort()
    if not files:
        return None
    uncompressed = compression == "none"
    method = zipfile.ZIP_STORED if uncompressed else zipfile.ZIP_DEFLATED
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
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
    return buffer.getvalue()


# Archive All: checking that a file really is a workflow or a wildcard backup.

WILDCARD_ENDINGS = (".txt", ".yaml", ".yml", ".json")


def is_workflow(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    # The full workflow, with its layout.
    if isinstance(data.get("nodes"), list) and ("links" in data or "last_node_id" in data or "version" in data):
        return True
    # The API format: every entry is a node with a class_type.
    return bool(data) and all(isinstance(node, dict) and "class_type" in node for node in data.values())


def is_wildcard_backup(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
    except (OSError, zipfile.BadZipFile, ValueError):
        return False
    return bool(names) and all(name.lower().endswith(WILDCARD_ENDINGS) for name in names)


def find_all(output_folder, archive, include_regular=True, extra_folders=()):
    # Walks the output folder for files to store: .archive files, and, with include_regular on, workflows and
    # wildcard backups (checked by their contents). Files and folders whose names start with a dot are hidden and
    # skipped, like the archive itself and the Workflow Extractor's _extracted folders, and so is the archive's
    # folder. Returns
    # [(path, day, clock, real name)].
    archive_real = os.path.normcase(os.path.realpath(archive))
    found = []
    seen = set()

    def add(path, day, clock, name):
        key = os.path.normcase(os.path.realpath(path))
        if key not in seen:
            seen.add(key)
            found.append((path, day, clock, name))

    for dirpath, dirnames, filenames in os.walk(output_folder):
        dirnames[:] = sorted(
            name for name in dirnames
            if not is_hidden(name)
            and os.path.normcase(os.path.realpath(os.path.join(dirpath, name))) != archive_real
        )
        for leftover in leftovers_in(dirpath):
            add(*leftover)
        if not include_regular:
            continue
        for file_name in sorted(filenames):
            if is_hidden(file_name):
                continue
            lower = file_name.lower()
            path = os.path.join(dirpath, file_name)
            if lower.endswith(".json") and is_workflow(path):
                pass
            elif lower.endswith(".zip") and is_wildcard_backup(path):
                pass
            else:
                continue
            try:
                day, clock = stamp_parts(os.path.getmtime(path))
            except OSError:
                continue
            add(path, day, clock, file_name)
    for folder in extra_folders:
        try:
            for leftover in leftovers_in(folder):
                add(*leftover)
        except OSError:
            pass
    return found
