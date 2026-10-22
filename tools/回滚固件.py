"""把备份好的固件烧回板子（2026-10-22）

改固件之前先有这条退路：出问题一条命令烧回去，不用编译、不用看源码。

用法：
    E:\\Python\\python.exe tools\\回滚固件.py --dry-run    # 只检查，不烧
    E:\\Python\\python.exe tools\\回滚固件.py              # 真的烧
    E:\\Python\\python.exe tools\\回滚固件.py --bin 路径   # 指定烧哪个

⭐ 为什么要 --dry-run：
   烧固件是不可逆的动作（烧坏了板子就哑了）。所以先能"检查一遍"——
   找到包、找到串口、确认板子型号，都对了再真烧。

⭐ 烧完会等板子重启，然后 HTTP 探一下 192.168.137.39，
   看到它应声才算回滚成功。只看"烧写 100%"是不够的 ——
   烧进去但跑不起来也是失败。
"""
import argparse
import glob
import os
import subprocess
import sys
import time

sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = r"E:\天翼"
BACKUP_DIR = os.path.join(ROOT, "backups", "固件")
PY = r"E:\Python\python.exe"
BOARD_IP = "192.168.137.39"
APP_OFFSET = "0x10000"          # app 分区偏移（app3M_fat9M_16MB 方案）


def esptool_cmd():
    """返回调用 esptool 的命令前缀；找不到返回 None。"""
    # ① 先看 Python 里有没有
    try:
        r = subprocess.run([PY, "-m", "esptool", "version"],
                           capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return [PY, "-m", "esptool"]
    except Exception:
        pass
    # ② 再看 Arduino 15 目录下自带的
    pat = os.path.join(os.path.expanduser("~"), ".arduino15", "packages", "esp32",
                       "tools", "esptool_py", "*", "esptool.exe")
    for p in sorted(glob.glob(pat), reverse=True):
        if os.path.exists(p):
            return [p]
    return None


def find_bins():
    return sorted(glob.glob(os.path.join(BACKUP_DIR, "固件_*.bin")), reverse=True)


def find_board(esp):
    """挨个 COM 口问一句 chip_id，回 ESP32-S3 的就是板子。"""
    try:
        r = subprocess.run(esp + ["version"], capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        print("  esptool: " + ((r.stdout or "").strip().splitlines() or ["?"])[0])
    except Exception:
        pass

    ports = []
    try:
        r = subprocess.run([PY, "-c",
                            "import serial.tools.list_ports as p;"
                            "print('\\n'.join(x.device for x in p.comports()))"],
                           capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        ports = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    except Exception:
        pass

    print("  当前串口：" + (", ".join(ports) if ports else "一个都没扫到"))
    if not ports:
        ports = [f"COM{i}" for i in range(3, 9)]

    for port in ports:
        try:
            # ⚠️ esptool v5 用 chip-id，v4 用 chip_id，两个都试
            for sub in ("chip-id", "chip_id"):
                r = subprocess.run(esp + ["--port", port, "--chip", "esp32s3", sub],
                                   capture_output=True, text=True, timeout=25,
                                   encoding="utf-8", errors="replace")
                out = (r.stdout or "") + (r.stderr or "")
                if "ESP32-S3" in out:
                    print("  ✓ 板子在 " + port + "（" + sub + "）")
                    return port
                if "unexpected keyword" in out or "No such command" in out:
                    continue
        except Exception:
            continue
    print("  ✗ 没找到 ESP32-S3。检查三件事：")
    print("    ① 板子插 USB 了吗（只连 WiFi 是找不到的，烧写必须插线）")
    print("    ② Windows 设备管理器里有没有 COM 口出现/消失（没有就是驱动/线的问题）")
    print("    ③ 上面列出的串口里有没有新增的")
    return None


def board_alive(timeout=6.0):
    """烧完探一下板子应不应声。"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{BOARD_IP}/", timeout=timeout) as r:
            body = r.read(400).decode("utf-8", "replace")
        try:
            j = json.loads(body)
            return True, f"在线（{list(j.keys())[:4]}…）"
        except ValueError:
            return True, "在线（返回了非 JSON）"
    except Exception as e:
        return False, f"没应声：{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只检查，不烧")
    ap.add_argument("--bin", default="", help="指定要烧的 .bin")
    args = ap.parse_args()

    print("=" * 70)
    print("  固件回滚" + ("（演练模式，不会真烧）" if args.dry_run else ""))
    print("=" * 70)

    # ① 找 .bin
    if args.bin:
        target = args.bin
    else:
        bins = find_bins()
        if not bins:
            print("  ✗ backups\\固件\\ 下没有备份。先跑 tools\\固件备份.py")
            return 2
        target = bins[0]
        if len(bins) > 1:
            print("  可用的备份（默认用最新的那个）：")
            for b in bins:
                print("    " + os.path.basename(b) + "  " + str(os.path.getsize(b)) + " 字节")
    if not os.path.exists(target):
        print("  ✗ 找不到 " + target)
        return 2
    print("")
    print("  要烧的文件  " + os.path.basename(target))
    print("              " + str(os.path.getsize(target)) + " 字节")
    print("  偏移        " + APP_OFFSET + "（app 分区）")

    # ② 找 esptool
    esp = esptool_cmd()
    if not esp:
        print("  ✗ 找不到 esptool。装一个：E:\\Python\\python.exe -m pip install esptool")
        return 2
    print("  esptool     " + " ".join(esp))

    # ③ 找板子
    print("")
    port = find_board(esp)
    if not port:
        return 2

    # ④ 现在板子活着吗（烧之前先记一下）
    print("")
    ok_before, msg_before = board_alive()
    print("  烧之前：" + ("在线" if ok_before else "不在线") + " —— " + msg_before)

    if args.dry_run:
        print("")
        print("  ✓ 检查完了，一切就绪。")
        print("    真烧就把 --dry-run 去掉：")
        print("      E:\\Python\\python.exe tools\\回滚固件.py")
        return 0

    # ⑤ 真的烧
    print("")
    print("  开始烧写（约 20~40 秒，中途别拔线）…")
    cmd = esp + ["--port", port, "--chip", "esp32s3", "--baud", "921600",
                 "write_flash", "--flash_mode", "dio", "--flash_freq", "80m",
                 "--flash_size", "detect", APP_OFFSET, target]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace")
    dt = time.time() - t0
    out = (r.stdout or "") + (r.stderr or "")
    print(f"    用时 {dt:.1f} 秒，退出码 {r.returncode}")
    if r.returncode != 0:
        print("  ✗ 烧写失败，最后几行：")
        for line in out.strip().splitlines()[-15:]:
            print("    " + line)
        return 2
    print("    烧写完成")

    # ⑥ 等它重启，然后探一下
    print("")
    print("  等板子重启并联网（最多 45 秒）…")
    alive = False
    for i in range(15):
        time.sleep(3)
        alive, msg = board_alive(timeout=3.0)
        print(f"    第 {i + 1:2d} 次探测：{msg[:40]}")
        if alive:
            break

    print("")
    if alive:
        print("  ✓ 回滚成功，板子在线")
        return 0
    print("  ⚠ 烧写完成但板子没应声")
    print("    可能原因：还在连 WiFi（再等等）、或者 IP 变了")
    print(f"    确认方法：手机连同一热点，浏览器开 http://{BOARD_IP}/")
    return 1


if __name__ == "__main__":
    sys.exit(main())
