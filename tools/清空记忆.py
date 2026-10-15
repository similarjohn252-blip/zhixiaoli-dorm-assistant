"""清空长期记忆（演示前用 ✓）：停服务端 → 删 4 个文件 → 提示重启。

为什么需要它（2026-10-15 **实测**出两个坑 ✗）：
  ① **只删 `记忆.db` 没用** ✗ —— 再次启动时会从 `记忆.json` **自动迁移回来**
     （实测：删库后重启，104 条历史事件原样回来 ✓✓）
  ② **不停服务端也没用** ✗ —— 进程内存里那份会在下一次写盘时**原样写回** ✓
  → 所以"真正清空" = **停服务端** ＋ 删【`记忆.db` / `-wal` / `-shm` / `记忆.json`】**四个文件** ✓✓
     （`-wal` 尤其不能漏 ✗：SQLite 的数据可能还压在 WAL 里，实测它当时有 53KB ✓）

用法：
    E:\\Python\\python.exe tools\\清空记忆.py                  # 只看会删什么（默认演练 ✓ 不删）
    E:\\Python\\python.exe tools\\清空记忆.py --yes            # 真的删 ✓
    E:\\Python\\python.exe tools\\清空记忆.py --yes --backup   # 删之前先挪到 语音\\_清空备份_<时间>\\ ✓

⚠️ 只想清「已知信息」、**保留**历史事件 → 别用本工具 ✗，用接口：
    POST /v1/memory/clear  （需要 `Authorization: Bearer <token>`，token 在 `.env` ✓）
"""
import os
import shutil
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "code"))

from server import config  # noqa: E402

PORT = config.PORT


def server_running():
    """5000 端口有人在听吗 —— 在听就**必须拒绝执行** ✗（否则删了又会被写回来 ✓）"""
    s = socket.socket()
    s.settimeout(1.5)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def targets():
    """要删的四个文件（顺序：库 → 它的两个附属 → 旧 JSON）"""
    return [
        (config.MEMORY_DB, "记忆库（SQLite）"),
        (config.MEMORY_DB + "-wal", "库的预写日志（数据可能压在这里 ✗ 别漏）"),
        (config.MEMORY_DB + "-shm", "库的共享内存索引"),
        (config.MEMORY_FILE, "旧记忆（迁移备份 —— 不删它，下次启动会把事件迁回来 ✗）"),
    ]


def main():
    yes = "--yes" in sys.argv
    backup = "--backup" in sys.argv

    print("=" * 78)
    print("清空长期记忆" + ("（**演练**：只看不删 ✓）" if not yes else "（**真要删** ✗）"))
    print("=" * 78)

    # ① 先卡住"服务端还在跑"这一条 ✗
    if server_running():
        print(f"✗ 拒绝执行：{PORT} 端口上有服务端在跑 ✗")
        print("   为什么：它内存里那份记忆，会在下一次写盘时**原样写回文件** ✓")
        print("   怎么办：到服务端窗口按 Ctrl+C 停掉，再跑本工具 ✓")
        return 1
    print(f"✓ {PORT} 端口没有服务端在跑（这一步必须先满足 ✓）")

    # ② 列出将要删的东西
    files = [(p, why) for p, why in targets()]
    exist = [(p, why) for p, why in files if os.path.exists(p)]
    print(f"\n将要处理 {len(exist)} 个文件：")
    if not exist:
        print("  （一个都不存在 —— 记忆本来就是空的 ✓）")
    for p, why in exist:
        print(f"  · {os.path.relpath(p, ROOT):<22} {os.path.getsize(p):>9,} B   {why}")

    if not yes:
        print("\n这是演练 ✓ 什么都没有删。要真删就加 --yes：")
        print("  E:\\Python\\python.exe tools\\清空记忆.py --yes")
        print("  （想留个后路：再加 --backup，会先挪到 语音\\_清空备份_<时间>\\ ✓）")
        return 0

    # ③ 真删（可选先备份）
    bak = None
    if backup and exist:
        bak = os.path.join(config.AUDIO_DIR, "_清空备份_" + time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(bak, exist_ok=True)
        print(f"\n先备份到：{bak}")
    for p, _why in exist:
        if bak:
            shutil.move(p, os.path.join(bak, os.path.basename(p)))
        else:
            os.remove(p)
    print("  ✓ 已处理" + ("（挪走了 ✓）" if bak else "（删掉了 ✓）"))

    # ④ 校验：文件真的没了吗（**别只看自己说删了** ✗）
    left = [p for p, _ in files if os.path.exists(p)]
    if left:
        print("  ✗ 还有残留：", [os.path.relpath(p, ROOT) for p in left])
        return 1
    print("✓ 四个文件都已不在 ✓（下次启动就是全新记忆 ✓）")

    print("\n下一步：")
    print("  ① 起服务端：cd E:\\天翼\\code  →  E:\\Python\\python.exe run.py")
    print("  ② 启动横幅里应出现：")
    print("       [记忆] 载入 …\\记忆.db：已知信息 0 条，历史事件 0 条 ✓")
    print("  ③ （可选）把这条写进演示前自检的清单 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
