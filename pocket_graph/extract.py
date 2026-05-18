"""AST extraction for 24 languages.

Two-pass design:
  Pass 1: Per-file structural extraction (classes, functions, imports)
  Pass 2: Cross-file import resolution -> INFERRED edges

Edge relations:
  defines, contains, calls, imports, imports_from, uses, includes,
  references_constant, binds_method, bound_to, listened_by, rationale_for,
  uses_component, uses_static_prop
"""
from __future__ import annotations
import hashlib
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from .languages import LanguageConfig, get_language

# ============================================================
# Helpers
# ============================================================
def _make_id(*parts: str) -> str:
    """Normalize an ID string."""
    s = "_".join(str(p) for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    return cleaned.strip("_").lower()


def _read_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _resolve_name(node, source: bytes, config: LanguageConfig) -> str | None:
    """Find the name child of a node. Tries field name first, then identifier descendants."""
    name_node = node.child_by_field_name(config.name_field)
    if name_node is not None:
        return _read_text(name_node, source)
    # Fallback: first identifier descendant
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "name", "constant",
                          "scoped_identifier", "qualified_name",
                          "simple_identifier", "field_identifier"):
            return _read_text(child, source)
    return None


def _file_stem(path: Path) -> str:
    return path.stem


def _decorator_text(node, source: bytes) -> str:
    """Extract a decorator's textual form, stripping leading @ for canonical form."""
    text = _read_text(node, source).strip()
    if text.startswith("@"):
        text = text[1:]
    return text


