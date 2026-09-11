from __future__ import annotations

import sys
from pathlib import Path

PROTO_PACKAGE_DIR = Path(__file__).resolve().parent
SRC_ROOT = PROTO_PACKAGE_DIR.parents[1]
PROTO_FILES = sorted(PROTO_PACKAGE_DIR.glob("*.proto"))


def main() -> int:
    from grpc_tools import protoc

    if not PROTO_FILES:
        print(f"no .proto files found in {PROTO_PACKAGE_DIR}", file=sys.stderr)
        return 1

    args = [
        "protoc",
        f"--proto_path={SRC_ROOT}",
        f"--python_out={SRC_ROOT}",
        f"--grpc_python_out={SRC_ROOT}",
        f"--pyi_out={SRC_ROOT}",
        *[str(path) for path in PROTO_FILES],
    ]

    exit_code = protoc.main(args)
    if exit_code == 0:
        for path in PROTO_FILES:
            print(f"generated stubs for {path.relative_to(SRC_ROOT)}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
