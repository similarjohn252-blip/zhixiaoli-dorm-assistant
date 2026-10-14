"""知识库稳定性探针：同一个问题连问 N 次，数命中率。

为什么需要它：
  平台侧的知识库检索有随机性。偶尔失败 = 可接受；**同一个问题反复失败** = 说明那条知识
  "处在临界状态"（命中分数贴着阈值），这时该做的是加固知识库（补文件 / 调 TopK / 降阈值），
  而不是反复重试或放宽断言 ✗。

用法：
    E:\\Python\\python.exe tools\\测_知识库稳定性.py                # 默认测 3 条各 5 次
    E:\\Python\\python.exe tools\\测_知识库稳定性.py "校训是什么" 8  # 自定义问题与次数
"""
import json
import re
import sys
import time

import requests
import urllib3

urllib3.disable_warnings()

ENV = dict(re.findall(r"^([A-Z_]+)=(.*)$",
                      open(r"E:\天翼\.env", encoding="utf-8").read(), re.M))
URL = ENV.get("ZHIXIAOLI_CLOUD_URL",
              "https://aiagent.cqut.edu.cn:31134/v1/chat-messages")
KEY = ENV["ZHIXIAOLI_CLOUD_KEY"].strip()

# 默认测三条：两条"应该稳"的做对照，一条"怀疑临界"的
CASES = [
    ("咱们学校的校训是什么", "明德"),          # 怀疑临界（测试里反复失败）
    ("宿舍管理有什么要求", "电器"),            # 对照：应稳
    ("留宿他人会有什么处理", "留宿"),          # 对照：应稳
]

if len(sys.argv) >= 2:
    CASES = [(sys.argv[1], None)]
TIMES = int(sys.argv[2]) if len(sys.argv) >= 3 else 5


def ask(q):
    r = requests.post(URL, headers={
        "Authorization": "Bearer " + KEY,
        "Content-Type": "application/json"}, json={
        "inputs": {"query": q}, "query": q,
        "response_mode": "blocking", "user": "probe"},
        timeout=45, verify=False)
    raw = r.json().get("answer", "")
    i = raw.rfind("{")
    try:
        return json.loads(raw[i:raw.rfind("}") + 1]).get("voice_text", "")
    except Exception:
        return raw[:60]


print("=" * 84)
print(f"知识库稳定性：每条问题连问 {TIMES} 次")
print("=" * 84)
verdict = []
for q, word in CASES:
    ok = 0
    bad = 0
    for _ in range(TIMES):
        try:
            vt = ask(q)
        except Exception as e:
            vt = f"异常 {type(e).__name__}"
        miss = ("查不到" in vt) or (word is not None and word not in vt)
        if miss:
            bad += 1
        else:
            ok += 1
        time.sleep(0.3)
    rate = bad / max(ok + bad, 1)
    if rate == 0:
        tag = "稳定 ✓"
    elif rate < 0.5:
        tag = "偶发 ⚠"
    else:
        tag = "**反复失败 → 该加固知识库** ✗"
    print(f"\n  {q}")
    print(f"    命中 {ok} / 失败 {bad}   → {tag}")
    verdict.append((q, bad, ok))

print()
print("=" * 84)
total_bad = sum(v[1] for v in verdict)
total = sum(v[1] + v[2] for v in verdict)
print(f"总计：命中 {total - total_bad} / 失败 {total_bad}（共 {total} 次）")
if total_bad == 0:
    print("结论：都稳 —— 测试里那次失败属平台随机性，记录即可 ✓")
else:
    worst = sorted(verdict, key=lambda v: -v[1])[0]
    print(f"结论：'{worst[0]}' 失败 {worst[1]} 次 —— 若反复如此，"
          f"请确认对应知识文件已上传 + TopK 调到 5、阈值 0.15 ✓")
print("=" * 84)
