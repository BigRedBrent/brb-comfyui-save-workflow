import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

// Page-side tweaks for the Save Workflow node:
// - "overwrite" is shown right below "always_add_counter", and a gap separates "format" from the wildcard
//   backup settings.
// - Turning "overwrite" or "always_add_counter" on turns the other off, since they do opposite things.
// - Settings are saved by name, so changing the order of the settings can never mix up their values.
// - Each text input that's connected, or has text from an opened workflow file, gets a read-only box at the
//   bottom showing its text. The box is labeled like its input, so renaming an input renames its box.
// - The archive settings sit below a gap, with the Create Archive, Retry Failed Archiving, Archive All and Rebuild
//   Archive and Scrub Archive buttons under them, and "keep_text" below those. While "archive" is on, "zip_compression" and "overwrite" are grayed
//   out (they keep their values, which are ignored). The buttons always work, whether "archive" is on or off.
// - Pop-ups: when archiving fails, while a button's task runs, and to confirm Archive All, Rebuild Archive and Scrub Archive.
//   Nothing is ever written to the browser's console.

const NODE_TYPE = "BRBSaveWorkflow";

// Height of the empty space between "format" and "backup_wildcards".
const GAP_HEIGHT = 10;

// The settings of the first published version, in the order its workflows saved them.
const FIRST_VERSION_ORDER = ["extension", "always_add_counter", "api_format_only", "format"];

// Defaults for the settings added later, used for nodes from workflows saved with the first version.
const NEWER_DEFAULTS = {
    backup_wildcards: false,
    zip_compression: "9",
    wildcards_folder: "wildcards",
    overwrite: false,
    keep_text: false,
    archive: false,
    archive_path: ".workflows",
    compress_every: 1000,
    retry_failed: true,
    compress_in_background: false,
    wildcard_snapshot: true,
};

// The settings grayed out while "archive" is on, and the node's buttons.
const IGNORED_WHILE_ARCHIVING = ["zip_compression", "overwrite"];
const BUTTONS = [
    { label: "Create Archive", action: "create" },
    { label: "Retry Failed Archiving", action: "retry_failed" },
    { label: "Archive All", action: "archive_all" },
    { label: "Rebuild Archive", action: "rebuild" },
    { label: "Scrub Archive", action: "scrub" },
];

// The color of the progress bar in the task pop-up.
const PROGRESS_COLOR = "#1976d2";

function findWidget(node, name) {
    return (node.widgets || []).find((widget) => widget.name === name) || null;
}

// How many text inputs the node has (TEXT_INPUTS in __init__.py), and the names of their boxes.
const TEXT_INPUTS = 3;
const BOX_PREFIX = "text_box_";

// Multi-line boxes don't show their label, so a small title is shown above each one, like Last Preview Text's.
const TITLE_PREFIX = "text_title_";
// The title is only as tall as its label, so the label sits as close to the setting above and its box below as
// ComfyUI's normal spacing allows.
const TITLE_LINE_HEIGHT = 11;
// The label's line plus a little space below it, so it doesn't sit right on its box.
const TITLE_SPACE_BELOW = 2;
const TITLE_HEIGHT = TITLE_LINE_HEIGHT + TITLE_SPACE_BELOW;
// The space ComfyUI keeps above and below each title, the same for every box.
const TITLE_MARGIN = 0;

// The space ComfyUI keeps around each text box, 10 pixels unless told otherwise.
const TEXT_BOX_MARGIN = 4;

// Empty space at the very bottom of the node, below the last box or setting. With the 4 pixels ComfyUI adds before
// it, it comes to about 4.
const BOTTOM_GAP = 0;
const BOTTOM_GAP_NAME = "gap_bottom";

// Space below keep_text, above the first box. With the 4 pixels ComfyUI adds between widgets, it comes to about 4.
const KEEP_TEXT_GAP = 0;

function isTextWidget(widget) {
    return !!widget?.name && (widget.name.startsWith(BOX_PREFIX) || widget.name.startsWith(TITLE_PREFIX));
}

function isSetting(widget) {
    return !!widget?.name && widget.type !== "gap" && widget.type !== "button" && !isTextWidget(widget);
}

function inputLabel(node, name) {
    const input = (node.inputs || []).find((item) => item.name === name);
    return input?.label || name;
}

function addTitle(node, name, margin) {
    const element = document.createElement("div");
    element.textContent = inputLabel(node, name);
    Object.assign(element.style, {
        boxSizing: "border-box",
        width: "100%",
        height: `${TITLE_HEIGHT}px`,
        lineHeight: `${TITLE_LINE_HEIGHT}px`,
        padding: "0 4px",
        fontSize: "11px",
        opacity: "0.75",
        color: "var(--input-text, #ddd)",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        pointerEvents: "none",
        userSelect: "none",
    });
    const widget = node.addDOMWidget(TITLE_PREFIX + name, "save_workflow_title", element, {
        serialize: false,
        hideInPanel: true,
        getMinHeight: () => TITLE_HEIGHT,
        getMaxHeight: () => TITLE_HEIGHT,
        // ComfyUI adds a margin around widgets like this one, 10 pixels unless told otherwise.
        margin,
    });
    widget.serialize = false;
    widget.titleElement = element;
}

