"""结构地图：列出服务端源码的顶层定义、路由、全局状态。

⚠️ 2026-10-15 修 ✗：原来默认指向 `code\\mock_zhixiaoli.py`（拆包前那个 933 行单文件 ✓）
   —— 它**早就被拆成 10 个模块并删掉了** ✓ → 工具一跑就 FileNotFoundError ✓
   （教训：**改了结构，工具里的旧路径要一起改** ✗ —— 本项目的"旧名字要 grep"第 N 次 ✓）

用法：
    E:\\Python\\python.exe tools\\结构地图.py                        # 默认：整个 code\\server（每文件一行概要 ✓）
    E:\\Python\\python.exe tools\\结构地图.py code\\server           # 同上，但每个文件都列详情 ✓
    E:\\Python\\python.exe tools\\结构地图.py code\\server\\app.py    # 单个文件详情 ✓
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(ROOT, "code", "server")


def analyze(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = {"lines": len(src.splitlines()), "top": len(tree.body),
           "imports": [], "consts": [], "funcs": [], "classes": [], "routes": [],
           "route_n": 0, "top_code": []}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            txt = ast.get_source_segment(src, node) or ""
            out["imports"].append((node.lineno, txt.splitlines()[0]))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                name = getattr(t, "id", None) or (
                    t.attr if isinstance(t, ast.Attribute) else "?")
                out["consts"].append((node.lineno, name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            deco = [ast.get_source_segment(src, d) for d in node.decorator_list]
            # ⚠️ 只认真正的 @app.route ✗ —— 原来写成 "route" 或 "app."，
            #    于是 @app.errorhandler / @app.after_request 也被算成"路由" ✓
            #    → 报出 21 条，而真实路由只有 16 条（别让工具的数字骗人 ✓）
            if any(d and ".route(" in d for d in deco):
                out["routes"].append((node.lineno, node.name, deco))
                # ⚠️ 一个函数可能挂**多个** @app.route ✗（本项目就有 ✓）——
                #    所以"路由条数"要数**装饰器**，不能数函数 ✓（否则会报 15，而真实是 16 ✓）
                out["route_n"] += sum(1 for d in deco if d and ".route(" in d)
            else:
                out["funcs"].append((node.lineno, node.name,
                                     node.end_lineno - node.lineno + 1))
        elif isinstance(node, ast.ClassDef):
            out["classes"].append((node.lineno, node.name))
    out["top_code"] = [(n.lineno,
                        (ast.get_source_segment(src, n) or "").splitlines()[0])
                       for n in tree.body
                       if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    return out


def brief(path):
    a = analyze(path)
    print(f"  {os.path.basename(path):<16} {a['lines']:>4} 行  "
          f"函数 {len(a['funcs']):>2}  路由 {a['route_n']:>2}  "
          f"常量 {len(a['consts']):>2}  顶层语句 {a['top']:>3}")


def detail(path):
    a = analyze(path)
    print(f"=== {os.path.relpath(path, ROOT)} ===")
    print(f"总行数: {a['lines']}   顶层语句: {a['top']}")
    print("\n--- 导入 ---")
    for ln, t in a["imports"]:
        print(f"  {ln:4d}  {t}")
    print(f"\n--- 全局常量/配置（{len(a['consts'])} 个）---")
    for ln, n in a["consts"]:
        print(f"  {ln:4d}  {n}")
    print(f"\n--- 普通函数（{len(a['funcs'])} 个，按行号）---")
    for ln, n, size in a["funcs"]:
        print(f"  {ln:4d}  {n:<28} {size:>4} 行")
    if a["classes"]:
        print(f"\n--- 类（{len(a['classes'])} 个）---")
        for ln, n in a["classes"]:
            print(f"  {ln:4d}  {n}")
    print(f"\n--- Flask 路由（本文件声明 {a['route_n']} 条，来自 {len(a['routes'])} 个函数）---")
    for ln, n, deco in a["routes"]:
        print(f"  {ln:4d}  {n:<28} {' '.join(d or '' for d in deco)}")
    if a["route_n"]:
        print("  ⚠️ 只数**本文件写的** @app.route ✗ —— Flask 会自动再加一条 "
              "/static/<path:filename> ✓（所以 url_map 会多 1 ✓）")
    print(f"\n--- 顶层可执行语句（启动逻辑等，{len(a['top_code'])} 处）---")
    for ln, seg in a["top_code"]:
        print(f"  {ln:4d}  {seg[:70]}")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else SERVER_DIR
    if not os.path.isabs(arg):
        arg = os.path.join(ROOT, arg)
    if not os.path.exists(arg):
        print(f"✗ 找不到：{arg}")
        print("  提示：默认会看 code\\server 整个目录 ✓（原来默认指向的那个单文件早已拆掉了 ✗）")
        return 1
    if os.path.isdir(arg):
        files = sorted(f for f in os.listdir(arg) if f.endswith(".py"))
        print(f"=== {os.path.relpath(arg, ROOT)} 的结构地图（{len(files)} 个文件）===")
        print("（路由 = **本文件声明**的 @app.route 条数 ✓；"
              "Flask 自带的 /static 不在此列 ✗ —— 所以 url_map 通常比它多 1 ✓）")
        for f in files:
            brief(os.path.join(arg, f))
        print("\n（想看某个文件的详情：把该文件路径作为参数再跑一次 ✓）")
        return 0
    detail(arg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
