"""部署自检：判断"能不能给评委用"，并指出差在哪一步。

为什么需要它（工程债 C3）：把服务端从"我们自己的热点"搬到"评委能访问的地方"，
有 4 个**很容易漏**的点，每个都能让演示当场失败，而且**看代码看不出来** ✗
  ① 服务端只监听 127.0.0.1（外面连不上）
  ② `ZHIXIAOLI_PUBLIC_HOST` 还是热点地址 → 板子/评委拿到的 audio_url 打不开 ✗
  ③ Windows 防火墙没放行端口 → 本机能通、外面不通 ✗
  ④ 没配 token 就对外 → 谁能调都在动你的硬件与记录 ✗（工程债 D4 的硬约束）

用法：E:\\Python\\python.exe tools\\部署自检.py
（不依赖板子：板子不在时只会把那一项标成"跳过" ✓）
"""
import json
import socket
import sys

import requests

sys.path.insert(0, r"E:\天翼\code")

from server import config  # noqa: E402

OK, WARN, BAD, SKIP = "✓", "⚠", "✗", "—"
issues = []


def local_ips():
    """本机所有可能的 IP：主出口地址 + 主机名解析出来的。"""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # UDP connect 不发包，只为问出默认出口 IP
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


print("=" * 74)
print("智小理服务端 · 部署自检")
print("=" * 74)

# ---------- 1) 本机地址 ----------
ips = local_ips()
print(f"\n[1] 本机可用地址：{', '.join(ips) if ips else '（没查到）'}")
for ip in ips:
    kind = "热点网段（只有同一热点内的设备能用）" if ip.startswith("192.168.137.") \
        else ("校园网/局域网（评委若在同一校园网，可能可达 ✓）" if ip.startswith("10.")
              else "其它")
    print(f"      {ip:<16} {kind}")

# ---------- 2) 服务端在跑吗 ----------
url = f"http://127.0.0.1:{config.PORT}"
health = None
try:
    r = requests.get(url + "/v1/health", timeout=6)
    if r.status_code == 404:
        # 这一条专治本项目的常见病：改完代码忘了重启，外面完全看不出来 ✗
        print(f"\n[2] {BAD} 服务端在跑，但**没有 /v1/health** → 它跑的是**旧代码** ✗\n"
              f"        处理：到跑 Flask 的那个窗口按 Ctrl+C，再 `E:\\Python\\python.exe run.py`")
        issues.append("服务端是旧进程（缺 /v1/health）→ 重启")
    else:
        health = r.json()
        print(f"\n[2] {OK} 服务端在跑：{url}  版本={health.get('version')} "
              f"启动={health.get('started_at')} 已运行 {health.get('uptime_s')}s")
except Exception as e:
    print(f"\n[2] {BAD} 连不上本机服务端（{url}）—— 先启动："
          f"cd E:\\天翼\\code && E:\\Python\\python.exe run.py")
    print(f"      原因：{type(e).__name__}: {str(e)[:80]}")
    issues.append("服务端没在跑")

# ---------- 3) 对外地址是否配对（最容易漏 ✗）----------
pub = config.PUBLIC_HOST
print(f"\n[3] ZHIXIAOLI_PUBLIC_HOST = {pub}")
if pub in ips:
    if pub.startswith("192.168.137."):
        print(f"    {WARN} 这是**热点网段**地址 → 只在「我们自己开热点」时正确 ✓\n"
              f"        评委若从校园网访问，拿到的 audio_url（http://{pub}:{config.PUBLIC_PORT}\n"
              f"        /audio/…）会**打不开** ✗ → 对外演示前要改成校园网可达的地址")
        issues.append("PUBLIC_HOST 是热点地址，对外演示前必须改")
    else:
        print(f"    {OK} 与某个本机地址一致，看起来对外可用 ✓")
else:
    print(f"    {BAD} 它不在本机地址列表里 → 板子/评委拿到的音频地址一定打不开 ✗\n"
          f"        候选：{', '.join(ips)}")
    issues.append("PUBLIC_HOST 与本机地址不一致")

# ---------- 4) 鉴权（对外必须开）----------
print(f"\n[4] 鉴权 ZHIXIAOLI_API_TOKEN = {'已配置 ✓' if config.API_TOKEN else '未配置 ✗'}")
if config.API_TOKEN:
    print(f"    {OK} `/v1/device/*` 与 `/v1/memory*` 需要 Bearer token ✓"
          f"（`/v1/chat`、`/v1/voice`、`/audio` 仍开放，板子与评委网页要用 ✓）")
else:
    print(f"    {WARN} 现在是局域网演示状态 ✓ —— **对外部署前必须配一个随机串** ✓\n"
          f"        （工程债 D4 的硬约束：不能裸奔到校园网/公网）")
    issues.append("对外部署前需要配 token")

# ---------- 5) 板子（可选）----------
try:
    r = requests.get(f"http://{config.BOARD_HOST}/status", timeout=3).json()
    print(f"\n[5] {OK} 板子在线：{config.BOARD_HOST} → {r}")
except Exception as e:
    print(f"\n[5] {SKIP} 板子没连上（{config.BOARD_HOST}）：{type(e).__name__}\n"
          f"        —— 板子不在身边/热点没开时属正常，不影响其它检查 ✓")
    if health:
        print(f"        （服务端记录的板子状态：{(health.get('checks') or {}).get('board')}）")

# ---------- 6) 服务端自检（磁盘/记忆/云端）----------
if health:
    print("\n[6] 服务端自检（来自 /v1/health）：")
    for k, v in (health.get("checks") or {}).items():
        print(f"      {'✓' if v.get('ok') else '✗'} {k:<8} {json.dumps(v, ensure_ascii=False)[:90]}")
    if health.get("failing"):
        issues.append("服务端自检未过：" + ",".join(health["failing"]))

# ---------- 7) 防火墙提示（要管理员权限，这里只给命令）----------
port = config.PORT
print("\n[7] Windows 防火墙：本机通了不代表外面能通 ✗")
print(f"    若评委从别的机器连不上，用**管理员** PowerShell 放行 {port} 端口：")
print(f'      netsh advfirewall firewall add rule name="zhixiaoli-{port}" '
      f'dir=in action=allow protocol=TCP localport={port}')
print(f"    （删除规则：netsh advfirewall firewall delete rule name=\"zhixiaoli-{port}\"）")

# ---------- 结论 ----------
print("\n" + "=" * 74)
if not issues:
    print("结论：看起来可以对外演示 ✓")
else:
    print(f"结论：还有 {len(issues)} 项要先处理 ✗")
    for i, s in enumerate(issues, 1):
        print(f"     {i}. {s}")
    print("\n  （其中「token」与「PUBLIC_HOST」两项，决定评委那边能不能真的用 ✓）")
print("=" * 74)
