#!/usr/bin/env python3
"""Harness Framework — One-Click Install Script.

Installs Harness Framework skills to Claude Code, Codex, or both.

Installation targets:
  --claude           .claude/skills/  (project)  or  ~/.claude/skills/  (user)
  --codex            .agents/skills/  (project)  or  ~/.agents/skills/  (user)
"""

import argparse
import shutil
import sys
from pathlib import Path

# ---- constants ----------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_SKILLS = SCRIPT_DIR / "skills"

MINIMAL_SKILLS = {
    "stage-bridge",
    "task-executor",
    "file-kv",
    "harness-sync",
}

PLATFORM_CLAUDE = "claude"
PLATFORM_CODEX = "codex"

COPY_INDENT = "  "

# ---- platform target definitions ----------------------------------------

PLATFORM_CONFIG = {
    PLATFORM_CLAUDE: {
        "label": "Claude Code",
        "project_skills": ".claude/skills",
        "user_skills": Path.home() / ".claude" / "skills",
        "invoke_hint": "/stage-bridge (after reload)",
    },
    PLATFORM_CODEX: {
        "label": "Codex",
        "project_skills": ".agents/skills",
        "user_skills": Path.home() / ".agents" / "skills",
        "invoke_hint": "stage-bridge (after restart)",
    },
}


# ---- helpers ------------------------------------------------------------

def error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"{COPY_INDENT}{msg}")


