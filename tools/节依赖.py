"""节依赖分析：算出"这一节需要 import 什么"。

背景：人工列导入清单会漏（本项目 music.py 就漏了 traceback / memory ✗）。
本工具用 AST 分析，只认事实：**在这几节里被用到、但没有在这些节里定义/导入的名字**。

用法：
    E:\\Python\\python.exe tools\\节依赖.py <文件> <节标题关键词> [更多关键词...]

例（⚠️ 2026-10-15 修 ✗：原例写的是 code\\mock_zhixiaoli.py —— 拆包前的单文件，早已删掉 ✓）：
    E:\\Python\\python.exe tools\\节依赖.py code\\server\\music.py 音乐
（按 `# ===== 标题 =====` 分节；匹配到的节会合并分析）
"""
import ast
import builtins
import re
import sys

path = sys.argv[1]
keywords = sys.argv[2:]
if not keywords:
    print("用法: python tools\\节依赖.py <文件> <节关键词> [更多...]")
    raise SystemExit(2)

src = open(path, encoding="utf-8").read()
lines = src.splitlines()

# ---- 按分节标记切节 ----
marks = [(i, m.group(1).strip())
         for i, l in enumerate(lines)
         if (m := re.match(r"^# ={3,}\s*(.+?)\s*={3,}\s*$", l))]
sections = []
for idx, (start, title) in enumerate(marks):
    end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
    sections.append((title, start, end))

picked = [(t, s, e) for (t, s, e) in sections if any(k in t for k in keywords)]
if not picked:
    print("没匹配到任何节，可用的节标题：")
    for t, _, _ in sections:
        print("   ", t)
    raise SystemExit(1)

print("=== 选中的节 ===")
for t, s, e in picked:
    print(f"  {t}   行{s + 1}-{e}   （{e - s} 行）")

seg = "\n".join("\n".join(lines[s:e]) for _, s, e in picked)
tree = ast.parse(seg)

defined = set(dir(builtins))
imported = set()
used_names = set()
attr_roots = set()

for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defined.add(node.name)
        for a in node.args.args + node.args.kwonlyargs:
            defined.add(a.arg)
        if node.args.vararg:
            defined.add(node.args.vararg.arg)
        if node.args.kwarg:
            defined.add(node.args.kwarg.arg)
    elif isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        else:
            used_names.add(node.id)
    elif isinstance(node, ast.Attribute):
        n = node
        while isinstance(n, ast.Attribute):
            n = n.value
        if isinstance(n, ast.Name):
            attr_roots.add(n.id)
    elif isinstance(node, ast.alias):
        imported.add((node.asname or node.name).split(".")[0])
    elif isinstance(node, (ast.comprehension,)):
        for t in ast.walk(node.target):
            if isinstance(t, ast.Name):
                defined.add(t.id)

missing = sorted(n for n in used_names - defined - imported if not n.startswith("__"))

cfg = [n for n in missing if n.isupper()]
funcs = [n for n in missing if not n.isupper()]
mods = sorted(attr_roots)

print("\n=== 这些节里用到、但本节没定义的【模块前缀】（→ need import）===")
for m in mods:
    if m not in defined and m not in ("self", "cls"):
        print("   ", m)

print("\n=== 疑似缺失的【配置常量】（全大写 → 多半来自 .config）===")
print("   ", ", ".join(cfg) if cfg else "（无）")

print("\n=== 疑似缺失的【函数/模块名】（小写 → 多半来自 memory/speech/music）===")
print("   ", ", ".join(funcs) if funcs else "（无）")

print("\n=== 可直接粘贴的导入块（第一版，之后再用 ruff 复核）===")
stdlib_mods = [m for m in mods if m in ("os", "re", "json", "time", "hashlib", "asyncio",
                                        "traceback", "uuid", "sqlite3", "random")]
third_mods = [m for m in mods if m in ("requests", "flask")]
own_mods = [m for m in mods if m not in stdlib_mods + third_mods and m not in defined]
for m in stdlib_mods:
    print("import", m)
if stdlib_mods and (third_mods or own_mods or cfg):
    print()
for m in third_mods:
    print("import", m)
if cfg:
    wrapped, cur = [], "    "
    for c in cfg:
        if len(cur) + len(c) + 2 > 84:
            wrapped.append(cur.rstrip())
            cur = "    "
        cur += c + ", "
    wrapped.append(cur.rstrip())
    print("from .config import (")
    for w in wrapped:
        print(w)
    print(")")
for m in own_mods:
    print(f"from . import {m}" if m in ("memory",) else f"# TODO: {m} 从哪来？")
