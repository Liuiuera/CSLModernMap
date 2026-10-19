# -*- coding: utf-8 -*-
"""i18n 翻译校对工具（零第三方依赖）。

检查 resources/i18n/ 下各语言 JSON 的一致性与代码引用完整性：

  1. JSON 解析         —— 文件能否正常解析、有无重复键
  2. 键集比对          —— zh-CN / en 缺失或多余的键
  3. 占位符比对        —— str.format 占位符（{n} / {name} / {}）不一致
  4. 空值检查          —— 空串 / 纯空白文案
  5. 疑似未翻译        —— zh-CN 与 en 文案完全相同（启发式提示）
  6. 代码引用检查      —— tr("...") 静态键在翻译表中缺失；动态键前缀无对应键族

用法：
    python i18n_proofread.py            # 全量校对
    python i18n_proofread.py --no-code  # 跳过代码引用检查
    python i18n_proofread.py --quiet    # 只输出错误与汇总

退出码：0 = 无错误；1 = 存在错误。
"""
from __future__ import annotations

import ast
import json
import re
from string import Formatter
import sys
from collections import Counter
from pathlib import Path

# 管道/重定向时也以 UTF-8 输出，避免中文乱码（控制台交互不受影响）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")
I18N_DIR = ROOT / "resources" / "i18n"
# 代码引用扫描范围：根目录全部 .py（跳过归档/产物目录）
CODE_SCAN = [ROOT]
_SKIP_DIRS = {"archive", "build", "dist", "cache", "web_cache",
              "__pycache__", ".pytest_cache", ".codex_tmp"}

# 翻译函数名（ast 匹配 Name 调用）
_TRANSLATE_FUNCS = {"tr", "_", "_T", "_TF"}


# --------------------------------------------------------------------------- 读取

def load_table(path: Path) -> tuple[dict, list]:
    """读取 JSON 翻译表；返回 (内容, 重复键列表)。重复键在 json 中静默取最后一个。"""
    dupes: list[str] = []
    pairs: list = []

    def hook(obj):
        seen = set()
        for k, v in obj:
            if k in seen:
                dupes.append(k)
            seen.add(k)
            pairs.append((k, v))
        return obj

    try:
        text = path.read_text(encoding="utf-8-sig")
        json.loads(text, object_pairs_hook=hook)
    except (OSError, ValueError) as exc:
        print(f"  [错误] 无法解析 {path.name}: {exc}")
        return {}, []
    data = {k: v for k, v in pairs if isinstance(v, str)}
    return data, dupes


def placeholder_set(text: str) -> frozenset:
    """提取 str.format 占位符集合：{}、{n}、{name}、{name:fmt}、{name!r}。"""
    return frozenset(field for _, field, _, _ in Formatter().parse(text) if field is not None)


def strip_placeholders(text: str) -> str:
    """去掉全部 {…} 占位符（含格式说明符），返回剩余文案。"""
    return re.sub(r"\{[^}]*\}", "", text)


def collect_code_keys() -> tuple[list, list]:
    """用 ast 扫描代码，返回 (静态键列表, 动态键前缀列表)。

    仅统计真实调用节点，天然排除 docstring / 注释中的示例文本；
    语法解析失败的文件跳过（不影响其他文件）。
    """
    static_keys, dyn_prefixes = [], []
    for target in CODE_SCAN:
        files = [target] if target.is_file() else [
            f for f in target.rglob("*.py")
            if f.resolve() != Path(__file__).resolve()
            and not any(part in _SKIP_DIRS for part in f.parts)
        ]
        for f in files:
            try:
                src = f.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in _TRANSLATE_FUNCS):
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    static_keys.append(arg.value)
                elif isinstance(arg, ast.JoinedStr):
                    # 动态 f-string：取第一个 { 之前的字面前缀
                    prefix = ""
                    for part in arg.values:
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            prefix += part.value
                        else:
                            break
                    if "." in prefix:
                        dyn_prefixes.append(prefix.rsplit(".", 1)[0] + ".")
    return static_keys, dyn_prefixes


# --------------------------------------------------------------------------- 校对

