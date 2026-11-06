"""问一句（演示/排障用）

为什么写它：录制演示或现场排障时，要一条命令看清"问了什么、多久、谁答的、
动作是什么、几号链路"。用 curl 传中文会乱码（本仓库踩过这个坑），
用 Python 就干净。

用法：
    E:\\Python\\python.exe tools\\问一句.py "明天几节课"
    E:\\Python\\python.exe tools\\问一句.py            （不给参数就跑一组预设）
"""

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5000"


def ask(query: str):
    t0 = time.time()
    req = urllib.request.Request(
        BASE + "/v1/chat",
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8")), time.time() - t0


def show(query: str) -> None:
    try:
        got, dt = ask(query)
    except Exception as exc:  # noqa: BLE001 - 演示工具，出错就直说
        print(f"  问：{query}")
        print(f"  ✗ 失败：{type(exc).__name__}: {exc}")
        print("    先确认服务端在跑：cd E:\\天翼\\code && E:\\Python\\python.exe run.py")
        return
    print(f"  问：{query}")
    print(f"  答：{got.get('voice_text') or '（空）'}")
    print(f"  来源：{got.get('source')}    动作：{got.get('device_action')}    "
          f"耗时：{dt:.1f}s    链路号：{got.get('request_id')}")
    timings = got.get("timings") or {}
    if timings:
        parts = "  ".join(f"{k} {v:.1f}s" for k, v in timings.items() if v)
        print(f"  分段：{parts}")


if __name__ == "__main__":
    queries = sys.argv[1:] or [
        "我们宿舍的灯坏了要报修",
        "咱们学校的校训是什么",
        "今天有课吗",
    ]
    for q in queries:
        show(q)
        print()
