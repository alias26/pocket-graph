"""File discovery, type classification, corpus health checks."""
from __future__ import annotations
import re
from enum import Enum
from pathlib import Path


class FileType(str, Enum):
    CODE = "code"
    DOCUMENT = "document"
    PAPER = "paper"
    IMAGE = "image"
    VIDEO = "video"


# Code file extensions -- 38 entries (39 incl. blade.php special-case)
CODE_EXTENSIONS = {
    ".py", ".ts", ".js", ".jsx", ".tsx", ".mjs", ".ejs",
    ".go", ".rs", ".java",
    ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
    ".rb", ".swift", ".kt", ".kts", ".cs", ".scala",
    ".php", ".lua", ".toc",
    ".zig", ".ps1", ".ex", ".exs",
    ".m", ".mm", ".jl",
    ".vue", ".svelte", ".dart",
    ".v", ".sv", ".sql", ".r",
}

DOC_EXTENSIONS = {".md", ".mdx", ".txt", ".rst", ".html", ".yaml", ".yml"}
PAPER_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
OFFICE_EXTENSIONS = {".docx", ".xlsx"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
                    ".mp3", ".wav", ".m4a", ".ogg"}

CORPUS_WARN_THRESHOLD = 50_000
CORPUS_UPPER_THRESHOLD = 500_000
FILE_COUNT_UPPER = 200

# Sensitive file patterns -- skip silently
_SENSITIVE_PATTERNS = [
    re.compile(r"(^|[\\/])\.(env|envrc)(\.|$)", re.IGNORECASE),
    re.compile(r"\.(pem|key|p12|pfx|cert|crt|der|p8)$", re.IGNORECASE),
    re.compile(r"(credential|secret|passwd|password|token|private_key)", re.IGNORECASE),
    re.compile(r"(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$"),
    re.compile(r"(\.netrc|\.pgpass|\.htpasswd)$", re.IGNORECASE),
    re.compile(r"(aws_credentials|gcloud_credentials|service.account)", re.IGNORECASE),
]

# Heuristic: a .md/.txt that reads like an academic paper
_PAPER_SIGNALS = [
    re.compile(r"\barxiv\b", re.IGNORECASE),
    re.compile(r"\bdoi\s*:", re.IGNORECASE),
    re.compile(r"\babstract\b", re.IGNORECASE),
    re.compile(r"\bproceedings\b", re.IGNORECASE),
    re.compile(r"\bjournal\b", re.IGNORECASE),
    re.compile(r"\bpreprint\b", re.IGNORECASE),
    re.compile(r"\\cite\{"),
    re.compile(r"\[\d+\]"),
    re.compile(r"\d{4}\.\d{4,5}"),
    re.compile(r"\bwe propose\b", re.IGNORECASE),
]
# Strong signals — if any of these match, it's definitely a paper
# (no need to count smaller signals).
_PAPER_STRONG_SIGNALS = [
    re.compile(r"^arxiv_id\s*:", re.MULTILINE | re.IGNORECASE),  # frontmatter from fetch
    re.compile(r"^type\s*:\s*paper\b", re.MULTILINE | re.IGNORECASE),  # explicit
    re.compile(r"arxiv\.org/(abs|pdf)/\d{4}\.\d{4,5}", re.IGNORECASE),  # arxiv URL
    re.compile(r"^paper_authors\s*:", re.MULTILINE | re.IGNORECASE),  # frontmatter
]
_PAPER_SIGNAL_THRESHOLD = 3

_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                "dist", "build", "target", ".pytest_cache", ".mypy_cache",
                "pocket-graph-out", "graph-out",
                "_meta"}  # vault meta files (decisions/index/log/schema) are system-level, not content


def _is_sensitive(path: Path) -> bool:
    return any(p.search(path.name) for p in _SENSITIVE_PATTERNS)


def _looks_like_paper(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:3000]
        # Strong signals — any one is enough
        if any(p.search(text) for p in _PAPER_STRONG_SIGNALS):
            return True
        # Otherwise count weak signals
        hits = sum(1 for p in _PAPER_SIGNALS if p.search(text))
        return hits >= _PAPER_SIGNAL_THRESHOLD
    except Exception:
        return False