function addTextBox(node, name, text) {
    addTitle(node, name, TITLE_MARGIN);
    const widget = ComfyWidgets.STRING(
        node,
        BOX_PREFIX + name,
        ["STRING", { multiline: true, default: "", placeholder: `Shows the text wired into ${name}…` }],
        app
    ).widget;
    widget.label = inputLabel(node, name);
    widget.options = widget.options || {};
    widget.options.margin = TEXT_BOX_MARGIN;
    widget.options.read_only = true;
    widget.options.serialize = false;
    widget.serialize = false;
    widget.value = text;
    // "element" is the current name; "inputEl" is only for older ComfyUI versions.
    const textBox = widget.element ?? widget.inputEl;
    if (textBox) {
        textBox.readOnly = true;
    }
}

function keepsText(node) {
    return findWidget(node, "keep_text")?.value !== false;
}

function textsOf(node) {
    // The text shown in the boxes. With keep_text on, it's kept in the node's saved data, so it survives
    // undo and redo, and comes back when the workflow is opened again. With keep_text off, it's only held
    // while the page is open. Either way, the next run replaces it.
    node.properties = node.properties || {};
    if (!keepsText(node)) {
        node.shownText = node.shownText || {};
        return node.shownText;
    }
    const texts = node.properties.saved_text;
    if (!texts || typeof texts !== "object" || Array.isArray(texts)) {
        node.properties.saved_text = {};
    }
    return node.properties.saved_text;
}

function setTexts(node, texts) {
    node.properties = node.properties || {};
    if (keepsText(node)) {
        node.properties.saved_text = { ...texts };
    } else {
        node.shownText = { ...texts };
        delete node.properties.saved_text;
    }
}

function applyKeepText(node) {
    // Moves the shown text to wherever keep_text says it belongs.
    node.properties = node.properties || {};
    const current = node.properties.saved_text || node.shownText || {};
    setTexts(node, current);
    if (keepsText(node)) {
        node.shownText = null;
    }
}

function addBottomGap(node) {
    // Keeps an empty space as the node's last widget, however many boxes there are.
    const widgets = node.widgets || [];
    const existing = widgets.find((widget) => widget.name === BOTTOM_GAP_NAME);
    if (existing) {
        widgets.splice(widgets.indexOf(existing), 1);
    }
    widgets.push(existing || {
        type: "gap",
        name: BOTTOM_GAP_NAME,
        value: null,
        serialize: false,
        draw() {},
        computeSize(width) {
            return [width, BOTTOM_GAP];
        },
    });
}

function updateTextBoxes(node) {
    // Shows a box for each text input that's connected or has text, in order, and none for the others.
    node.savedText = textsOf(node);
    const wanted = [];
    for (let number = 1; number <= TEXT_INPUTS; number++) {
        const name = `text_${number}`;
        const input = (node.inputs || []).find((item) => item.name === name);
        if (input?.link != null || typeof node.savedText[name] === "string") {
            wanted.push(name);
        }
    }
    const shown = (node.widgets || []).filter((widget) => widget.name?.startsWith(BOX_PREFIX)).map((widget) => widget.name);
    const same = shown.length === wanted.length && shown.every((name, index) => name === BOX_PREFIX + wanted[index]);
    if (same) {
        for (const name of wanted) {
            const widget = findWidget(node, BOX_PREFIX + name);
            widget.value = node.savedText[name] ?? "";
        }
    } else {
        for (const widget of [...(node.widgets || [])]) {
            if (isTextWidget(widget)) {
                node.removeWidget(widget);
            }
        }
        for (const name of wanted) {
            addTextBox(node, name, node.savedText[name] ?? "");
        }
        addBottomGap(node);
    }
    // The space below keep_text only separates it from the first box, so with no boxes it's hidden, and the
    // bottom space alone is left.
    const keepTextGap = findWidget(node, "gap_after_keep_text");
    if (keepTextGap) {
        keepTextGap.computeSize = wanted.length ? (width) => [width, KEEP_TEXT_GAP] : () => [0, -4];
    }
    if (!same) {
        const size = node.computeSize?.();
        if (size) {
            node.setSize([node.size[0], size[1]]);
        }
    }
    node.setDirtyCanvas?.(true, true);
}

function syncBoxLabels(node) {
    // Keeps each box's title the same as its input's label, after an input is renamed.
    for (const widget of node.widgets || []) {
        if (widget.name?.startsWith(TITLE_PREFIX) && widget.titleElement) {
            const label = inputLabel(node, widget.name.slice(TITLE_PREFIX.length));
            if (widget.titleElement.textContent !== label) {
                widget.titleElement.textContent = label;
            }
        }
    }
}

function moveAfter(node, name, afterName) {
    const widgets = node.widgets || [];
    const widget = findWidget(node, name);
    const after = findWidget(node, afterName);
    if (!widget || !after) {
        return;
    }
    widgets.splice(widgets.indexOf(widget), 1);
    widgets.splice(widgets.indexOf(after) + 1, 0, widget);
}

function insertGapAfter(node, afterName, height = GAP_HEIGHT) {
    const after = findWidget(node, afterName);
    if (!after) {
        return;
    }
    const gap = {
        type: "gap",
        name: `gap_after_${afterName}`,
        value: null,
        serialize: false,
        draw() {},
        computeSize(width) {
            return [width, height];
        },
    };
    node.widgets.splice(node.widgets.indexOf(after) + 1, 0, gap);
}

function linkToggles(node, firstName, secondName) {
    const first = findWidget(node, firstName);
    const second = findWidget(node, secondName);
    if (!first || !second) {
        return;
    }
    for (const [widget, other] of [[first, second], [second, first]]) {
        const callback = widget.callback;
        widget.callback = function (value, ...rest) {
            const result = callback?.apply(this, [value, ...rest]);
            // A grayed-out setting keeps its value.
            if (value && other.value && !other.disabled) {
                other.value = false;
                node.setDirtyCanvas?.(true, true);
            }
            return result;
        };
    }
}

