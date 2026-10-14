"""对照实验：到底是什么让平台回"我没听清"。

已知：直连平台 9/9 正常 ✓，但走本地 5000 每个问题都回"我没听清" ✗
本脚本固定"问题文本"，只改 payload 里的一个变量，逐项对照：
  ① 换 user（ptest vs 服务端用的那个）
  ② 带 / 不带 conversation_id
  ③ 多带一个 inputs（模拟服务端可能多塞的字段）

用法：E:\\Python\\python.exe tools\\诊断_云端payload对照.py
"""
import json
import os
import sys

sys.path.insert(0, r"E:\天翼\code")

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()

from server import config  # noqa: E402

Q = "我们宿舍的灯坏了，要报修"


def ask(tag, body):
    try:
        r = requests.post(config.CLOUD_URL,
                          headers={"Authorization": "Bearer " + config.CLOUD_KEY,
                                   "Content-Type": "application/json"},
                          json=body, timeout=60, verify=False)
        raw = r.text
        try:
            ans = r.json().get("answer", "")
        except Exception:
            ans = raw
        i = ans.rfind("{")
        try:
            j = json.loads(ans[i:ans.rfind("}") + 1])
            print(f"  [{tag}] HTTP {r.status_code}  动作={j.get('device_action')}")
            print(f"       回复={j.get('voice_text')}")
        except Exception:
            print(f"  [{tag}] HTTP {r.status_code}")
            print(f"       原始={ans[:100]!r}")
    except Exception as e:
        print(f"  [{tag}] 异常 {type(e).__name__}: {str(e)[:90]}")


print("=== 配置 ===")
print(f"  CLOUD_USER = {config.CLOUD_USER!r}")
print(f"  CLOUD_MULTITURN = {getattr(config, 'CLOUD_MULTITURN', '(无此配置)')}")
print(f"  URL = {config.CLOUD_URL}")
print()
print("=== 对照实验（问题固定为：我们宿舍的灯坏了，要报修）===")

base = {"inputs": {"query": Q}, "query": Q, "response_mode": "blocking"}

ask("① 陌生人 user", {**base, "user": "probe_stranger"})
ask("② 服务端的 user", {**base, "user": config.CLOUD_USER})
ask("③ 服务端 user + 假 conv_id", {**base, "user": config.CLOUD_USER,
                                  "conversation_id": "00000000-0000-0000-0000-000000000000"})
ask("④ 服务端 user + 空 query", {"inputs": {"query": ""}, "query": "",
                                "response_mode": "blocking", "user": config.CLOUD_USER})
