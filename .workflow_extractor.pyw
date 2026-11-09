"""
Workflow Extractor: searches a Save Workflow archive by file name, and extracts or deletes the files found.

Save Workflow copies this next to each archive it saves to. Double-click it to open the archive. It finds the
archive by itself: it looks in its own folder first, then in the folders directly inside it. If it finds more
than one, it lists them so you can pick one. To always open a certain archive, set ARCHIVE_PATH below.

Type part of a file name and click Search to list the matching files, with the date and time each was stored.
A blank search lists everything. To list only files stored within certain days, click the From or To date box and
pick a day from the calendar; either can be left blank. The time box next to each date narrows it to the minute,
using a 24-hour clock; left blank, From starts at 00:00 and To ends at 23:59. A time picked without a date is for
today: Search fills in today's date. The list starts sorted by name, then by date, oldest first.
Click the Name or Stored heading to sort by that column first, and click it again to reverse its order. The other
column, in its own order, decides between files that are the same in the first. Then:
- Extract All or Extract Selected copies the files out, in the folders they were saved in, into a folder next
  to the archive named after it, like ".workflows_extracted". The originals remain in the archive.
- Delete All or Delete Selected deletes the files from the archive, after you type DELETE to confirm.

Requires Git for Windows (git must be available from the command line).
"""

import calendar
import datetime
import hashlib
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk

# The archive to use. Leave it blank to find it automatically. A relative path is relative to this script's
# folder; a full path is used as is.
ARCHIVE_PATH = ""

# Added to the archive's name to name the folder files are extracted into, next to the archive.
EXTRACT_SUFFIX = "_extracted"

# How many results are added to the list at a time. More are added as you scroll near the bottom.
LIST_BATCH = 1000

# Branch name used inside the archive.
BRANCH = "main"

# Marks an archive made by Save Workflow. Archives without it are never used.
MARKER_KEY = "workflowarchive.format"
MARKER_VALUE = "1"

# Where Save Workflow keeps its count of saves since the last compression.
BOOKMARK_REF = "refs/workflowarchive/compressed"

# Shown when hovering over the "Scrub immediately" box.
SCRUB_TOOLTIP = [
    # (text, bold, extra space above, centered)
    ("Scrubs the deleted files’ data from the archive right away.", False, False, False),
    ("WARNING!!!", True, True, True),
    (
        "Only use this while nothing else is using the archive: if ComfyUI saves to it at the same moment, the files "
        "being saved can be lost, and the archive itself can be damaged so that its files can't be read.",
        True,
        False,
        False,
    ),
    (
        "Otherwise, the data is scrubbed by a later compression operation: usually the next one, or for files saved "
        "since the last compression, the first compression that happens at least 27 hours after they were saved.",
        False,
        True,
        False,
    ),
]

TITLE = "Workflow Extractor"
# The extractor's own version, separate from Save Workflow's; bumped only when the extractor itself changes.
VERSION = "1.0.0"


def window_title(archive):
    # Like "Workflow Extractor 1.7.1: .workflows". Also used to find this archive's window when it's already open.
    return f"{TITLE} {VERSION}: {archive.name}"

BASE = Path(__file__).resolve().parent

# Stored names look like "Text2Img/2026-11-03/10-21-05 my_clip.json", or "10-21-05_2 my_clip.json" when another
# file with the same name was stored in the same second. The day folder and the time are hidden in the list.
DAY_FOLDER = re.compile(r"\d{4}-\d{2}-\d{2}$")
TIMED_NAME = re.compile(r"(\d{2})-(\d{2})-(\d{2})(?:_(\d+))? (.+)$", re.DOTALL)

# Keeps Git from opening a console window for each command on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Used for the archive's own records of each change, in case the archive has no name set.
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Workflow Archive",
    "GIT_AUTHOR_EMAIL": "workflow-archive@localhost",
    "GIT_COMMITTER_NAME": "Workflow Archive",
    "GIT_COMMITTER_EMAIL": "workflow-archive@localhost",
}