# ============================================================
# Generic AST walk
# ============================================================
def _extract_generic(path: Path, config: LanguageConfig) -> dict:
    """Single-file structural extraction.

    Returns:
        {"nodes": [...], "edges": [...]} matching the documented schema.
    """
    src = path.read_bytes()
    parser = config.parser()
    tree = parser.parse(src)
    root = tree.root_node

    str_path = str(path)
    stem = _file_stem(path)
    file_nid = _make_id(stem)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    # File node -- represents the whole file as a hub
    nodes.append({
        "id": file_nid,
        "label": path.name,
        "file_type": "code",
        "source_file": str_path,
        "source_location": "L1",
    })
    seen_ids.add(file_nid)

    def add_node(nid: str, label: str, line: int, file_type: str = "code") -> None:
        if nid in seen_ids:
            return
        seen_ids.add(nid)
        nodes.append({
            "id": nid,
            "label": label,
            "file_type": file_type,
            "source_file": str_path,
            "source_location": f"L{line}",
        })

    def add_edge(src_id: str, tgt_id: str, relation: str, line: int,
                 confidence: str = "EXTRACTED", **extra) -> None:
        e = {
            "source": src_id,
            "target": tgt_id,
            "relation": relation,
            "confidence": confidence,
            "source_file": str_path,
            "source_location": f"L{line}",
        }
        e.update(extra)
        edges.append(e)

    # ============== imports (Pass 1, file-level) ==============
    # Aliases collected from this file: {alias_name: real_name}
    # Includes: `from X import Y as Z`, `import X as Z`, and module-level
    # rebinds like `Z = Y`. Used by Pass 2 to resolve calls through aliases.
    file_aliases: dict[str, str] = {}

    def walk_imports(node) -> None:
        # Detect aliased_import nodes (Python: `Y as Z`)
        if node.type == "aliased_import":
            real_node = node.child_by_field_name("name")
            if real_node is None and node.children:
                real_node = node.children[0]
            alias_node = None
            for c in node.children:
                if c.type == "identifier":
                    alias_node = c
            if real_node and alias_node:
                real_name = _read_text(real_node, src).strip().split(".")[-1]
                alias_name = _read_text(alias_node, src).strip()
                if real_name and alias_name and real_name != alias_name:
                    file_aliases[alias_name] = real_name
                    # Also lowercase variant -- Pass 2 uses normalized funcnames
                    if alias_name.lower() != alias_name:
                        file_aliases[alias_name.lower()] = real_name

        if node.type in config.import_node_types:
            text = _read_text(node, src).strip()
            # heuristic: extract the imported name
            # Python: "import x" or "from y import z"
            # JS: "import {x} from 'y'" etc.
            # We record edges like file --imports--> "x" (target may be unresolved)
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Pull module/symbol names
                m = re.search(r"(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+)|use\s+([\w:]+)|require[\s(]+['\"]([^'\"]+)|#include\s*[<\"]([^>\"]+)|using\s+([\w.]+))", line)
                if not m:
                    continue
                target = next((g for g in m.groups() if g), None)
                if not target:
                    continue
                # Normalize and emit
                for chunk in re.split(r"[,\s]+", target.strip()):
                    chunk = chunk.strip(".:/<>\"'")
                    if not chunk:
                        continue
                    target_id = _make_id(chunk)
                    if target_id and target_id not in seen_ids:
                        # external/unresolved imports become "concept" nodes
                        nodes.append({
                            "id": target_id,
                            "label": chunk,
                            "file_type": "concept",
                            "source_file": "",
                            "source_location": "",
                        })
                        seen_ids.add(target_id)
                    add_edge(file_nid, target_id, "imports", node.start_point[0] + 1)
        for c in node.children:
            walk_imports(c)

    walk_imports(root)

    # Detect module-level rebinds: `alias_name = real_name` at module top-level only.
    # Tree-sitter Python module body holds expression_statement -> assignment.
    for child in root.children:
        if child.type != "expression_statement":
            continue
        for sub in child.children:
            if sub.type != "assignment":
                continue
            # assignment: identifier = identifier (right side must be a bare name)
            lhs = None
            rhs = None
            seen_eq = False
            for c in sub.children:
                if c.type == "=" or c.type == "operator":
                    seen_eq = True
                    continue
                if c.type == "identifier":
                    if not seen_eq:
                        lhs = _read_text(c, src).strip()
                    else:
                        rhs = _read_text(c, src).strip()
            if lhs and rhs and lhs != rhs:
                # Only register if the RHS is something we know about (an existing
                # alias target or any user-defined symbol). Conservative: register
                # if RHS looks like a function name (snake_case identifier).
                file_aliases[lhs] = file_aliases.get(rhs, rhs)

    # ============== classes / functions / methods (Pass 1, structural) ==============
    def walk_defs(node, parent_class_nid: str | None = None) -> None:
        node_type = node.type

        if node_type in config.class_node_types:
            name = _resolve_name(node, src, config)
            if name:
                cls_nid = _make_id(stem, name)
                add_node(cls_nid, name, node.start_point[0] + 1)
                add_edge(file_nid, cls_nid, "defines", node.start_point[0] + 1)
                # decorators
                if config.decorator_node_type and node.parent and \
                   node.parent.type == "decorated_definition":
                    for dec in node.parent.children:
                        if dec.type == config.decorator_node_type:
                            dt = _decorator_text(dec, src)
                            dec_nid = _make_id("decorator", dt[:40])
                            add_node(dec_nid, f"@{dt[:60]}", dec.start_point[0] + 1)
                            add_edge(cls_nid, dec_nid, "uses", dec.start_point[0] + 1)
                # walk body for methods
                body = node.child_by_field_name(config.body_field)
                if body is None:
                    body = node
                for c in body.children:
                    walk_defs(c, parent_class_nid=cls_nid)
                return

        if node_type in config.function_node_types:
            name = _resolve_name(node, src, config)
            if name:
                # Go-style method receiver detection: a method_declaration
                # has its receiver as the FIRST parameter_list (before the name),
                # and the function's own params come after. The receiver
                # parameter_declaration contains the type which IS the class.
                # Pattern: `func (l *Logger) Log(...)` -> bind to Logger.
                method_receiver_class = None
                if (node_type == "method_declaration"
                        and parent_class_nid is None
                        and config.name == "go"):
                    # Find the first parameter_list child
                    plists = [c for c in node.children if c.type == "parameter_list"]
                    if plists:
                        recv = plists[0]
                        for c in recv.children:
                            if c.type != "parameter_declaration":
                                continue
                            # Look for type (pointer_type or type_identifier)
                            for tc in c.children:
                                if tc.type in ("type_identifier",):
                                    method_receiver_class = _read_text(tc, src).strip()
                                    break
                                if tc.type == "pointer_type":
                                    for ptc in tc.children:
                                        if ptc.type == "type_identifier":
                                            method_receiver_class = _read_text(ptc, src).strip()
                                            break
                            if method_receiver_class:
                                break
                    # Promote: synthesize a class node id and use as parent.
                    # The class node itself is created by the type_declaration
                    # walk (already happens), so we just reuse its id.
                    if method_receiver_class:
                        receiver_cls_nid = _make_id(stem, method_receiver_class)
                        # Add the class node if it didn't exist yet
                        # (defensive -- type_declaration may run after method_declaration
                        # depending on AST ordering)
                        if not any(n["id"] == receiver_cls_nid for n in nodes):
                            add_node(receiver_cls_nid, method_receiver_class, 1)
                            add_edge(file_nid, receiver_cls_nid, "defines", 1)
                        parent_class_nid = receiver_cls_nid

                if parent_class_nid:
                    fn_nid = _make_id(parent_class_nid, name)
                    add_node(fn_nid, f"{name}()", node.start_point[0] + 1)
                    add_edge(parent_class_nid, fn_nid, "contains",
                             node.start_point[0] + 1)
                else:
                    fn_nid = _make_id(stem, name)
                    add_node(fn_nid, f"{name}()", node.start_point[0] + 1)
                    add_edge(file_nid, fn_nid, "defines", node.start_point[0] + 1)
                # decorators
                if config.decorator_node_type and node.parent and \
                   node.parent.type == "decorated_definition":
                    for dec in node.parent.children:
                        if dec.type == config.decorator_node_type:
                            dt = _decorator_text(dec, src)
                            dec_nid = _make_id("decorator", dt[:40])
                            add_node(dec_nid, f"@{dt[:60]}", dec.start_point[0] + 1)
                            add_edge(fn_nid, dec_nid, "uses", dec.start_point[0] + 1)
                # walk function body for calls (Pass 2 input)
                body = node.child_by_field_name(config.body_field)
                if body:
                    walk_calls(body, fn_nid, enclosing_class_nid=parent_class_nid)
                return

        for c in node.children:
            walk_defs(c, parent_class_nid=parent_class_nid)

    def walk_calls(body_node, caller_nid: str,
                    enclosing_class_nid: str | None = None) -> None:
        """Collect call sites within a function body. Adds INFERRED `calls` edges.

        If a call is `cls(...)` or `Self.method()` inside a method, the target
        is the enclosing class (Python @classmethod pattern, also covers
        Rust Self::new etc).
        """
        # Field names that hold the called function/method/type -- varies by language.
        # Order matters: more specific first.
        # Python/JS:        function
        # Java method_invocation: name
        # Java object_creation:   type
        # C# invocation:    function
        # C# object_creation:     type
        # Rust:             function
        # Many others:      function (default)
        callee_fields = ("function", "name", "type")

        def visit(n):
            if n.type in config.call_node_types:
                fn_node = None
                for fname in callee_fields:
                    fn_node = n.child_by_field_name(fname)
                    if fn_node is not None:
                        break
                if fn_node is None and n.children:
                    # Fallback: first non-keyword identifier-like child
                    for c in n.children:
                        if c.type not in ("(", ")", ".", ",", "new"):
                            fn_node = c
                            break
                if fn_node is None:
                    return
                callee_text = _read_text(fn_node, src).strip()
                # take the trailing identifier (e.g. "self.foo.bar" -> "bar")
                callee_name = re.split(r"[.:]+", callee_text)[-1] if callee_text else None

                # Special case: bare `cls()` or `Self::new()` -- target the enclosing class
                if (callee_name in ("cls", "Self") and enclosing_class_nid
                        and "." not in callee_text and "::" not in callee_text):
                    add_edge(caller_nid, enclosing_class_nid, "calls",
                             n.start_point[0] + 1, confidence="INFERRED")
                    for c in n.children:
                        visit(c)
                    return

                if callee_name and re.match(r"^[A-Za-z_][\w]*$", callee_name):
                    # Target node is created lazily. ID is the callee's bare name
                    # -- Pass 2 may rewire this to a real definition.
                    callee_nid = _make_id(stem, callee_name)
                    if callee_nid != caller_nid:
                        # Capture the receiver attribute (e.g. for `self.wishlist.add(...)`,
                        # receiver = "wishlist") so Pass 2 can disambiguate when
                        # multiple classes have a method with the same name.
                        receiver = None
                        if "." in callee_text:
                            parts = callee_text.split(".")
                            if len(parts) >= 2:
                                receiver = parts[-2]  # right before the method name
                        edge_meta = {"_receiver": receiver} if receiver else {}
                        add_edge(caller_nid, callee_nid, "calls",
                                 n.start_point[0] + 1, confidence="INFERRED",
                                 **edge_meta)
            for c in n.children:
                visit(c)
        visit(body_node)

    walk_defs(root)

    return {"nodes": nodes, "edges": edges, "aliases": file_aliases}


