# Save Workflow

A ComfyUI node that saves the workflow as a .json file, so you can open the exact workflow that made a result. It's made for savers that don't store the workflow themselves, like most video savers: wire the saver's file path into it, and the workflow is saved next to the file with the same name, like `my_clip_00007.mp4` and `my_clip_00007.json`. Drag the .json file into ComfyUI to open the workflow again.

Unless **overwrite** is on, it never replaces a file. If the name is already taken, it adds a number like `_00001`.

## Inputs

- **passthrough:** any value, sent out unchanged through the **passthrough** output. The node waits for it before saving, so wire it from a saver to make sure the workflow is saved after the file.
- **path:** where to save. A full path saves right there, a name or prefix like `videos/my_clip` saves in ComfyUI's output folder, and a file list like Video Combine's uses the last file in it. If **path** isn't connected, the file is saved as `workflow` in the output folder.
- **text_1, text_2, text_3:** any values, turned into text, such as your positive and negative prompts. Each connected one is shown in its own box at the bottom of the node and saved in the workflow file, so opening that file in ComfyUI shows them there again. Right-click an input to give it a label, like "positive", and its box is titled the same. Left out when nothing is connected, or when **api_format_only** is on.
- **extension:** `replace` saves `my_clip.mp4` as `my_clip.json`, `append` saves it as `my_clip.mp4.json`, and `none` uses the name as it is.
- **always_add_counter:** always adds a number like `_00001`, even when the name isn't taken.
- **api_format_only:** saves only the node settings used to run the job, without the layout, groups or notes.
- **format:** `compact` for the smallest file, or `readable` to open in a text editor.
- **backup_wildcards:** also saves a zip of the wildcards folder next to the workflow file, with the same name, like `my_clip_00007.zip`. That way you know exactly which wildcard files a result was made with.
- **zip_compression:** how much the wildcard backup is compressed, from 1 (fastest) to 9 (smallest, the default). `none` stores the files without compression. Each zip is larger, but if you keep many backups in an archive that stores only differences, such as Git, uncompressed zips take up far less space overall.
- **wildcards_folder:** the folder to back up. `wildcards` means the one inside the ComfyUI folder, where Dynamic Prompts keeps its wildcards. A full path works too.
- **wildcard_snapshot:** makes the wildcard backup from the snapshot taken by a [Save Workflow (wildcard snapshot)](#wildcard-snapshot) node, when one is in the job. On by default. Without that node, the backup is read from the wildcards folder as usual.
- **overwrite:** saves to the exact same name every time, replacing the file and its wildcard backup if they already exist. No number is ever added. Turning it on turns off **always_add_counter**, and the other way around. Useful when another program picks up the file after each job. Off by default, so existing files are never replaced.
- **archive:** saves the workflow and its wildcard backup into an archive instead of the folder. See [Archiving](#archiving) below. Off by default.
- **archive_path:** where the archive is. A name or relative path, like `.workflows` (the default), is inside ComfyUI's output folder, and a full path is used as is.
- **compress_every:** how many saves between compressions of the archive, 1000 by default. 0 means never, so the archive keeps growing.
- **retry_failed:** stores files left from failed saves on later saves. On by default.
- **compress_in_background:** off by default, so the job pauses while the archive compresses. Turn it on to compress alongside your jobs instead.
- **keep_text:** off by default, so the text in the boxes is only shown, and is lost on undo or when you open the workflow again. Turn it on to keep the text with your workflow. The saved workflow file always gets the text either way.

## Outputs

- **passthrough:** the **passthrough** input, unchanged.
- **summary:** what the node did in the run, one line for each thing, with a blank line between them so they show separately in Markdown, like:

  ```
  Archived the workflow and its wildcard backup

  Wildcards: from the snapshot taken at 17:40:12 (2026-11-05)

  Saves since the last compression: 688
  ```

  Wire it into a text display node to see it.

## Wildcard snapshot

The wildcard backup is made when Save Workflow runs, at the end of the job. If you change a wildcard file while the job runs, the backup gets the changed file, not the one the job used. The **Save Workflow (wildcard snapshot)** node fixes that by taking a snapshot of the wildcards folder just before the wildcards are used.

- **Wiring it:** put it between the prompt text and the Random Prompts node. Type the prompt into its **prompt_text** box, or wire the text into it, then wire its **prompt_text** output into Random Prompts' **text**. It passes the text through unchanged. ComfyUI always runs it right before Random Prompts, so the snapshot matches the files Random Prompts reads.
- **Which folders:** it finds the Save Workflow nodes in the job and takes a snapshot of each one's **wildcards_folder**. It skips nodes with **backup_wildcards** or **wildcard_snapshot** off, and bypassed ones. If **wildcards_folder** is wired from a node that works out the folder while the job runs, it can't tell which folder that is, so it takes no snapshot for it and a pop-up says so.
- **wildcard_lock:** on by default. It locks the wildcard files before the snapshot, so they can't change before Random Prompts reads them. See [Wildcard lock](#wildcard-lock).
- **Needs Dynamic Prompts Wildcard Auto Reload:** Random Prompts only reads the wildcard files once after ComfyUI starts, so without [Dynamic Prompts Wildcard Auto Reload](https://github.com/BigRedBrent/brb-comfyui-dynamicprompts-wildcard-auto-reload), files edited since then aren't used, and the backup may not match what it used. If Random Prompts is in the job, **wildcards_folder** is the folder it reads, and that add-on isn't installed, a pop-up says so, and it updates in place on later jobs.
- **Fast:** the snapshot is kept in memory until ComfyUI closes. Each job, only new and changed files are read again, so an unchanged folder takes a few milliseconds.
- **Checking it works:** wire either node's **summary** output into a text display node. The snapshot node shows something like `Snapshot of 34 wildcard file(s) at 17:40:12 (2026-11-05): 2 updated`, and Save Workflow shows `Wildcards: from the snapshot taken at 17:40:12 (2026-11-05)`. The same time on both means Save Workflow used the snapshot from that job. Save Workflow only uses a snapshot taken during the same job, and otherwise reads the folder, showing `Wildcards: from the folder`.

### Wildcard lock

ComfyUI doesn't always run Random Prompts right after the snapshot node. Another node, like a model loader, can run in between, so a wildcard file changed during that time could be read by Random Prompts but not be in the snapshot. With **wildcard_lock** on, the snapshot node closes that gap by locking the wildcard files before taking its snapshot.

- **What locking does:** other programs can still read the files, but not change, rename or delete them, and each one is checked afterward. On Windows, that's by trying to open it for writing, which must fail. On Linux and Mac, it's by reading its permissions back, which catches drives that accept the change but ignore it, and works even when ComfyUI runs as the administrator. Saving a wildcard file in an editor fails while it's locked, so just save again.
- **How it locks:** on Windows, each file is held open in a way that only allows reading, and Windows lets go of it by itself if ComfyUI stops. Linux and Mac locks don't stop other programs, so there the files and all their folders are made read-only, which also blocks adding files and editors that save by renaming a new file over the old one. On Mac, they get the locked flag, the same as Finder's Lock checkbox. Their original permissions are put back exactly when they're unlocked.
- **The Save Workflow (wildcard unlock) node:** wire Random Prompts' output into its **prompt_text** input, and its **prompt_text** output on to wherever Random Prompts' output went. It passes the text through unchanged, and unlocks the files as soon as Random Prompts has read them, usually within a split second, or a few seconds when a model loads in between.
- **Without the unlock node,** the files stay locked until the job ends, then unlock. Turn **wildcard_lock** off if that gets in the way.
- **New files:** a file added to the folder while the others are locked can't be locked. If one appears, the unlock node's **summary** says so, since the backup may not match what Random Prompts read.
- **If the job is cancelled or fails,** the files are unlocked within a second.
- **If the job finishes without running the unlock node**, though it's in the job, the files are unlocked, and a pop-up says its output needs to lead on to something, since ComfyUI skips nodes that lead nowhere.
- **If the job runs for 30 seconds without the unlock node unlocking them**, though it's in the job, a pop-up asks whether to unlock them now, since Random Prompts may not have read them yet. **Keep Locked** asks again 30 seconds later if they're still locked. If the files unlock in the meantime, the pop-up closes by itself. This usually means the unlock node isn't wired right after Random Prompts. The next job's snapshot also unlocks any files still locked.
- **If files can't be locked**, a pop-up says how many, and it updates in place on later jobs. On Windows, it's usually because another program has them open. On Linux and Mac, it's usually because they belong to another user, or their drive doesn't support permissions, like a USB drive formatted for Windows.
- **Linux and Mac:** read-only permissions and the locked flag stay on the files if ComfyUI stops while they're locked, so their original permissions are saved to a file in ComfyUI's user folder first. If ComfyUI stopped while they were locked, they stay locked until the next time ComfyUI starts, which puts them back. An editor running as the administrator isn't stopped by read-only files on Linux. The Mac locking hasn't been tested on a Mac yet.

## Archiving

With **archive** on, each saved workflow (and wildcard backup) goes into one archive instead of its own file. Workflows are mostly small variations on each other, and the archive stores each one as just its differences from a similar one, so thousands of them take up a few megabytes. Identical wildcard backups are stored only once, however many saves use them. The archive needs [Git](https://git-scm.com/) 2.36 or newer (Git for Windows on Windows).

- **Where it goes:** files keep the folder they'd have been saved in, relative to the output folder, along with the date and time they were stored. Files saved outside the output folder keep their full path, drive included.
- **Getting files back:** use the **Workflow Extractor**, `.workflow_extractor.pyw`. Save Workflow copies it next to the archive when it creates the archive, and when the node loads, it replaces that copy if it's been changed or is from an older version. If you delete the copy, it isn't added back unless you click **Create Archive**. Double-click it to open the archive. It finds the archive by itself, and if there's more than one next to it, it lists them so you can pick one. It lists everything with the date and time it was stored, searches by name and date, and extracts files with their original names into a folder next to the archive, like `.workflows_extracted`. It can also delete files from the archive. It needs Python with its window toolkit, which Python for Windows includes.
- **Names:** numbers like `_00001` work the same as on disk, but count what's already in the archive. **zip_compression** and **overwrite** are grayed out and ignored while archiving: wildcard backups are stored uncompressed, which lets the archive store only their differences, and nothing is ever overwritten.
- **The archive folder:** it's created on the first save, or by the **Create Archive** button. An existing folder is only used if it's empty or is already an archive made by Save Workflow, so nothing else is ever written into.
- **Compressing:** new saves take up more space until the archive is compressed, once every **compress_every** saves. The job pauses at Save Workflow while it compresses, about 40 seconds for 1,000 saves, with its status and elapsed time shown on the node. If it's interrupted, like by closing ComfyUI, nothing is lost, and it's simply done again on the next save.
- **If archiving fails:** the files are saved to their folder instead, named the way they'd be stored, with `.archive` added, like `2026-11-03_10-21-05 my_clip_00001.json.archive`, and a pop-up says why. With **retry_failed** on, later saves store them in the archive, and the **Retry Failed Archiving** button stores them right away. You can also rename files yourself to end in `.archive`, like `my_clip.json.archive`, and they're stored on the first save to their folder after ComfyUI starts, dated by when they were last modified.

### Buttons

The buttons below the archive settings work on the archive set in **archive_path**, whether **archive** is on or off. Each shows its progress in a pop-up.

- **Create Archive:** creates a new, empty archive at **archive_path**, if there isn't one there yet. It won't create one in a folder that already has other files in it. The first save with **archive** on also creates the archive, so this is only needed to set one up ahead of time, like to use the other buttons first.
- **Retry Failed Archiving:** stores every file ending in `.archive` in the output folder and its subfolders right away. Files and folders whose names start with a dot are skipped as hidden.
- **Archive All:** moves every workflow and wildcard backup in the output folder and its subfolders into the archive, dated by when each was last modified. Each file's contents are checked first, so other `.json` and `.zip` files are left alone, and files and folders whose names start with a dot are skipped as hidden. You type `archive` to confirm, then confirm once more.
- **Rebuild Archive:** redoes the compression of the whole archive from scratch, for the smallest size, then checks that everything in it is intact. It can take a long time for a big archive, so it's best to run while no jobs are running. You type `rebuild` to confirm, then confirm once more.
  - **Purge immediately:** a checkbox on the same screen, off by default. It also deletes leftover data right after rebuilding: old copies no longer used, which are otherwise only removed by a compression at least 27 hours after they were replaced. If a job saves to the archive while it's purging, the save waits until the purge is done. This, however, doesn't stop saves from another copy of ComfyUI, or files being deleted with the Workflow Extractor, so only use it while nothing else is using the archive: if another copy of ComfyUI saves to it, or the Workflow Extractor deletes files from it, at the same moment, those files can be lost, and the archive can be damaged.
- **Purge Archive:** first checks how much leftover data is waiting to be removed, which can take a while on a big archive, so a window shows its status and elapsed time, and **Cancel** stops it. Then it shows how much there is: old copies no longer used, which are otherwise only removed by a compression at least 27 hours after they were replaced. You can then delete it right away, which only takes a few seconds. You type `purge` to confirm, then confirm once more. If a job saves to the archive while it's purging, the save waits until the purge is done. This, however, doesn't stop saves from another copy of ComfyUI, or files being deleted with the Workflow Extractor, so only use it while nothing else is using the archive: otherwise, those files can be lost, and the archive can be damaged.

