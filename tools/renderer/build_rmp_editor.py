"""构建供 WinUI 主窗口地铁工作区使用的固定版本 RMP 静态资源。"""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RMP = ROOT / "dev" / "third_party" / "rmp"


def main():
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise SystemExit("构建 RMP 编辑器需要 Node.js/npm；运行应用不需要 Node.js。")
    if not (RMP / "node_modules" / ".package-lock.json").is_file():
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=RMP, check=True)
    subprocess.run([npm, "run", "build:embed"], cwd=RMP, check=True)
    output = RMP / "dist-embed"
    dependencies = []
    lock = json.loads((RMP / "package-lock.json").read_text(encoding="utf-8"))
    for name, package in lock["packages"].items():
        if not name or package.get("dev"):
            continue
        dependencies.append({"path": name, "version": package.get("version"), "license": package.get("license")})
        directory = RMP / name
        for source in directory.glob("*"):
            if source.is_file() and source.name.lower().startswith(("license", "licence", "notice", "copying")):
                target = output / "licenses" / name.replace("/", "_")
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target / source.name)
    (output / "licenses" / "dependencies.json").write_text(
        json.dumps(dependencies, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    shutil.copy2(RMP / "CSLMM-UPSTREAM.md", output / "licenses" / "CSLMM-UPSTREAM.md")
    (output / "build-info.json").write_text(json.dumps({
        "upstreamCommit": "401ddafeb869255d10a5b51e3fae1acb34322d57",
        "lockfileSha256": hashlib.sha256((RMP / "package-lock.json").read_bytes()).hexdigest(),
        "saveVersion": 77, "bridgeProtocolVersion": 1,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"RMP embed assets: {sum(path.stat().st_size for path in output.rglob('*') if path.is_file()):,} bytes")


if __name__ == "__main__":
    main()