# ============================================================
# Python-specific: NOTE/WHY/HACK comment + docstring rationale
# ============================================================
_RATIONALE_MARKERS = re.compile(
    r"#\s*(NOTE|WHY|HACK|TODO|FIXME|XXX|IMPORTANT)\s*:?\s*(.*)",
    re.IGNORECASE,
)


def _extract_python_rationale(path: Path, result: dict) -> None:
    """Add `rationale` nodes for inline comments and docstrings.

    Each rationale becomes a node with file_type='rationale' linked to the
    nearest enclosing definition via `rationale_for` edge.
    """
    src = path.read_bytes().decode("utf-8", errors="replace")
    str_path = str(path)
    stem = _file_stem(path)

    # Index existing nodes by line for "nearest preceding def" lookup
    def_lines = []
    for node in result["nodes"]:
        if node["source_file"] != str_path:
            continue
        loc = node.get("source_location", "")
        m = re.match(r"L(\d+)", loc)
        if m:
            def_lines.append((int(m.group(1)), node["id"]))
    def_lines.sort()

    def nearest_def(line: int) -> str | None:
        best = None
        for ln, nid in def_lines:
            if ln <= line:
                best = nid
            else:
                break
        return best

    seen = {n["id"] for n in result["nodes"]}

    # NOTE/WHY/HACK comments
    for i, line in enumerate(src.split("\n"), 1):
        m = _RATIONALE_MARKERS.search(line)
        if not m:
            continue
        marker = m.group(1).upper()
        text = m.group(2).strip()[:120]
        if not text:
            continue
        rid = _make_id(stem, "rationale", str(i))
        if rid in seen:
            continue
        seen.add(rid)
        result["nodes"].append({
            "id": rid,
            "label": f"{marker}: {text}",
            "file_type": "rationale",
            "source_file": str_path,
            "source_location": f"L{i}",
        })
        target = nearest_def(i)
        if target:
            result["edges"].append({
                "source": rid,
                "target": target,
                "relation": "rationale_for",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "source_location": f"L{i}",
            })

    # Docstrings -- match """...""" or '''...''' at start of body
    docstring_pattern = re.compile(
        r'(?:def\s+(\w+)|class\s+(\w+))[^\n]*\n\s*("""(.*?)"""|\'\'\'(.*?)\'\'\')',
        re.DOTALL,
    )
    # Build name -> node_id index for this file's nodes (so we can target methods
    # whose IDs include both class and method name, e.g. pipeline_searchpipeline_query)
    name_to_nid: dict[str, str] = {}
    for node in result["nodes"]:
        if node["source_file"] != str_path:
            continue
        label = node.get("label", "")
        # Strip parens for methods like "query()"
        bare = re.sub(r"\(\)$", "", label).strip()
        if bare:
            # later-added (method) wins over earlier (class) when names collide,
            # but we keep both -- methods are more specific, so prefer the LAST
            # match in iteration order (which equals AST order = top-down).
            name_to_nid[bare] = node["id"]

    for m in docstring_pattern.finditer(src):
        line = src[:m.start()].count("\n") + 1
        owner_name = m.group(1) or m.group(2)
        body = (m.group(4) or m.group(5) or "").strip()
        if not body or len(body) < 8:
            continue
        rid = _make_id(stem, "docstring", str(line))
        if rid in seen:
            continue
        seen.add(rid)
        first_line = body.split("\n", 1)[0].strip()[:120]
        result["nodes"].append({
            "id": rid,
            "label": f"docstring: {first_line}",
            "file_type": "rationale",
            "source_file": str_path,
            "source_location": f"L{line}",
            "body": body[:1000],  # full body text (for query-time access)
        })
        # Look up the actual node ID by name in this file
        target_id = name_to_nid.get(owner_name) if owner_name else None
        if target_id:
            result["edges"].append({
                "source": rid,
                "target": target_id,
                "relation": "rationale_for",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "source_location": f"L{line}",
            })