function setByName(node, values) {
    for (const [name, value] of Object.entries(values)) {
        const widget = findWidget(node, name);
        if (widget && isSetting(widget) && value !== undefined && value !== null) {
            widget.value = value;
        }
    }
}

function setDisabled(widget, disabled) {
    if (!widget) {
        return;
    }
    widget.disabled = disabled;
    widget.options = widget.options || {};
    widget.options.disabled = disabled;
}

function updateArchiveState(node) {
    const archiving = findWidget(node, "archive")?.value === true;
    for (const name of IGNORED_WHILE_ARCHIVING) {
        setDisabled(findWidget(node, name), archiving);
    }
    node.setDirtyCanvas?.(true, true);
}

// Pop-ups. The action button sits in the middle and Cancel at the far right. Clicking outside a pop-up that asks
// something cancels it.

function popupBox() {
    const box = document.createElement("div");
    Object.assign(box.style, {
        background: "var(--comfy-menu-bg, #202020)",
        color: "var(--fg-color, #ddd)",
        border: "1px solid var(--border-color, #4e4e4e)",
        borderRadius: "8px",
        padding: "16px 18px",
        boxShadow: "0 8px 24px rgba(0, 0, 0, 0.5)",
        maxWidth: "560px",
        minWidth: "340px",
        fontSize: "14px",
        lineHeight: "1.45",
    });
    return box;
}

function popupText(text) {
    const element = document.createElement("div");
    element.style.whiteSpace = "pre-wrap";
    element.style.overflowWrap = "anywhere";
    element.textContent = text;
    return element;
}

function popupButton(label) {
    const button = document.createElement("button");
    button.textContent = label;
    Object.assign(button.style, {
        padding: "5px 18px",
        borderRadius: "5px",
        border: "1px solid var(--border-color, #4e4e4e)",
        background: "var(--comfy-input-bg, #333)",
        color: "var(--input-text, #ddd)",
        cursor: "pointer",
        fontSize: "14px",
    });
    return button;
}

function buttonRow(action, cancel) {
    // The action in the middle, Cancel at the far right.
    const row = document.createElement("div");
    Object.assign(row.style, {
        display: "grid",
        gridTemplateColumns: "1fr auto 1fr",
        alignItems: "center",
        marginTop: "16px",
    });
    if (action) {
        action.style.gridColumn = "2";
        row.appendChild(action);
    }
    if (cancel) {
        cancel.style.gridColumn = "3";
        cancel.style.justifySelf = "end";
        row.appendChild(cancel);
    }
    return row;
}

function showModal(box, onOutsideClick) {
    const overlay = document.createElement("div");
    Object.assign(overlay.style, {
        position: "fixed",
        inset: "0",
        background: "rgba(0, 0, 0, 0.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: "10000",
    });
    overlay.appendChild(box);
    overlay.addEventListener("mousedown", (event) => {
        if (event.target === overlay) {
            onOutsideClick();
        }
    });
    document.body.appendChild(overlay);
    return overlay;
}

function confirmPopup(text, actionLabel, typedWord = null, centered = false, onOpen = null, extra = null) {
    // Resolves true for the action, false for Cancel or a click outside. With typedWord, the action only works once
    // that word is typed, ignoring upper and lower case and spaces. extra, if given, is shown just above the text box
    // (or the buttons, without one).
    return new Promise((resolve) => {
        const box = popupBox();
        const message = popupText(text);
        if (centered) {
            message.style.textAlign = "center";
        }
        box.appendChild(message);
        if (extra) {
            box.appendChild(extra);
        }
        let input = null;
        if (typedWord) {
            input = document.createElement("input");
            input.type = "text";
            Object.assign(input.style, {
                marginTop: "10px",
                width: "100%",
                boxSizing: "border-box",
                padding: "5px 8px",
                borderRadius: "4px",
                border: "1px solid var(--border-color, #4e4e4e)",
                background: "var(--comfy-input-bg, #333)",
                color: "var(--input-text, #ddd)",
                fontSize: "14px",
            });
            box.appendChild(input);
        }
        const action = popupButton(actionLabel);
        const cancel = popupButton("Cancel");
        box.appendChild(buttonRow(action, cancel));
        const matches = () => !typedWord || input.value.replace(/\s+/g, "").toLowerCase() === typedWord;
        const refresh = () => {
            action.disabled = !matches();
            action.style.opacity = action.disabled ? "0.45" : "1";
            action.style.cursor = action.disabled ? "default" : "pointer";
        };
        refresh();
        input?.addEventListener("input", refresh);
        let overlay = null;
        const finish = (value) => {
            overlay?.remove();
            resolve(value);
        };
        action.addEventListener("click", () => {
            if (matches()) {
                finish(true);
            }
        });
        cancel.addEventListener("click", () => finish(false));
        overlay = showModal(box, () => finish(false));
        // Lets the caller close it, the same as Cancel, like when what it asks about is no longer happening.
        onOpen?.(() => finish(false));
        (input || action).focus();
    });
}

function messagePopup(text) {
    // A message with OK in the middle. Clicking outside closes it too.
    const box = popupBox();
    box.appendChild(popupText(text));
    const ok = popupButton("OK");
    box.appendChild(buttonRow(ok, null));
    let overlay = null;
    ok.addEventListener("click", () => overlay?.remove());
    overlay = showModal(box, () => overlay?.remove());
    ok.focus();
}