def success(msg: str) -> None:
    print(f"SUCCESS: {msg}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="One-click Harness Framework installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "EXAMPLES:\n"
            "  python install.py                                    # current dir, Claude\n"
            "  python install.py --codex                            # current dir, Codex\n"
            "  python install.py --claude --codex                   # current dir, both\n"
            "  python install.py --target /path/to/project          # specific project\n"
            "  python install.py --user                             # user-wide, Claude\n"
            "  python install.py --user --claude --codex --minimal  # user-wide, both\n"
            "  python install.py --target /tmp/test --dry-run       # preview\n"
            "\n"
            "MINIMAL SKILLS (4):\n"
            "  stage-bridge      Agent lifecycle (register, heartbeat, claim, complete)\n"
            "  task-executor     Per-type task execution (backend/test/design/review/deploy)\n"
            "  file-kv           Local-file KV CLI for zero-network mode\n"
            "  harness-sync      Sync dependencies.json to Consul, init workflow\n"
            "\n"
            "FULL: all 9 skill directories under skills/"
        ),
    )
    parser.add_argument(
        "--user",
        action="store_true",
        help="Install globally to user home (default: install to current directory)",
    )
    parser.add_argument(
        "--target",
        type=str,
        default="",
        metavar="PATH",
        help="Install into specific project directory (default: current dir)",
    )
    parser.add_argument(
        "--claude",
        action="store_true",
        dest="claude",
        help="Install for Claude Code (default)",
    )
    parser.add_argument(
        "--codex",
        action="store_true",
        dest="codex",
        help="Install for Codex",
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Install only 4 essential skills (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would happen; no changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    return parser


# ---- argument parsing ---------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.user and args.target:
        error("--user and --target are mutually exclusive.")

    if args.target:
        target = Path(args.target).resolve()
        if not target.exists():
            error(f"--target path '{args.target}' does not exist.")
        if not target.is_dir():
            error(f"--target '{args.target}' is not a directory.")
        args.target = target

    if not SOURCE_SKILLS.is_dir():
        error(f"Source skills/ not found at {SOURCE_SKILLS}")

    # Default: install to current directory when neither --user nor --target given
    if not args.user and not args.target:
        args.target = Path.cwd()

    # Default: --claude only when neither specified
    if not args.claude and not args.codex:
        args.claude = True

    return args


# ---- skill discovery ----------------------------------------------------

def get_skill_list(args: argparse.Namespace) -> list[str]:
    """Return sorted list of skill directory names to install."""
    all_skills = sorted(
        d.name
        for d in SOURCE_SKILLS.iterdir()
        if d.is_dir()
    )

    if args.minimal:
        return sorted(s for s in all_skills if s in MINIMAL_SKILLS)

    return all_skills


# ---- platform resolution ------------------------------------------------

def get_platform_roots(args: argparse.Namespace) -> dict[str, Path]:
    """Return {platform: root_dir} dict for selected platforms."""
    roots: dict[str, Path] = {}

    active_platforms = []
    if args.claude:
        active_platforms.append(PLATFORM_CLAUDE)
    if args.codex:
        active_platforms.append(PLATFORM_CODEX)

    for plat in active_platforms:
        cfg = PLATFORM_CONFIG[plat]
        if args.user:
            root = cfg["user_skills"]
        else:
            assert args.target is not None
            root = args.target / cfg["project_skills"]
        roots[plat] = root

    return roots


# ---- confirmation -------------------------------------------------------

def confirm_or_exit(
    args: argparse.Namespace,
    skill_count: int,
    platform_roots: dict[str, Path],
) -> None:
    if args.force or args.dry_run:
        return

    print()
    for plat, root in platform_roots.items():
        label = PLATFORM_CONFIG[plat]["label"]
        print(f"  [{label}] {skill_count} skills -> {root}/")
    print()

    confirm = input("  Proceed? [y/N] ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Cancelled.")
        sys.exit(0)


# ---- install skills -----------------------------------------------------

def install_skills(
    args: argparse.Namespace,
    skill_list: list[str],
    platform_roots: dict[str, Path],
    dry_run: bool,
) -> None:
    for plat, dest_root in platform_roots.items():
        label = PLATFORM_CONFIG[plat]["label"]

        if not dry_run:
            dest_root.mkdir(parents=True, exist_ok=True)
        else:
            print(f"[DRY-RUN] mkdir -p \"{dest_root}\"")
            continue

        count = 0
        for skill_name in skill_list:
            src = SOURCE_SKILLS / skill_name
            dst = dest_root / skill_name

            info(f"[{label}] {skill_name} ...")
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, symlinks=False)
            count += 1

        success(f"[{label}] Installed {count} skill(s) to {dest_root}")

    if dry_run:
        for plat, dest_root in platform_roots.items():
            label = PLATFORM_CONFIG[plat]["label"]
            for skill_name in skill_list:
                info(f"[{label}] {skill_name} -> {dest_root / skill_name}/")


# ---- summary ------------------------------------------------------------

def print_summary(
    args: argparse.Namespace,
    platform_roots: dict[str, Path],
) -> None:
    if args.dry_run:
        print()
        print("=" * 44)
        print("  DRY-RUN complete (no changes made).")
        print("  Re-run without --dry-run to install.")
        print("=" * 44)
        return

    print()
    print("=" * 44)
    print("  Installation complete")
    print("=" * 44)
    for plat, root in platform_roots.items():
        label = PLATFORM_CONFIG[plat]["label"]
        print(f"  {label:12}  {root}")
    print()
    print("  To start Harness Framework:")
    print(f"    python -m harness_framework.daemon --local")
    print()
    print("  To start using framework skills:")
    for plat in platform_roots:
        label = PLATFORM_CONFIG[plat]["label"]
        hint = PLATFORM_CONFIG[plat]["invoke_hint"]
        print(f"    {label:12}  {hint}")
    print()
    print("  Tip: re-run this script anytime to update to the latest skills.")
    print("=" * 44)


# ---- main ---------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    platform_roots = get_platform_roots(args)
    skill_list = get_skill_list(args)
    dry_run = args.dry_run

    confirm_or_exit(args, len(skill_list), platform_roots)

    print()
    print("=== Installing Harness Framework ===")
    print()

    install_skills(args, skill_list, platform_roots, dry_run)

    print_summary(args, platform_roots)


if __name__ == "__main__":
    main()
