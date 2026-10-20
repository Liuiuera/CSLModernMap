"""Zip a complete assembled Windows application and write its checksum."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "dev/CSLModernMapRenderer"
APP = RENDERER / "dist/winui/CSLModernMapRenderer"


def validate_application(app: Path) -> None:
    required = (
        "CSLModernMapRenderer.exe", "CSLModernMapRenderer.pri",
        "Assets/Rmp/embed.html", "Assets/Rmp/build-info.json",
        "Assets/Rmp/licenses/dependencies.json", "Assets/Rmp/licenses/RMP-LICENSE.txt",
        "Assets/Licenses/LICENSE", "Assets/Licenses/LICENSING.md",
        "Assets/Licenses/THIRD_PARTY_NOTICES.md", "Assets/Licenses/RibbonSpace-MIT.txt",
        "engine/CSLModernMapEngine.exe", "engine/viewer/dist/index.html",
        "engine/viewer/dist/THIRD_PARTY_LICENSES.txt", "engine/licenses/LICENSE",
        "engine/licenses/LICENSING.md", "engine/licenses/THIRD_PARTY_NOTICES.md",
    )
    missing = [item for item in required if not (app / item).is_file()]
    if missing:
        raise ValueError("Application files are missing: " + ", ".join(missing))
    spec = importlib.util.spec_from_file_location("cmm_assemble", RENDERER / "winui/assemble.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.complete_engine(app / "engine"):
        raise ValueError("The engine resources or translations are incomplete")


def package(app: Path, output: Path) -> Path:
    if output == app or output.is_relative_to(app):
        raise ValueError("Choose an output directory outside the application folder")
    validate_application(app)
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+[A-Za-z]?(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("Invalid version.json version")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"CSLModernMap-{version}-windows-x64.zip"
    if archive.exists():
        raise FileExistsError(archive)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(app.rglob("*")):
            if file.is_file():
                bundle.write(file, Path("CSLModernMapRenderer") / file.relative_to(app))
        bundle.writestr("CSLModernMapRenderer/BUILD-INFO.json", json.dumps(
            {"version": version, "commit": commit, "platform": "windows-x64"}, indent=2) + "\n")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, default=APP)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    package(args.app_dir.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