function floatingPopup(title) {
    // A pop-up near the top of the page that doesn't block anything, for things that keep going or keep updating.
    // Its title bar can be dragged to move it out of the way.
    const holder = document.createElement("div");
    Object.assign(holder.style, {
        position: "fixed",
        top: "70px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: "10000",
    });
    const box = popupBox();
    box.style.paddingTop = "0";
    const bar = document.createElement("div");
    bar.textContent = title;
    Object.assign(bar.style, {
        margin: "0 -18px 12px",
        padding: "7px 18px",
        borderBottom: "1px solid var(--border-color, #4e4e4e)",
        borderRadius: "8px 8px 0 0",
        background: "var(--comfy-input-bg, #333)",
        fontWeight: "bold",
        cursor: "move",
        userSelect: "none",
    });
    box.appendChild(bar);
    holder.appendChild(box);
    document.body.appendChild(holder);

    bar.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) {
            return;
        }
        event.preventDefault();
        const start = holder.getBoundingClientRect();
        const offsetX = event.clientX - start.left;
        const offsetY = event.clientY - start.top;
        // From here on, placed by its corner instead of centered.
        holder.style.transform = "none";
        const move = (moveEvent) => {
            const maxLeft = Math.max(0, window.innerWidth - holder.offsetWidth);
            const maxTop = Math.max(0, window.innerHeight - holder.offsetHeight);
            holder.style.left = `${Math.min(Math.max(0, moveEvent.clientX - offsetX), maxLeft)}px`;
            holder.style.top = `${Math.min(Math.max(0, moveEvent.clientY - offsetY), maxTop)}px`;
        };
        const stop = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", stop);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", stop);
        move(event);
    });
    return { holder, box, bar };
}

// Archiving failed: one pop-up, updated in place with the count and the latest reason while it's open.
let failurePopup = null;

function showArchiveFailure(detail) {
    if (!failurePopup) {
        const { holder, box } = floatingPopup("Archiving Failed");
        const text = popupText("");
        box.appendChild(text);
        const ok = popupButton("OK");
        box.appendChild(buttonRow(ok, null));
        failurePopup = { holder, text, count: 0 };
        ok.addEventListener("click", () => {
            holder.remove();
            failurePopup = null;
        });
    }
    failurePopup.count += 1;
    const times = failurePopup.count === 1 ? "Archiving failed" : `Archiving failed ${failurePopup.count} times`;
    const where = detail?.folder
        ? `, so the files were saved to their folder instead, with .archive added to their names:\n${detail.folder}\n\n` +
          "They'll be stored in the archive by a later save with retry_failed on, or by the Retry Failed Archiving button."
        : ", and the files couldn't be saved to their folder either.";
    failurePopup.text.textContent = `${times}${where}\n\nLatest reason:\n${detail?.reason || "Unknown."}`;
}

// When a Save Workflow (wildcard snapshot) node can't tell which wildcards folder to take a snapshot of. Like the
// archiving failure pop-up, it updates in place while it's open.
let snapshotPopup = null;

function showSnapshotProblem() {
    if (!snapshotPopup) {
        const { holder, box } = floatingPopup("Wildcard Snapshot");
        const text = popupText("");
        box.appendChild(text);
        const ok = popupButton("OK");
        box.appendChild(buttonRow(ok, null));
        snapshotPopup = { holder, text, count: 0 };
        ok.addEventListener("click", () => {
            holder.remove();
            snapshotPopup = null;
        });
    }
    snapshotPopup.count += 1;
    const times = snapshotPopup.count === 1 ? "" : `\n\nThis happened ${snapshotPopup.count} times.`;
    snapshotPopup.text.textContent =
        "The wildcard snapshot node couldn't tell which wildcards folder to take a snapshot of, because a Save " +
        "Workflow node's wildcards_folder is wired from a node that works out the folder while the job runs. No " +
        "snapshot was taken for it, so its wildcard backup was read from the folder instead.\n\nType the folder " +
        "into wildcards_folder, or wire it from a node that just holds the text." + times;
}

// When the job finished without the unlock node unlocking the wildcard files. Like the others, it updates in place
// while it's open.
let lockPopup = null;

function showLockTimeout(detail) {
    if (!lockPopup) {
        const { holder, box } = floatingPopup("Wildcard Lock");
        const text = popupText("");
        box.appendChild(text);
        const ok = popupButton("OK");
        box.appendChild(buttonRow(ok, null));
        lockPopup = { holder, text, count: 0 };
        ok.addEventListener("click", () => {
            holder.remove();
            lockPopup = null;
        });
    }
    lockPopup.count += 1;
    const times = lockPopup.count === 1 ? "" : `\n\nThis happened ${lockPopup.count} times.`;
    lockPopup.text.textContent =
        "The job finished without the Save Workflow (wildcard unlock) node running, so the wildcard files were " +
        "unlocked automatically.\n\nComfyUI only runs a node whose output leads on to something that's saved or " +
        "shown, so wire the unlock node's prompt_text output on to wherever the Random Prompts node's output went." +
        times;
}

// When the job has run for a long time without the unlock node unlocking the wildcard files: asks whether to unlock
// them. It closes by itself once they're unlocked some other way, like when the job ends. Keep Locked asks again 30
// seconds later if they're still locked by then.
let lockAskPopup = null;

