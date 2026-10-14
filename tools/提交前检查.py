"""提交前检查：别把 .env 里的真实密钥/真 token 提交进仓库。

为什么需要（本会话真实踩到 3 次）：
  1) .env.bak（含真实密钥）被提交；2) 文档里把真实密钥前缀当 grep 例子；
  3) 转发文档被填上真 token 后又被提交。
都是"人一时疏忽"型，所以按《工程债偿还指南》第 8 条：纪律靠机器，人只负责判断。

本脚本故意不起子进程（subprocess 管道在受限环境会 EPERM，曾让护栏自己崩掉），
暂存文件列表由 .githooks/pre-commit（sh）取好、当参数传进来。

用法：E:\\Python\\python.exe tools\\提交前检查.py 文件1 文件2 ...
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
files = sys.argv[1:]
if not files:
    print("[提交前检查] 没拿到暂存文件列表，跳过")
    sys.exit(0)
if not os.path.exists(ENV):
    sys.exit(0)

secrets = []
for line in open(ENV, encoding="utf-8"):
    if line.lstrip().startswith("#") or "=" not in line:
        continue
    k, v = line.rstrip("\n").split("=", 1)
    v = v.strip()
    if len(v) >= 8 and re.search(r"KEY|SECRET|TOKEN|PASSWORD", k.upper()):
        secrets.append((k.strip(), v))
if not secrets:
    sys.exit(0)

bad = []
for f in files:
    p = os.path.join(ROOT, f)
    if not os.path.isfile(p):
        continue
    try:
        txt = open(p, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    for k, v in secrets:
        if v in txt:
            bad.append((f, k))

if bad:
    print("")
    print("[提交前检查] x 提交被拦下：暂存的文件里出现了 .env 的真实密钥")
    for f, k in bad:
        print("    " + f + "   <- 含 " + k + " 的值")
    print("    处理：把真值换成占位符（密钥只进 .env）")
    print("          确实要提交请用 --no-verify（不推荐）")
    print("")
    sys.exit(1)
print("[提交前检查] ok 已检查 " + str(len(files)) + " 个暂存文件，没有 .env 的真实密钥")