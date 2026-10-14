"""诊断二阶：前缀的【位置与形态】怎么影响知识库检索。

一阶结论：`(背景：…)` 放在问题前面会让校训这类知识库问题退化成"查不到"。
本脚本比较几种"问题在前 / 背景在后 / 更短"的写法，找出既保住上下文、又不伤检索的拼法。

用法：E:\\Python\\python.exe tools\\诊断_上下文影响.py --order
"""
import json
import re

import requests
import urllib3

urllib3.disable_warnings()

ENV = dict(re.findall(r"^([A-Z_]+)=(.*)$",
                      open(r"E:\天翼\.env", encoding="utf-8").read(), re.M))
URL = ENV.get("ZHIXIAOLI_CLOUD_URL",
              "https://aiagent.cqut.edu.cn:31134/v1/chat-messages")
KEY = ENV["ZHIXIAOLI_CLOUD_KEY"].strip()

Q = "咱们学校的校训是什么"
FACTS = "用户叫小李；用户住 3 栋 502"
TURNS = "用户说「我们宿舍的灯坏了，要报修」，你答「好的，已经帮你登记报修啦」"

VARIANTS = [
    ("A 当前写法：背景在前（已知会退化）", f"(背景：{FACTS}) ({Q})"),
    ("B 问题在前，背景在后", f"{Q} (背景：{FACTS})"),
    ("C 问题在前，用句号分隔", f"{Q}。补充背景：{FACTS}"),
    ("D 裸问题（对照）", Q),
    ("E 背景+对话都在问题后面", f"{Q} (背景：{FACTS}) (刚才的对话：{TURNS})"),
    ("F 最简背景（只留房间号）", f"{Q} (背景：住 3 栋 502)"),
]

print("=" * 88)
print(f"目标：同一句「{Q}」在各种拼法下，知识库还查得到吗")
print("=" * 88)
for tag, q in VARIANTS:
    try:
        r = requests.post(URL, headers={
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json"}, json={
            "inputs": {"query": q}, "query": q,
            "response_mode": "blocking", "user": "diag2"},
            timeout=45, verify=False)
        raw = r.json().get("answer", "")
        i = raw.rfind("{")
        try:
            j = json.loads(raw[i:raw.rfind("}") + 1])
        except Exception:
            j = {}
        vt = j.get("voice_text", raw[:80])
        hit = ("明德" in vt) or ("校训是" in vt and "查不到" not in vt)
        print(f"\n{tag}")
        print(f"    长度 {len(q)} 字 → {vt[:80]}")
        print(f"    {'✓ 查得到' if hit else '✗ 退化了'}")
    except Exception as e:
        print(f"\n{tag}\n    异常 {type(e).__name__}: {str(e)[:80]}")