function showLockAsk(detail) {
    lockAskPopup?.holder.remove();
    const { holder, box } = floatingPopup("Wildcard Lock");
    const seconds = Number(detail?.seconds) || 30;
    const minutes = Math.floor(seconds / 60);
    const howLong = minutes ? `${minutes} min${seconds % 60 ? ` ${seconds % 60} sec` : ""}` : `${seconds} seconds`;
    box.appendChild(popupText(
        `The wildcard files have been locked for ${howLong}, and the job is still running without the ` +
        "Save Workflow (wildcard unlock) node unlocking them. This usually means the unlock node isn't wired right " +
        "after the Random Prompts node.\n\nRandom Prompts may not have read them yet, so unlocking them now could " +
        "let a change get into this job without being in its backup.\n\nUnlock them now? Otherwise, they stay " +
        "locked until the job ends."
    ));
    const unlock = popupButton("Unlock");
    const keep = popupButton("Keep Locked");
    box.appendChild(buttonRow(unlock, keep));
    const id = detail?.id;
    lockAskPopup = { holder, id };
    const close = () => {
        holder.remove();
        if (lockAskPopup?.holder === holder) {
            lockAskPopup = null;
        }
    };
    unlock.addEventListener("click", () => {
        close();
        api.fetchApi("/brb_save_workflow/unlock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id }),
        }).catch(() => {});
    });
    keep.addEventListener("click", () => {
        close();
        api.fetchApi("/brb_save_workflow/keep_locked", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id }),
        }).catch(() => {});
    });
}

function closeLockAsk(detail) {
    if (lockAskPopup && lockAskPopup.id === detail?.id) {
        lockAskPopup.holder.remove();
        lockAskPopup = null;
    }
}

// When the snapshot node couldn't lock some wildcard files. Like the others, it updates in place while it's open.
let lockProblemPopup = null;

function showLockProblem(detail) {
    if (!lockProblemPopup) {
        const { holder, box } = floatingPopup("Wildcard Lock");
        const text = popupText("");
        box.appendChild(text);
        const ok = popupButton("OK");
        box.appendChild(buttonRow(ok, null));
        lockProblemPopup = { holder, text, count: 0 };
        ok.addEventListener("click", () => {
            holder.remove();
            lockProblemPopup = null;
        });
    }
    lockProblemPopup.count += 1;
    const times = lockProblemPopup.count === 1 ? "" : `\n\nThis happened ${lockProblemPopup.count} times.`;
    lockProblemPopup.text.textContent =
        `${Number(detail?.failed) || 0} wildcard file(s) couldn't be locked, ` +
        `${detail?.why || "usually because another program has them open"}. They could still be changed before ` +
        "the Random Prompts node reads them.\n\nTo stop locking, " +
        `turn off wildcard_lock on the Save Workflow (wildcard snapshot) node.${times}`;
}

// When Random Prompts is used without Dynamic Prompts Wildcard Auto Reload. Like the others, it updates in place
// while it's open.
let reloadPopup = null;

function showReloadMissing(detail) {
    if (!reloadPopup) {
        const { holder, box } = floatingPopup("Wildcard Snapshot");
        const text = popupText("");
        box.appendChild(text);
        const ok = popupButton("OK");
        box.appendChild(buttonRow(ok, null));
        reloadPopup = { holder, text, count: 0 };
        ok.addEventListener("click", () => {
            holder.remove();
            reloadPopup = null;
        });
    }
    reloadPopup.count += 1;
    const times = reloadPopup.count === 1 ? "" : `\n\nThis happened ${reloadPopup.count} times.`;
    const name = detail?.name || "Dynamic Prompts Wildcard Auto Reload";
    const repo = detail?.repo || "brb-comfyui-dynamicprompts-wildcard-auto-reload";
    reloadPopup.text.textContent =
        "Random Prompts only reads the wildcard files once after ComfyUI starts, so files edited since then " +
        "aren't used, and the wildcard backup may not match what it used.\n\n" +
        `Install ${name} (${repo}) so edited wildcard files are used on the next run.${times}`;
}

// The rebuild confirmation's "Scrub immediately" checkbox, off each time, shown just above the text box like the
// Workflow Extractor's, with the same tooltip layout. prompt is the line asking for the word, kept right above the
// text box.
const SCRUB_TOOLTIP = [
    // [text, bold, extra space above, centered]
    [
        "Scrubs leftover data from the archive right after rebuilding: old copies that are no longer used. If a job " +
            "saves to the archive while it's scrubbing, the save waits until the scrub is done. This, however, doesn't " +
            "stop saves from another copy of ComfyUI, or files being deleted with the Workflow Extractor.",
        false, false, false,
    ],
    ["WARNING!!!", true, true, true],
    [
        "Only use this while nothing else is using the archive: if another copy of ComfyUI saves to it, or the " +
            "Workflow Extractor deletes files from it, at the same moment, those files can be lost, and the archive " +
            "itself can be damaged so that its files can't be read.",
        true, false, false,
    ],
    [
        "Otherwise, the leftover data is scrubbed by a later compression operation: the first one that happens at " +
            "least 27 hours after it was replaced.",
        false, true, false,
    ],
];

