# -*- coding: utf-8 -*-
"""结构地图：列出服务端源码的顶层定义、路由、全局状态，作为拆包的依据。

用法：E:\\Python\\python.exe E:\\天翼\\tools\\结构地图.py [文件路径]
"""
import ast
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"E:\天翼\code\mock_zhixiaoli.py"
src = open(path, encoding="utf-8").read()
tree = ast.parse(src)
lines = src.splitlines()

print(f"=== {path} ===")
print(f"总行数: {len(lines)}   顶层语句: {len(tree.body)}")
print()

imports, consts, funcs, classes, routes = [], [], [], [], []
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        txt = ast.get_source_segment(src, node) or ""
        imports.append((node.lineno, txt.splitlines()[0]))
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            name = getattr(t, "id", None) or (
                t.attr if isinstance(t, ast.Attribute) else "?")
            consts.append((node.lineno, name))
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        deco = [ast.get_source_segment(src, d) for d in node.decorator_list]
        if any(d and ("route" in d or "app." in d) for d in deco):
            routes.append((node.lineno, node.name, deco))
        else:
            funcs.append((node.lineno, node.name, node.end_lineno - node.lineno + 1))
    elif isinstance(node, ast.ClassDef):
        classes.append((node.lineno, node.name))

print("--- 导入 ---")
for ln, t in imports:
    print(f"  {ln:4d}  {t}")

print(f"\n--- 全局常量/配置（{len(consts)} 个）---")
for ln, n in consts:
    print(f"  {ln:4d}  {n}")

print(f"\n--- 普通函数（{len(funcs)} 个，按行号）---")
for ln, n, size in funcs:
    print(f"  {ln:4d}  {n:<28} {size:>4} 行")

print(f"\n--- 类（{len(classes)} 个）---")
for ln, n in classes:
    print(f"  {ln:4d}  {n}")

print(f"\n--- Flask 路由（{len(routes)} 个）---")
for ln, n, deco in routes:
    print(f"  {ln:4d}  {n:<28} {' '.join(d or '' for d in deco)}")

# 顶层代码块（不在函数/类里的可执行语句）
top_code = [n for n in tree.body if isinstance(n, ast.Expr) and
            isinstance(n.value, ast.Call)]
print(f"\n--- 顶层可执行语句（启动逻辑等，{len(top_code)} 处）---")
for n in top_code:
    seg = (ast.get_source_segment(src, n) or "").splitlines()[0]
    print(f"  {n.lineno:4d}  {seg[:70]}")
