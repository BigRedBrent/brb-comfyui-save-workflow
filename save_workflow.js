import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

// Page-side tweaks for the Save Workflow node:
// - "overwrite" is shown right below "always_add_counter", and a gap separates "format" from the wildcard
//   backup settings.
// - Turning "overwrite" or "always_add_counter" on turns the other off, since they do opposite things.
// - Settings are saved by name, so changing the order of the settings can never mix up their values.
// - Each text input that's connected, or has text from an opened workflow file, gets a read-only box at the
//   bottom showing its text. The box is labeled like its input, so renaming an input renames its box.

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
};

function findWidget(node, name) {
    return (node.widgets || []).find((widget) => widget.name === name) || null;
}

// How many text inputs the node has (TEXT_INPUTS in __init__.py), and the names of their boxes.
const TEXT_INPUTS = 3;
const BOX_PREFIX = "text_box_";

// Multi-line boxes don't show their label, so a small title is shown above each one, like Last Preview Text's.
const TITLE_PREFIX = "text_title_";
const TITLE_HEIGHT = 20;

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

function addTitle(node, name) {
    const element = document.createElement("div");
    element.textContent = inputLabel(node, name);
    Object.assign(element.style, {
        boxSizing: "border-box",
        width: "100%",
        height: `${TITLE_HEIGHT}px`,
        lineHeight: `${TITLE_HEIGHT}px`,
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
    });
    widget.serialize = false;
    widget.titleElement = element;
}

function addTextBox(node, name, text) {
    addTitle(node, name);
    const widget = ComfyWidgets.STRING(
        node,
        BOX_PREFIX + name,
        ["STRING", { multiline: true, default: "", placeholder: `Shows the text wired into ${name}…` }],
        app
    ).widget;
    widget.label = inputLabel(node, name);
    widget.options = widget.options || {};
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

function textsOf(node) {
    // The text shown in the boxes. It's kept in the node's saved data, so it survives undo and redo, and
    // comes back when the workflow is opened again. The next run replaces it.
    node.properties = node.properties || {};
    const texts = node.properties.saved_text;
    if (!texts || typeof texts !== "object" || Array.isArray(texts)) {
        node.properties.saved_text = {};
    }
    return node.properties.saved_text;
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

function insertGapAfter(node, afterName) {
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
            return [width, GAP_HEIGHT];
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
            if (value && other.value) {
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

app.registerExtension({
    name: "BRB.SaveWorkflow",
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
            updateTextBoxes(this);
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

            // Show the text kept with the node, such as from a workflow file this node saved.
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
                this.properties = this.properties || {};
                this.properties.saved_text = { ...received };
                updateTextBoxes(this);
            }
            return result;
        };
    },
});
