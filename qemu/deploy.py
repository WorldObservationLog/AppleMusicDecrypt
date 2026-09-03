#!/usr/bin/env python3
"""Download and deploy a local QEMU wrapper backend.

Supports:
  manager (default): wrapper-manager-qemu from WorldObservationLog/wrapper-manager v2
                     (guest assets: wrapper-manager-initramfs.cpio.gz + vmlinuz)
  lite:              wrapper-lite-qemu from WorldObservationLog/wrapper
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

LITE_BASE = ("https://nightly.link/WorldObservationLog/wrapper/workflows/"
             "build-lite/lite/wrapper-lite-qemu-{platform}.zip")
MANAGER_LAUNCHER_BASE = ("https://nightly.link/WorldObservationLog/wrapper-manager/workflows/"
                         "build-manager-qemu/v2/wrapper-manager-qemu-{platform}.zip")
MANAGER_GUEST_URL = ("https://nightly.link/WorldObservationLog/wrapper-manager/workflows/"
                     "build-manager-qemu/v2/manager-qemu-guest.zip")


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


def _find_launcher(tmp: Path, backend: str):
    base = ("wrapper-manager-qemu" if backend == "manager" else "wrapper-lite-qemu")
    name = base + (".exe" if os.name == "nt" else "")
    return next((p for p in tmp.rglob(name)), None)


def _unzip_to(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)


def deploy_lite(zip_path: Path, force: bool) -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _unzip_to(zip_path, tmp)
        if _find_launcher(tmp, "lite") is None:
            inner = list(tmp.rglob("*.zip"))
            if inner:
                _unzip_to(inner[0], tmp)
        launcher = _find_launcher(tmp, "lite")
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


def deploy_manager(launcher_zip: Path, guest_zip: Path | None, force: bool) -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    target_dir = DEST / "manager"
    if target_dir.exists() and not force:
        print("%s already exists. Use --force to overwrite." % target_dir)
        return
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _unzip_to(launcher_zip, tmp)
        launcher = _find_launcher(tmp, "manager")
        if launcher is None:
            candidates = list(tmp.rglob("wrapper-manager-qemu*"))
            launcher = candidates[0] if candidates else None
        if launcher is None:
            raise RuntimeError("wrapper-manager-qemu not found in launcher artifact")
        shutil.copy2(launcher, target_dir / launcher.name)
        print("Installed launcher -> %s" % (target_dir / launcher.name))

    if guest_zip is not None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _unzip_to(guest_zip, tmp)
            guest_dir = target_dir / "guest"
            if guest_dir.exists():
                shutil.rmtree(guest_dir)
            guest_dir.mkdir(parents=True, exist_ok=True)
            for item in tmp.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(tmp)
                    if item.name in ("vmlinuz-lite-qemu",
                                     "wrapper-manager-initramfs.cpio.gz",
                                     "data.img"):
                        dest_item = guest_dir / item.name
                    else:
                        dest_item = guest_dir / rel
                    dest_item.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest_item)
                    print("Installed guest asset -> %s" % dest_item)


def patch_config(backend: str) -> None:
    cfg_path = REPO_ROOT / "config.toml"
    if not cfg_path.exists():
        print("config.toml not found; skipping config patch.")
        return
    if os.name == "nt":
        exe = "wrapper-manager-qemu.exe" if backend == "manager" else "wrapper-lite-qemu.exe"
    else:
        exe = "wrapper-manager-qemu" if backend == "manager" else "wrapper-lite-qemu"
    launcher_rel = "qemu/manager/" + exe if backend == "manager" else "qemu/" + exe
    text = cfg_path.read_text(encoding="utf-8")

    # Manager guest defaults to port 8080; lite uses 12340.
    if backend == "manager":
        text = re.sub(r'hostPort\s*=\s*\d+', 'hostPort = 8080', text)
        text = re.sub(r'guestPort\s*=\s*\d+', 'guestPort = 8080', text)
    else:
        text = re.sub(r'hostPort\s*=\s*\d+', 'hostPort = 12340', text)
        text = re.sub(r'guestPort\s*=\s*\d+', 'guestPort = 12340', text)

    if "wrapperType" in text:
        text = re.sub(r'wrapperType\s*=\s*"[^"]*"',
                      'wrapperType = "' + backend + '"', text)
    else:
        lines = text.splitlines(keepends=True)
        out = []
        inserted = False
        for line in lines:
            out.append(line)
            stripped = line.strip()
            if not inserted and stripped == "[localInstance]":
                out.append('wrapperType = "' + backend + '"\n')
                inserted = True
        if inserted:
            text = "".join(out)
        else:
            text = text.rstrip() + '\n[localInstance]\nwrapperType = "' + backend + '"\n'

    if "launcherBin" in text:
        text = re.sub(r'launcherBin\s*=\s*"[^"]*"',
                      'launcherBin = "' + launcher_rel + '"', text)
    else:
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
                out.append('launcherBin = "' + launcher_rel + '"\n')
                inserted = True
        if inserted:
            text = "".join(out)
        else:
            text = text.rstrip() + '\n[localInstance]\nlauncherBin = "' + launcher_rel + '"\n'

    cfg_path.write_text(text, encoding="utf-8")
    print("config.toml: wrapperType=" + backend + " launcherBin=" + launcher_rel)


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy wrapper QEMU backend locally")
    ap.add_argument("--type", choices=["manager", "lite"], default="manager")
    ap.add_argument("--url", help="explicit launcher artifact URL")
    ap.add_argument("--guest-url", help="explicit guest asset URL (manager)")
    ap.add_argument("--platform", help="platform token (e.g. windows-x86_64)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    plat = args.platform or detect_platform()
    backend = args.type
    print("Target backend: %s platform: %s" % (backend, plat))
    DEST.mkdir(parents=True, exist_ok=True)

    if backend == "lite":
        url = args.url or LITE_BASE.format(platform=plat)
        zip_path = DEST / ("wrapper-lite-qemu-" + plat + ".zip")
        try:
            download(url, zip_path)
            deploy_lite(zip_path, force=args.force)
            patch_config("lite")
        finally:
            try:
                zip_path.unlink()
            except OSError:
                pass
    else:
        launcher_url = args.url or MANAGER_LAUNCHER_BASE.format(platform=plat)
        launcher_zip = DEST / ("wrapper-manager-qemu-" + plat + ".zip")
        guest_zip = None
        try:
            download(launcher_url, launcher_zip)
            guest_url = args.guest_url or MANAGER_GUEST_URL
            guest_zip = DEST / "manager-qemu-guest.zip"
            try:
                download(guest_url, guest_zip)
            except Exception as e:
                print("Guest asset download failed (continuing without it): %s" % e)
                guest_zip = None
            deploy_manager(launcher_zip, guest_zip, force=args.force)
            patch_config("manager")
        finally:
            try:
                launcher_zip.unlink()
            except OSError:
                pass
            if guest_zip is not None:
                try:
                    guest_zip.unlink()
                except OSError:
                    pass

    print("\nDone. Launch the app; wrapper-manager accounts are managed with the login/logout commands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