def run_git(archive, args, input_bytes=None, env=None):
    # Text is sent and received as raw bytes, so Windows doesn't change line breaks inside file names.
    result = subprocess.run(
        ["git", "--git-dir", str(archive)] + args,
        input=input_bytes,
        capture_output=True,
        env=env,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {args[0]} failed:\n{error}")
    return result.stdout


def is_marked_archive(folder):
    if not (folder / "HEAD").is_file() or not (folder / "config").is_file():
        return False
    try:
        value = run_git(folder, ["config", "--get", MARKER_KEY]).decode("utf-8").strip()
    except RuntimeError:
        return False
    return value == MARKER_VALUE


def find_archives():
    # Returns (the archive folders found, in alphabetical order, a description of where it looked).
    if ARCHIVE_PATH.strip():
        folder = Path(ARCHIVE_PATH.strip())
        if not folder.is_absolute():
            folder = BASE / folder
        folder = folder.resolve()
        return ([folder] if is_marked_archive(folder) else []), f"ARCHIVE_PATH:\n{folder}"
    if is_marked_archive(BASE):
        return [BASE], ""
    try:
        folders = sorted((item for item in BASE.iterdir() if item.is_dir()), key=lambda item: item.name.lower())
    except OSError:
        folders = []
    return [folder for folder in folders if is_marked_archive(folder)], f"{BASE}\nand the folders directly inside it."


def pick_archive(root, archives):
    # A list of the archives found, to pick the one to open. OK only works once one is selected. Returns the archive,
    # or None if canceled.
    dialog = tk.Toplevel(root)
    dialog.title(TITLE)
    dialog.resizable(False, False)
    result = {"archive": None}

    frame = ttk.Frame(dialog, padding=14)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Select which archive to open:", justify="left").pack(anchor="w")

    holder = ttk.Frame(frame)
    holder.pack(fill="both", expand=True, pady=(8, 0))
    names = tk.Listbox(
        holder, selectmode="browse", activestyle="none", exportselection=False,
        height=min(len(archives), 10), width=max([40] + [len(archive.name) + 2 for archive in archives]),
    )
    for archive in archives:
        names.insert("end", archive.name)
    names.pack(side="left", fill="both", expand=True)
    if len(archives) > 10:
        scroll = ttk.Scrollbar(holder, orient="vertical", command=names.yview)
        scroll.pack(side="right", fill="y")
        names.configure(yscrollcommand=scroll.set)

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(14, 0))

    def accept():
        chosen = names.curselection()
        if chosen:
            result["archive"] = archives[chosen[0]]
            dialog.destroy()

    # OK in the middle, Cancel at the far right.
    ok_button = ttk.Button(buttons, text="OK", command=accept, state="disabled")
    place_buttons(buttons, ttk.Button(buttons, text="Cancel", command=dialog.destroy), ok_button)
    names.bind("<<ListboxSelect>>", lambda _: ok_button.configure(state="normal" if names.curselection() else "disabled"))

    # In the middle of the screen, a little above center.
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
    y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 3
    dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    dialog.lift()
    dialog.focus_force()
    names.focus_set()
    dialog.wait_window()
    return result["archive"]


