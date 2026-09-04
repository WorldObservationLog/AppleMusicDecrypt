#!/usr/bin/env python3
"""Migrate an old config.toml to the current config schema.

Preserves every user-set value and all existing comments/order.  New keys
introduced by newer versions are appended to their [section] using default
values from config.example.toml.  The old file is backed up as
config.toml.bak.

Usage:
    python scripts/migrate_config.py [path/to/config.toml]
"""

import shutil
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "config.example.toml"
CURRENT_VERSION = "0.2.0"


def _parse_example():
    """Return {section: {key: default_value}} parsed from example TOML."""
    with open(EXAMPLE, "rb") as f:
        return tomllib.load(f)


def _format_value(value) -> str:
    """Render a Python value as a TOML literal (basic support)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        # Keep double quotes; escape embedded quotes/backslashes minimally.
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{k} = {_format_value(v)}" for k, v in value.items()) + " }"
    return str(value)


def _existing_dict(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def migrate(config_path: Path) -> bool:
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        return False

    old_text = config_path.read_text(encoding="utf-8")
    try:
        old_cfg = _existing_dict(config_path)
    except Exception as e:
        print(f"Could not parse existing config: {e}")
        return False

    old_ver = old_cfg.get("version", "0.0.0")
    print(f"Old config version: {old_ver} -> {CURRENT_VERSION}")

    example = _parse_example()

    # Compute missing keys per section.
    missing = {}
    for section, values in example.items():
        if section == "version":
            continue
        old_section = old_cfg.get(section, {})
        if not isinstance(old_section, dict):
            continue
        for key, default in values.items():
            if key not in old_section:
                # Backward-compatible special cases for old configs.
                if section == "localInstance" and key == "wrapperType":
                    # Old configs predate wrapperType and were lite
                    # deployments.  Keep them working as lite.
                    default = "lite"
                elif section == "localInstance" and key in ("hostPort", "guestPort"):
                    # Old lite configs often omitted host/guest ports and
                    # passed -port through startArgs.  Preserve that port.
                    start_args = old_section.get("startArgs", "")
                    port = None
                    # Look for "-port N" in startArgs (lite style).
                    parts = str(start_args).replace(chr(92) + "n", " ").split()
                    for i, tok in enumerate(parts):
                        if tok == "-port" and i + 1 < len(parts):
                            if parts[i + 1].isdigit():
                                port = int(parts[i + 1])
                            break
                    if port is None:
                        port = 12340
                    default = port
                elif section == "download" and key == "alacFix":
                    default = True
                missing.setdefault(section, []).append((key, default))

    if not missing:
        print("No new config keys are required; only version will be bumped.")

    # Append missing keys to the end of each existing section.
    lines = old_text.splitlines(keepends=True)
    section_end = {}  # section -> line index after which to insert
    current = None
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
        if current is not None:
            section_end[current] = i + 1  # after this line

    inserts = []  # (insert_after_line, text)
    for section, keys in missing.items():
        if section not in section_end:
            # New section not present in old config: append at end.
            pos = len(lines)
            chunk = f"\n[{section}]\n"
        else:
            pos = section_end[section]
            chunk = "\n"
        for key, default in keys:
            chunk += f"{key} = {_format_value(default)}\n"
        inserts.append((pos, chunk))

    # Insert from bottom to top to keep indices valid.
    inserts.sort(key=lambda x: x[0], reverse=True)
    for pos, chunk in inserts:
        lines.insert(pos, chunk)

    # Update version.
    for i, raw in enumerate(lines):
        if raw.strip().startswith("version"):
            lines[i] = f'version = "{CURRENT_VERSION}"\n'
            break
    else:
        lines.append(f'version = "{CURRENT_VERSION}"\n')

    new_text = "".join(lines)

    # Validate.
    try:
        tomllib.loads(new_text)
    except Exception as e:
        print(f"Migration produced invalid TOML: {e}")
        return False

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup)
    config_path.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"Backup written: {backup}")
    print(f"Migrated: {config_path}")
    print("Migration OK: new config parses as TOML.")
    return True


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "config.toml"
    return 0 if migrate(path) else 1


if __name__ == "__main__":
    sys.exit(main())
