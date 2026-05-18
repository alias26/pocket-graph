"""Per-language tree-sitter configurations.

Each LanguageConfig captures the tree-sitter node types relevant to AST-based
fact extraction: function/class/method definitions, name fields, body fields,
import statements. Importable languages are registered automatically.
"""
from __future__ import annotations
import importlib
from dataclasses import dataclass, field
from tree_sitter import Language, Parser


@dataclass
class LanguageConfig:
    name: str                                   # canonical name e.g. "python"
    ts_module: str                              # importable tree-sitter package
    extensions: tuple[str, ...]                 # file suffixes
    function_node_types: tuple[str, ...]        # AST node types for funcs
    class_node_types: tuple[str, ...] = ()      # AST node types for classes
    method_node_types: tuple[str, ...] = ()     # AST node types for methods
    import_node_types: tuple[str, ...] = ()     # AST nodes signalling imports
    call_node_types: tuple[str, ...] = ("call_expression", "call",)
    name_field: str = "name"
    body_field: str = "body"
    decorator_node_type: str | None = None
    _entry_attr: str | None = None              # override: `language_php` etc.
    _language: Language | None = field(default=None, repr=False)
    _parser: Parser | None = field(default=None, repr=False)

    def parser(self) -> Parser:
        if self._parser is None:
            mod = importlib.import_module(self.ts_module)
            # Per-language entry-point name. Most packages expose `language()`,
            # a few expose alternates (typescript: language_typescript / _tsx;
            # php: language_php / _php_only).
            entry_attr = getattr(self, "_entry_attr", None)
            if entry_attr and hasattr(mod, entry_attr):
                lang_capsule = getattr(mod, entry_attr)()
            elif hasattr(mod, "language"):
                lang_capsule = mod.language()
            elif hasattr(mod, "language_typescript"):
                lang_capsule = mod.language_typescript()
            elif hasattr(mod, "language_php"):
                lang_capsule = mod.language_php()
            else:
                raise ImportError(f"{self.ts_module} has no language() function")
            self._language = Language(lang_capsule)
            self._parser = Parser(self._language)
        return self._parser


# ==== language definitions ====
# Function / class / import node types from tree-sitter grammars.

