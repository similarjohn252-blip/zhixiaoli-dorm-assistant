"""清理仓库里的生成物（2026-10-25）

原则：**只删能重新生成的东西**，文档一个字不动（那是答辩的证据链）。

删三类：
  ① code\\sketch_...\\build\\  —— Arduino 编译中间产物（elf 30MB + map 20MB + merged.bin 16MB + bin 2.2MB）
     删之前已经跑过 固件备份.py，当前固件 .bin 存在 backups\\固件\\ 里了 ✓，烧板子不用重新编译也能烧
  ② 语音\\output_*.wav 等 —— TTS 每次请求都生成的临时语音（254 个，69MB）
  ③ 缓存 —— __pycache__ / .pytest_cache / .ruff_cache / .coverage / .pytest_tmp

保留：
  · 语音\\music\\（6 个缓存歌曲，6MB）—— 演示时网络若不好，这是现成的）
  · 语音\\access.log、链路日志.jsonl —— 诊断证据
  · 语音\\chime.wav、mic_last.wav —— 运行时会自动重建，但太小，留着省事
  · 所有 .md / 图片 / 提交表
"""
import glob
import os
import shutil

ROOT = r"E:\天翼"


def human(n):
    return f"{n / 1024 / 1024:.1f} MB"


# ---------- ① 先列清单 ----------
targets_files = []
targets_dirs = []

# 语音：生成物
for pat in ("output_*.wav", "output_x_*.wav", "output.wav", "output.mp3",
            "baidu_tts.mp3", "edge_tts.mp3", "_chk.wav"):
    targets_files += glob.glob(os.path.join(ROOT, "语音", pat))

# 编译中间产物
build = os.path.join(ROOT, "code", "sketch_oct8a_copy_20261008145732", "build")
if os.path.isdir(build):
    targets_dirs.append(build)

# 缓存
for p in ("__pycache__", ".pytest_cache", ".ruff_cache", ".pytest_tmp", ".coverage"):
    full = os.path.join(ROOT, p)
    if os.path.isdir(full):
        targets_dirs.append(full)
    elif os.path.isfile(full):
        targets_files.append(full)
for p in ("code/__pycache__", "code/server/__pycache__", "tests/__pycache__",
          "tools/__pycache__", "code/sketch_oct8a_copy_20261008145732/__pycache__"):
    full = os.path.join(ROOT, *p.split("/"))
    if os.path.isdir(full):
        targets_dirs.append(full)

size_files = sum(os.path.getsize(f) for f in targets_files)
size_dirs = 0
for d in targets_dirs:
    for dp, _dn, fn in os.walk(d):
        for f in fn:
            try:
                size_dirs += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass

print(f"  将删文件 {len(targets_files)} 个，共 {human(size_files)}")
print(f"  将删目录 {len(targets_dirs)} 个，共 {human(size_dirs)}")
print(f"  合计释放 ≈ {human(size_files + size_dirs)}")
print("")
print("  目录清单：")
for d in targets_dirs:
    print("     " + d.replace(ROOT + "\\", ""))
print("")
print("  语音文件按类型：")
kinds = {}
for f in targets_files:
    base = os.path.basename(f)
    key = "output_x_*.wav" if base.startswith("output_x_") else (
        "output_*.wav" if base.startswith("output_") else base)
    kinds[key] = kinds.get(key, 0) + 1
for k, v in sorted(kinds.items()):
    print(f"     {k:<20} {v} 个")

# ---------- ② 安全校验：别把仓库根或文档删了 ----------
bad = [p for p in targets_dirs + targets_files
       if os.path.normcase(os.path.abspath(p)) in (os.path.normcase(ROOT),
                                                   os.path.normcase(ROOT + "\\code"),
                                                   os.path.normcase(ROOT + "\\语音"))]
if bad:
    raise SystemExit("  拒绝执行：清单里出现了不该删的目录 " + str(bad))

# ---------- ③ 执行 ----------
print("")
for d in targets_dirs:
    shutil.rmtree(d, ignore_errors=True)
    print("  删目录 " + d.replace(ROOT + "\\", ""))
for f in targets_files:
    try:
        os.remove(f)
    except OSError:
        pass
print(f"  删文件 {len(targets_files)} 个（语音生成物）")

# ---------- ④ 复查 ----------
left = 0
for dp, _dn, fn in os.walk(ROOT):
    if ".git" in dp:
        continue
    for f in fn:
        try:
            left += os.path.getsize(os.path.join(dp, f))
        except OSError:
            pass
print("")
print(f"  清理后仓库体积 ≈ {human(left)}")

keep = [
    ("语音\\music", len(glob.glob(os.path.join(ROOT, "语音", "music", "*")))),
    ("语音\\chime.wav", 1 if os.path.exists(os.path.join(ROOT, "语音", "chime.wav")) else 0),
    ("语音\\access.log", 1 if os.path.exists(os.path.join(ROOT, "语音", "access.log")) else 0),
    ("backups\\固件", len(glob.glob(os.path.join(ROOT, "backups", "固件", "*.bin")))),
]
print("  该留的还在：")
for name, n in keep:
    print(f"     {name:<20} {n} 项")
