"""服务端包：配置 / 记忆 / 语音 / 大脑 / 音乐 / 路由。"""

# ⚠️ 2026-10-19 加 ✗：**把 stdout / stderr 强制成 UTF-8**
#
# 来历 ✓（这是一个真崩，不是测试问题 ✗）：
#   钩子里的 pytest 突然红了两条 ✗，报的是
#       UnicodeEncodeError: 'gbk' codec can't encode character '\u2717'
#   原因 ✓：子进程的 stdout 是**管道** ✗ → Python 按 Windows 默认的 **GBK** 编码 ✗
#          而我们满屏的 print("…✗…") 里那个 ✗ 编不出来 → 进程直接崩 ✗✗
#   为什么平时没事 ✓：我手动跑时每命令都设了 PYTHONIOENCODING=utf-8 ✓ 钩子里没设 ✗
#
# ⭐ 为什么必须修在包初始化里（而不是只改测试 ✓）：
#   服务端的 stdout 一旦被**重定向**（写日志文件 / 无窗口启动 / 注册成服务 ✓）
#   就是一模一样的崩 ✗ → 而 `server` 包的任何模块被 import 都会先走这里 ✓
#   → 一处修好，全部受益 ✓
import sys as _sys

for _name in ("stdout", "stderr"):
    _s = getattr(_sys, _name, None)
    if _s is None:
        continue
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # 编不出来就替换，不许崩 ✗
    except Exception:
        pass   # 某些环境（已被重定向成奇怪对象）不支持 reconfigure ✓ 那就保持原样 ✓
