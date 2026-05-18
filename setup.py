"""Setup wrapper to run a post-install hook that copies the Claude Code skill.

pyproject.toml drives the actual package metadata. This file exists only to
add a post-install hook that copies SKILL.md into ~/.claude/skills/pocket-graph/
during `pip install` / `pipx install`.

The hook is best-effort - if it fails (no HOME, permission denied, etc.) the
install still succeeds and the skill gets installed lazily on first command.
"""
from setuptools import setup
from setuptools.command.install import install
from setuptools.command.develop import develop


def _install_skill():
    """Copy bundled SKILL.md to ~/.claude/skills/pocket-graph/SKILL.md.

    Silent failure - never crash the install. The CLI's first-run hook
    will handle the case where this didn't run.
    """
    try:
        import os
        import platform
        import shutil
        from pathlib import Path

        # OS-aware destination
        if platform.system() == "Windows":
            base = os.environ.get("USERPROFILE") or str(Path.home())
            dest_dir = Path(base) / ".claude" / "skills" / "pocket-graph"
        else:
            dest_dir = Path.home() / ".claude" / "skills" / "pocket-graph"

        # Locate the bundled SKILL.md (within this source tree)
        here = Path(__file__).resolve().parent
        src = here / "pocket_graph" / "skill_assets" / "SKILL.md"
        if not src.exists():
            return  # nothing to install

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "SKILL.md"
        shutil.copy2(src, dest)
        print(f"[pocket-graph] installed Claude Code skill -> {dest}")
    except Exception as e:
        # Don't fail the package install on hook errors
        print(f"[pocket-graph] skill install skipped ({e}); "
              "will be installed on first CLI run")


def _setup_pythonutf8():
    """On Windows, automatically set PYTHONUTF8=1 and notify the user.

    Why automatic: pip install can't reliably prompt for input - stdin is
    non-interactive in pip's wheel-build environment. We tried prompting;
    input() always fell through to non-tty fallback. So we just set it.

    Why this is OK: PYTHONUTF8=1 is Python's standard UTF-8 mode (PEP 540).
    cp949 is Microsoft's pre-Unicode codepage. We're aligning Python with
    the rest of modern software, not introducing non-standard behavior.
    User can undo with `setx PYTHONUTF8 ""` if they prefer cp949.

    All output is pure ASCII so this can never crash on cp949 (the very
    problem we're solving). Whole function wrapped in broad except -
    install must never crash from a quality-of-life setting.
    """
    try:
        import platform
        if platform.system() != "Windows":
            return  # only Windows hits cp949

        import os
        if os.environ.get("PYTHONUTF8") == "1":
            return  # already set, no action needed

        import subprocess
        result = subprocess.run(
            ["setx", "PYTHONUTF8", "1"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            print(
                "\n"
                "[pocket-graph] Set PYTHONUTF8=1 (Python's standard UTF-8 mode).\n"
                "  Reason: Korean Windows defaults Python's stdout to cp949,\n"
                "  causing UnicodeEncodeError when reading paper PDFs.\n"
                "  Effect: takes effect in shells you open from now on.\n"
                "  Undo: setx PYTHONUTF8 \"\""
            )
        else:
            err = result.stderr.strip() if result.stderr else "unknown"
            print(
                f"\n[pocket-graph] Could not set PYTHONUTF8 ({err}).\n"
                "  To enable manually:  setx PYTHONUTF8 1"
            )
    except Exception:
        # Never let a quality-of-life setting break the install.
        pass


class PostInstall(install):
    def run(self):
        super().run()
        _install_skill()
        _setup_pythonutf8()


class PostDevelop(develop):
    def run(self):
        super().run()
        _install_skill()
        _setup_pythonutf8()


setup(cmdclass={"install": PostInstall, "develop": PostDevelop})
