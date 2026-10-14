"""打出服务端发给平台的"真实查询文本"，并对照平台原样回答。

背景：直连平台 9/9 正常 ✓，但走本地 5000 时每个问题都回"我没听清" ✗
→ 说明本地服务端发出去的文本有问题。本脚本在进程内复现"服务端要发的东西"，
   再把它原样发给平台，一次分清是"文本坏了"还是"服务端进程的问题"。

用法：E:\\Python\\python.exe tools\\诊断_云端查询文本.py
"""
import json
import os
import sys

os.environ["ZHIXIAOLI_MEMORY_FILE"] = os.path.join(
    os.environ.get("TEMP", "."), "查询文本诊断_记忆.json")
sys.path.insert(0, r"E:\天翼\code")

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()

from server import brain, memory  # noqa: E402

QS = ["我们宿舍的灯坏了，要报修", "咱们学校的校训是什么", "放一首周杰伦的歌"]

print("=== 1) build_query 的输出（这就是要发给平台的文本）===")
for q in QS:
    try:
        out = memory.build_query(q)
    except Exception as e:
        out = f"<异常 {type(e).__name__}: {e}>"
    print(f"  输入: {q!r}")
    print(f"  发出: {out!r}")
    print(f"  长度: {len(out)}")
print()
print("=== 2) 记忆状态（会不会把背景拼进去）===")
print(f"  facts   = {memory._memory.get('facts')}")
print(f"  history = {len(memory._history)} 轮")
print()
print("=== 3) 把 'build_query 的输出' 原样发给平台，看平台怎么答 ===")
url = os.environ.get("ZHIXIAOLI_CLOUD_URL") or None
if not url:
    from server import config
    url = config.CLOUD_URL
from server import config  # noqa: E402
key = config.CLOUD_KEY          # 注意：是 CLOUD_KEY（不是 CLOUD_API_KEY）
for q in QS:
    text = memory.build_query(q)
    try:
        r = requests.post(url, headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json"},
                          json={"inputs": {"query": text}, "query": text,
                                "response_mode": "blocking", "user": config.CLOUD_USER},
                          timeout=60, verify=False)
        raw = r.json().get("answer", "")
        i = raw.rfind("{")
        try:
            j = json.loads(raw[i:raw.rfind("}") + 1])
            print(f"  {q}")
            print(f"    → 动作={j.get('device_action')} 回复={j.get('voice_text')}")
        except Exception:
            print(f"  {q}\n    → 原始返回: {raw[:120]}")
    except Exception as e:
        print(f"  {q}\n    → 请求异常 {type(e).__name__}: {str(e)[:100]}")