# ============================================================
# Public single-file extractor
# ============================================================
def extract_file(path: Path) -> dict:
    """Extract one file. Dispatch on extension; return schema dict."""
    suffix = path.suffix.lower()

    # Detect whether this looks like an academic paper.
    # Papers get a single root node — no heading-based section nodes —
    # because LLM apply-semantic adds the real concepts with proper
    # section references afterward.
    try:
        from .detect import _looks_like_paper, FileType, classify
        is_paper = (suffix == ".pdf") or (
            suffix in {".md", ".mdx", ".rst", ".txt"} and _looks_like_paper(path)
        )
    except Exception:
        is_paper = (suffix == ".pdf")

    # Pass 3 dispatch: docs and PDFs
    if suffix in {".md", ".mdx", ".rst", ".txt"}:
        try:
            from .extract_docs import extract_markdown
            return extract_markdown(path, is_paper=is_paper)
        except Exception as e:
            return {"nodes": [], "edges": [], "error": f"md: {e}"}
    if suffix == ".pdf":
        try:
            from .extract_docs import extract_pdf
            return extract_pdf(path, is_paper=is_paper)
        except Exception as e:
            return {"nodes": [], "edges": [], "error": f"pdf: {e}"}
    if suffix == ".docx":
        try:
            from .extract_docs import extract_docx
            return extract_docx(path)
        except Exception as e:
            return {"nodes": [], "edges": [], "error": f"docx: {e}"}
    if suffix == ".xlsx":
        try:
            from .extract_docs import extract_xlsx
            return extract_xlsx(path)
        except Exception as e:
            return {"nodes": [], "edges": [], "error": f"xlsx: {e}"}

    # Code (Pass 1)
    config = get_language(path)
    if config is None:
        return {"nodes": [], "edges": []}

    try:
        result = _extract_generic(path, config)
    except Exception as e:
        return {"nodes": [], "edges": [], "error": f"{type(e).__name__}: {e}"}

    if config.name == "python":
        try:
            _extract_python_rationale(path, result)
        except Exception:
            pass

    return result


