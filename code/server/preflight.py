"""启动前自检（工程债 B5 的尾巴）：配置不对就**立刻退出**，并把话说清楚。

为什么需要：
  演示/交接时最常见的失败是"配置没配好"，而原来的表现是：
    · 缺第一个变量就退出 ✗ → 你补一个、重启、又缺下一个 ✗（来回好几轮）
    · 端口被占用时抛一段 traceback ✗ → 看不出"是旧进程没关" ✗（本项目反复踩 ✓）
  本模块把这些**一次性列全**，每条都给**可以直接照做的处理办法** ✓

用法：
    run.py 启动时自动调用 ✓（有问题就退出，绝不会带病启动）
    也可以单独跑：E:\\Python\\python.exe E:\\天翼\\code\\server\\preflight.py
"""
import os
import socket
import sys

HARD = []   # 必须解决，否则不许启动
SOFT = []   # 只提醒，不影响启动


def _p(path):
    return os.path.abspath(path)


def _root():
    """项目根 E:\\天翼（从本文件往上三层：server → code → 天翼 ✓）"""
    return os.path.dirname(os.path.dirname(os.path.dirname(_p(__file__))))


def check_env_only():
    """第一道自检：只读 .env / 环境变量，**不导入 server 包** ✓

    为什么必须单独有它：`config` 在 import 时就会对缺失的必需变量 fail-fast ✗，
    那样"一次只能报一个" —— 你补一个、重启、又缺下一个 ✗（来回好几轮）
    在这一步拦住，就能**一次列全** ✓
    返回 (是否齐全, 缺失列表)
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_root(), ".env"))
        load_dotenv()
    except Exception:
        pass
    required = ("BAIDU_API_KEY", "BAIDU_SECRET_KEY", "ZHIXIAOLI_CLOUD_KEY")
    missing = [k for k in required if not (os.environ.get(k) or "").strip()]
    print("=" * 74)
    print("启动前自检 · 第一步：必需配置")
    print("=" * 74)
    env = os.path.join(_root(), ".env")
    print(f"  {'.env 位置' if os.path.exists(env) else '.env 不存在'}：{env}")
    if missing:
        print("\n✗ 缺少必需配置（一次列全，省得补一个重启一次 ✓）：")
        for k in missing:
            print("    " + k)
        print("\n  处理：在 E:\\天翼\\.env 里填上（可参考 .env.example ✓）")
        return False, missing
    print("  ✓ 必需密钥齐全（百度 / 平台）")
    return True, []


def check_env_file():
    """① .env 到底加载了哪个？—— "改了配置没生效"十有八九是找错文件 ✗"""
    env = os.path.join(_root(), ".env")
    if os.path.exists(env):
        print(f"  ✓ .env 位置：{env}")
    else:
        SOFT.append(f"没找到 .env（{env}）—— 现在只用系统环境变量；"
                    f"可复制 .env.example 为 .env 再填 ✓")


def check_required(cfg):
    """② 必需变量：**一次性列全**，不要让人补一个重启一次 ✗"""
    required = {
        "BAIDU_API_KEY": cfg.BAIDU_API_KEY,
        "BAIDU_SECRET_KEY": cfg.BAIDU_SECRET_KEY,
        "ZHIXIAOLI_CLOUD_KEY": cfg.CLOUD_KEY,
    }
    missing = [k for k, v in required.items() if not (v or "").strip()]
    if missing:
        HARD.append("缺少必需配置：" + "、".join(missing)
                    + "\n      处理：在 E:\\天翼\\.env 里填上（可参考 .env.example ✓）")
    else:
        print("  ✓ 必需密钥齐全（百度 / 平台）")


def check_values(cfg):
    """③ 值的合法性：写错了要**现在**说，而不是运行时才炸 ✗"""
    bad = []
    if not (1 <= cfg.PORT <= 65535):
        bad.append(f"ZHIXIAOLI_PORT={cfg.PORT} 不是合法端口（1~65535）")
    if cfg.TTS_FORMAT not in ("wav", "mp3"):
        bad.append(f"ZHIXIAOLI_TTS_FORMAT={cfg.TTS_FORMAT} 只能是 wav 或 mp3")
    if cfg.BRAIN not in ("cloud", "local"):
        bad.append(f"ZHIXIAOLI_BRAIN={cfg.BRAIN} 只能是 cloud 或 local")
    if not (1 <= cfg.HISTORY_TURNS <= 20):
        bad.append(f"ZHIXIAOLI_HISTORY_TURNS={cfg.HISTORY_TURNS} 应在 1~20")
    if bad:
        HARD.append("配置值不合法：\n      " + "\n      ".join(bad))
    else:
        print(f"  ✓ 关键配置值合法（端口 {cfg.PORT} / {cfg.TTS_FORMAT} / 大脑 {cfg.BRAIN}）")


def check_paths(cfg):
    """④ 该写的目录能不能写（写不了的话，音频/台账/日志全废 ✗）"""
    for name, path in (("音频目录", cfg.AUDIO_DIR),
                       ("工单台账", cfg.TICKETS_DB),
                       ("链路日志", cfg.LOG_FILE)):
        d = path if os.path.isdir(path) else os.path.dirname(path) or "."
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".preflight_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except Exception as e:
            HARD.append(f"{name}不可写：{path}\n      原因：{e}")
    if not any("不可写" in x for x in HARD):
        print("  ✓ 音频 / 台账 / 日志目录都可写")


def check_port(cfg):
    """⑤ 端口被占 —— 最常见、最好查、但以前只给一段 traceback ✗"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", cfg.PORT))
        print(f"  ✓ 端口 {cfg.PORT} 空闲")
    except OSError:
        HARD.append(
            f"端口 {cfg.PORT} 已被占用 ✗ —— 多半是**上一个服务端还没关**\n"
            f"      查：netstat -ano | findstr \":{cfg.PORT}\"\n"
            f"      处理：结束那个进程（本项目反复踩：日志写着新功能、行为却是旧的 ✓）")
    finally:
        s.close()