def current_commit(archive):
    result = subprocess.run(
        ["git", "--git-dir", str(archive), "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}"],
        capture_output=True,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8").strip()


def read_tree(archive, commit):
    # Every stored file as (mode, contents id, stored name).
    if commit is None:
        return []
    output = run_git(archive, ["ls-tree", "-r", "-z", commit])
    entries = []
    for line in output.split(b"\0"):
        if not line:
            continue
        info, _, name = line.partition(b"\t")
        mode, kind, content_id = info.decode("utf-8").split()
        if kind == "blob":
            entries.append((mode, content_id, name.decode("utf-8")))
    return entries


class Entry:
    # One stored file, as shown in the list.
    __slots__ = ("stored", "content_id", "shown", "day", "minute", "date", "keys")

    def __init__(self, stored, content_id):
        self.stored = stored
        self.content_id = content_id
        parts = stored.split("/")
        match = TIMED_NAME.match(parts[-1]) if len(parts) >= 2 and DAY_FOLDER.match(parts[-2]) else None
        if match:
            day = parts[-2]
            hours, minutes, seconds, counter, name = match.groups()
            self.shown = "/".join(parts[:-2] + [name])
            self.day = day
            self.minute = f"{day} {hours}:{minutes}"
            self.date = f"{day} {hours}:{minutes}:{seconds}"
            when = (day, hours, minutes, seconds, int(counter or 1))
        else:
            # Not in the archive's usual layout: shown as stored, without a date.
            self.shown = stored
            self.day = ""
            self.minute = ""
            self.date = ""
            when = ("", "", "", "", 0)
        # What each column sorts by.
        self.keys = {"name": (self.shown.lower(), self.shown), "date": when}


def list_archive(archive):
    return [Entry(name, content_id) for _, content_id, name in read_tree(archive, current_commit(archive))]


def extract_parts(shown):
    # The folders and name to extract to, inside the extract folder. A drive like "D:" becomes a folder named
    # "D", and a network path like "\\server\share" becomes the folders "server" and "share".
    parts = shown.split("/")
    first = parts[0]
    if re.fullmatch(r"[A-Za-z]:", first):
        parts[0] = first[0]
    elif first.startswith("\\\\"):
        parts = [part for part in first.split("\\") if part] + parts[1:]
    for part in parts:
        if part in ("", ".", "..") or any(character in part for character in '\\:*?"<>|'):
            raise RuntimeError("A file in the archive has a folder path that can't be extracted.")
    return parts


def unique_path(folder, file_name):
    # Don't overwrite: add (2), (3), ... if a file with that name is already there.
    target = folder / file_name
    counter = 2
    while target.exists():
        target = folder / f"{Path(file_name).stem} ({counter}){Path(file_name).suffix}"
        counter += 1
    return target


def extract(archive, entries, folder, progress):
    # Reads all the files through one Git process, which is much faster than one per file.
    total = len(entries)
    folder.mkdir(exist_ok=True)
    process = subprocess.Popen(
        ["git", "--git-dir", str(archive), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    saved = 0
    try:
        for entry in entries:
            process.stdin.write(f"{entry.content_id}\n".encode("utf-8"))
            process.stdin.flush()
            header = process.stdout.readline().decode("utf-8").split()
            if len(header) != 3 or header[1] != "blob":
                raise RuntimeError(f"Couldn't read this file from the archive:\n{entry.shown}")
            contents = process.stdout.read(int(header[2]))
            process.stdout.read(1)
            parts = extract_parts(entry.shown)
            target_folder = folder.joinpath(*parts[:-1])
            target_folder.mkdir(parents=True, exist_ok=True)
            unique_path(target_folder, parts[-1]).write_bytes(contents)
            saved += 1
            progress(f"Extracting {saved:,} of {total:,} file(s)...")
    finally:
        process.stdin.close()
        process.wait()
    return saved


def delete(archive, entries, scrub, progress):
    to_delete = {entry.stored for entry in entries}
    env = dict(os.environ)
    env.update(GIT_IDENTITY)
    progress(f"Deleting {len(to_delete):,} file(s)...")

    # The remaining files are saved as a fresh start with no history, so nothing in the archive refers to the
    # deleted files anymore. The change is only made if nothing else changed the archive in the meantime, like
    # ComfyUI saving to it; if something did, it's done again from the archive as it is now.
    for _ in range(50):
        old = current_commit(archive)
        kept = [item for item in read_tree(archive, old) if item[2] not in to_delete]
        with tempfile.TemporaryDirectory() as temporary:
            index_env = dict(env)
            index_env["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
            index_text = "".join(f"{mode} {content_id}\t{name}\n" for mode, content_id, name in kept)
            run_git(archive, ["update-index", "--add", "--index-info"], index_text.encode("utf-8"), index_env)
            tree = run_git(archive, ["write-tree"], env=index_env).decode("utf-8").strip()
        message = f"Deleted {len(to_delete)} workflow file(s)"
        commit = run_git(archive, ["commit-tree", tree, "-m", message], env=env).decode("utf-8").strip()
        try:
            run_git(archive, ["update-ref", f"refs/heads/{BRANCH}", commit, old or ("0" * 40)])
            break
        except RuntimeError:
            time.sleep(0.1)
            continue
    else:
        raise RuntimeError("The archive kept changing while deleting. Nothing was deleted. Try again.")

    # Save Workflow's bookmark would otherwise keep the old history, and the deleted files with it.
    run_git(archive, ["update-ref", BOOKMARK_REF, commit])
    run_git(archive, ["reflog", "expire", "--expire=now", "--all"])

    if scrub:
        progress("Scrubbing the deleted files’ data. This can take a minute...")
        run_git(archive, ["repack", "-a", "-d", "-q"])
        run_git(archive, ["prune", "--expire=now"])

    # Check the files are gone.
    remaining = {name for _, _, name in read_tree(archive, current_commit(archive))}
    if remaining & to_delete:
        raise RuntimeError(f"{len(remaining & to_delete)} file(s) are still in the archive.")
    return len(to_delete)


def place_buttons(frame, cancel_button, action_button):
    # A confirmation's buttons: the action in the middle, Cancel at the far right, below the window's own X.
    frame.columnconfigure(0, weight=1, uniform="sides")
    frame.columnconfigure(2, weight=1, uniform="sides")
    action_button.grid(row=0, column=1)
    cancel_button.grid(row=0, column=2, sticky="e")


class Tooltip:
    # A small note that appears while the mouse is over a widget.
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        self.pending = None
        widget.bind("<Enter>", self.schedule)
        widget.bind("<Leave>", self.hide)

    def schedule(self, _event=None):
        # Shown straight away, and hidden when the mouse leaves. It only reacts to the mouse entering and leaving,
        # so it does no work otherwise.
        self.show()

    def show(self):
        if self.window:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window.wm_geometry(f"+{x}+{y}")
        # The text is either plain, or a list of (text, bold, extra space above, centered), each on its own line.
        parts = [(self.text, False, False, False)] if isinstance(self.text, str) else self.text
        frame = tk.Frame(self.window, background="#ffffe0", relief="solid", borderwidth=1, padx=6, pady=4)
        frame.pack()
        font = tkfont.nametofont("TkDefaultFont")
        bold = font.copy()
        bold.configure(weight="bold")
        self.bold_font = bold
        for text, is_bold, space_above, centered in parts:
            tk.Label(
                frame, text=text, justify="left", anchor="w", wraplength=380, background="#ffffe0",
                font=bold if is_bold else font,
            ).pack(anchor="center" if centered else "w", pady=(font.metrics("linespace") if space_above else 0, 0))

    def hide(self, _event=None):
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window:
            self.window.destroy()
            self.window = None


class DatePicker:
    # A small calendar that opens below a date box. Clicking a day fills the box, Clear empties it.
    def __init__(self, parent, anchor, variable):
        self.variable = variable
        self.today = datetime.date.today()
        try:
            self.picked = datetime.date.fromisoformat(variable.get())
        except ValueError:
            self.picked = None
        shown = self.picked or self.today

        window = tk.Toplevel(parent)
        self.window = window
        window.title("Pick a date")
        window.transient(parent)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)

        header = ttk.Frame(frame)
        header.pack(fill="x")
        ttk.Button(header, text="<", width=3, command=lambda: self.move(-1)).pack(side="left")
        # Each month shows its number too, like "1 - January".
        self.month = ttk.Combobox(
            header, values=[f"{number} - {calendar.month_name[number]}" for number in range(1, 13)], state="readonly",
            width=14,
        )
        self.month.current(shown.month - 1)
        self.month.bind("<<ComboboxSelected>>", lambda event: self.draw_days())
        self.month.pack(side="left", padx=(6, 4))
        self.year = tk.StringVar(value=str(shown.year))
        year_box = ttk.Spinbox(
            header, from_=1980, to=2999, textvariable=self.year, width=6, command=self.draw_days
        )
        year_box.pack(side="left", padx=(0, 6))
        year_box.bind("<KeyRelease>", lambda event: self.draw_days())
        ttk.Button(header, text=">", width=3, command=lambda: self.move(1)).pack(side="left")

        self.days = ttk.Frame(frame)
        self.days.pack(pady=(8, 0))

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Cancel", command=window.destroy).pack(side="right")
        ttk.Button(bottom, text="Clear", command=self.clear).pack(side="right", padx=(0, 6))
        self.draw_days()

        window.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        # Kept fully on the screen. It doesn't block the main window, so the main window can still be used or closed
        # while it's open.
        x = min(x, window.winfo_screenwidth() - window.winfo_reqwidth())
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        # It becomes the active window, so a click anywhere on the main window, title bar included, makes the main
        # window active again, which closes it.
        window.focus_force()

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()

    def shown_month(self):
        try:
            year = int(self.year.get())
        except ValueError:
            return None
        if not 1 <= year <= 9999:
            return None
        return year, self.month.current() + 1

    def move(self, step):
        shown = self.shown_month()
        if shown is None:
            return
        year, month = shown
        month += step
        if month < 1:
            year, month = year - 1, 12
        elif month > 12:
            year, month = year + 1, 1
        self.year.set(str(year))
        self.month.current(month - 1)
        self.draw_days()

    def draw_days(self):
        shown = self.shown_month()
        if shown is None:
            return
        year, month = shown
        for widget in self.days.winfo_children():
            widget.destroy()
        week_starts_sunday = calendar.Calendar(firstweekday=6)
        for column, name in enumerate(("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa")):
            ttk.Label(self.days, text=name, anchor="center", width=4).grid(row=0, column=column)
        for row, week in enumerate(week_starts_sunday.monthdayscalendar(year, month), start=1):
            for column, day in enumerate(week):
                if not day:
                    continue
                date = datetime.date(year, month, day)
                button = tk.Button(
                    self.days, text=str(day), width=3, relief="flat", command=lambda date=date: self.pick(date)
                )
                if date == self.picked:
                    button.configure(background="#1976d2", foreground="white", activebackground="#1565c0")
                if date == self.today:
                    button.configure(font=("TkDefaultFont", 9, "bold"), relief="groove")
                button.grid(row=row, column=column, padx=1, pady=1)

    def pick(self, date):
        self.variable.set(date.isoformat())
        self.window.destroy()

    def clear(self):
        self.variable.set("")
        self.window.destroy()


class TimePicker:
    # A small pop-up for picking a time on a 24-hour clock, like the calendar: an hour list and a minute list. OK fills
    # the box, Clear empties it.
    def __init__(self, parent, anchor, variable, default_hour, default_minute):
        self.variable = variable
        try:
            hour, minute = (int(part) for part in variable.get().split(":"))
        except ValueError:
            hour, minute = default_hour, default_minute

        window = tk.Toplevel(parent)
        self.window = window
        window.title("Pick a time")
        window.transient(parent)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)

        row = ttk.Frame(frame)
        row.pack(anchor="w")
        ttk.Label(row, text="Hour:").pack(side="left")
        self.hour = ttk.Combobox(row, values=[f"{number:02d}" for number in range(24)], state="readonly", width=4)
        self.hour.current(hour)
        self.hour.pack(side="left", padx=(4, 10))
        ttk.Label(row, text="Minute:").pack(side="left")
        self.minute = ttk.Combobox(row, values=[f"{number:02d}" for number in range(60)], state="readonly", width=4)
        self.minute.current(minute)
        self.minute.pack(side="left", padx=(4, 0))

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Cancel", command=window.destroy).pack(side="right")
        ttk.Button(bottom, text="Clear", command=self.clear).pack(side="right", padx=(0, 6))
        ttk.Button(bottom, text="OK", command=self.accept).pack(side="right", padx=(0, 6))

        # It doesn't block the main window, so the main window can still be used or closed while it's open.
        window.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        # Kept fully on the screen.
        x = min(x, window.winfo_screenwidth() - window.winfo_reqwidth())
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        # It becomes the active window, so a click anywhere on the main window, title bar included, makes the main
        # window active again, which closes it.
        window.focus_force()

    def accept(self):
        self.variable.set(f"{self.hour.get()}:{self.minute.get()}")
        self.close()

    def clear(self):
        self.variable.set("")
        self.close()

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()


class ExtractorWindow:
    def __init__(self, root, archive):
        self.root = root
        self.archive = archive
        self.extract_folder = archive.parent / (archive.name + EXTRACT_SUFFIX)
        self.matches = []
        self.shown = 0
        self.busy = False
        # The list starts sorted by name, A first, then by date, oldest first. Each column keeps its own direction.
        self.sort_column = "name"
        self.sort_reversed = {"name": False, "date": False}
        self.progress_text = None
        self.results_queue = queue.Queue()
        root.title(window_title(archive))
        root.geometry("1140x520")
        root.minsize(640, 320)

        style = ttk.Style(root)
        style.configure("Results.Treeview", font=("Consolas", 10), rowheight=20)

        top = ttk.Frame(root, padding=(10, 10, 10, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Search:").pack(side="left")
        self.search_text = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search_text)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.focus_set()
        # Pressing Enter in the search field does the same as clicking Search.
        entry.bind("<Return>", lambda event: self.search())
        entry.bind("<KP_Enter>", lambda event: self.search())
        # Optional dates to limit the search to, picked from a calendar. Blank means no limit on that side.
        self.from_text = tk.StringVar()
        self.to_text = tk.StringVar()
        self.from_time = tk.StringVar()
        self.to_time = tk.StringVar()
        self.picker = None
        # The confirmation window that's open, if any.
        self.dialog = None
        # Clicking anywhere in the main window outside the calendar, time picker or a confirmation window closes it,
        # the same as Cancel. Clicks on the title bar go to Windows instead, but each of these is the active window
        # while it's open, so the main window becoming active again closes it, and so does the main window being
        # moved.
        root.bind_all("<Button-1>", self.close_popups_on_click, add="+")
        root.bind("<FocusIn>", self.close_popups_on_click, add="+")
        root.bind("<Configure>", self.close_popups_on_move, add="+")
        self.window_position = None
        for label, variable, time_variable, default_time in (
            ("From:", self.from_text, self.from_time, (0, 0)),
            ("To:", self.to_text, self.to_time, (23, 59)),
        ):
            ttk.Label(top, text=label).pack(side="left", padx=(6, 0))
            date_box = ttk.Entry(top, textvariable=variable, width=11, state="readonly")
            date_box.pack(side="left", padx=(4, 0))
            date_box.bind("<Button-1>", lambda event, box=date_box, variable=variable: self.pick_date(box, variable))
            time_box = ttk.Entry(top, textvariable=time_variable, width=6, state="readonly")
            time_box.pack(side="left", padx=(4, 0))
            time_box.bind(
                "<Button-1>",
                lambda event, box=time_box, variable=time_variable, time=default_time: self.pick_time(box, variable, time),
            )
        self.search_button = ttk.Button(top, text="Search", command=self.search)
        self.search_button.pack(side="left", padx=(10, 0))

        middle = ttk.Frame(root, padding=(10, 0, 10, 0))
        middle.pack(fill="both", expand=True)
        # Several lines can be selected: click one, then Shift-click another to select everything between them,
        # or Ctrl-click to add or remove single lines.
        self.results = ttk.Treeview(
            middle, columns=("name", "date"), show="headings", selectmode="extended", style="Results.Treeview"
        )
        # Clicking a heading sorts by that column, and clicking it again reverses the order.
        self.results.heading("name", anchor="w", command=lambda: self.sort_by("name"))
        self.results.heading("date", anchor="w", command=lambda: self.sort_by("date"))
        self.update_headings()
        self.results.column("name", anchor="w", stretch=True, width=900)
        self.results.column("date", anchor="w", stretch=False, width=170)
        scroll_y = ttk.Scrollbar(middle, orient="vertical", command=self.results.yview)
        scroll_x = ttk.Scrollbar(middle, orient="horizontal", command=self.results.xview)
        self.scroll_y = scroll_y
        self.results.configure(yscrollcommand=self.on_scroll, xscrollcommand=scroll_x.set)
        self.results.grid(row=0, column=0, sticky="nsew")
        # Double-click a line, or right-click it and choose Copy, to copy that line's name to the clipboard.
        self.results.bind("<Double-Button-1>", self.copy_clicked_line)
        self.results.bind("<Button-3>", self.show_copy_menu)
        self.results.bind("<<TreeviewSelect>>", lambda event: self.update_buttons())
        self.copy_menu = tk.Menu(root, tearoff=0)
        self.copy_menu.add_command(label="Copy", command=self.copy_selected_line)
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        middle.rowconfigure(0, weight=1)
        middle.columnconfigure(0, weight=1)

        bottom = ttk.Frame(root, padding=(10, 6, 10, 10))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left", fill="x", expand=True)
        self.extract_selected_button = ttk.Button(bottom, text="Extract Selected", command=self.extract_selected)
        self.extract_selected_button.pack(side="right")
        self.extract_button = ttk.Button(bottom, text="Extract All", command=self.extract_all)
        self.extract_button.pack(side="right", padx=(0, 6))
        self.delete_selected_button = ttk.Button(bottom, text="Delete Selected", command=self.delete_selected)
        self.delete_selected_button.pack(side="right", padx=(0, 24))
        self.delete_button = ttk.Button(bottom, text="Delete All", command=self.delete_all)
        self.delete_button.pack(side="right", padx=(0, 6))
        self.update_buttons()

        self.run_in_background(
            lambda progress: list_archive(self.archive),
            lambda entries: self.status.configure(text=f"The archive holds {len(entries):,} file(s)."),
            "Reading the archive...",
        )

    # Long tasks run in the background, so the window stays responsive and can show their progress.

    def run_in_background(self, task, on_done, starting_text):
        self.busy = True
        self.progress_text = starting_text
        self.status.configure(text=starting_text)
        self.update_buttons()

        def progress(text):
            self.progress_text = text

        def work():
            try:
                self.results_queue.put(("done", task(progress)))
            except Exception as error:
                self.results_queue.put(("error", error))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, lambda: self.check_background(on_done))

    def check_background(self, on_done):
        try:
            kind, value = self.results_queue.get_nowait()
        except queue.Empty:
            self.status.configure(text=self.progress_text)
            self.root.after(100, lambda: self.check_background(on_done))
            return
        self.busy = False
        self.update_buttons()
        if kind == "error":
            self.status.configure(text="Stopped by an error.")
            self.message(str(value))
            return
        on_done(value)

    def update_buttons(self):
        state = "disabled" if self.busy else "normal"
        has_matches = "normal" if self.matches and not self.busy else "disabled"
        has_selected = "normal" if self.results.selection() and not self.busy else "disabled"
        self.search_button.configure(state=state)
        self.extract_button.configure(state=has_matches)
        self.delete_button.configure(state=has_matches)
        self.extract_selected_button.configure(state=has_selected)
        self.delete_selected_button.configure(state=has_selected)

    # The list of results.

    def on_scroll(self, first, last):
        # Update the scroll bar, and add the next batch of results when the view nears the bottom.
        self.scroll_y.set(first, last)
        if float(last) >= 0.98 and self.shown < len(self.matches):
            self.root.after_idle(self.show_more)

    def show_more(self):
        if self.shown >= len(self.matches):
            return
        end = min(self.shown + LIST_BATCH, len(self.matches))
        for index in range(self.shown, end):
            entry = self.matches[index]
            self.results.insert("", "end", iid=str(index), values=(entry.shown, entry.date))
        self.shown = end
        self.update_match_count()

    def update_match_count(self):
        if self.shown < len(self.matches):
            self.status.configure(
                text=f"{len(self.matches):,} match(es), showing the first {self.shown:,}. Scroll down for more."
            )
        else:
            self.status.configure(text=f"{len(self.matches):,} match(es).")

    def update_headings(self):
        # Arrows point up for oldest or A first. The column sorted by first has a solid arrow, the other an outline.
        for column, title in (("name", "Name"), ("date", "Stored")):
            if column == self.sort_column:
                arrow = " ▼" if self.sort_reversed[column] else " ▲"
            else:
                arrow = " ▽" if self.sort_reversed[column] else " △"
            self.results.heading(column, text=title + arrow)

    def sort_matches(self):
        # Sorts by the other column first, then by the main one, which keeps the other column's order among files
        # that are the same in the main one.
        other = "name" if self.sort_column == "date" else "date"
        for column in (other, self.sort_column):
            self.matches.sort(key=lambda entry: entry.keys[column], reverse=self.sort_reversed[column])

    def sort_by(self, column):
        # Clicking the column already sorted by first reverses it. Clicking the other one makes it the first, in
        # the direction it had.
        if self.busy:
            return
        if column == self.sort_column:
            self.sort_reversed[column] = not self.sort_reversed[column]
        else:
            self.sort_column = column
        self.update_headings()
        if not self.matches:
            return
        # Show the list again from the top, in the new order.
        self.sort_matches()
        self.results.delete(*self.results.get_children())
        self.shown = 0
        self.show_more()
        self.update_buttons()

    def selected_entries(self):
        return [self.matches[int(item)] for item in self.results.selection()]

    def line_at(self, event):
        item = self.results.identify_row(event.y)
        if not item:
            return None
        # Right-clicking a line that's already selected keeps the other selected lines.
        if item not in self.results.selection():
            self.results.selection_set(item)
        self.update_buttons()
        return item

    def copy_line(self, item):
        text = self.matches[int(item)].shown
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.status.configure(text=f"Copied: {text}")

    def copy_clicked_line(self, event):
        item = self.line_at(event)
        if item is not None:
            self.copy_line(item)

    def show_copy_menu(self, event):
        if self.line_at(event) is not None:
            self.copy_menu.tk_popup(event.x_root, event.y_root)
            self.copy_menu.grab_release()

    def copy_selected_line(self):
        selected = self.results.selection()
        if selected:
            self.copy_line(selected[0])

    # Searching.

    def close_popups(self, keep=None):
        # Closes the picker and the confirmation window, except one the click was inside.
        if self.picker is not None and not str(keep or "").startswith(str(self.picker.window)):
            self.picker.close()
            self.picker = None
        if self.dialog is not None and not str(keep or "").startswith(str(self.dialog)):
            if self.dialog.winfo_exists():
                self.dialog.destroy()
            self.dialog = None

    def close_popups_on_click(self, event):
        self.close_popups(keep=event.widget)

    def close_popups_on_move(self, event):
        if event.widget is not self.root:
            return
        position = (self.root.winfo_rootx(), self.root.winfo_rooty())
        if self.window_position is not None and position != self.window_position:
            self.close_popups()
        self.window_position = position

    def show_dialog(self, dialog, focus_widget):
        # Shows a confirmation window as the active window, and waits until it's closed, by its buttons, its X, or a
        # click on the main window.
        dialog.focus_force()
        focus_widget.focus_set()
        self.dialog = dialog
        self.root.wait_window(dialog)
        self.dialog = None

    def pick_date(self, box, variable):
        # Only one calendar or time picker is open at a time.
        if self.picker is not None:
            self.picker.close()
            self.picker = None
        if not self.busy:
            self.picker = DatePicker(self.root, box, variable)
        return "break"

    def pick_time(self, box, variable, default_time):
        if self.picker is not None:
            self.picker.close()
            self.picker = None
        if not self.busy:
            self.picker = TimePicker(self.root, box, variable, *default_time)
        return "break"

    def search(self):
        if self.busy:
            return
        text = self.search_text.get().strip().lower()
        # A time picked without a date is for today, so today's date is filled in.
        for day, clock in ((self.from_text, self.from_time), (self.to_text, self.to_time)):
            if clock.get() and not day.get():
                day.set(datetime.date.today().isoformat())
        # Compared as "2026-10-28 14:30". A blank time means the start of the From day, or the end of the To day.
        first = f"{self.from_text.get()} {self.from_time.get() or '00:00'}" if self.from_text.get() else ""
        last = f"{self.to_text.get()} {self.to_time.get() or '23:59'}" if self.to_text.get() else ""
        if first and last and first > last:
            self.message("The From date and time are after the To date and time.")
            return
        self.results.delete(*self.results.get_children())
        self.matches = []
        self.shown = 0

        def task(progress):
            entries = list_archive(self.archive)
            # A blank search lists everything in the archive. Searches match the names as shown. With dates picked,
            # only files stored on or between them are listed, including the whole To minute itself.
            if text:
                entries = [entry for entry in entries if text in entry.shown.lower()]
            if first:
                entries = [entry for entry in entries if entry.minute and entry.minute >= first]
            if last:
                entries = [entry for entry in entries if entry.minute and entry.minute <= last]
            return entries

        def done(matches):
            self.matches = matches
            self.sort_matches()
            # Show the first batch now; more are added as you scroll near the bottom.
            self.show_more()
            self.update_match_count()
            self.update_buttons()

        self.run_in_background(task, done, "Searching...")

    # Extracting.

    def extract_all(self):
        self.extract_entries(list(self.matches))

    def extract_selected(self):
        self.extract_entries(self.selected_entries())

    def message(self, text):
        # A message with an OK button in the center, used instead of the Windows message box, whose OK sits at the
        # right. Like the confirmations, a click on the main window closes it.
        dialog = tk.Toplevel(self.root)
        dialog.title(TITLE)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=text, justify="left").pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        buttons.columnconfigure(0, weight=1, uniform="sides")
        buttons.columnconfigure(2, weight=1, uniform="sides")
        ok_button = ttk.Button(buttons, text="OK", command=dialog.destroy)
        ok_button.grid(row=0, column=1)

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.show_dialog(dialog, ok_button)

    def confirm(self, title, text, ok_text, centered=False):
        # A small window with the given message and two buttons: ok_text and Cancel. Returns True for ok_text.
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        result = {"confirmed": False}

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=text, justify="center" if centered else "left").pack(anchor="center" if centered else "w")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))

        def accept():
            result["confirmed"] = True
            dialog.destroy()

        # ok_text in the middle, Cancel at the far right.
        ok_button = ttk.Button(buttons, text=ok_text, command=accept)
        place_buttons(buttons, ttk.Button(buttons, text="Cancel", command=dialog.destroy), ok_button)

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.show_dialog(dialog, ok_button)
        return result["confirmed"]

    def extract_entries(self, entries):
        if not entries or self.busy:
            return
        folder = self.extract_folder
        count = len(entries)
        if not self.confirm(
            "Confirm Extract",
            f"Extract {count:,} file{'' if count == 1 else 's'} to:\n{folder}\n\n"
            "The originals will remain in the archive.",
            "Continue",
        ):
            self.status.configure(text="Nothing was extracted.")
            return

        def done(saved):
            self.status.configure(text=f"Extracted {saved:,} file(s).")
            self.message(
                f"Extracted {saved:,} file{'' if saved == 1 else 's'} to:\n{folder}\n\n"
                "The originals will remain in the archive."
            )

        self.run_in_background(
            lambda progress: extract(self.archive, entries, folder, progress), done, "Extracting..."
        )

    # Deleting.

    def confirm_delete(self, entries):
        # A confirmation window whose Delete button only works once DELETE is typed, in capitals.
        # It's created fresh each time, so the typing box always starts empty and the box unchecked.
        count = len(entries)
        preview = "\n".join(f"{entry.shown}   {entry.date}".rstrip() for entry in entries[:10])
        if count > 10:
            preview += f"\n...and {count - 10:,} more"

        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm Delete")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        result = {"confirmed": False, "scrub": False}

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=f"Permanently delete {count:,} file(s) from the archive?\n\n{preview}\n\n"
            "They can't be recovered from the archive afterward.",
            justify="left",
        ).pack(anchor="w")

        scrub = tk.BooleanVar(value=False)
        scrub_box = ttk.Checkbutton(frame, text="Scrub immediately", variable=scrub)
        scrub_box.pack(anchor="w", pady=(12, 0))
        Tooltip(scrub_box, SCRUB_TOOLTIP)

        ttk.Label(frame, text="Type DELETE to confirm:").pack(anchor="w", pady=(12, 2))
        typed = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=typed, width=20)
        entry.pack(anchor="w")
        entry.focus_set()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))

        def do_delete():
            if typed.get() == "DELETE":
                result["confirmed"] = True
                result["scrub"] = scrub.get()
                dialog.destroy()

        # Delete in the middle, Cancel at the far right.
        delete_button = ttk.Button(buttons, text="Delete", command=do_delete, state="disabled")
        cancel_button = ttk.Button(buttons, text="Cancel", command=dialog.destroy)
        place_buttons(buttons, cancel_button, delete_button)

        def update_button(*_):
            delete_button.configure(state="normal" if typed.get() == "DELETE" else "disabled")

        typed.trace_add("write", update_button)

        # Keep the window on top of the main window until it's closed.
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.show_dialog(dialog, entry)
        return result["confirmed"], result["scrub"]

    def delete_all(self):
        self.delete_entries(list(self.matches))

    def delete_selected(self):
        self.delete_entries(self.selected_entries())

    def delete_entries(self, entries):
        if not entries or self.busy:
            return
        confirmed, scrub = self.confirm_delete(entries)
        # One last chance to back out.
        if confirmed:
            count = len(entries)
            note = "\nTheir data will be scrubbed right away." if scrub else ""
            confirmed = self.confirm(
                "Confirm Delete",
                f"Are you sure?\n\n{count:,} file{'' if count == 1 else 's'} will be permanently deleted from the "
                f"archive.{note}",
                "Continue",
                centered=True,
            )
        if not confirmed:
            self.status.configure(text="Nothing was deleted.")
            return

        def done(deleted):
            # Search again, so the list shows what's left.
            self.search()
            note = " Their data was scrubbed." if scrub else ""
            self.message(f"Deleted {deleted:,} file(s) from the archive.{note}")

        self.run_in_background(lambda progress: delete(self.archive, entries, scrub, progress), done, "Deleting...")