# ============================================================
# Pass 2: cross-file import resolution
# ============================================================
def _resolve_cross_file_calls(extractions: list[dict]) -> list[dict]:
    """Pass 2: rewire INFERRED `calls` edges that point to undefined targets
    onto matching definitions in other files.
    
    Conservative resolution rules to avoid false positives:
    - Only resolve if there is EXACTLY ONE matching candidate by exact label match
      (not by trailing-name match).
    - Common stdlib method names are blacklisted (lower, upper, append, get, ...).
    - If the unresolved target ID has no underscore (suggests a local-only name),
      leave it unresolved.
    """
    # Standard methods/builtins that often appear as `.method()` and should not
    # be confused with user-defined functions of the same name.
    STDLIB_METHODS = {
        # str methods
        "lower", "upper", "strip", "split", "rsplit", "join", "replace", "format",
        "encode", "decode", "startswith", "endswith", "find", "rfind",
        "isalpha", "isdigit", "isspace", "isupper", "islower",
        "capitalize", "title", "swapcase", "expandtabs", "ljust", "rjust",
        "center", "zfill", "translate", "maketrans", "splitlines", "lstrip", "rstrip",
        "casefold", "format_map", "isalnum", "isascii", "isdecimal", "isidentifier",
        "isnumeric", "isprintable", "istitle",
        # list methods
        "append", "extend", "insert", "pop", "remove", "clear", "copy",
        "sort", "reverse", "count", "index",
        # dict methods
        "get", "keys", "values", "items", "update", "setdefault", "fromkeys", "popitem",
        # set methods
        "add", "discard", "union", "intersection", "difference", "symmetric_difference",
        "issubset", "issuperset", "isdisjoint", "intersection_update", "difference_update",
        # IO
        "read", "write", "close", "open", "seek", "tell", "flush", "readline",
        "readlines", "writelines", "readable", "writable", "seekable",
        # builtins as methods
        "next", "iter", "len", "type", "isinstance", "issubclass",
        "hasattr", "getattr", "setattr", "delattr", "callable",
        "id", "hash", "repr", "str", "int", "float", "bool",
        "list", "dict", "tuple", "set", "frozenset",
        "print", "sorted", "reversed", "filter", "map", "zip", "range", "enumerate",
        "min", "max", "sum", "abs", "round", "pow", "divmod", "all", "any",
        # re module
        "match", "search", "sub", "subn", "compile", "finditer", "fullmatch",
        "groups", "group", "groupdict", "span", "start", "end",
        # pathlib
        "exists", "is_file", "is_dir", "is_symlink", "is_absolute", "is_reserved",
        "rglob", "glob", "iterdir", "mkdir", "rmdir", "unlink", "rename",
        "stat", "lstat", "chmod", "lchmod", "resolve", "absolute", "relative_to",
        "with_suffix", "with_name", "with_stem", "joinpath", "samefile",
        "read_text", "read_bytes", "write_text", "write_bytes", "is_relative_to",
        # datetime
        "now", "utcnow", "isoformat", "strftime", "strptime", "timestamp",
        "date", "time", "today", "fromtimestamp",
        # json
        "loads", "dumps", "load", "dump",
        # other common
        "decode", "encode", "freeze", "thaw",
    }

    # Build label -> [node_id] index across all files
    label_to_ids: dict[str, list[str]] = {}
    # Also build a "stem prefixes" set so we can strip them from edge targets.
    stems = set()
    # Receiver-type map: for each class, which attribute names hold which class
    # instances. Populated by scanning each class's __init__ body for patterns
    # like `self.X = ClassName(...)`. Used in Pass 2 to disambiguate
    # `self.X.method()` between same-named methods on different classes.
    # Schema: {class_label_lower: {attr_name: class_label_lower}}
    receiver_type_map: dict[str, dict[str, str]] = {}

    for ext in extractions:
        for node in ext["nodes"]:
            label = node.get("label", "").strip()
            if not label:
                continue
            bare = re.sub(r"\(\)$", "", label).strip().lower()
            if bare and node.get("file_type") == "code":
                label_to_ids.setdefault(bare, []).append(node["id"])
            if node.get("source_file"):
                from pathlib import Path
                stem = _make_id(Path(node["source_file"]).stem)
                stems.add(stem)

    # Scan source files for `self.X = ClassName(...)` patterns
    # Compile regex once outside the loop.
    _CLASS_PATTERN = re.compile(
        r"^class\s+(\w+).*?:[^\n]*\n((?:[ \t]+.*\n)+)",
        re.MULTILINE,
    )
    _ATTR_PATTERN = re.compile(
        r"self\.(\w+)\s*(?::\s*[\w\[\], ]+\s*)?=\s*([A-Z][\w]*)\s*\(",
    )
    seen_files: set[str] = set()
    for ext in extractions:
        for node in ext["nodes"]:
            src = node.get("source_file", "")
            if not src or src in seen_files:
                continue
            seen_files.add(src)
            try:
                text = Path(src).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for cm in _CLASS_PATTERN.finditer(text):
                cls_name = cm.group(1).lower()
                body = cm.group(2)
                for am in _ATTR_PATTERN.finditer(body):
                    attr_name = am.group(1)
                    type_name = am.group(2).lower()
                    receiver_type_map.setdefault(cls_name, {})[attr_name] = type_name

    all_ids = {n["id"] for ext in extractions for n in ext["nodes"]}

    # Build per-file alias maps. Each extraction has a source file (visible
    # via the file-hub node -- usually the first node) and an `aliases` field.
    file_to_aliases: dict[str, dict[str, str]] = {}
    for ext in extractions:
        aliases = ext.get("aliases") or {}
        if not aliases:
            continue
        # Find the file path: any node with a non-empty source_file works
        for n in ext["nodes"]:
            if n.get("source_file"):
                file_to_aliases[n["source_file"]] = aliases
                break

    # Build node-id -> source_file index for fast lookup
    nid_to_file: dict[str, str] = {}
    for ext in extractions:
        for n in ext["nodes"]:
            if n.get("source_file"):
                nid_to_file[n["id"]] = n["source_file"]

    # ============================================================
    # Pass 2 lookup indexes -- built ONCE in O(N+E) before main loop
    # ============================================================
    # Without these the main loop becomes O(E × N²) because each call edge
    # triggers nested scans of every extraction's nodes/edges.
    # 
    # nid_to_node:        node id -> node dict
    # nid_to_parent_cls:  node id -> enclosing class label (lowercase) or None
    # nid_to_dir:         node id -> directory of its source_file
    # cls_label_for_nid:  node id -> label (lowercase) IF this node IS a class
    nid_to_node: dict[str, dict] = {}
    for ext in extractions:
        for n in ext["nodes"]:
            nid_to_node[n["id"]] = n

    # `contains` edges go class -> method, so the SOURCE of a contains edge
    # is the parent class for the TARGET. Build child -> parent map.
    nid_to_parent_cls_id: dict[str, str] = {}
    for ext in extractions:
        for e in ext.get("edges", []):
            if e.get("relation") == "contains":
                nid_to_parent_cls_id[e["target"]] = e["source"]

    # Helper: get enclosing class label for a node id (or None)
    def _parent_class_label(nid: str) -> str | None:
        parent_id = nid_to_parent_cls_id.get(nid)
        if not parent_id:
            return None
        parent = nid_to_node.get(parent_id)
        if not parent:
            return None
        label = parent.get("label", "")
        if not label or label.endswith("()") or label.startswith("@"):
            return None
        return label.lower()

    # Helper: get source-file directory for a node (cached)
    nid_to_dir: dict[str, str] = {}
    for nid, src_file in nid_to_file.items():
        nid_to_dir[nid] = "/".join(src_file.split("/")[:-1])

    def extract_funcname(target_id: str) -> str:
        """Strip the stem prefix from a target ID to recover the function name.
        E.g. 'build_validate_extraction' (stem=build) -> 'validate_extraction'.
        Falls back to last segment if no stem prefix matches."""
        # Try each known stem (prefer longer prefixes to handle stems with underscores)
        for stem in sorted(stems, key=len, reverse=True):
            prefix = stem + "_"
            if target_id.startswith(prefix):
                return target_id[len(prefix):]
        # Fallback: last segment
        return target_id.rsplit("_", 1)[-1]

    rewired = 0
    dropped = 0
    for ext in extractions:
        kept_edges = []
        for edge in ext["edges"]:
            if edge["relation"] != "calls":
                kept_edges.append(edge)
                continue
            if edge["target"] in all_ids:
                kept_edges.append(edge)
                continue
            tgt = edge["target"]
            funcname = extract_funcname(tgt)
            is_stdlib_name = ("_" not in funcname) and (funcname in STDLIB_METHODS)

            # Look up candidates by exact funcname AND underscore-prefix variant
            # (e.g. lookup "tokenize" should also match user method "_tokenize").
            # Skip the underscore variant when funcname is stdlib-shaped -- otherwise
            # `text.lower()` matches a user-defined `_lower()` and creates noise.
            candidates = list(label_to_ids.get(funcname, []))
            if not candidates and not funcname.startswith("_") and not is_stdlib_name:
                candidates = list(label_to_ids.get("_" + funcname, []))

            # Alias-aware resolution: if direct lookup failed, consult the
            # source file's alias map. `from .utils import slugify as make_slug`
            # -> aliases["make_slug"] = "slugify", so a call to make_slug(...)
            # in this file can be resolved to slugify.
            via_alias = False
            if not candidates:
                src_file = nid_to_file.get(edge["source"])
                if src_file:
                    aliases = file_to_aliases.get(src_file, {})
                    real_name = aliases.get(funcname)
                    if real_name:
                        # Recursive alias chain -- resolve until stable
                        seen_alias = {funcname}
                        while real_name in aliases and real_name not in seen_alias:
                            seen_alias.add(real_name)
                            real_name = aliases[real_name]
                        candidates = list(label_to_ids.get(real_name.lower(), []))
                        if candidates:
                            via_alias = True

            # If no user-defined match AND name is stdlib-shaped -> really stdlib, drop
            if not candidates:
                if is_stdlib_name:
                    dropped += 1
                    continue
                # name not stdlib but no candidate (external import) -- drop silently
                dropped += 1
                continue
            # Filter to candidates that actually have this as their function name
            # (stem may differ but the bare label should match exactly)
            # First filter out the source itself (self-loops are almost always
            # stdlib clashing with the caller's own name).
            candidates = [c for c in candidates if c != edge["source"]]
            if not candidates:
                dropped += 1
                continue

            # Receiver-based disambiguation: if the call site is `self.X.method()`,
            # `_receiver` field holds "X". Look up X's type in the caller's class
            # via receiver_type_map to filter candidates to the right class.
            receiver = edge.get("_receiver")
            if len(candidates) > 1 and receiver:
                # O(1) lookup of caller's enclosing class via prebuilt index
                caller_class = _parent_class_label(edge["source"])

                # Look up receiver's type
                receiver_type = None
                if caller_class and caller_class in receiver_type_map:
                    receiver_type = receiver_type_map[caller_class].get(receiver)

                if receiver_type:
                    # Filter candidates to those whose enclosing class matches receiver_type
                    # -- O(k) where k = candidate count (was O(k × N × E))
                    filtered = [
                        cid for cid in candidates
                        if _parent_class_label(cid) == receiver_type
                    ]
                    if len(filtered) == 1:
                        candidates = filtered

            if len(candidates) == 1:
                edge["target"] = candidates[0]
                edge["confidence"] = "AMBIGUOUS" if is_stdlib_name else "INFERRED"
                # Strip the temporary _receiver field
                edge.pop("_receiver", None)
                rewired += 1
                kept_edges.append(edge)
            elif len(candidates) > 1:
                # Try to disambiguate by file proximity: prefer same-directory
                # -- O(1) src lookup + O(k) candidate scan (was O(N) twice each)
                src_dir = nid_to_dir.get(edge["source"])
                if src_dir is not None:
                    same_dir = [
                        cid for cid in candidates
                        if nid_to_dir.get(cid, "").startswith(src_dir)
                    ]
                    if len(same_dir) == 1:
                        edge["target"] = same_dir[0]
                        edge["confidence"] = "AMBIGUOUS" if is_stdlib_name else "INFERRED"
                        edge.pop("_receiver", None)
                        rewired += 1
                        kept_edges.append(edge)
                        continue
                # Multi-candidate, can't disambiguate by directory -- pick first but mark AMBIGUOUS
                edge["target"] = candidates[0]
                edge["confidence"] = "AMBIGUOUS"
                edge.pop("_receiver", None)
                rewired += 1
                kept_edges.append(edge)
            else:
                # No candidates anywhere -- leave unresolved (build will drop the edge)
                dropped += 1
        ext["edges"] = kept_edges

    if rewired or dropped:
        import sys as _sys
        print(f"[pocket_graph] Pass 2: rewired {rewired} call edges, "
              f"dropped {dropped} unresolved/ambiguous", file=_sys.stderr)

    return extractions


