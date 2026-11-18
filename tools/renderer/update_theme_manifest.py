"""记录发行预设的校验值，用于识别程序目录中被用户修改的主题。"""
import hashlib
import json
from pathlib import Path
import sys


def main():
    styles = Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer/styles"
    sys.path.insert(0, str(styles.parent))
    from csmodernmap.style import load_style
    from csmodernmap.theme_library import content_digest
    manifest = {path.name: {"sha256": hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                            "content": content_digest(load_style(str(path)).data)}
                for path in sorted(styles.glob("*.yaml")) if path.stem != "metro_map"}
    (styles / "presets.sha256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    baseline = {path.stem: load_style(str(path)).data for path in sorted(styles.glob("*.yaml"))
                if path.stem != "metro_map"}
    (styles / "presets.current.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