# Kept for as long as the window is open, so Windows knows this archive is already open in the Workflow Extractor.
INSTANCE_LOCK = None


def claim_archive(archive):
    # Returns False if this archive is already open in another Workflow Extractor window, after bringing that window
    # to the front. Uses a name only one running program can hold at a time, which Windows releases when it closes.
    global INSTANCE_LOCK
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    name = "Local\\BRBWorkflowExtractor." + hashlib.sha1(str(archive).lower().encode("utf-8")).hexdigest()
    INSTANCE_LOCK = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() != 183:
        return True

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    window = user32.FindWindowW(None, window_title(archive))
    if window:
        if user32.IsIconic(window):
            user32.ShowWindow(window, 9)
        user32.SetForegroundWindow(window)
    return False


def main():
    root = tk.Tk()
    # Hidden until it's ready, so nothing flashes on screen if this archive is already open.
    root.withdraw()
    if shutil.which("git") is None:
        messagebox.showerror(TITLE, "Git wasn't found. Install Git for Windows, then run this again.")
        return
    archives, looked_in = find_archives()
    if not archives:
        messagebox.showerror(TITLE, f"No workflow archive was found. Looked in:\n{looked_in}")
        return
    archive = archives[0] if len(archives) == 1 else pick_archive(root, archives)
    if archive is None:
        return
    if not claim_archive(archive):
        return
    ExtractorWindow(root, archive)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