# ============================================================
# SHA256 cache
# ============================================================
def _cache_key(path: Path) -> str:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return h[:16]


def _cache_path(cache_root: Path, path: Path) -> Path:
    return cache_root / "cache" / f"{_cache_key(path)}.json"


def _load_cached(path: Path, cache_root: Path) -> dict | None:
    cp = _cache_path(cache_root, path)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_cached(path: Path, result: dict, cache_root: Path) -> None:
    cp = _cache_path(cache_root, path)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


# ============================================================
# Public batch extractor
# ============================================================
def _extract_one_for_pool(path_str: str) -> tuple[str, dict]:
    return path_str, extract_file(Path(path_str))


def extract(
    paths: list[Path],
    cache_root: Path | None = None,
    *,
    parallel: bool = True,
    max_workers: int | None = None,
) -> list[dict]:
    """Extract many files. Returns list of per-file extraction dicts.

    Pass 2 (cross-file resolution) is run automatically on the result.
    """
    if cache_root is None:
        cache_root = Path("graph-out")
    cache_root.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    uncached: list[Path] = []

    for p in paths:
        cached = _load_cached(p, cache_root)
        if cached is not None:
            results.append(cached)
        else:
            uncached.append(p)

    # Parallel for >= 4 files
    if parallel and len(uncached) >= 4:
        import os
        if max_workers is None:
            max_workers = min(os.cpu_count() or 4, len(uncached), 8)
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            for path_str, res in ex.map(_extract_one_for_pool, [str(p) for p in uncached]):
                _save_cached(Path(path_str), res, cache_root)
                results.append(res)
    else:
        for p in uncached:
            res = extract_file(p)
            _save_cached(p, res, cache_root)
            results.append(res)

    # Pass 2
    results = _resolve_cross_file_calls(results)
    return results


__all__ = ["extract", "extract_file", "_extract_generic", "_extract_python_rationale"]
