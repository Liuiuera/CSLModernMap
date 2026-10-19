"""Install, type-check and build the central MapLibre Viewer."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")
VIEWER = ROOT / "viewer"


def _npm() -> str:
    executable = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not executable:
        raise RuntimeError("未找到 npm；请先安装 Node.js 24 或兼容版本")
    return executable


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=VIEWER, check=True)


def main() -> int:
    npm = _npm()
    lock = VIEWER / "package-lock.json"
    _run([npm, "ci" if lock.is_file() else "install"])
    _run([npm, "run", "typecheck"])
    _run([npm, "run", "build"])
    output = VIEWER / "dist" / "index.html"
    if not output.is_file():
        raise RuntimeError(f"Viewer 构建未生成 {output}")
    print(f"Viewer ready: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
