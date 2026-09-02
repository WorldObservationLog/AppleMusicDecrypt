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
import io
import os
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

# Allow running from repo root or from qemu/.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEST = REPO_ROOT / "qemu"

# nightly.link URL template for a build-lite artifact.
BASE_URL = "https://nightly.link/WorldObservationLog/wrapper/workflows/build-lite/lite/wrapper-lite-qemu-{platform}.zip"

# Mapping from local runtime to artifact platform token.
def detect_platform() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "intel64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine
    if os.name == "nt":
        return f"windows-{arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    raise RuntimeError(f"Unsupported platform: {sys.platform} {machine}")


def download(url: str, dest: Path) -> None:
    print(f"Downloading {url}")
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    print(f"  {len(data)} bytes received")
    dest.write_bytes(data)


def deploy(zip_path: Path, force: bool = False) -> None:
    """Unpack the artifact into qemu/.

    Expected layout (or nested zip-of-zip): a wrapper-lite-qemu(.exe)
    and a qemu/ asset directory (vmlinuz-lite-qemu,
    lite-initramfs.cpio.gz, data.img, ...).
    """
    DEST.mkdir(parents=True, exist_ok=True)

    # Extract into a temp dir first so we can locate files recursively.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)

        # If the artifact is a zip containing another zip, unwrap once.
        inner_zips = list(tmp.rglob("*.zip"))
        if inner_zips and not any(x.is_file() and x.name in ("wrapper-lite-qemu", "wrapper-lite-qemu.exe") for x in tmp.rglob("*")):
            with zipfile.ZipFile(inner_zips[0]) as z:
                z.extractall(tmp)

        exe_name = "wrapper-lite-qemu.exe" if os.name == "nt" else "wrapper-lite-qemu"
        launcher = next((p for p in tmp.rglob(exe_name)), None)
        if launcher is None:
            raise RuntimeError(f"{exe_name} not found in artifact {zip_path.name}")

        qemu_dirs = [p for p in tmp.rglob("qemu") if p.is_dir()]

        # Install launcher.
        target_launcher = DEST / exe_name
        if target_launcher.exists() and not force:
            print(f"{target_launcher} already exists. Use --force to overwrite.")
            return
        shutil.copy2(launcher, target_launcher)
        print(f"Installed launcher -> {target_launcher}")

        # Install qemu assets.
        for qd in qemu_dirs:
            target_qemu = DEST / "qemu"
            if target_qemu.exists() and not force:
                print(f"{target_qemu} already exists. Use --force to overwrite.")
                return
            if target_qemu.exists():
                shutil.rmtree(target_qemu)
            shutil.copytree(qd, target_qemu)
            print(f"Installed qemu assets -> {target_qemu}")
            break
        else:
            # Some artifacts may ship the assets flat next to the launcher.
            print("No qemu/ asset directory found in artifact; launcher installed only.")


def patch_config(force: bool = False) -> None:
    """Point config.toml's localInstance at the bundled launcher."""
    cfg_path = REPO_ROOT / "config.toml"
    if not cfg_path.exists():
        print("config.toml not found; skipping config patch.")
        return
    exe = "wrapper-lite-qemu.exe" if os.name == "nt" else "wrapper-lite-qemu"
    launcher_rel = f"qemu/{exe}"
    text = cfg_path.read_text(encoding="utf-8")
    if "launcherBin" in text:
        import re
        text = re.sub(r'launcherBin\s*=\s*"[^"]*"',
                      f'launcherBin = "{launcher_rel}"', text)
    else:
        text += f'\n[localInstance]\nlauncherBin = "{launcher_rel}"\n'
    cfg_path.write_text(text, encoding="utf-8")
    print(f"config.toml: localInstance.launcherBin -> {launcher_rel}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy wrapper-lite-qemu locally")
    ap.add_argument("--url", help="explicit artifact URL (default: auto per platform)")
    ap.add_argument("--platform", help="artifact platform token (e.g. windows-x86_64)")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    plat = args.platform or detect_platform()
    url = args.url or BASE_URL.format(platform=plat)
    print(f"Target platform: {plat}")

    DEST.mkdir(parents=True, exist_ok=True)
    zip_path = DEST / f"wrapper-lite-qemu-{plat}.zip"
    try:
        download(url, zip_path)
        deploy(zip_path, force=args.force)
        patch_config(force=args.force)
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass
    print("\nDone. Launch via qemu/login.py or the app's localInstance mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
