"""把当前固件编译成一个 .bin 存起来（2026-10-22）

为什么要这个：
    改固件之前必须先有一份"确定能跑"的产物。光有源码不够 ——
    源码要重新编译，而编译环境（core 版本、依赖）以后可能变。
    存一份编译好的 .bin，回滚时直接烧，不编译、不赌编译环境。

存哪儿：
    backups\\固件\\ 下，文件名带日期和 git 提交号，方便认。
    这个目录不进 git（几 MB 的二进制进仓库不合适），
    真正的"备份"其实是 git 里的源码，.bin 只是让回滚更快更稳。

用法：
    E:\\Python\\python.exe tools\\固件备份.py
"""
import glob
import os
import shutil
import subprocess
import sys
import time

sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"E:\天翼"
SKETCH = os.path.join(ROOT, "code", "sketch_oct8a_copy_20261008145732")
CLI = r"E:\Arduino\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
BACKUP_DIR = os.path.join(ROOT, "backups", "固件")

# ⚠️ 这个 FQBN 必须和实际烧录用的一致，否则产物不对
FQBN = ("esp32:esp32:esp32s3:UploadSpeed=921600,USBMode=hwcdc,FlashSize=16M,"
        "PartitionScheme=app3M_fat9M_16MB,DebugLevel=none,PSRAM=opi")


def git_short():
    try:
        out = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip() or "nogit"
    except Exception:
        return "nogit"


def main():
    if not os.path.exists(CLI):
        print("  ✗ arduino-cli 找不到：" + CLI)
        return 2
    if not os.path.isdir(SKETCH):
        print("  ✗ 固件源码目录找不到：" + SKETCH)
        return 2

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    revision = git_short()
    out_dir = os.path.join(BACKUP_DIR, f"_build_{stamp}")

    print("  编译中（第一次大概一两分钟）…")
    print("    FQBN  " + FQBN)
    print("    源码  " + SKETCH)
    t0 = time.time()
    r = subprocess.run([CLI, "compile", "--fqbn", FQBN, "--output-dir", out_dir, SKETCH],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    dt = time.time() - t0
    tail = (r.stdout or "") + (r.stderr or "")
    print(f"    用时 {dt:.1f} 秒，退出码 {r.returncode}")

    if r.returncode != 0:
        print("  ✗ 编译失败，最后几行：")
        for line in tail.strip().splitlines()[-12:]:
            print("    " + line)
        return 2

    bins = glob.glob(os.path.join(out_dir, "*.ino.bin"))
    if not bins:
        print("  ✗ 编译成功但没找到 .bin，产物列表：")
        for f in glob.glob(os.path.join(out_dir, "*")):
            print("    " + os.path.basename(f))
        return 2

    src = bins[0]
    size = os.path.getsize(src)
    dst = os.path.join(BACKUP_DIR, f"固件_{stamp}_{revision}.bin")
    shutil.copy2(src, dst)
    shutil.rmtree(out_dir, ignore_errors=True)

    print("")
    print("  ✓ 备份好了")
    print("    文件  " + dst)
    print("    大小  " + str(size) + f" 字节（{size / (3 * 1024 * 1024) * 100:.0f}% 于 3MB 应用分区）")
    print("    提交  " + revision)
    print("")
    print("  回滚时用：")
    print("    E:\\Python\\python.exe tools\\回滚固件.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
