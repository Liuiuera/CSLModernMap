"""Export reviewable UI messages without changing application translations.

C# and TypeScript inputs come from their official syntax parsers. Python uses
ast; resource keys remain identities so edits can be matched after sorting.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
ENGLISH = re.compile(r"[A-Za-z]{3,}\s+[A-Za-z]{2,}")
LOG_CALL = re.compile(r"(?:logger|logging|log|_log|print|console)\b", re.I)
ENGLISH_UI_MODULES = {"guir_backend", "winui_engine", "winui_workflows", "object_inspector",
    "theme_metadata", "theme_contract", "poi_profiles", "output_profiles", "ui_contracts",
    "settings_store", "web_export_v2", "preview_scene", "field_validation", "range_model"}
COMMON_ACTIONS = {"取消": {"Cancel"}, "关闭": {"Close"}, "保存": {"Save"}, "应用": {"Apply"},
    "确定": {"OK", "Confirm"}, "继续编辑": {"Continue editing", "Keep editing"},
    "放弃": {"Discard"}, "全选": {"Select all"}, "取消全选": {"Deselect all"},
    "浏览…": {"Browse…", "Browse..."}, "恢复默认": {"Restore defaults", "Reset to defaults"}}
SCREEN_NAMES = {
    "ThemeEditorDialog": "主题编辑器", "ExportWindow": "输出窗口",
    "MapRangeWindow": "地图范围", "FilteredMultiSelector": "多选器",
    "AppearanceControls": "颜色与字体控件", "ObjectInspectorPanel": "对象属性",
    "PoiMappingDialog": "POI 映射", "PoiProfileEditorPanel": "地点显示设置",
    "MainWindow.MapSettings": "地图显示与外观", "MainWindow.Onboarding": "首次使用引导",
    "MainWindow.PreviewSettings": "预览设置", "MainWindow.PublicTransitSettings": "交通设置",
    "MainWindow.TransitRibbon": "交通与地铁功能区", "MainWindow.Selectors": "线路与区域选择",
    "MainWindow.Workflows": "语言、诊断与创作工具", "MainWindow.Overview": "地图概况",
    "MainWindow.Status": "状态与进度", "MainWindow.File": "文件与导出流程",
    "MainWindow.Layers": "图层与外观", "MainWindow.Commands": "菜单与命令",
    "MainWindow": "主窗口", "UiLanguage": "通用控件与反查翻译",
    "ContextRibbonToggle": "功能区开关", "MapTaskState": "任务状态与空白地图",
    "RibbonIcons": "功能区与图层名称", "EngineClient": "引擎启动与通信反馈",
    "App": "应用启动", "EditorWindow": "编辑窗口",
    "object_inspector": "对象属性", "theme_metadata": "主题字段说明",
    "ribbon_schema": "功能区与外观字段", "poi_profiles": "地点显示设置",
    "svg_export": "矢量网页导出", "terrain_model": "地形设置",
    "guir_backend": "地图、导出与设置反馈", "winui_engine": "引擎界面反馈",
    "winui_workflows": "语言、诊断与创作工具", "web_export_v2": "导出网页",
}
DOMAIN_NAMES = {
    "style": "主题字段与样式", "cli": "命令行（用途待确认）", "lang": "语言菜单",
    "metro": "地铁示意图", "range": "地图范围", "poi": "地点显示设置",
    "style_editor": "主题编辑器", "poi_editor": "POI 映射", "setup_wizard": "首次使用引导",
    "export": "输出窗口与导出", "progress": "进度反馈", "error": "错误反馈",
    "about": "关于", "theme": "主题选择", "layer": "图层设置",
    "field": "外观字段", "menu": "菜单", "preview": "预览设置",
    "transit": "公共交通", "common": "通用控件", "font": "字体设置",
}


def screen(path: str) -> str:
    stem = Path(path).stem.replace(".xaml", "")
    for prefix in sorted(SCREEN_NAMES, key=len, reverse=True):
        if stem.startswith(prefix):
            return SCREEN_NAMES[prefix]
    if "/viewer/" in path:
        return "网页工具栏与反馈" if stem == "main" else "网页预览与渲染反馈"
    return "引擎用户反馈（用途待确认）"


def catalog_screen(key: str) -> str:
    parts = key.split(".")
    domain = parts[1] if parts[0] == "gui" and len(parts) > 1 else parts[0]
    return DOMAIN_NAMES.get(domain, "共享文案 · " + domain)


def subsystem(path: str) -> str:
    if "/winui/" in path: return "WinUI"
    if "/viewer/" in path: return "网页"
    return "共享资源 / Python 引擎"


def python_syntax(root: Path):
    messages, references, files, diagnostics = [], [], [], []
    sources = sorted((root / "dev/CSLModernMapRenderer/csmodernmap").rglob("*.py"))
    sources.append(root / "dev/CSLModernMapRenderer/main.py")
    for path in sources:
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        files.append(relative)
        try: tree = ast.parse(source)
        except SyntaxError as error:
            diagnostics.append(f"{relative}:{error.lineno}: {error.msg}")
            continue
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        def ancestors(node):
            while node in parents:
                node = parents[node]
                yield node
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr)): continue
            if isinstance(node, ast.Constant) and not isinstance(node.value, str): continue
            lineage = list(ancestors(node))
            if any(isinstance(item, ast.JoinedStr) for item in lineage): continue
            if isinstance(node, ast.Constant) and isinstance(parents.get(node), ast.Expr):
                expression = parents[node]
                owner = parents.get(expression)
                if isinstance(owner, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and owner.body[0] is expression:
                    continue
            if isinstance(node, ast.JoinedStr):
                text = "".join(part.value if isinstance(part, ast.Constant) else "{" + ast.unparse(part.value) + ("!" + chr(part.conversion) if part.conversion != -1 else "") + (":" + "".join(p.value for p in part.format_spec.values if isinstance(p, ast.Constant)) if part.format_spec else "") + "}" for part in node.values)
            else: text = node.value
            parent = parents.get(node)
            english = ""
            if isinstance(parent, (ast.Tuple, ast.List)) and len(parent.elts) == 2 and parent.elts[0] is node and isinstance(parent.elts[1], ast.Constant) and isinstance(parent.elts[1].value, str):
                english = parent.elts[1].value
            if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node and len(parent.args) >= 2 and isinstance(parent.args[1], ast.Constant) and isinstance(parent.args[1].value, str) and ast.unparse(parent.func).split(".")[-1] in {"text", "local", "Label"}:
                english = parent.args[1].value
            member = next((item.name for item in lineage if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))), "")
            statement = next((item for item in lineage if isinstance(item, ast.stmt)), node)
            calls = [ast.unparse(item.func) for item in lineage if isinstance(item, ast.Call)]
            references.append({"path": relative, "line": node.lineno, "key": text,
                               "expression": (ast.get_source_segment(source, statement) or "")[:900],
                               "dynamic": isinstance(node, ast.JoinedStr) and any(call.endswith(("tr", "tr_for")) for call in calls)})
            assignment = next((item for item in lineage if isinstance(item, (ast.Assign, ast.AnnAssign))), None)
            assigned = ast.unparse(assignment.targets[0] if isinstance(assignment, ast.Assign) else assignment.target) if assignment is not None else ""
            messages.append({"path": relative, "line": node.lineno, "text": text, "english": english,
                "member": member, "context": (ast.get_source_segment(source, statement) or "")[:900],
                "assigned": assigned, "log": any(LOG_CALL.search(call) for call in calls), "interpolated": isinstance(node, ast.JoinedStr)})
    return {"messages": messages, "references": references, "files": files, "diagnostics": diagnostics}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--syntax-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    csharp = json.loads((args.syntax_dir / "csharp.json").read_text(encoding="utf-8"))
    typescript = json.loads((args.syntax_dir / "typescript.json").read_text(encoding="utf-8"))
    python = python_syntax(root)
    embedded_js = []
    class DisplayHtml(HTMLParser):
        def __init__(self, item):
            super().__init__(); self.item = item; self.hidden = 0
        def handle_starttag(self, tag, attributes):
            if tag in {"style", "script"}: self.hidden += 1
            for attribute, value in attributes:
                if attribute in {"title", "aria-label", "placeholder", "alt"} and value and CJK.search(value): self.emit(value, attribute)
        def handle_endtag(self, tag):
            if tag in {"style", "script"}: self.hidden = max(0, self.hidden - 1)
        def handle_data(self, data):
            if not self.hidden and CJK.search(data): self.emit(data.strip(), "HTML 文字")
        def emit(self, text, role):
            python["messages"].append({**self.item, "line": self.item["line"] + self.getpos()[0] - 1,
                "text": text, "member": self.item["member"] + " · " + role, "context": role + ": " + text, "embedded": True})
    template_items = list(python["messages"])
    for item in template_items:
        if len(item["text"]) > 400 and "<html" in item["text"]:
            DisplayHtml(item).feed(item["text"])
            item["template"] = True
        elif len(item["text"]) > 400 and re.search(r"function\s*\(|document\.get", item["text"]):
            embedded_js.append(item); item["template"] = True
    if embedded_js:
        embedded_input = args.syntax_dir / "embedded-js-input.json"
        embedded_output = args.syntax_dir / "embedded-js.json"
        embedded_input.write_text(json.dumps(embedded_js, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["node", str(root / "tools/renderer/i18n_extract_ts.cjs"), str(root), str(embedded_output), str(embedded_input)], check=True)
        embedded = json.loads(embedded_output.read_text(encoding="utf-8"))
        typescript["messages"].extend(embedded["messages"])
        typescript["diagnostics"].extend(embedded["diagnostics"])
    diagnostics = csharp["diagnostics"] + typescript["diagnostics"] + python["diagnostics"]
    if diagnostics: raise ValueError("Syntax extraction failed: " + "\n".join(diagnostics))
    zhpath = "dev/CSLModernMapRenderer/resources/i18n/zh-CN.json"
    zh = json.loads((root / zhpath).read_text(encoding="utf-8"))
    en = json.loads((root / zhpath.replace("zh-CN", "en")).read_text(encoding="utf-8"))
    messages, locations, excluded = {}, [], []
    aliases = {}
    by_zh = defaultdict(list)
    def add(identity, original, english, component, page, purpose, note=""):
        original_identity = identity
        if original in COMMON_ACTIONS and (not english or english in COMMON_ACTIONS[original]):
            identity = "common-action:" + original
            component, page, purpose = "共享资源 / Python 引擎", "通用操作", "通用按钮"
        if identity not in messages:
            prefix = {"WinUI": "WIN", "网页": "WEB"}.get(component, "ENG")
            messages[identity] = {"id": prefix + "-" + hashlib.sha256(identity.encode()).hexdigest()[:12],
                "identity": identity, "component": component, "screen": page, "purpose": purpose,
                "original": original, "reviewed": original, "english": english, "status": "待校对", "notes": note,
                "context": "", "usage": ""}
        aliases[original_identity] = messages[identity]
        return messages[identity]
    def locate(message, path, line, key, mechanism, snippet=""):
        locations.append({"id": message["id"], "key": key, "path": path, "line": line,
            "mechanism": mechanism, "context": snippet[:900].replace("\r\n", "\n").replace("\r", "\n")})
    json_lines = (root / zhpath).read_text(encoding="utf-8").splitlines()
    for key, value in zh.items():
        note = "用途待确认：尚未发现本次范围内的明确引用。"
        row = add("catalog:" + key, value, en.get(key, ""), "共享资源 / Python 引擎", catalog_screen(key), key, note)
        by_zh[value].append(row)
        line = next((i + 1 for i, text in enumerate(json_lines) if json.dumps(key, ensure_ascii=False) + ":" in text), 1)
        locate(row, zhpath, line, key, "已有 JSON 翻译键", value)
    for reference in python["references"]:
        key = reference["key"]
        keys = [key] if "catalog:" + key in aliases else []
        if reference["dynamic"]:
            prefix = key.split("{", 1)[0]
            keys = [key for key in zh if prefix and key.startswith(prefix)]
        for key in keys:
            row = aliases["catalog:" + key]
            row["notes"] = "动态键族引用，需结合具体字段核对。" if reference["dynamic"] else ""
            locate(row, reference["path"], reference["line"], key, "动态翻译键族" if reference["dynamic"] else "翻译键引用或元数据", reference["expression"])
    literals = csharp["messages"] + typescript["messages"] + python["messages"]
    english_pairs = {item.get("english", "") for item in literals if CJK.search(item["text"]) and item.get("english") and not CJK.search(item["english"])}
    dictionary = [item for item in csharp["messages"] if item["path"].endswith("UiLanguage.cs") and CJK.search(item["text"])]
    by_inline = defaultdict(list)
    for item in literals:
        text = item["text"]
        if item["path"].endswith("UiLanguage.cs"): continue
        reason = ""
        if item.get("template"): reason = "代码或 HTML 模板；仅提取其中可见文字"
        elif item["path"].endswith("ui_palette.py"): reason = "Qt 专用样式文字，不在本次界面范围"
        elif item.get("assigned") in {"_LEGACY_THEME_NAMES", "_LEGACY_RANGE_PRESETS"}: reason = "兼容解析旧配置名称，不是当前界面文案"
        elif text in {"Microsoft YaHei", "Segoe MDL2 Assets", "Segoe UI", "宋体", "微软雅黑", "黑体", "SimSun", "Arial"}: reason = "字体标识，不能作为译文更改"
        elif "/viewer/" in item["path"] and (re.search(r"fontFamily\s*=|\.className\s*=|precision highp|uniform mat4|attribute vec", item["context"]) or "sans-serif" in text): reason = "CSS / 字体 / shader 标识"
        elif item["path"].endswith("/main.py"): reason = "CLI 帮助或启动诊断，非本次界面文案"
        elif text == "process environment": reason = "许可来源标识，非界面文案"
        elif text.startswith("^") or "|high density" in text: reason = "游戏对象分类正则，不能翻译"
        elif text.endswith((".app", ".exe")) or re.search(r"(?:worker|thread)\.Name\s*=", item["context"], re.I): reason = "程序路径或线程标识，不能翻译"
        if reason:
            excluded.append({**item, "reason": reason}); continue
        if item["log"]:
            if CJK.search(text): excluded.append({**item, "reason": "日志调用，不作为界面译文"})
            continue
        if text in zh: continue
        if not CJK.search(text):
            if item["path"].endswith(".py") and Path(item["path"]).stem not in ENGLISH_UI_MODULES: continue
            if text in english_pairs or not ENGLISH.search(text) or len(text) > 700 or any(token in text for token in ("<", "{\"", "#version", "\\", "http:", "https:", "typeof ", "function ", "--")):
                continue
            # English-only UI candidates remain visible instead of inventing Chinese.
        if text.strip() in {"，", "。", "：", "；", "（", "）", "→", "中文", "English"} and len(text.strip()) <= 1: continue
        component = subsystem(item["path"])
        page = screen(item["path"])
        english = item.get("english", "")
        if CJK.search(english): english = ""
        matches = list({row["id"]: row for row in by_zh.get(text, [])}.values())
        if english:
            narrowed = [row for row in matches if row["english"] == english]
            if narrowed: matches = narrowed
        # A unique resource match is safe; ambiguous same-text resources stay separate.
        row = matches[0] if len(matches) == 1 else None
        if row:
            row["notes"] = "界面同时存在相同文字的写死字符串；后续改为资源引用。"
        else:
            original = text if CJK.search(text) else ""
            role = "标题" if re.search(r"\bTitle\s*=", item["context"]) else "工具提示" if "SetToolTip" in item["context"] or ".title" in item["context"] else "无障碍名称" if "AutomationProperties" in item["context"] else "界面文案"
            identity = "inline:" + component + ":" + page + ":" + role + ":" + text
            purpose = role + (" · " + item["member"] if item["member"] else "")
            mechanism = "中英硬编码分支" if english else "写死字符串 / 动态模板"
            note = "原文是英文硬编码；请补中文，暂不自动翻译。" if not original else ""
            if "用途待确认" in page or not original: note = (note + " 用途待确认：需沿用户反馈入口确认。").strip()
            if item["interpolated"]: note = (note + " 花括号中保留原表达式；只校对固定文字。").strip()
            row = add(identity, original, english or (text if not original else ""), component, page, purpose, note)
        mechanism = "中文原文反查或写死字符串" if matches else "中英硬编码分支" if english else "写死字符串 / 动态模板"
        locate(row, item["path"], item["line"], "", mechanism, item["context"])
        by_inline[text].append(row)
    for item in dictionary:
        matches = list({row["id"]: row for row in by_zh.get(item["text"], []) + by_inline.get(item["text"], [])}.values())
        if not matches:
            row = add("reverse:" + item["text"], item["text"], item.get("english", ""), "WinUI", "通用控件与反查翻译", "反查字典", "用途待确认：仅找到反查字典，没有明确界面入口。")
            matches = [row]
        for row in matches:
            if not row["english"]: row["english"] = item.get("english", "")
            locate(row, item["path"], item["line"], "", "UiLanguage 中文原文反查", item["context"])
    # XAML display attributes are parsed as XML, with source lines retained for review.
    import xml.etree.ElementTree as ET
    xaml_files = []
    for path in sorted((root / "dev/CSLModernMapRenderer/winui").glob("*.xaml")):
        relative = path.relative_to(root).as_posix(); xaml_files.append(relative)
        source = path.read_text(encoding="utf-8-sig"); lines = source.splitlines(); seen_attributes = Counter()
        for element in ET.fromstring(source).iter():
            for attribute, text in element.attrib.items():
                if attribute not in {"Text", "Content", "Header", "Title", "PlaceholderText", "ToolTipService.ToolTip", "AutomationProperties.Name"} or text.startswith("{") or not CJK.search(text): continue
                occurrences = [(i + 1) for i, value in enumerate(lines) if attribute + '="' + html.escape(text, quote=True) + '"' in value or attribute + '="' + text + '"' in value]
                occurrence = seen_attributes[(attribute, text)]; seen_attributes[(attribute, text)] += 1
                if not occurrences: raise ValueError(f"Cannot locate XAML attribute: {relative}: {attribute}={text}")
                line = occurrences[min(occurrence, len(occurrences)-1)]
                matches = list({row["id"]: row for row in by_zh.get(text, [])}.values())
                row = matches[0] if len(matches) == 1 else add("xaml:" + relative + ":" + attribute + ":" + text, text, "", "WinUI", screen(relative), attribute)
                locate(row, relative, line, "", "XAML 写死属性", attribute + "=" + text)
    unique_locations = list({json.dumps(value, sort_keys=True, ensure_ascii=False): value for value in locations}.values())
    grouped = defaultdict(list)
    for location in unique_locations: grouped[location["id"]].append(location)
    for row in messages.values():
        refs = grouped[row["id"]]
        row["usage"] = "、".join(sorted({subsystem(ref["path"]) for ref in refs}))
        tokens = list(dict.fromkeys(re.findall(r"(?<!\{)\{[^{}]+\}(?!\})", row["original"] or row["english"])))
        notes = []
        if tokens: notes.append("占位符保持不变：" + "、".join(tokens))
        if "\n" in row["original"]: notes.append("包含换行，请保留必要分行")
        units = [unit for unit in ("px", "km", "%", "°", "米", "公里", "像素") if unit in row["original"]]
        if units: notes.append("单位 / 符号：" + "、".join(units))
        notes.append("使用位置 " + str(len(refs)) + " 处；详见使用位置表")
        row["context"] = "\n".join(notes)
    order = {"WinUI": 0, "网页": 1, "共享资源 / Python 引擎": 2}
    screen_order = {name: i for i, name in enumerate(("主窗口", "菜单与命令", "文件与导出流程", "输出窗口", "地图范围", "地图概况", "图层与外观", "地图显示与外观", "交通与地铁功能区", "交通设置", "线路与区域选择", "对象属性", "主题编辑器", "POI 映射", "地点显示设置", "预览设置", "首次使用引导", "语言、诊断与创作工具", "状态与进度"))}
    rows = sorted(messages.values(), key=lambda row: (order[row["component"]], screen_order.get(row["screen"], 99), row["screen"], row["purpose"], row["id"]))
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)): raise ValueError("Message identity collision")
    summary = {"baselineCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "messages": len(rows), "locations": len(unique_locations), "byComponent": dict(Counter(row["component"] for row in rows)),
        "englishOnly": sum(not row["original"] for row in rows), "scopePending": sum("用途待确认" in row["notes"] for row in rows),
        "catalogKeys": len(zh), "syntaxErrors": diagnostics,
        "files": sorted(set(csharp["files"] + typescript["files"] + python["files"] + xaml_files)),
        "excludedStrings": len(excluded), "exclusionReasons": dict(Counter(item["reason"] for item in excluded))}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "inventory.json").write_text(json.dumps({"summary": summary, "messages": rows, "locations": unique_locations, "excluded": excluded}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "files"}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