def main() -> int:
    args = set(sys.argv[1:])
    quiet = "--quiet" in args
    check_code = "--no-code" not in args
    chinese_only = "--chinese-only" in args

    print("=" * 46)
    print(" i18n 翻译校对报告")
    print("=" * 46)

    # 1. 加载
    tables, dupes_map = {}, {}
    for lang in ("zh-CN", "en"):
        path = I18N_DIR / f"{lang}.json"
        if not path.exists():
            print(f"  [错误] 缺少翻译文件: {path.name}")
            return 1
        data, dupes = load_table(path)
        tables[lang] = data
        dupes_map[lang] = dupes
        print(f"  {lang}.json: {len(data)} 键" + (f"  (重复键 {len(dupes)})" if dupes else ""))

    zh, en = tables["zh-CN"], tables["en"]
    errors, warns, infos = [], [], []

    # 2. 键集比对
    missing_english = zh.keys() - en.keys()
    if chinese_only:
        infos.append(f"{len(missing_english)} 个键等待翻译；运行时回退中文")
    else:
        for key in sorted(missing_english):
            errors.append(f"缺翻译键  {key}    zh-CN 有 / en 缺失")
    for key in sorted(en.keys() - zh.keys()):
        errors.append(f"缺翻译键  {key}    en 有 / zh-CN 缺失")

    # 3. 占位符比对
    for key in sorted(zh.keys() & en.keys()):
        pz, pe = placeholder_set(zh[key]), placeholder_set(en[key])
        if pz != pe:
            errors.append(
                f"占位符不一致  {key}\n"
                f"      zh-CN: {zh[key]!r}\n"
                f"      en:    {en[key]!r}"
            )

    # 4. 空值检查
    for key in sorted(zh.keys() & en.keys()):
        for lang, table in (("zh-CN", zh), ("en", en)):
            if not table[key].strip():
                warns.append(f"空文案  [{lang}] {key}")

    # 5. 疑似未翻译（zh == en；跳过纯占位符文案与 lang.* 语言名键）
    for key in sorted(zh.keys() & en.keys()):
        text = zh[key]
        if key.startswith("lang.") or not text.strip():
            continue
        stripped = strip_placeholders(text)
        if len(stripped.strip()) <= 3:  # 去掉占位符后基本没有实际文案
            continue
        if zh[key] == en[key]:
            infos.append(f"疑似未翻译  {key}: {zh[key]!r}")

    # 6. 重复键
    for lang, dupes in dupes_map.items():
        for key in dupes:
            warns.append(f"重复键（后值覆盖前值）  [{lang}] {key}")

    # 7. 代码引用检查
    if check_code:
        static_keys, dyn_prefixes = collect_code_keys()
        used = set(static_keys)
        # 动态键前缀覆盖的键族不算死键（如 tr(f"gui.theme.{stem}.name")）
        dyn_covered = {k for k in zh.keys() | en.keys()
                       if any(k.startswith(p) for p in dyn_prefixes)}
        missing = sorted(used - zh.keys() - en.keys())
        dead = sorted((zh.keys() & en.keys()) - used - dyn_covered)
        for key in missing:
            errors.append(f"代码引用但翻译表缺失  {key}")
        if dead:
            infos.append(f"翻译表有但代码未引用（死键） {len(dead)} 个: {', '.join(dead[:10])}"
                         + (" ..." if len(dead) > 10 else ""))
        # 动态键前缀族检查
        pref = Counter(dyn_prefixes)
        for p, count in sorted(pref.items()):
            if not any(k.startswith(p) for k in zh.keys() | en.keys()):
                errors.append(f"动态键前缀无对应键族  {p!r}  (出现 {count} 次)")
        print(f"  代码引用: 静态键 {len(used)} / 动态键前缀 {len(pref)} / 死键 {len(dead)}")

    # ------------------------------------------------------------------ 输出
    def dump(title, items):
        print(f"\n[{title}] ({len(items)})")
        for item in items[:20]:
            print("  " + item)
        if len(items) > 20:
            print(f"  ... 其余 {len(items) - 20} 条省略")

    if errors:
        dump("错误", errors)
    if warns and not quiet:
        dump("警告", warns)
    if infos and not quiet:
        dump("提示", infos)

    print("\n" + "=" * 46)
    status = "通过 ✓" if not errors else f"发现 {len(errors)} 个错误"
    print(f" 汇总: 错误 {len(errors)} / 警告 {len(warns)} / 提示 {len(infos)}    {status}")
    print("=" * 46)

    if not errors and not warns and not infos:
        print(" 所有检查项均正常，未发现问题。")
    return 1 if errors else 0


if __name__ == "__main__":
    code = main()
    try:  # 双击运行时窗口不会闪退
        input("\n按回车键退出...")
    except EOFError:
        pass
    sys.exit(code)
