"""清空工单（2026-10-18 新增 ✓）

用法 ✓：
  E:\\Python\\python.exe tools\\清空工单.py                    # 只预览（列出当前工单 ✓ 一个都不删 ✓）
  E:\\Python\\python.exe tools\\清空工单.py --yes               # 真的删 ✓
  E:\\Python\\python.exe tools\\清空工单.py --yes --backup      # 删之前先备份 ✓

⚠️ 服务端在跑时不能删 ✗（SQLite 被占用 ✓）→ 先关服务端 ✓ 删完再启动 ✓
⭐ 为什么要这个 ✓：实测 39 例测试建了 6 张真实工单（BX20261018-0002~0006）✗
   演示时评委看到一堆"测试工单"会显得不真实 ✓ 上台前清一次 ✓
"""
import os
import shutil
import socket
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "code", "工单.db")
PORT = 5000
BACKUP_DIR = os.path.join(ROOT, "语音", "_清空备份_" + time.strftime("%Y%m%d_%H%M%S"))


def server_running() -> bool:
    """5000 端口有没有在听 ✓（在听 = 服务端占用着数据库 ✗）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def targets() -> list:
    """要删的文件 ✓（db + 两个 WAL 附属文件 ✓ 少删一个都可能残留 ✓）"""
    out = []
    for suffix in ("", "-shm", "-wal"):
        p = DB + suffix
        if os.path.exists(p):
            out.append(p)
    return out


def preview() -> int:
    """打印当前工单 ✓ 返回条数 ✓"""
    if not os.path.exists(DB):
        print("  工单库还不存在 ✓（说明还没建过单 ✓ 不用清 ✓）")
        return 0
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
        # ⚠️ 列名必须按真实表来 ✓ —— 我第一版猜了个 created_at ✗ 表里叫 created ✓
        #    （教训：*别猜列名，先 PRAGMA table_info* ✓ 今天又栽了一次 ✗）
        rows = c.execute(
            "SELECT id, COALESCE(room,''), COALESCE(category,''), COALESCE(status,''), "
            "COALESCE(created,'') FROM tickets ORDER BY rowid DESC").fetchall()
        c.close()
    except Exception as e:
        print(f"  ✗ 读不出来：{type(e).__name__}: {e}")
        print("     （服务端在跑时数据库被锁 ✓ 先关服务端再试 ✓）")
        return -1
    if not rows:
        print("  当前工单：0 条 ✓（很干净 ✓）")
        return 0
    print(f"  当前工单：{len(rows)} 条 ✓")
    print("  " + "-" * 72)
    for tid, room, cat, st, at in rows[:30]:
        print(f"   {tid:<22} {room:<10} {cat:<8} {st:<8} {at}")
    if len(rows) > 30:
        print(f"   …… 还有 {len(rows) - 30} 条")
    print("  " + "-" * 72)
    return len(rows)


def main():
    yes = "--yes" in sys.argv
    backup = "--backup" in sys.argv

    print("=" * 76)
    print("清空工单")
    print("=" * 76)

    n = preview()

    if server_running():
        print("\n  ✗ 服务端正在运行（5000 端口在听）✗")
        print("     → 先关掉服务端 ✓ 再跑这个 ✓ 删完再启动 ✓")
        if yes:
            print("\n  ✗ 因为服务端在跑，这次不删任何东西 ✓")
            raise SystemExit(1)
        else:
            print("     （预览模式，本来也不会删 ✓）")

    if not yes:
        print("\n  这是【预览模式】✓ 什么都没删 ✓")
        print("  真要删：加 --yes ✓（想先备份就再加 --backup ✓）")
        return

    if server_running():
        raise SystemExit(1)

    files = targets()
    if not files:
        print("\n  没有可删的文件 ✓（工单库不存在 ✓）")
        return

    if backup and n > 0:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        for p in files:
            shutil.move(p, os.path.join(BACKUP_DIR, os.path.basename(p)))
        print(f"\n  ✓ 已备份到：{BACKUP_DIR}")
    else:
        for p in files:
            try:
                os.remove(p)
            except Exception as e:
                print(f"  ✗ 删不掉 {os.path.basename(p)}：{type(e).__name__}: {e}")
                raise SystemExit(1)
        print(f"\n  ✓ 已删除 {len(files)} 个文件（工单.db / -shm / -wal）")

    # 自验 ✓
    left = targets()
    if left:
        print(f"  ✗ 还有残留：{[os.path.basename(x) for x in left]}")
        raise SystemExit(1)
    print("  ✓ 自验通过：工单库已清空 ✓（服务端下次启动会自动重建空库 ✓）")


if __name__ == "__main__":
    main()