function addTooltip(target, parts) {
    // Shows the parts in a box by the pointer as soon as it's over the target, and hides it when the pointer leaves.
    // It only reacts to the pointer entering and leaving, so it does no work otherwise.
    let tip = null;
    const hide = () => {
        tip?.remove();
        tip = null;
    };
    target.addEventListener("mouseenter", (event) => {
        hide();
        tip = document.createElement("div");
        Object.assign(tip.style, {
            position: "fixed", zIndex: "100000", maxWidth: "380px", padding: "6px 9px", borderRadius: "4px",
            // The same light yellow as the Workflow Extractor's tooltips.
            background: "#ffffe0", color: "#000000", border: "1px solid #000000", fontSize: "12px",
            lineHeight: "1.4", pointerEvents: "none", boxShadow: "0 2px 8px rgba(0, 0, 0, 0.5)",
        });
        for (const [text, bold, spaceAbove, centered] of parts) {
            const line = document.createElement("div");
            line.textContent = text;
            Object.assign(line.style, {
                fontWeight: bold ? "bold" : "normal",
                textAlign: centered ? "center" : "left",
                marginTop: spaceAbove ? "1.4em" : "0",
            });
            tip.appendChild(line);
        }
        document.body.appendChild(tip);
        const x = event.clientX;
        const y = event.clientY;
        const left = Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8);
        const top = y + 18 + tip.offsetHeight > window.innerHeight ? y - tip.offsetHeight - 10 : y + 18;
        tip.style.left = `${Math.max(8, left)}px`;
        tip.style.top = `${Math.max(8, top)}px`;
    });
    // Stays up while clicking, so the warning is still showing when the box is checked.
    target.addEventListener("mouseleave", hide);
}

function formattedText(parts) {
    // The parts laid out like a tooltip's, for showing in a pop-up instead.
    const element = document.createElement("div");
    element.style.marginTop = "12px";
    for (const [text, bold, spaceAbove, centered] of parts) {
        const line = popupText(text);
        Object.assign(line.style, {
            fontWeight: bold ? "bold" : "normal",
            textAlign: centered ? "center" : "left",
            marginTop: spaceAbove ? "1.4em" : "0",
        });
        element.appendChild(line);
    }
    return element;
}

// The Scrub Archive confirmation's explanation and warning, laid out the same way as the rebuild's tooltip.
const SCRUB_ARCHIVE_TEXT = [
    [
        "Scrubbing deletes it now, instead of waiting for a later compression. If a job saves to the archive while " +
            "it's scrubbing, the save waits until the scrub is done. This, however, doesn't stop saves from another " +
            "copy of ComfyUI, or files being deleted with the Workflow Extractor.",
        false, false, false,
    ],
    SCRUB_TOOLTIP[1],
    SCRUB_TOOLTIP[2],
    ["Type scrub to continue:", false, true, false],
];

function scrubOption(prompt) {
    const element = document.createElement("div");
    element.style.marginTop = "12px";
    const label = document.createElement("label");
    Object.assign(label.style, { display: "inline-flex", alignItems: "center", gap: "8px", cursor: "pointer" });
    const box = document.createElement("input");
    box.type = "checkbox";
    label.append(box, document.createTextNode("Scrub immediately"));
    addTooltip(label, SCRUB_TOOLTIP);
    element.appendChild(label);
    const ask = popupText(prompt);
    ask.style.marginTop = "12px";
    element.appendChild(ask);
    return { element, checked: () => box.checked };
}

// A button's task: a pop-up showing its progress, then its result.
async function runTask(node, action, options = {}) {
    const archivePath = findWidget(node, "archive_path")?.value ?? ".workflows";
    let response;
    try {
        response = await (await api.fetchApi("/brb_save_workflow/task", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, archive_path: archivePath, scrub: options.scrub === true }),
        })).json();
    } catch {
        messagePopup("Couldn't reach ComfyUI to start this. Make sure it's still running.");
        return;
    }
    if (response.error) {
        messagePopup(response.error);
        return;
    }
    // Quick actions, like Create Archive, answer right away.
    if (response.result) {
        messagePopup(response.result);
        return;
    }

    const titles = {
        retry_failed: "Retry Failed Archiving", archive_all: "Archive All", rebuild: "Rebuild Archive",
        scrub: "Scrub Archive",
    };
    const title = titles[action] || "Save Workflow";
    const { holder, box, bar } = floatingPopup(title);
    const text = popupText("Starting...");
    box.appendChild(text);
    const track = document.createElement("div");
    Object.assign(track.style, {
        marginTop: "12px",
        height: "8px",
        borderRadius: "4px",
        background: "var(--comfy-input-bg, #333)",
        overflow: "hidden",
    });
    const fill = document.createElement("div");
    Object.assign(fill.style, { height: "100%", width: "0%", background: PROGRESS_COLOR, transition: "width 0.3s" });
    track.appendChild(fill);
    box.appendChild(track);
    // Only steps with real progress, like storing files, show the bar. Steps without it, like compressing, which
    // Git doesn't report, hide it: their message counts the time so far instead, which shows it's still working.
    track.style.display = "none";
    const showProgress = (fraction) => {
        if (fraction === null || fraction === undefined) {
            track.style.display = "none";
            return;
        }
        track.style.display = "block";
        fill.style.width = `${Math.round(fraction * 100)}%`;
    };

    // Abort stops the task. The window stays, with the button grayed out, until the task has actually stopped.
    const abort = popupButton("Abort");
    const abortRow = buttonRow(abort, null);
    box.appendChild(abortRow);
    let finished = false;
    let closeConfirm = null;
    abort.addEventListener("click", async () => {
        if (abort.disabled) {
            return;
        }
        // One last chance to back out, in case Abort was clicked by accident.
        // What stopping means for each task.
        const afterStopping = action === "rebuild"
            ? "The archive stays as it was before the rebuild."
            : action === "scrub"
                ? "Leftover data already scrubbed stays gone, and the rest stays until a later compression."
                : "Files already stored stay in the archive, and the rest stay in their folders.";
        const sure = await confirmPopup(
            `Are you sure?\n\n${title} will be stopped.\n${afterStopping}`,
            "Yes, Abort",
            null,
            true,
            (close) => {
                closeConfirm = close;
            }
        );
        closeConfirm = null;
        if (!sure || finished) {
            return;
        }
        abort.disabled = true;
        abort.textContent = "Aborting...";
        abort.style.opacity = "0.45";
        abort.style.cursor = "default";
        try {
            await api.fetchApi(`/brb_save_workflow/task/${response.task}/abort`, { method: "POST" });
        } catch {
            abort.disabled = false;
            abort.textContent = "Abort";
            abort.style.opacity = "1";
            abort.style.cursor = "pointer";
        }
    });

    const poll = async () => {
        let status;
        try {
            status = await (await api.fetchApi(`/brb_save_workflow/task/${response.task}`)).json();
        } catch {
            setTimeout(poll, 1000);
            return;
        }
        if (!status.done) {
            // A step with a status, like "Rebuilding the archive... [Status: working] (Elapsed Time: 1 min 23 sec)",
            // shows the step and status as the window's title, and the elapsed time centered below it. Other steps,
            // like storing files, show their message as it is, under the button's name.
            const timed = /^(.*) \((Elapsed Time: .*)\)$/.exec(status.text || "");
            // Steps done by ComfyUI itself, like storing files, get their status in the title too, like
            // "Archiving files... [Status: working]".
            const own = (status.text || "").startsWith("Looking")
                ? "Looking for files..."
                : action === "archive_all" ? "Archiving files..." : "Storing files...";
            bar.textContent = timed ? timed[1] : status.status ? `${own} [Status: ${status.status}]` : title;
            text.textContent = timed ? timed[2] : status.text || "";
            text.style.textAlign = timed ? "center" : "left";
            showProgress(status.fraction);
            // Asks for the latest status once a second.
            setTimeout(poll, 1000);
            return;
        }
        finished = true;
        // If the task finished while its abort check was still open, that check closes by itself.
        closeConfirm?.();
        // Once an abort has finished, the window goes away.
        if (status.aborted) {
            holder.remove();
            return;
        }
        // The result shows in the same window, wherever it was moved to, with OK to close it.
        abortRow.remove();
        track.remove();
        bar.textContent = title;
        text.style.textAlign = "left";
        text.textContent = status.error ? `Stopped:\n${status.error}` : status.result || "Done.";
        const ok = popupButton("OK");
        box.appendChild(buttonRow(ok, null));
        ok.addEventListener("click", () => holder.remove());
    };
    setTimeout(poll, 300);
}