def classify(path: Path) -> FileType | None:
    """Return FileType for a path, or None if it should be ignored."""
    ext = path.suffix.lower()
    if _is_sensitive(path):
        return None
    if ext in CODE_EXTENSIONS:
        return FileType.CODE
    if ext in PAPER_EXTENSIONS:
        return FileType.PAPER
    if ext in IMAGE_EXTENSIONS:
        return FileType.IMAGE
    if ext in VIDEO_EXTENSIONS:
        return FileType.VIDEO
    if ext in DOC_EXTENSIONS:
        # promote to PAPER if heuristic triggers
        if ext in {".md", ".mdx", ".txt", ".rst"} and _looks_like_paper(path):
            return FileType.PAPER
        return FileType.DOCUMENT
    if ext in OFFICE_EXTENSIONS:
        # Treated as documents (extracted via python-docx / openpyxl)
        return FileType.DOCUMENT
    return None


def _load_ignore_file(root: Path) -> tuple[list[str], list[str]]:
    """Load .pocketignore (gitignore syntax).

    Returns (positive_globs, negation_globs):
      positive: paths that match get excluded
      negation: paths starting with '!' that override exclusion
    """
    ignore_path = root / ".pocketignore"
    if not ignore_path.exists():
        return [], []
    positive: list[str] = []
    negation: list[str] = []
    for line in ignore_path.read_text(encoding="utf-8", errors="ignore").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            negation.append(line[1:].strip())
        else:
            positive.append(line)
    return positive, negation


def _matches_glob(rel_path: str, glob: str) -> bool:
    """fnmatch with .gitignore-style directory semantics.

    'foo/' matches anything inside foo/.
    'foo/**' matches anything inside foo/ recursively.
    'foo' matches a file named foo OR anything inside a directory foo/.
    """
    import fnmatch
    rel = rel_path.replace("\\", "/")
    g = glob.rstrip("/")
    # Trailing slash -> directory match
    if glob.endswith("/"):
        return rel == g or rel.startswith(g + "/")
    # Plain pattern
    if fnmatch.fnmatch(rel, g):
        return True
    # Directory-style match (any part of the path equals the glob)
    parts = rel.split("/")
    if g in parts:
        return True
    # ** support
    if "**" in g and fnmatch.fnmatch(rel, g):
        return True
    return False


def collect_files(root: Path, ignore_globs: list[str] | None = None,
                   load_ignore_file: bool = True) -> list[Path]:
    """Walk root and return paths classified by classify().

    Skips:
    - hidden directories (.git, etc.)
    - common build/cache directories
    - sensitive files (api keys, secrets)
    - files matching ignore globs from .pocketignore (when load_ignore_file=True)
      and any extras in the `ignore_globs` argument.
      Negation patterns (lines starting with `!`) override exclusion.
    """
    files: list[Path] = []
    extra_globs = list(ignore_globs or [])

    file_globs, negation_globs = ([], [])
    if load_ignore_file:
        file_globs, negation_globs = _load_ignore_file(root)
    all_positive = extra_globs + file_globs

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # Hidden directory parts (anywhere in the path)
        if any(part.startswith(".") and len(part) > 1 for part in p.parts[:-1]):
            continue
        if any(part in _IGNORE_DIRS for part in p.parts):
            continue

        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        # Negation overrides exclusion (gitignore semantics)
        if any(_matches_glob(rel, g) for g in negation_globs):
            pass  # override: include
        elif any(_matches_glob(rel, g) for g in all_positive):
            continue

        if classify(p) is None:
            continue
        files.append(p)

    files.sort()
    return files


def corpus_stats(files: list[Path]) -> dict:
    """Return per-FileType counts and rough word count."""
    by_type: dict[str, int] = {}
    word_count = 0
    for f in files:
        ft = classify(f)
        if ft is None:
            continue
        by_type[ft.value] = by_type.get(ft.value, 0) + 1
        try:
            if ft in (FileType.CODE, FileType.DOCUMENT):
                word_count += len(f.read_text(encoding="utf-8", errors="ignore").split())
        except Exception:
            pass
    return {
        "file_count": len(files),
        "by_type": by_type,
        "word_count": word_count,
    }


__all__ = [
    "FileType",
    "CODE_EXTENSIONS", "DOC_EXTENSIONS", "PAPER_EXTENSIONS",
    "IMAGE_EXTENSIONS", "OFFICE_EXTENSIONS", "VIDEO_EXTENSIONS",
    "classify", "collect_files", "corpus_stats",
]
