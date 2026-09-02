#!/usr/bin/env python3
"""Download and deploy wrapper-lite-qemu for the current platform.

Fetches the latest wrapper-lite-qemu build from the wrapper repo's
build-lite workflow via nightly.link and unpacks it into ``qemu/`` so
``[localInstance]`` works out of the box.

Usage:
    python qemu/deploy.py
    python qemu/deploy.py --url https://nightly.link/.../xxx.zip
    python qemu/deploy.py --force
"""

import argparse
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST = REPO_ROOT / "qemu"

BASE_URL = ("https://nightly.link/WorldObservationLog/wrapper/workflows/"
            "build-lite/lite/wrapper-lite-qemu-{platform}.zip")


def detect_platform() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "intel64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine
    if os.name == "nt":
        return "windows-" + arch
    if sys.platform == "darwin":
        return "macos-" + arch
    if sys.platform.startswith("linux"):
        return "linux-" + arch
    raise RuntimeError("Unsupported platform: %s %s" % (sys.platform, machine))


def download(url: str, dest: Path) -> None:
    print("Downloading %s" % url)
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    print("  %d bytes received" % len(data))
    dest.write_bytes(data)


def _find_launcher(tmp: Path):
    exe_name = "wrapper-lite-qemu.exe" if os.name == "nt" else "wrapper-lite-qemu"
    return next((p for p in tmp.rglob(exe_name)), None)


def deploy(zip_path: Path, force: bool = False) -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)

        # Unwrap one level of nested zip if needed.
        if _find_launcher(tmp) is None:
            inner = list(tmp.rglob("*.zip"))
            if inner:
                with zipfile.ZipFile(inner[0]) as z:
                    z.extractall(tmp)

        launcher = _find_launcher(tmp)
        if launcher is None:
            raise RuntimeError("wrapper-lite-qemu(.exe) not found in artifact")

        target_launcher = DEST / launcher.name
        if target_launcher.exists() and not force:
            print("%s already exists. Use --force to overwrite." % target_launcher)
            return
        shutil.copy2(launcher, target_launcher)
        print("Installed launcher -> %s" % target_launcher)

        qemu_dirs = [p for p in tmp.rglob("qemu") if p.is_dir()]
        target_qemu = DEST / "qemu"
        for qd in qemu_dirs:
            if target_qemu.exists() and not force:
                print("%s already exists. Use --force to overwrite." % target_qemu)
                return
            if target_qemu.exists():
                shutil.rmtree(target_qemu)
            shutil.copytree(qd, target_qemu)
            print("Installed qemu assets -> %s" % target_qemu)
            break
        else:
            print("No qemu/ asset directory found; launcher installed only.")


def patch_config() -> None:
    cfg_path = REPO_ROOT / "config.toml"
    if not cfg_path.exists():
        print("config.toml not found; skipping config patch.")
        return
    exe = "wrapper-lite-qemu.exe" if os.name == "nt" else "wrapper-lite-qemu"
    launcher_rel = "qemu/" + exe
    text = cfg_path.read_text(encoding="utf-8")

    if "launcherBin" in text:
        text = re.sub(r'launcherBin\s*=\s*"[^"]*"',
                      'launcherBin = "' + launcher_rel + '"', text)
    else:
        # Insert into the existing [localInstance] section after "enable = ...".
        lines = text.splitlines(keepends=True)
        out = []
        inside = False
        inserted = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                inside = stripped == "[localInstance]"
            out.append(line)
            if inside and not inserted and stripped.startswith("enable"):
                out.append("launcherBin = \"" + launcher_rel + "\"\n")
                inserted = True
        if inserted:
            text = "".join(out)
        else:
            text = text.rstrip() + "\n[localInstance]\nlauncherBin = \"" + launcher_rel + "\"\n"
    cfg_path.write_text(text, encoding="utf-8")
    print("config.toml: localInstance.launcherBin -> " + launcher_rel)


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy wrapper-lite-qemu locally")
    ap.add_argument("--url", help="explicit artifact URL")
    ap.add_argument("--platform", help="artifact platform token (e.g. windows-x86_64)")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    plat = args.platform or detect_platform()
    url = args.url or BASE_URL.format(platform=plat)
    print("Target platform: " + plat)

    DEST.mkdir(parents=True, exist_ok=True)
    zip_path = DEST / ("wrapper-lite-qemu-" + plat + ".zip")
    try:
        download(url, zip_path)
        deploy(zip_path, force=args.force)
        patch_config()
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass
    print("\nDone. Launch via qemu/login.py or the app's localInstance mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
