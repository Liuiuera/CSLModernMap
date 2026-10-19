# -*- coding: utf-8 -*-
"""i18n 人工校对编辑器（本地网页版，零第三方依赖）。

启动本地 HTTP 服务并自动打开浏览器：
  1. 加载 resources/i18n/{zh-CN,en}.json，自动标注问题
     （缺键 / 占位符不一致 / 空文案 / 疑似未翻译 / 死键 / 代码引用缺失）
  2. 网页中双栏编辑 zh-CN 与 en 文案，实时显示占位符差异
  3. 保存直接写回 JSON 文件（保持 2 空格缩进 + CRLF + 尾换行）

用法：
    python i18n_editor.py [--port 8765] [--no-browser]

按 Ctrl+C 退出服务。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")))
import i18n_proofread as proof  # 复用加载与校对逻辑

ROOT = (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")
I18N_DIR = ROOT / "resources" / "i18n"
HTML_PATH = Path(__file__).resolve().parent / "i18n_editor.html"
LANGS = ("zh-CN", "en")
DEFAULT_PORT = 8765


# --------------------------------------------------------------------------- 数据

def load_all() -> dict:
    """读取两个语言文件，返回 {lang: {key: text}}；解析失败抛出异常。"""
    tables = {}
    for lang in LANGS:
        path = I18N_DIR / f"{lang}.json"
        if not path.exists():
            raise RuntimeError(f"缺少翻译文件: {path.name}")
        data, _ = proof.load_table(path)
        if not data:
            raise RuntimeError(f"{path.name} 加载失败（文件为空或解析错误）")
        tables[lang] = data
    return tables


def compute_issues(tables: dict) -> dict:
    """计算各键问题标注与全局统计。

    返回 {issues: {key: [标志...]}, stats: {...}}；标志见 _FLAGS。
    """
    zh, en = tables["zh-CN"], tables["en"]
    issues: dict[str, list] = {}
    stats = {f: 0 for f in _FLAGS}

    def add(key, flag):
        if flag not in issues.setdefault(key, []):
            issues[key].append(flag)
        stats[flag] += 1

    for key in zh.keys() - en.keys():
        add(key, "missing-en")
    for key in en.keys() - zh.keys():
        add(key, "missing-zh")
    for key in zh.keys() & en.keys():
        if proof.placeholder_set(zh[key]) != proof.placeholder_set(en[key]):
            add(key, "ph")
        for lang in LANGS:
            if not tables[lang][key].strip():
                add(key, f"empty-{lang}")
        text = zh[key]
        if (not key.startswith("lang.") and text.strip()
                and len(proof.strip_placeholders(text).strip()) > 3
                and zh[key] == en[key]):
            add(key, "suspect")

    # 代码引用（静态键缺失 + 死键；动态键族自动排除）
    try:
        static_keys, dyn_prefixes = proof.collect_code_keys()
    except Exception:  # noqa: BLE001 —— 扫描失败不阻断编辑
        static_keys, dyn_prefixes = [], []
    used = set(static_keys)
    dyn_covered = {k for k in zh.keys() | en.keys()
                   if any(k.startswith(p) for p in dyn_prefixes)}
    for key in sorted(used - zh.keys() - en.keys()):
        add(key, "code-missing")
    for key in (zh.keys() & en.keys()) - used - dyn_covered:
        add(key, "dead")
    return {"issues": issues, "stats": stats}


# 标志 -> (级别, 中文名)
_FLAGS = {
    "missing-en":  ("error", "zh 有/en 缺"),
    "missing-zh":  ("error", "en 有/zh 缺"),
    "ph":          ("error", "占位符不一致"),
    "empty-zh-CN": ("warn",  "zh 空文案"),
    "empty-en":    ("warn",  "en 空文案"),
    "suspect":     ("info",  "疑似未翻译"),
    "dead":        ("info",  "死键"),
    "code-missing": ("error", "代码引用缺失"),
}


def save_table(lang: str, data: dict) -> None:
    """写回 JSON，按行重建实现最小 diff：未变键保留原始行文本（含原始缩进）。

    文件格式约定：每键一行（多行值以 \\n 转义在单行内）、首行 { 末行 }。
    """
    if lang not in LANGS:
        raise ValueError(f"未知语言: {lang}")
    path = I18N_DIR / f"{lang}.json"
    # 二进制读取：文本模式会把 CRLF 转成 LF，导致行尾检测失效
    raw_bytes = path.read_bytes()
    raw = raw_bytes.decode("utf-8-sig")
    old = json.loads(raw)
    if old == data:
        return  # 无变化不写文件
    newline = "\r\n" if b"\r\n" in raw_bytes else "\n"
    lines = raw.splitlines()
    if len(lines) < 2 or lines[0].strip() != "{":
        # 非标准单行结构：整体重写
        text = json.dumps(data, ensure_ascii=False, indent=2)
        text = text.replace("\n", newline) + newline
    else:
        # lines[0]="{" lines[-1]="}"，中间每行一个键
        row_map = {}  # key -> 原始行文本
        for line in lines[1:-1]:
            m = re.match(r'^(\s*)"([^"]*)": ', line)
            if m:
                row_map[m.group(2)] = (m.group(1), line)
        body = []
        for key in old.keys():
            if key not in data:
                continue  # 键被删除
            row = row_map.get(key)
            if data[key] == old[key] and row:
                body.append(row[1])  # 未变键保留原始行（含缩进与逗号）
            else:
                indent = row[0] if row else "  "
                body.append(indent + json.dumps(key, ensure_ascii=False) + ": "
                            + json.dumps(data[key], ensure_ascii=False))
        for key in data.keys():
            if key not in old:  # 新增键追加在末尾
                body.append("  " + json.dumps(key, ensure_ascii=False) + ": "
                            + json.dumps(data[key], ensure_ascii=False))
        # 统一修复逗号：非最后一行必须以逗号结尾，最后一行不得有逗号
        for i in range(len(body) - 1):
            if not body[i].rstrip().endswith(","):
                body[i] += ","
        last = body[-1].rstrip()
        body[-1] = last[:-1] if last.endswith(",") else last
        text = lines[0] + newline + newline.join(body) + newline + lines[-1] + newline
    tmp = path.with_suffix(".json.tmp")
    # newline="" 禁止文本模式把 \n 二次转换为 \r\n（否则出现 \r\r\n）
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)  # 原子替换


# --------------------------------------------------------------------------- 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "i18n-editor/1.0"

    def log_message(self, fmt, *args):  # 精简日志
        print(f"  [http] {self.address_string()} {fmt % args}")

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if not HTML_PATH.exists():
                self._json({"error": f"缺少前端文件 {HTML_PATH.name}"}, 500)
                return
            body = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/data":
            try:
                tables = load_all()
                payload = compute_issues(tables)
                payload["langs"] = tables
                payload["flags"] = {k: v for k, v in _FLAGS.items()}
                self._json(payload)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/save":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            save_table(payload["lang"], payload["data"])
            self._json({"ok": True, "saved": payload["lang"]})
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 400)


def main() -> int:
    port = DEFAULT_PORT
    open_browser = "--no-browser" not in sys.argv
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 50)
    print(" i18n 人工校对编辑器")
    print(f" 地址:  http://127.0.0.1:{port}")
    print(" 数据:  " + str(I18N_DIR))
    print(" 按 Ctrl+C 退出")
    print("=" * 50)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
