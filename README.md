# NodesImport — Surgical Node Import for Nuke

NodesImport is a Nuke tool that lets you **visually browse any `.nk` script** and **selectively import individual nodes or entire sections** into your current node graph — without opening the file or disrupting your session.

Designed for compositors who need to rescue specific nodes from old versions, borrow setups from sibling shots, or recover work from a corrupted scripts.

---

## The Problem It Solves

Imagine you are working on a comp in Nuke. You need a DMP, a 3D projection, or a whole CG colour correction setup from a previous version of the script, or from a similar shot in the sequence — but using `File > Insert Comp Nodes...` would dump hundreds of unwanted nodes into your Node Graph.

NodesImport solves this by showing you the full node graph of any `.nk` file in a separate window. You select any amount of nodes you need, click **Import Selected Nodes**, and only those nodes will land in your graph.

**Crash recovery:** You were deep into a heavy comp and Nuke crashed. You try reopening with the autosave and it crashes again before you can even see your nodes. Instead of digging through the `.nk` file in a text editor trying to figure out what broke, open Nuke with your last stable save, open NodesImport, load the autosave (`.nk~` files are fully supported), and import only the nodes you were working on.

---

## Features

### Node Graph Viewer

The graph renders the script the same way Nuke does — colour-coded nodes using Nuke's default palette, wires landing at the correct input slots, backdrops with their labels and fill colours, and Dot nodes as circles. You can see exactly what you're about to import and how it connects.

### Navigation

Optimized for Wacom pens.
Intentionally identical to Nuke's node graph — no learning curve.

| Action | Result |
|---|---|
| F | Zoom to selection, or fit all nodes if nothing is selected |
| Ctrl + A | Select all nodes |

### Selection

Identical to Nuke's node graph.

| Action | Result |
|---|---|
| LMB click | Select node, deselect rest |
| LMB drag | Rubber-band — replace selection |
| Shift + LMB click | Add individual node to selection |
| Shift + LMB drag | Rubber-band toggle (select unselected, deselect selected) |

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| Shift + I | Open Nodes Import |
| Enter | Import selected nodes |
| Ctrl + F | Open search node |
| Esc | Close search node |

### Import

Select any combination of nodes, Dots and Backdrops and click **Import Selected Nodes**. They land in your live Nuke session wired correctly to each other. Any inputs that point to nodes outside your selection — a Read node you already have in your comp, for example — come in disconnected, ready to wire up manually.

### Search

When you know what you're looking for but the script has hundreds of nodes, hit **Ctrl+F** or the 🔍 button. Search covers node names, node types, label text, backdrop text and sticky note content.

Results are sorted by relevance: exact name match first, then names starting with your query, then any node that contains it anywhere. Within each tier, results are ordered by parse index so `Blur` always appears before `Blur1`, `Blur2` and so on. Step through matches with the `‹ ›` arrows, check the counter to see where you are (`3 / 14 matches`), and close with **Esc** or ✕.

### Autosave Support

NodesImport reads `.nk~` autosave files directly, using the same parser as regular scripts. If Nuke crashed mid-session, just point NodesImport at the autosave and import what you need into a clean script without ever risking opening the file that caused the crash.

### Settings

One option lives in the ⚙ panel at the moment: **Close window after importing**. If you prefer NodesImport window to close the moment you hit Import, turn this on. The preference is saved automatically and persists across sessions.

### Script Info

Hover the **ⓘ** button in the bottom-right corner to see the script's key metadata: resolution, frame range, total frame count, and FPS. A quick way to confirm you're looking at the right file or the right version before importing anything.

### Window Memory

NodesImport remembers its size and position between sessions. However you've arranged it on your screen, it'll be exactly where you left it next time.

---

## Compatibility

### Nuke Versions

NodesImport uses the Qt bindings that already ship inside Nuke — nothing extra to install.

| Nuke Version | Qt Binding | Status |
|---|---|---|
| Nuke 13 / 14 / 15.x | PySide2 (bundled) | ✅ Supported |
| Nuke 16+ | PySide6 (bundled) | ✅ Supported |

### Operating Systems

Fully compatible with **Windows**, **macOS** and **Linux** — no platform-specific configuration needed.

### Non-native and Third-party Nodes

NodesImport parses and imports any node that appears in a `.nk` file, regardless of whether it is native to Nuke. OFX plugins, studio-specific gizmos, and third-party tools like **Stamps** are all handled correctly — they are read, displayed in the graph, and imported with their connections intact just like any other node.

---

## Installation

1. Download or clone this repository and place the `NodesImport` folder inside your Nuke plugins directory:

```
~/.nuke/NodesImport/
```

2. Add this line to your **init.py** file:

```
nuke.pluginAddPath("./NodesImport")
```

3. Restart Nuke. The **Nodes Import** entry will appear in Nuke's **File** menu, just above **Insert Comp Nodes**. You can also open it at any time with **Shift+I**.

> If you're storing the folder somewhere other than `~/.nuke/`, add the parent directory to your `~/.nuke/menu.py`:
> ```python
> import sys
> sys.path.insert(0, "/path/to/the/folder/containing/NodesImport")
> ```

---

## File Structure

```
NodesImport/
├── __init__.py
├── menu.py                 # Registers the tool in Nuke's File menu on startup
├── core/
│   ├── __init__.py
│   └── parsing.py          # Parses .nk and .nk~ files without invoking Nuke
└── ui/
    ├── __init__.py
    ├── graph_view.py        # Node graph scene, view, navigation and import logic
    └── main_window.py       # Main window, toolbar, search, settings and info panel
```

---

## How It Works

Nuke's `.nk` format is a stack-based text format. NodesImport reads the file directly — without invoking Nuke's reader — and reconstructs the full node graph in memory, including every connection.

When you import, it doesn't just paste the raw node blocks. It simulates Nuke's stack reader from scratch to work out the correct `push`/`set` directives for your specific selection, then calls `nuke.nodePaste()` with a syntactically valid `.nk` snippet. This means connections are correct regardless of which nodes you pick, how many there are, or how branched the upstream graph is.

---

## Known Limitations

- **Read-only** — the graph is for browsing and selection only. Nodes can't be rearranged inside NodesImport's view.
- **External connections** — inputs that point to nodes outside your selection come in disconnected. You'll have to reconnect them manually after importing.

---

## License

MIT License. See `LICENSE` for details.

---

*Built for compositors, by a compositor.*
