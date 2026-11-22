"""Build ``aws/build/function.zip`` for the Lambda (no ``zip`` binary needed).

The archive holds the three modules of ``aws/lambda``, the two pure-Python
modules the main project shares with the function (``shared.backup_codec`` and
``shared.subscriptions``), and ``msgpack`` built for the *Lambda* platform
(Linux), not for the machine running this script. boto3 is already part of the
Lambda runtime, so it is not packaged.

    python aws/scripts/package.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "aws" / "lambda"
SHARED = ROOT / "shared" / "src" / "shared"
BUILD = ROOT / "aws" / "build"
PKG = BUILD / "package"
ZIP = BUILD / "function.zip"

PYTHON_VERSION = "3.12"  # keep equal to --runtime in deploy.ps1 / deploy.sh
SHARED_MODULES = ["__init__.py", "backup_codec.py", "subscriptions.py"]
DEPENDENCIES = ["msgpack"]


def install_dependencies() -> None:
    uv = shutil.which("uv")
    if uv:
        cmd = [
            uv, "pip", "install", "--quiet", "--target", str(PKG),
            "--python-platform", "x86_64-manylinux_2_28",
            "--python-version", PYTHON_VERSION, *DEPENDENCIES,
        ]  # fmt: skip
    else:
        cmd = [
            sys.executable, "-m", "pip", "install", "--quiet", "--target", str(PKG),
            "--platform", "manylinux2014_x86_64", "--implementation", "cp",
            "--python-version", PYTHON_VERSION, "--only-binary=:all:", *DEPENDENCIES,
        ]  # fmt: skip
    subprocess.run(cmd, check=True)


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    PKG.mkdir(parents=True)

    for name in ("handler.py", "service.py", "stores.py"):
        shutil.copy2(SRC / name, PKG / name)
    (PKG / "shared").mkdir()
    for name in SHARED_MODULES:
        shutil.copy2(SHARED / name, PKG / "shared" / name)
    install_dependencies()

    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PKG.rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.name != ".lock"
            ):
                archive.write(path, path.relative_to(PKG).as_posix())
    size_kb = ZIP.stat().st_size / 1024
    print(f"built {ZIP.relative_to(ROOT)} ({size_kb:.0f} KiB)")


if __name__ == "__main__":
    main()
