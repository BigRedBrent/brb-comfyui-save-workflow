# Save Workflow

A ComfyUI node that saves the workflow as a .json file, so you can open the exact workflow that made a result. It's made for savers that don't store the workflow themselves, like most video savers: wire the saver's file path into it, and the workflow is saved next to the file with the same name, like `my_clip_00007.mp4` and `my_clip_00007.json`. Drag the .json file into ComfyUI to open the workflow again.

It never overwrites a file. If the name is already taken, it adds a number like `_00001`.

## Inputs

- **passthrough:** any value, sent out unchanged through the **passthrough** output. The node waits for it before saving, so wire it from a saver to make sure the workflow is saved after the file.
- **path:** where to save. A full path saves right there, a name or prefix like `videos/my_clip` saves in ComfyUI's output folder, and a file list like Video Combine's uses the last file in it. If **path** isn't connected, the file is saved as `workflow` in the output folder.
- **extension:** `replace` saves `my_clip.mp4` as `my_clip.json`, `append` saves it as `my_clip.mp4.json`, and `none` uses the name as it is.
- **always_add_counter:** always adds a number like `_00001`, even when the name isn't taken.
- **api_format_only:** saves only the node settings used to run the job, without the layout, groups or notes.
- **format:** `compact` for the smallest file, or `readable` to open in a text editor.
