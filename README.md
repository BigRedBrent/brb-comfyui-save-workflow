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
