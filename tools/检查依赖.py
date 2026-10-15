"""依赖核查（工程债 A3）：把"本机实际装的"和"仓库里锁定的"对一遍。

《指南》对 A3 的验收标准是：「**新机器按 README 装出的版本与演示机一致**」✓
—— 所以这件事必须**可核查**，而不是"我记得装的是 4.0.0" ✗

用法：E:\\Python\\python.exe E:\\天翼\\tools\\检查依赖.py
      （同时核对 Python 依赖 `requirements.txt` 与固件依赖 `固件依赖.json` ✓）
"""
import hashlib
import json
import os
import sys
from importlib import metadata

ROOT = r"E:\天翼"
LIB = os.path.join(os.environ["USERPROFILE"], "Documents", "Arduino", "libraries")
PKG = os.path.join(os.environ["LOCALAPPDATA"], "Arduino15", "packages")

ok_all = True
bad = []


def line(ok, what, want, got):
    global ok_all
    if not ok:
        ok_all = False
        bad.append(f"{what}（期望 {want}，实际 {got}）")
    print(f"  {'✓' if ok else '✗'} {what:<30} 期望 {want:<16} 实际 {got}")


def find_lib(name):
    """按目录名包含库名来找（目录可能叫 xxx-master ✓）"""
    if not os.path.isdir(LIB):
        return None
    for d in os.listdir(LIB):
        if name.lower() in d.lower():
            return os.path.join(LIB, d)
    return None


def lib_version(folder):
    p = os.path.join(folder, "library.properties")
    if not os.path.exists(p):
        return "（没有 library.properties）"
    for ln in open(p, encoding="utf-8", errors="replace"):
        if ln.startswith("version="):
            return ln.split("=", 1)[1].strip()
    return "（没有 version 字段）"


print("=" * 84)
print("Python 依赖（requirements.txt）")
print("=" * 84)
req = os.path.join(ROOT, "requirements.txt")
if not os.path.exists(req):
    print("  ✗ 找不到 requirements.txt —— 没有锁定文件就谈不上「一致」✗")
    ok_all = False
else:
    for ln in open(req, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "==" not in ln:
            continue
        name, want = ln.split("==", 1)
        try:
            got = metadata.version(name)
        except Exception:
            got = "（未安装）"
        line(got == want, name, want, got)

spec_path = os.path.join(ROOT, "固件依赖.json")
spec = json.load(open(spec_path, encoding="utf-8")) if os.path.exists(spec_path) else {}

print()
print("=" * 84)
print("固件库（library.properties 里的 version）")
print("=" * 84)
for name, want in (spec.get("libs") or {}).items():
    folder = find_lib(name)
    got = lib_version(folder) if folder else "（未安装）"
    line(got == want, name, want, got)

print()
print("=" * 84)
print("内容指纹（SHA256 前 16 位）—— 用来识别「版本号相同但内容不同」✓")
print("=" * 84)
for rel, want in (spec.get("fingerprints") or {}).items():
    libname, rest = rel.split("/", 1)
    folder = find_lib(libname)
    got = "（找不到文件）"
    if folder:
        p = os.path.join(folder, rest.replace("/", os.sep))
        if os.path.exists(p):
            got = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16].upper()
    line(got == want, rest.split("/")[-1], want, got)

print()
print("=" * 84)
print("ESP32 核心（按已安装目录名判断 ✓，不起子进程）")
print("=" * 84)
core = spec.get("core", "")
want = core.split("@")[1] if "@" in core else core
core_dir = os.path.join(PKG, "esp32", "hardware", "esp32")
installed = os.listdir(core_dir) if os.path.isdir(core_dir) else []
line(want in installed, "esp32 core", want, "、".join(installed) or "（未安装）")

print()
print("=" * 84)
if ok_all:
    print("结论：与本仓库锁定的版本完全一致 ✓（可以放心编译/烧录/演示）")
else:
    print(f"结论：{len(bad)} 处对不上 ✗ —— 装成一致再编译/演示 ✓")
    for x in bad:
        print("   · " + x)
    print("\n  安装指引见 固件依赖.json 的「安装方式」字段 ✓")
print("=" * 84)
sys.exit(0 if ok_all else 1)