_CONFIGS = [
    LanguageConfig(
        name="python", ts_module="tree_sitter_python",
        extensions=(".py",),
        function_node_types=("function_definition",),
        class_node_types=("class_definition",),
        method_node_types=("function_definition",),
        import_node_types=("import_statement", "import_from_statement"),
        decorator_node_type="decorator",
    ),
    LanguageConfig(
        name="javascript", ts_module="tree_sitter_javascript",
        extensions=(".js", ".jsx", ".mjs", ".ejs"),
        function_node_types=("function_declaration", "arrow_function",
                             "function_expression", "method_definition"),
        class_node_types=("class_declaration",),
        method_node_types=("method_definition",),
        import_node_types=("import_statement", "lexical_declaration"),
    ),
    LanguageConfig(
        name="typescript", ts_module="tree_sitter_typescript",
        extensions=(".ts", ".tsx"),
        function_node_types=("function_declaration", "arrow_function",
                             "function_expression", "method_definition"),
        class_node_types=("class_declaration", "interface_declaration",
                          "type_alias_declaration"),
        method_node_types=("method_definition", "method_signature"),
        import_node_types=("import_statement", "import_alias"),
        _entry_attr="language_typescript",
    ),
    LanguageConfig(
        name="go", ts_module="tree_sitter_go",
        extensions=(".go",),
        function_node_types=("function_declaration", "method_declaration"),
        class_node_types=("type_declaration",),
        method_node_types=("method_declaration",),
        import_node_types=("import_declaration", "import_spec"),
    ),
    LanguageConfig(
        name="rust", ts_module="tree_sitter_rust",
        extensions=(".rs",),
        function_node_types=("function_item",),
        class_node_types=("struct_item", "enum_item", "trait_item", "impl_item"),
        import_node_types=("use_declaration",),
    ),
    LanguageConfig(
        name="java", ts_module="tree_sitter_java",
        extensions=(".java",),
        function_node_types=("method_declaration", "constructor_declaration"),
        class_node_types=("class_declaration", "interface_declaration",
                          "enum_declaration"),
        method_node_types=("method_declaration",),
        call_node_types=("method_invocation", "object_creation_expression"),
        import_node_types=("import_declaration",),
        decorator_node_type="annotation",
    ),
    LanguageConfig(
        name="c", ts_module="tree_sitter_c",
        extensions=(".c", ".h"),
        function_node_types=("function_definition",),
        class_node_types=("struct_specifier", "union_specifier", "enum_specifier"),
        import_node_types=("preproc_include",),
    ),
    LanguageConfig(
        name="cpp", ts_module="tree_sitter_cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp"),
        function_node_types=("function_definition",),
        class_node_types=("class_specifier", "struct_specifier",
                          "union_specifier", "enum_specifier", "namespace_definition"),
        import_node_types=("preproc_include",),
    ),
    LanguageConfig(
        name="ruby", ts_module="tree_sitter_ruby",
        extensions=(".rb",),
        function_node_types=("method", "singleton_method"),
        class_node_types=("class", "module"),
        import_node_types=("call",),  # require/require_relative are calls in Ruby
    ),
    LanguageConfig(
        name="csharp", ts_module="tree_sitter_c_sharp",
        extensions=(".cs",),
        function_node_types=("method_declaration", "constructor_declaration",
                             "destructor_declaration"),
        class_node_types=("class_declaration", "interface_declaration",
                          "struct_declaration", "enum_declaration"),
        method_node_types=("method_declaration",),
        call_node_types=("invocation_expression", "object_creation_expression"),
        import_node_types=("using_directive",),
        decorator_node_type="attribute",
    ),
    LanguageConfig(
        name="kotlin", ts_module="tree_sitter_kotlin",
        extensions=(".kt", ".kts"),
        function_node_types=("function_declaration",),
        class_node_types=("class_declaration", "object_declaration"),
        import_node_types=("import_header",),
    ),
    LanguageConfig(
        name="scala", ts_module="tree_sitter_scala",
        extensions=(".scala",),
        function_node_types=("function_definition", "function_declaration"),
        class_node_types=("class_definition", "object_definition", "trait_definition"),
        import_node_types=("import_declaration",),
    ),
    LanguageConfig(
        name="php", ts_module="tree_sitter_php",
        extensions=(".php",),
        function_node_types=("function_definition", "method_declaration"),
        class_node_types=("class_declaration", "interface_declaration", "trait_declaration"),
        method_node_types=("method_declaration",),
        import_node_types=("namespace_use_declaration",),
        _entry_attr="language_php",
    ),
    LanguageConfig(
        name="swift", ts_module="tree_sitter_swift",
        extensions=(".swift",),
        function_node_types=("function_declaration",),
        class_node_types=("class_declaration", "protocol_declaration"),
        import_node_types=("import_declaration",),
    ),
    LanguageConfig(
        name="lua", ts_module="tree_sitter_lua",
        extensions=(".lua", ".toc"),
        function_node_types=("function_declaration", "function_definition"),
        import_node_types=("function_call",),
    ),
    LanguageConfig(
        name="zig", ts_module="tree_sitter_zig",
        extensions=(".zig",),
        function_node_types=("FnProto",),
        class_node_types=("ContainerDecl",),
        import_node_types=("BUILTINIDENTIFIER",),
    ),
    LanguageConfig(
        name="julia", ts_module="tree_sitter_julia",
        extensions=(".jl",),
        function_node_types=("function_definition", "short_function_definition",
                             "assignment", "macro_definition"),
        class_node_types=("struct_definition", "abstract_definition"),
        import_node_types=("import_statement", "using_statement"),
    ),
    LanguageConfig(
        name="elixir", ts_module="tree_sitter_elixir",
        extensions=(".ex", ".exs"),
        function_node_types=("call",),
        class_node_types=("call",),  # defmodule is a call in Elixir
        import_node_types=("call",),
    ),
    LanguageConfig(
        name="powershell", ts_module="tree_sitter_powershell",
        extensions=(".ps1",),
        function_node_types=("function_statement",),
        class_node_types=("class_statement",),
        import_node_types=("using_statement",),
    ),
    LanguageConfig(
        name="objc", ts_module="tree_sitter_objc",
        extensions=(".m", ".mm"),
        function_node_types=("function_definition", "method_definition"),
        class_node_types=("class_interface", "class_implementation",
                          "category_interface", "protocol_declaration"),
        import_node_types=("preproc_import", "preproc_include"),
    ),
    LanguageConfig(
        name="verilog", ts_module="tree_sitter_verilog",
        extensions=(".v", ".sv"),
        function_node_types=("function_declaration", "task_declaration"),
        class_node_types=("module_declaration", "class_declaration"),
        import_node_types=("import_declaration",),
    ),
    LanguageConfig(
        name="svelte", ts_module="tree_sitter_svelte",
        extensions=(".svelte",),
        function_node_types=("function_declaration",),
        class_node_types=("class_declaration",),
        import_node_types=("import_statement",),
    ),
    LanguageConfig(
        name="sql", ts_module="tree_sitter_sql",
        extensions=(".sql",),
        function_node_types=("create_function_statement",),
        class_node_types=("create_table_statement", "create_view_statement"),
        import_node_types=(),
    ),
    LanguageConfig(
        name="bash", ts_module="tree_sitter_bash",
        extensions=(".sh", ".bash"),
        function_node_types=("function_definition",),
        import_node_types=("command",),  # source/. are commands
    ),
]


def _load_registry() -> dict[str, LanguageConfig]:
    """Try to load each LanguageConfig; skip ones whose tree-sitter package
    isn't installed. Build extension -> config map."""
    registry: dict[str, LanguageConfig] = {}
    for cfg in _CONFIGS:
        try:
            cfg.parser()  # triggers import + Language load
        except ImportError:
            continue
        except Exception as e:
            # version mismatch, missing language() function, etc.
            print(f"[pocket_graph] skipping {cfg.name}: {type(e).__name__}: {e}")
            continue
        for ext in cfg.extensions:
            registry[ext] = cfg
    return registry


# Public registry -- extension -> LanguageConfig
EXTENSION_REGISTRY: dict[str, LanguageConfig] = _load_registry()


def get_language(path) -> LanguageConfig | None:
    """Return the LanguageConfig for a file path, or None if unsupported."""
    from pathlib import Path
    if not isinstance(path, Path):
        path = Path(path)
    return EXTENSION_REGISTRY.get(path.suffix.lower())


def supported_extensions() -> set[str]:
    return set(EXTENSION_REGISTRY.keys())


def supported_languages() -> list[str]:
    return sorted({cfg.name for cfg in EXTENSION_REGISTRY.values()})


__all__ = ["LanguageConfig", "EXTENSION_REGISTRY",
           "get_language", "supported_extensions", "supported_languages"]
