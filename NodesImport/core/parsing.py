"""
parsing.py — Nuke .nk script parser for NodesImport.

SLOT CONVENTION (shared with graph_view.py — do not change independently):
  Nuke's .nk file is stack-based. When a node block is read, it pops
  `input_count` entries from the stack for its parent connections:

    first  pop → slot 0 (top of stack) = primary input   (A pipe on Merge2)
    second pop → slot 1                = secondary input  (B pipe on Merge2)
    third  pop → slot 2                = mask / tertiary

  After parsing, NukeNode.parent_indices is a slot-indexed list:
    parent_indices[0] = parse-index of the node wired to slot 0
    parent_indices[1] = parse-index of the node wired to slot 1
    parent_indices[i] = _NULL_INPUT (-1) if slot i is disconnected

  This list is NEVER filtered — slot positions are preserved so the
  writer and edge-drawer can use parent_indices[slot] directly.

  When writing an export file, slots are pushed in REVERSE order
  (highest slot first) so that slot 0 ends up on top of the stack
  when Nuke reads the node block.
"""

import re
import dataclasses
import os
from typing import List, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sentinel for a disconnected / null input slot (Nuke's "push 0").
_NULL_INPUT: int = -1

# Node types whose default input_count is 0 when no `inputs` knob is written.
# Every node NOT in this set defaults to 1 input if `inputs` is absent.
_ZERO_INPUT_TYPES: frozenset = frozenset({
    "backdrop",
    "backdropnode",     # Nuke 13+ renamed Backdrop → BackdropNode
    "stickynote",
    "root",
    "preferences",
    "postage_stamp",    # Stamps plugin; stack input is the Anchor, not a real pipe
})

# Node types that are display/terminal nodes: they may have visual inputs
# in Nuke's UI but do NOT participate in the compositing write-stack at all.
# Their `inputs N` knob is ignored — input_count is always forced to 0.
# This prevents them from popping entries off the stack during parse/export,
# which would corrupt connections for all subsequent nodes.
#
# Viewer: connects multiple streams for viewing but is not part of the DAG.
# ColorSpace: similarly decorative/display in some contexts.
_STACK_EXEMPT_TYPES: frozenset = frozenset({
    "viewer",
})

# Node types that carry no compositing meaning and should not appear in the
# graph view. They still participate in stack accounting so downstream
# connections are not corrupted.
_META_TYPES: frozenset = frozenset({
    "root",
    "preferences",
    "define_window_layout_xml",
    "clone_info",
})

# Node types that use Format B (flat layout) in .nk files:
#   Group {           ← header block; braces close here
#     name MyGroup
#   }
#    Blur { ... }     ← inner nodes at top level
#   end_group         ← terminator
_FLAT_GROUP_TYPES: frozenset = frozenset({
    "group",
    "livegroup",
    "gizmo",
})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class NukeNode:
    node_type: str
    name: str
    content: str        # Full exportable .nk text (includes body + end_group for flat Groups)
    xpos: float = 0.0
    ypos: float = 0.0
    tile_color: Optional[int] = None
    input_count: int = 0
    # Slot-indexed list of parent parse-indices.
    # parent_indices[i] is the parse-index of the node connected to input slot i.
    # _NULL_INPUT means that slot is disconnected.
    # Length == input_count (always, including trailing nulls).
    parent_indices: List[int] = dataclasses.field(default_factory=list)
    index: int = 0
    # Clone support: if this node is a clone, clone_var holds the $VAR string
    # (e.g. "$N8c38800") referencing the original. The content field holds the
    # raw clone block verbatim for export. clone_source_index is the parse
    # index of the original node so the exporter can remap $VAR reliably.
    clone_var: Optional[str] = None
    clone_source_index: int = -1   # parse index of the cloned original (-1 if not a clone)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_nuke_script(filepath: str) -> List[NukeNode]:
    """
    Parse a Nuke .nk script and return an ordered list of NukeNode objects.

    The list is in parse order (upstream → downstream), which is also the
    order in which nodes must be written to a valid .nk export snippet.

    Group format handling:
      FORMAT A — self-contained: `nodes { }` sub-block inside the header
                 braces. Brace counting captures the full blob. No end_group.
      FORMAT B — flat / legacy: header braces close immediately, inner nodes
                 follow at the top level until `end_group`. We accumulate the
                 full body into NukeNode.content so it exports correctly.

    Meta-nodes (Root, Preferences, define_window_layout_xml) participate in
    stack accounting but are excluded from the returned list.
    """
    if not os.path.exists(filepath):
        return []

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    nodes: List[NukeNode] = []
    stack: List[int] = []       # Parse-time stack: stores node indices
    variables: Dict[str, int] = {}  # Named variables from `set VAR [stack N]`

    node_start_re  = re.compile(r"^\s*([\w.]+)\s*\{")  # [\w.]+ handles OFX dotted names
    clone_start_re = re.compile(r"^\s*clone\s+(\$\w+)\s*\{")  # clone $VAR {

    current_type: Optional[str] = None
    current_lines: List[str] = []
    brace_count: int = 0
    in_string: bool = False
    node_index: int = 0
    current_clone_var: Optional[str] = None  # set when parsing a clone block

    # flat_body_stack: list of NukeNode objects whose flat Group body is
    # being accumulated.
    #
    # NESTING LOGIC:
    # A flat Group body may contain nested flat Groups, each requiring their
    # own end_group.  We track this with a PARALLEL counter list:
    #
    #   flat_body_depth[i] = number of end_group tokens still needed for
    #                        flat_body_stack[i] to be complete.
    #
    # The counter starts at 1.  Each time we see a nested flat Group header
    # CLOSE inside the body (detected by line-level brace tracking that is
    # string-aware via _count_braces), we increment the counter.
    # Each end_group decrements it.  When it reaches 0 the body is done.
    #
    # We track nested headers with a mini state machine that runs ONLY while
    # we are at brace depth 0 within the flat body (i.e. not inside any knob
    # value block or inner Format-A Group).  This prevents false positives
    # from the word "group" appearing inside string literals.
    flat_body_stack: List["NukeNode"] = []
    flat_body_depth: List[int]        = []   # parallel; flat_body_depth[i] for flat_body_stack[i]

    # Mini state machine for detecting nested flat Group headers
    _fb_type:    Optional[str] = None   # node type of the header being tracked
    _fb_lines:   List[str]     = []     # accumulated header lines
    _fb_brace:   int           = 0      # brace depth within current header
    _fb_in_str:  bool          = False  # inside string literal?

    for line in lines:

        # ── FLAT BODY CAPTURE ────────────────────────────────────────────────
        if flat_body_stack:
            group_node = flat_body_stack[-1]

            if re.match(r"^\s*end_group\b", line):
                flat_body_depth[-1] -= 1
                if flat_body_depth[-1] > 0:
                    # Closes an inner nested group — keep accumulating
                    group_node.content += "\n" + line
                    # Reset mini state machine
                    _fb_type = None; _fb_lines = []; _fb_brace = 0; _fb_in_str = False
                else:
                    # Closes OUR group — body complete
                    flat_body_stack.pop()
                    flat_body_depth.pop()
                    group_node.content += "\nend_group"
                    # Forward end_group into the next outer Group if doubly nested
                    if flat_body_stack:
                        flat_body_stack[-1].content += "\n" + line
                        flat_body_depth[-1] -= 1
                        if flat_body_depth[-1] <= 0:
                            # Edge case: outer group also closes (shouldn't happen normally)
                            outer = flat_body_stack.pop()
                            flat_body_depth.pop()
                            outer.content += "\nend_group"
                    _fb_type = None; _fb_lines = []; _fb_brace = 0; _fb_in_str = False

            else:
                group_node.content += "\n" + line

                # ── Nested flat Group header detection ────────────────────────
                # Only run the mini state machine when we are at depth 0
                # (not inside any inner header block).  This ensures we only
                # detect headers that are direct children of the flat body,
                # and never match text inside knob values or string literals.
                if _fb_type is None:
                    m_inner = node_start_re.match(line)
                    if m_inner:
                        _fb_type  = m_inner.group(1)
                        _fb_lines = [line]
                        _fb_brace = 0; _fb_in_str = False
                        _fb_brace, _fb_in_str = _count_braces(line, _fb_brace, _fb_in_str)
                        if _fb_brace <= 0:
                            # One-liner
                            if (
                                _fb_type.lower() in _FLAT_GROUP_TYPES
                                and not _has_nodes_subblock(_fb_lines)
                            ):
                                flat_body_depth[-1] += 1
                            _fb_type = None; _fb_lines = []
                else:
                    _fb_lines.append(line)
                    _fb_brace, _fb_in_str = _count_braces(line, _fb_brace, _fb_in_str)
                    if _fb_brace <= 0:
                        # Header closed — check if it is a flat Group type
                        if (
                            _fb_type.lower() in _FLAT_GROUP_TYPES
                            and not _has_nodes_subblock(_fb_lines)
                        ):
                            flat_body_depth[-1] += 1
                        _fb_type = None; _fb_lines = []; _fb_brace = 0; _fb_in_str = False

            continue

                # ── STATE A: scanning for the next top-level construct ───────────────
        if current_type is None:

            # push 0 → disconnected / null input slot
            if re.match(r"^\s*push\s+0\b", line):
                stack.append(_NULL_INPUT)
                continue

            # push $VAR → re-push a previously saved node index
            push_m = re.match(r"^\s*push\s+\$(\w+)", line)
            if push_m:
                vn = push_m.group(1)
                if vn in variables:
                    stack.append(variables[vn])
                # Unknown variable (corrupt script): skip rather than crash.
                continue

            # set VAR [stack N] → save a stack entry to a named variable
            set_m = re.match(r"^\s*set\s+(\w+)\s+\[stack\s+(\d+)\]", line)
            if set_m:
                vn  = set_m.group(1)
                pos = int(set_m.group(2))
                # pos=0 → top of stack, pos=1 → one below, etc.
                if len(stack) > pos:
                    variables[vn] = stack[-(pos + 1)]
                continue

            # Start of a new node block
            m = node_start_re.match(line)
            clone_m = clone_start_re.match(line)

            if clone_m and not m:
                # Clone block: `clone $VAR { ... }`
                # Treat it like a regular node block but tag it with clone_var.
                current_clone_var = clone_m.group(1)
                current_type = "clone"
                current_lines = [line]
                brace_count = 0
                in_string = False
                brace_count, in_string = _count_braces(line, brace_count, in_string)
                if brace_count <= 0:
                    # One-liner clone block
                    _finalize_clone(
                        nodes, current_clone_var, current_lines,
                        node_index, stack, variables,
                    )
                    node_index += 1
                    current_type = None
                    current_clone_var = None
            elif m:
                current_clone_var = None
                current_type = m.group(1)
                current_lines = [line]
                brace_count = 0
                in_string = False
                brace_count, in_string = _count_braces(line, brace_count, in_string)

                if brace_count <= 0:
                    # One-liner node (entire block on one line)
                    new_node = _finalize_node(
                        nodes, current_type, current_lines,
                        node_index, stack, variables,
                    )
                    if (
                        new_node is not None
                        and current_type.lower() in _FLAT_GROUP_TYPES
                        and not _has_nodes_subblock(current_lines)
                    ):
                        flat_body_stack.append(new_node)
                        flat_body_depth.append(1)
                    node_index += 1
                    current_type = None

            continue  # Always continue after STATE A

        # ── STATE B: accumulating lines for a multi-line node ────────────────
        current_lines.append(line)
        brace_count, in_string = _count_braces(line, brace_count, in_string)

        if brace_count <= 0:
            if current_clone_var is not None:
                # Finalize a clone block
                _finalize_clone(
                    nodes, current_clone_var, current_lines,
                    node_index, stack, variables,
                )
                node_index += 1
                current_type = None
                current_clone_var = None
                current_lines = []
                brace_count = 0
                in_string = False
            else:
                is_flat = current_type.lower() in _FLAT_GROUP_TYPES

                new_node = _finalize_node(
                    nodes, str(current_type), current_lines,
                    node_index, stack, variables,
                )

                if (
                    is_flat
                    and new_node is not None
                    and not _has_nodes_subblock(current_lines)
                ):
                    flat_body_stack.append(new_node)
                    flat_body_depth.append(1)

                node_index += 1
                current_type = None
                current_lines = []
                brace_count = 0
                in_string = False

    return nodes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _count_braces(line: str, brace_count: int, in_string: bool) -> Tuple[int, bool]:
    """Update brace depth and string-literal state for one line."""
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_string = not in_string
        if not in_string:
            if ch == "{":
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
    return brace_count, in_string


def _has_nodes_subblock(lines: List[str]) -> bool:
    """Return True if this block uses Format A (contains a `nodes {` sub-block)."""
    return bool(re.search(r"^\s*nodes\s*\{", "\n".join(lines), re.MULTILINE))


def _finalize_clone(
    nodes: List[NukeNode],
    clone_var: str,
    lines: List[str],
    index: int,
    stack: List[int],
    variables: Dict[str, int],
) -> Optional[NukeNode]:
    """
    Handle a `clone $VAR { ... }` block.

    Clones share the same type, name and parameters as their original node.
    We look up the original via variables, copy its metadata, then give the
    clone its own unique index, position, and content (the verbatim block).

    Stack behaviour: a clone pops inputs just like a regular node would —
    using the same input_count as the original. It then pushes itself so
    downstream nodes connect correctly.
    """
    content = "\n".join(lines)

    # variables dict stores keys WITHOUT the leading '$'
    var_key = clone_var.lstrip("$")
    original_idx = variables.get(var_key)
    original: Optional[NukeNode] = None
    if original_idx is not None:
        for nd in nodes:
            if nd.index == original_idx:
                original = nd
                break

    # Clone blocks may declare their own `inputs N+M` (e.g. `inputs 2+1`).
    # If present, use that; otherwise fall back to the original's input_count.
    inputs_m = re.search(r"^\s*inputs\s+(\d+)(?:\+(\d+))?", content, re.MULTILINE)
    if inputs_m:
        base  = int(inputs_m.group(1))
        extra = int(inputs_m.group(2)) if inputs_m.group(2) else 0
        input_count = base + extra
    elif original is not None:
        input_count = original.input_count
    else:
        input_count = 1

    # Extract position overrides
    xpos_m = re.search(r"^\s*xpos\s+(-?\d+)", content, re.MULTILINE)
    ypos_m = re.search(r"^\s*ypos\s+(-?\d+)", content, re.MULTILINE)
    xpos = float(xpos_m.group(1)) if xpos_m else (original.xpos if original else 0.0)
    ypos = float(ypos_m.group(1)) if ypos_m else (original.ypos if original else 0.0)

    # Pop stack for inputs, push self
    parent_indices: List[int] = [
        stack.pop() if stack else _NULL_INPUT
        for _ in range(input_count)
    ]
    stack.append(index)

    # Build a display name: original name + " (clone)"
    base_name = original.name if original else var_key
    # Each clone gets a unique name for display; import uses the verbatim block
    clone_name = f"{base_name} (clone)"

    node = NukeNode(
        node_type=original.node_type if original else "clone",
        name=clone_name,
        content=content,
        xpos=xpos,
        ypos=ypos,
        tile_color=original.tile_color if original else None,
        input_count=input_count,
        parent_indices=parent_indices,
        index=index,
        clone_var=clone_var,
        clone_source_index=original.index if original else -1,
    )
    nodes.append(node)
    return node


def _finalize_node(
    nodes: List[NukeNode],
    node_type: str,
    lines: List[str],
    index: int,
    stack: List[int],
    variables: Dict[str, int],
) -> Optional[NukeNode]:
    """
    Extract metadata from the accumulated lines, resolve stack connections,
    and append a NukeNode to `nodes`.

    Returns the NukeNode (so the caller can push it onto flat_body_stack),
    or None for meta-nodes (which are excluded from output but still
    consume and push stack entries to keep accounting correct).

    parent_indices contract:
      - Length always equals input_count.
      - parent_indices[i] is the parse-index of the node at slot i,
        or _NULL_INPUT if that slot is disconnected.
      - Slot positions are NEVER compressed — the list is always dense
        and slot-indexed so graph_view.py can use parent_indices[slot]
        directly without offset arithmetic.
    """
    content = "\n".join(lines)

    name_m  = re.search(r'^\s*name\s+(?:"([^"]+)"|(\S+))',         content, re.MULTILINE)
    xpos_m  = re.search(r"^\s*xpos\s+(-?\d+)",                     content, re.MULTILINE)
    ypos_m  = re.search(r"^\s*ypos\s+(-?\d+)",                     content, re.MULTILINE)
    color_m = re.search(r"^\s*tile_color\s+(0x[0-9a-fA-F]+|\d+)",  content, re.MULTILINE)

    # Find the `inputs` knob for this node.
    #
    # Nuke writes mask inputs as `inputs N+M` (e.g. `inputs 2+1` = 2 regular
    # inputs + 1 mask input = 3 total slots on the stack).
    # We must sum both parts so the correct number of stack pops happen and
    # the mask slot is tracked as parent_indices[N] rather than being lost.
    #
    # Search strategy: walk the lines of THIS node block, tracking brace depth.
    # lines[0] is always the "NodeType {" opener (depth becomes 1 after it).
    # We only match `inputs` at depth == 1 (directly inside the node block,
    # not inside any nested sub-block like a `nodes { }` or knob value block).
    inputs_m = None
    _depth = 0
    _in_str = False
    for _lidx, _line in enumerate(lines):
        # Count braces on this line
        for _ci, _ch in enumerate(_line):
            if _ch == '"' and (_ci == 0 or _line[_ci-1] != '\\'):
                _in_str = not _in_str
            if not _in_str:
                if _ch == '{': _depth += 1
                elif _ch == '}': _depth -= 1
        # After the opening line (lidx>0), if we are at depth 1 and not in
        # a string, this line is directly inside the node block — safe to match.
        # We skip lidx==0 because that line is "NodeType {" itself, not a knob.
        if _lidx > 0 and _depth == 1 and not _in_str:
            _m = re.match(r"^\s*inputs\s+(\d+)(?:\+(\d+))?", _line)
            if _m:
                inputs_m = _m
                break
        if _depth <= 0:
            break

    name = (name_m.group(1) or name_m.group(2)) if name_m else f"{node_type}_{index}"
    xpos = float(xpos_m.group(1)) if xpos_m else 0.0
    ypos = float(ypos_m.group(1)) if ypos_m else 0.0

    tile_color: Optional[int] = None
    if color_m:
        try:
            val = color_m.group(1)
            tile_color = int(val, 16) if val.startswith("0x") else int(val)
        except ValueError:
            pass

    # Stack-exempt types (Viewer, ColorSpace) never participate in the
    # write-stack regardless of what `inputs N` the file declares.
    # Their input connections are display-only and must not pop stack entries.
    if node_type.lower() in _STACK_EXEMPT_TYPES:
        input_count = 0
    elif inputs_m:
        base  = int(inputs_m.group(1))
        extra = int(inputs_m.group(2)) if inputs_m.group(2) else 0
        input_count = base + extra
    elif node_type.lower() in _ZERO_INPUT_TYPES:
        input_count = 0
    else:
        input_count = 1  # Nuke's implicit default for most node types

    # ── Stack pop: slot 0 = first pop = top of stack ─────────────────────────
    # Pop exactly input_count times. Each pop yields one parent index (or
    # _NULL_INPUT if the stack is empty / slot was a push 0).
    # The resulting list is slot-indexed: parent_indices[0] = slot 0, etc.
    parent_indices: List[int] = [
        stack.pop() if stack else _NULL_INPUT
        for _ in range(input_count)
    ]

    # Push self so downstream nodes can reference us.
    stack.append(index)

    # Meta-nodes: stack accounting done, but excluded from output.
    if node_type.lower() in _META_TYPES:
        return None

    node = NukeNode(
        node_type=node_type,
        name=name,
        content=content,
        xpos=xpos,
        ypos=ypos,
        tile_color=tile_color,
        input_count=input_count,
        parent_indices=parent_indices,
        index=index,
    )
    nodes.append(node)
    return node
