"""Filesystem watcher: auto-rebuild the graph on changes.

Watches a corpus directory and automatically re-runs incremental update
on file changes. Uses a debounce window so rapid saves don't trigger a
rebuild on every keystroke.

Behaviour:
- Code file changes (.py, .js, .ts, .java, ...): full incremental update
  (tree-sitter only, no LLM).
- Non-code file changes (.pdf, .md, .txt, ...): writes a `needs_update`
  flag in graph-out/ and notifies the user that they should run
  `pocket-graph update` (or, in Claude Code, /pocket-graph update) to
  apply LLM-backed semantic re-extraction.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

from .languages import EXTENSION_REGISTRY


_CODE_EXTENSIONS = set(EXTENSION_REGISTRY.keys())
_NON_CODE_EXTENSIONS = {".pdf", ".md", ".txt", ".rst", ".html", ".tex",
                          ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp",
                          ".gif", ".csv", ".json", ".yaml", ".yml"}
_WATCHED_EXTENSIONS = _CODE_EXTENSIONS | _NON_CODE_EXTENSIONS


def _has_non_code(changed_paths: list[Path]) -> bool:
    """True if any changed file has an extension outside the code set."""
    return any(p.suffix.lower() not in _CODE_EXTENSIONS for p in changed_paths)


def _notify_only(watch_path: Path, out_dir: Path) -> None:
    """Write a flag file and print a notification.

    Used for non-code file changes that need LLM re-extraction.
    """
    flag = out_dir / "needs_update"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1", encoding="utf-8")
    print(f"\n[pocket-graph watch] Non-code file changes detected in {watch_path}.")
    print(f"[pocket-graph watch] Run `pocket-graph update` to re-extract")
    print(f"[pocket-graph watch] (or `/pocket-graph` in Claude Code for LLM-backed semantic update).")
    print(f"[pocket-graph watch] Flag written to {flag}")


def _rebuild_code(watch_path: Path, out_dir: Path) -> bool:
    """Run incremental update for code-only changes.

    Returns True on success, False on error.
    """
    from .sync import update_graph
    try:
        update_graph(watch_path, out_dir=out_dir)
        # Clear stale needs_update flag on a successful code-only rebuild
        flag = out_dir / "needs_update"
        if flag.exists():
            flag.unlink()
        return True
    except Exception as exc:
        print(f"[pocket-graph watch] Rebuild failed: {exc}")
        return False


def watch(watch_path: Path, out_dir: Path, debounce: float = 3.0) -> None:
    """Watch ``watch_path`` recursively and rebuild the graph on changes.

    Code-only changes trigger an immediate AST re-extraction (no LLM).
    Non-code changes write a ``needs_update`` flag and notify the user.

    Press Ctrl+C to stop.
    """
    try:
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver
        from watchdog.events import FileSystemEventHandler
    except ImportError as e:
        raise ImportError(
            "watchdog not installed. Run: pip install watchdog"
        ) from e

    watch_root = watch_path.resolve()
    out_root = out_dir.resolve()

    last_trigger: float = 0.0
    pending: bool = False
    changed: set[Path] = set()

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            nonlocal last_trigger, pending
            if event.is_directory:
                return
            path = Path(event.src_path)
            # Ignore unsupported extensions
            if path.suffix.lower() not in _WATCHED_EXTENSIONS:
                return
            # Ignore hidden files / folders
            if any(part.startswith(".") for part in path.parts):
                return
            # Ignore our own output dirs
            if "graph-out" in path.parts:
                return
            # Ignore LLM Wiki internals
            if "LLM Wiki" in path.parts and "_meta" in path.parts:
                return
            last_trigger = time.monotonic()
            pending = True
            changed.add(path)

    handler = Handler()
    # Polling on macOS -- FSEvents misses rapid saves in some editors
    observer = PollingObserver() if sys.platform == "darwin" else Observer()
    observer.schedule(handler, str(watch_root), recursive=True)
    observer.start()

    print(f"[pocket-graph watch] Watching {watch_root} - press Ctrl+C to stop")
    print(f"[pocket-graph watch] Code changes auto-rebuild (no LLM).")
    print(f"[pocket-graph watch] Non-code changes write a flag and notify.")
    print(f"[pocket-graph watch] Debounce: {debounce}s")

    try:
        while True:
            time.sleep(0.5)
            if pending and (time.monotonic() - last_trigger) >= debounce:
                pending = False
                batch = list(changed)
                changed.clear()
                print(f"\n[pocket-graph watch] {len(batch)} file(s) changed:")
                for p in batch[:10]:
                    print(f"  {p}")
                if len(batch) > 10:
                    print(f"  ... and {len(batch) - 10} more")
                if _has_non_code(batch):
                    _notify_only(watch_root, out_root)
                else:
                    _rebuild_code(watch_root, out_root)
    except KeyboardInterrupt:
        print("\n[pocket-graph watch] Stopped.")
    finally:
        observer.stop()
        observer.join()


__all__ = ["watch"]
