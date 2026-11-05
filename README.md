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
- **overwrite:** saves to the exact same name every time, replacing the file and its wildcard backup if they already exist. No number is ever added. Turning it on turns off **always_add_counter**, and the other way around. Useful when another program picks up the file after each job. Off by default, so existing files are never replaced.
- **archive:** saves the workflow and its wildcard backup into an archive instead of the folder. See [Archiving](#archiving) below. Off by default.
- **archive_path:** where the archive is. A name or relative path, like `.workflows` (the default), is inside ComfyUI's output folder, and a full path is used as is.
- **compress_every:** how many saves between compressions of the archive, 1000 by default. 0 means never, so the archive keeps growing.
- **retry_failed:** stores files left from failed saves on later saves. On by default.
- **compress_in_background:** off by default, so the job pauses while the archive compresses. Turn it on to compress alongside your jobs instead.
- **keep_text:** off by default, so the text in the boxes is only shown, and is lost on undo or when you open the workflow again. Turn it on to keep the text with your workflow. The saved workflow file always gets the text either way.

## Archiving

With **archive** on, each saved workflow (and wildcard backup) goes into one archive instead of its own file. Workflows are mostly small variations on each other, and the archive stores each one as just its differences from a similar one, so thousands of them take up a few megabytes. Identical wildcard backups are stored only once, however many saves use them. The archive needs [Git](https://git-scm.com/) 2.36 or newer (Git for Windows on Windows).

- **Where it goes:** files keep the folder they'd have been saved in, relative to the output folder, inside a folder for the day they were stored, with the time in front of their name, like `Text2Img/2026-11-03/10-21-05 my_clip_00001.json`. Files saved outside the output folder keep their full path, drive included.
- **Getting files back:** use the **Workflow Extractor**, `.workflow_extractor.pyw`. Save Workflow copies it next to the archive when it creates the archive, and when the node loads, it replaces that copy if it's been changed or is from an older version. If you delete the copy, it isn't added back unless you click **Create Archive**. Double-click it to open the archive. It finds the archive by itself, and if there's more than one next to it, it lists them so you can pick one. It lists everything with the date and time it was stored, searches by name and date, and extracts files with their original names into a folder next to the archive, like `.workflows_extracted`. It can also remove files from the archive. It needs Python with its window toolkit, which Python for Windows includes.
- **Names:** numbers like `_00001` work the same as on disk, but count what's already in the archive. **zip_compression** and **overwrite** are grayed out and ignored while archiving: wildcard backups are stored uncompressed, which lets the archive store only their differences, and nothing is ever overwritten.
- **The archive folder:** it's created on the first save, or by the **Create Archive** button. An existing folder is only used if it's empty or is already an archive made by Save Workflow, so nothing else is ever written into.
- **Compressing:** new saves take up more space until the archive is compressed, once every **compress_every** saves. The job pauses at Save Workflow while it compresses, about 40 seconds for 1,000 saves, with its status and elapsed time shown on the node. If it's interrupted, like by closing ComfyUI, nothing is lost, and it's simply done again on the next save.
- **If archiving fails:** the files are saved to their folder instead, named the way they'd be stored, with `.archive` added, like `2026-11-03_10-21-05 my_clip_00001.json.archive`, and a pop-up says why. With **retry_failed** on, later saves store them in the archive, and the **Retry Failed Archive** button stores them right away. You can also rename files yourself to end in `.archive`, like `my_clip.json.archive`, and they're stored on the first save to their folder after ComfyUI starts, dated by when they were last modified.

### Buttons

The buttons below the archive settings work on the archive set in **archive_path**, whether **archive** is on or off. Each shows its progress in a pop-up.

- **Create Archive:** creates a new, empty archive at **archive_path**, if there isn't one there yet. It won't create one in a folder that already has other files in it. The first save with **archive** on also creates the archive, so this is only needed to set one up ahead of time, like to use the other buttons first.
- **Retry Failed Archive:** stores every file ending in `.archive` in the output folder and its subfolders right away. Files and folders whose names start with a dot are skipped as hidden.
- **Archive All:** moves every workflow and wildcard backup in the output folder and its subfolders into the archive, dated by when each was last modified. Each file's contents are checked first, so other `.json` and `.zip` files are left alone, and files and folders whose names start with a dot are skipped as hidden. You type `archive` to confirm, then confirm once more.
- **Rebuild Archive:** redoes the compression of the whole archive from scratch, for the smallest size, then checks that everything in it is intact. It can take a long time for a big archive, so it's best run while no jobs are running. You type `rebuild` to confirm, then confirm once more.