async function onArchiveButton(node, action) {
    const options = {};
    if (action === "archive_all") {
        const typed = await confirmPopup(
            "Archive All moves every workflow (.json) and wildcard backup (.zip) in ComfyUI's output folder and its " +
                "subfolders into the archive, along with any files ending in .archive. Each file's contents are " +
                "checked first, so only real workflows and wildcard backups are taken. Each is dated by when it was " +
                "last modified, and deleted from its folder once it's safely stored. Files and folders whose names " +
                "start with a dot, like the archive itself, are skipped as hidden. The archive is compressed " +
                "afterward.\n\n" +
                "Type archive to continue:",
            "OK",
            "archive"
        );
        if (!typed) {
            return;
        }
        const sure = await confirmPopup(
            "Are you sure?\n\nEvery workflow and wildcard backup found in the output folder will be moved into the archive.",
            "Continue",
            null,
            true
        );
        if (!sure) {
            return;
        }
    } else if (action === "scrub") {
        // First, how much there is to scrub.
        const archivePath = findWidget(node, "archive_path")?.value ?? ".workflows";
        let found;
        try {
            found = await (await api.fetchApi("/brb_save_workflow/leftovers", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ archive_path: archivePath }),
            })).json();
        } catch {
            messagePopup("Couldn't reach ComfyUI to check the archive. Make sure it's still running.");
            return;
        }
        if (found.error) {
            messagePopup(found.error);
            return;
        }
        if (!found.bytes) {
            messagePopup(`There's no leftover data to scrub.\n\nArchive size: ${found.archive_size}`);
            return;
        }
        const typed = await confirmPopup(
            `About ${found.size} of leftover data is waiting to be removed: old copies that are no longer used.`,
            "Continue",
            "scrub",
            false,
            null,
            formattedText(SCRUB_ARCHIVE_TEXT)
        );
        if (!typed) {
            return;
        }
        const sure = await confirmPopup(
            `Are you sure?\n\nAbout ${found.size} of leftover data will be permanently deleted from the archive.`,
            "Continue",
            null,
            true
        );
        if (!sure) {
            return;
        }
    } else if (action === "rebuild") {
        const scrub = scrubOption("Type rebuild to continue:");
        const typed = await confirmPopup(
            "Rebuild Archive redoes the compression of the whole archive from scratch, for the smallest size, then " +
                "checks that everything in it is intact. It can take a long time for a big archive, and " +
                "uses a lot of processor and memory while it runs, so it's best to run while no jobs are running. " +
                "Nothing in the archive is changed.",
            "Continue",
            "rebuild",
            false,
            null,
            scrub.element
        );
        if (!typed) {
            return;
        }
        options.scrub = scrub.checked();
        const sure = await confirmPopup(
            "Are you sure?\n\nThe whole archive will be rebuilt, which can take a long time for a big archive.",
            "Continue",
            null,
            true
        );
        if (!sure) {
            return;
        }
    }
    runTask(node, action, options);
}