def check_public_host(cfg):
    """⑥ 对外地址配得对不对（配错的话：板子/评委拿到的音频地址打不开 ✗）"""
    host = (cfg.PUBLIC_HOST or "").strip()
    if host.startswith("192.168.137."):
        SOFT.append(f"ZHIXIAOLI_PUBLIC_HOST={host} 是**热点网段**地址 → 只在本机开热点时可用 ✓\n"
                    f"      要给别人（评委/队友）访问，改成校园网地址："
                    f"跑 tools\\部署自检.py 看该填什么 ✓")
    else:
        print(f"  ✓ 对外地址：http://{host}:{cfg.PUBLIC_PORT}")


def check_disk():
    """⑦ 磁盘别满（满了会静默变成"没声音" ✗）"""
    try:
        import shutil
        free = shutil.disk_usage("E:\\").free / 1024 / 1024
        if free < 200:
            HARD.append(f"E 盘剩余空间只有 {free:.0f} MB（<200MB）→ 音频写不下")
        else:
            print(f"  ✓ E 盘剩余 {free / 1024:.1f} GB")
    except Exception:
        pass


def run(env_checked=False):
    """跑完整自检，返回 (是否可启动, 硬问题列表, 软提醒列表)。

    env_checked=True：调用方（run.py）已经先做过第一道 check_env_only()，
    这里就不再重复检查/打印必需配置 ✓（否则启动横幅里会出现两遍"必需密钥齐全" ✗）
    """
    sys.path.insert(0, os.path.dirname(os.path.dirname(_p(__file__))))
    from server import config as cfg

    print("=" * 74)
    print("启动前自检（有问题会立刻退出，不带病启动 ✓）")
    print("=" * 74)
    check_env_file()
    if not env_checked:
        check_required(cfg)
    check_values(cfg)
    check_paths(cfg)
    check_port(cfg)
    check_public_host(cfg)
    check_disk()

    if SOFT:
        print("\n提醒（不影响启动）：")
        for s in SOFT:
            print("  ⚠ " + s)
    if HARD:
        print(f"\n✗ 有 {len(HARD)} 个问题必须先解决：")
        for i, h in enumerate(HARD, 1):
            print(f"  {i}. {h}")
        print("\n" + "=" * 74)
        return False, HARD, SOFT
    print("\n✓ 自检通过，开始启动服务端")
    print("=" * 74)
    return True, HARD, SOFT


if __name__ == "__main__":
    ok, _hard, _soft = run()
    sys.exit(0 if ok else 1)
