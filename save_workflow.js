import { app } from "../../scripts/app.js";

// Page-side tweaks for the Save Workflow node:
// - "overwrite" is shown right below "always_add_counter", and a gap separates "format" from the wildcard
//   backup settings.
// - Turning "overwrite" or "always_add_counter" on turns the other off, since they do opposite things.
// - Settings are saved by name, so changing the order of the settings can never mix up their values.

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

function isSetting(widget) {
    return !!widget?.name && widget.type !== "gap" && widget.type !== "button";
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
            return result;
        };
    },
});