function addArchiveButtons(node) {
    insertGapAfter(node, "compress_in_background");
    for (const { label, action } of BUTTONS) {
        if ((node.widgets || []).some((widget) => widget.archiveAction === action)) {
            continue;
        }
        const widget = node.addWidget("button", label, null, () => onArchiveButton(node, action), { serialize: false });
        widget.serialize = false;
        widget.archiveAction = action;
    }
}

// Archive paths already sent to be checked since the page loaded, so each is only checked once.
const checkedExtractors = new Set();

function updateExtractor(node) {
    // Once the node has loaded, asks the server to update the Workflow Extractor next to its archive, if the
    // archive is valid and the extractor is already there.
    const archivePath = findWidget(node, "archive_path")?.value ?? ".workflows";
    if (checkedExtractors.has(archivePath)) {
        return;
    }
    checkedExtractors.add(archivePath);
    api.fetchApi("/brb_save_workflow/update_extractor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archive_path: archivePath }),
    }).catch(() => {});
}

app.registerExtension({
    name: "BRB.SaveWorkflow",
    setup() {
        api.addEventListener("brb.save_workflow.archive_failed", (event) => showArchiveFailure(event.detail));
        api.addEventListener("brb.save_workflow.snapshot_problem", () => showSnapshotProblem());
        api.addEventListener("brb.save_workflow.lock_timeout", (event) => showLockTimeout(event.detail));
        api.addEventListener("brb.save_workflow.lock_problem", (event) => showLockProblem(event.detail));
        api.addEventListener("brb.save_workflow.lock_ask", (event) => showLockAsk(event.detail));
        api.addEventListener("brb.save_workflow.lock_released", (event) => closeLockAsk(event.detail));
        api.addEventListener("brb.save_workflow.reload_missing", (event) => showReloadMissing(event.detail));
        api.addEventListener("brb.save_workflow.archive_problem", (event) => {
            if (event.detail?.text) {
                messagePopup(event.detail.text);
            }
        });
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_TYPE) {
            return;
        }

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            moveAfter(this, "overwrite", "always_add_counter");
            insertGapAfter(this, "format");
            linkToggles(this, "overwrite", "always_add_counter");
            moveAfter(this, "wildcard_snapshot", "wildcards_folder");
            insertGapAfter(this, "wildcard_snapshot");
            addArchiveButtons(this);
            // keep_text goes below the buttons, after a gap.
            insertGapAfter(this, BUTTONS[BUTTONS.length - 1].label);
            moveAfter(this, "keep_text", `gap_after_${BUTTONS[BUTTONS.length - 1].label}`);
            insertGapAfter(this, "keep_text", KEEP_TEXT_GAP);
            const archive = findWidget(this, "archive");
            if (archive) {
                const callback = archive.callback;
                archive.callback = (...args) => {
                    const result = callback?.apply(archive, args);
                    updateArchiveState(this);
                    return result;
                };
            }
            updateArchiveState(this);
            const keepText = findWidget(this, "keep_text");
            if (keepText) {
                const callback = keepText.callback;
                keepText.callback = (...args) => {
                    const result = callback?.apply(keepText, args);
                    applyKeepText(this);
                    return result;
                };
            }
            updateTextBoxes(this);
            addBottomGap(this);
            // After the node has fully loaded, including its saved settings.
            setTimeout(() => updateExtractor(this), 0);
            const size = this.computeSize?.();
            if (size) {
                this.setSize([Math.max(this.size[0], size[0]), Math.max(this.size[1], size[1])]);
            }
            return result;
        };

        const onSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (data) {
            onSerialize?.apply(this, arguments);
            const values = {};
            for (const widget of this.widgets || []) {
                if (isSetting(widget)) {
                    values[widget.name] = widget.value;
                }
            }
            data.properties = data.properties || {};
            data.properties.settings = values;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const result = onConfigure?.apply(this, arguments);
            const saved = info?.properties?.settings;
            if (saved && typeof saved === "object") {
                // Saved by this version: put each value back by name.
                setByName(this, saved);
            } else if (Array.isArray(info?.widgets_values) && info.widgets_values.length === FIRST_VERSION_ORDER.length) {
                // Saved by the first version, in its order: put those values back by name, and leave the
                // newer settings at their defaults.
                const values = { ...NEWER_DEFAULTS };
                FIRST_VERSION_ORDER.forEach((name, index) => {
                    values[name] = info.widgets_values[index];
                });
                setByName(this, values);
            }
            updateArchiveState(this);

            // Show the text kept with the node, such as from a workflow file this node saved. With keep_text
            // off, the text from the file is still shown, but not kept.
            applyKeepText(this);
            setTimeout(() => updateTextBoxes(this), 0);
            return result;
        };

        // Adds or removes boxes as text inputs are connected or disconnected. Disconnecting an input also
        // clears its text, so its box goes away.
        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (type, slot, connected) {
            const result = onConnectionsChange?.apply(this, arguments);
            if (!app.configuringGraph) {
                const isInput = type === (window.LiteGraph?.INPUT ?? 1);
                const input = isInput ? this.inputs?.[slot] : null;
                if (!connected && input?.name?.startsWith("text_")) {
                    delete textsOf(this)[input.name];
                }
                setTimeout(() => updateTextBoxes(this), 0);
            }
            return result;
        };

        const onDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function () {
            const result = onDrawForeground?.apply(this, arguments);
            syncBoxLabels(this);
            return result;
        };

        // Shows the text from the latest run.
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = onExecuted?.apply(this, arguments);
            const received = Array.isArray(message?.saved_text) ? message.saved_text[0] : message?.saved_text;
            if (received && typeof received === "object") {
                setTexts(this, received);
                updateTextBoxes(this);
            }
            return result;
        };
    },
});
