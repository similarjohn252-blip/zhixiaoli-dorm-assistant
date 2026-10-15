"""提交前检查：① .env 里的真实密钥 ② 运行时产物（录音/日志/数据库/备份）

为什么需要（本会话真实踩到 4 次 ✗）：
  ① .env.bak 含真实密钥被提交；② 文档里把真实密钥前缀当 grep 例子；
  ③ 转发文档被填上真 token 后又被提交；④ 语音\链路日志.jsonl（运行时日志）被提交。
前三次是"密钥"，第四次是"运行时产物" —— 所以护栏要**两类都查** ✓
按《工程债偿还指南》第 8 条：**纪律靠机器，人只负责判断** ✓

⚠️ 本脚本**故意不起子进程**（subprocess 管道在受限环境会 EPERM，
   曾让护栏自己崩掉、提交被"误拦" ✓）；
   暂存文件列表由 .githooks/pre-commit（sh）取好、当参数传进来 ✓

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

# ---------- ① 运行时产物：这些东西每次运行都会变，且夹带真实数据 ✗ ----------
RUNTIME = (
    r"\.db$", r"\.db-wal$", r"\.db-shm$",     # SQLite 台账
    r"\.jsonl$", r"\.log$",                   # 链路日志 / 访问日志
    r"\.wav$", r"\.mp3$", r"\.m4a$",          # 录音与音频缓存
    r"\.bak$",                                # 配置备份
    r"^语音/",                                 # 整个音频目录都是运行时产物 ✓
    # ⚠️ 2026-10-15 实测：**截图/照片里常含私人信息** ✗ ——
    #    `git add -A` 把 5 张报名截图（含 5 人的学号与手机号 ✓）顺手提交了 ✓，
    #    已撤销 + gc 清干净 ✓。**图片一律不进仓库** ✓，要展示就放网盘/单独发 ✓
    r"\.png$", r"\.jpg$", r"\.jpeg$", r"\.gif$", r"\.webp$", r"\.bmp$",
    r"^比赛图片/", r"^图片/", r"^截图/",
)
ALLOW = (r"\.env\.example$",)                 # 唯一允许入库的 .env 变体 ✓

rt_bad = []
for f in files:
    p = f.replace("\\", "/")
    if any(re.search(a, p) for a in ALLOW):
        continue
    if any(re.search(r, p) for r in RUNTIME):
        rt_bad.append(f)

# ---------- ② 真实密钥 ----------
# ⚠️ 2026-10-15 补了一个**真盲区** ✗：原来只从 `.env` 收集密钥 ✓
#    但固件的密钥在 `secrets.h` 里 ✓ —— 实测它里面的 CLOUD_API_KEY
#    与 `.env` 的任何值都**不一致** ✓ → 也就是说，谁要是把那个 key 抄进文档，
#    护栏**拦不住** ✗✗（因为它压根不认识这个值 ✓）
#    → 现在把 `secrets.h` 里的 `#define NAME "值"` 也一起收集 ✓
sec_bad = []
secrets = []                      # ⚠️ 提到 if 外面 ✓ 否则 .env 缺失时会 NameError ✗
if os.path.exists(ENV):
    for line in open(ENV, encoding="utf-8"):
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.rstrip("\n").split("=", 1)
        v = v.strip()
        if len(v) >= 8 and re.search(r"KEY|SECRET|TOKEN|PASSWORD", k.upper()):
            secrets.append((k.strip(), v))

SECRETS_H = os.path.join(ROOT, "code", "sketch_oct8a_copy_20261008145732", "secrets.h")
if os.path.exists(SECRETS_H):
    for line in open(SECRETS_H, encoding="utf-8", errors="ignore"):
        m = re.match(r'\s*#define\s+(\w*(?:KEY|SECRET|TOKEN|PASSWORD|PASS|PWD)\w*)\s+"([^"]{8,})"',
                     line, re.I)
        if m:
            secrets.append((m.group(1) + "（来自 secrets.h）", m.group(2)))

if secrets:
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
                sec_bad.append((f, k))

# ---------- 结论 ----------
if rt_bad or sec_bad:
    print("")
    print("[提交前检查] x 提交被拦下")
    if sec_bad:
        print("  ① 暂存文件里出现了 .env 的真实密钥：")
        for f, k in sec_bad:
            print("      " + f + "   <- 含 " + k + " 的值")
    if rt_bad:
        print("  ② 暂存文件里出现了【运行时产物】（每次运行都会变、且夹带真实数据）：")
        for f in rt_bad:
            print("      " + f)
        print("     处理：把它加进 .gitignore（已覆盖 *.db* / *.jsonl / *.log / *.wav / *.bak / 语音/ ✓）；")
        print("           已经误加的：git rm --cached <文件>")
    print("  确实要提交请用 --no-verify（不推荐）")
    print("")
    sys.exit(1)

print("[提交前检查] ok 已检查 " + str(len(files)) + " 个暂存文件：无真实密钥、无运行时产物")