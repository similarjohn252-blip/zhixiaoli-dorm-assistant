import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")   # 读脚本旁边的 .env，别写死绝对路径

# -*- coding: utf-8 -*-
"""平台端测试：只调用智小理平台的 API（和板子走的那条路完全一致），
不启动任何本地服务、不写任何文件。

跑法：E:\\Python\\python.exe E:\\天翼\\_平台测试.py
"""
import time

import requests
import urllib3

urllib3.disable_warnings()

URL = "https://aiagent.cqut.edu.cn:31134/v1/chat-messages"
H = {"Authorization": "Bearer " + os.environ["ZHIXIAOLI_CLOUD_KEY"],
     "Content-Type": "application/json"}

# (用例, 问题, 期望的 device_action(None=不校验), 答复里必须出现的词(None=不校验), 是否禁止出现"查不到")
CASES = [
    ("知识库 · 校训", "咱们学校的校训是什么", None, "明德", True),
    ("知识库 · 宿舍管理规定", "宿舍管理有什么要求", None, "电器", True),
    ("知识库 · 手册·留宿", "留宿他人会有什么处理", None, "留宿", True),
    ("宿舍 · 报修登记", "我们宿舍的灯坏了，要报修", "LED_GREEN_ON", None, False),
    ("宿舍 · 报修进度", "宿舍报修什么时候能来人修", "LED_BLUE_ON", None, False),
    ("宿舍 · 违规提醒", "宿舍能用电磁炉吗", "LED_RED_ON", None, False),
    ("宿舍 · 晚归报备", "我今晚晚点回宿舍", "LED_BLUE_ON", None, False),
    ("超范围 · 引导", "帮我订个会议室", "NONE", None, False),
    ("闲聊 · 问候", "你好呀", "NONE", None, False),
]


def jparse(ans):
    i = ans.rfind("{")
    if i < 0:
        return {}
    try:
        import json
        return json.loads(ans[i:ans.rfind("}") + 1])
    except Exception:
        return {}


def looks_broken(raw):
    """平台"没把用户输入传给模型"的特征。"""
    low = raw.lower()
    return "null" in low and ("user input is" in low or "user message is" in low)


print("=" * 96)
print("平台端测试（走 API，与板子同一条路）")
print("=" * 96)
def run_case(q, want_action, want_word, forbid_none):
    """跑一次用例，返回 (问题列表, voice_text, device_action, HTTP码, 耗时)。"""
    t0 = time.time()
    r = requests.post(URL, headers=H, json={
        "inputs": {"query": q}, "query": q,
        "response_mode": "blocking", "user": "ptest"},
        timeout=45, verify=False)
    raw = r.json().get("answer", "") if r.status_code == 200 else r.text
    j = jparse(raw)
    vt, act = j.get("voice_text", ""), j.get("device_action", "")
    problems = []
    if looks_broken(raw):
        problems.append("模型收到的是 null（提示词变量没取到值）")
    if not vt:
        problems.append("没解析出 voice_text")
    if want_action and act != want_action:
        problems.append(f"动作期望 {want_action}，实际 {act}")
    if want_word and want_word not in vt:
        problems.append(f"答复里没有“{want_word}”")
    if forbid_none and "查不到" in vt:
        problems.append("答了“查不到”（知识库没检索到）")
    return problems, vt, act, r.status_code, time.time() - t0


passed, failed, flaky = 0, 0, 0
for name, q, want_action, want_word, forbid_none in CASES:
    t0 = time.time()
    try:
        r = requests.post(URL, headers=H, json={
            "inputs": {"query": q}, "query": q,
            "response_mode": "blocking", "user": "ptest"},
            timeout=45, verify=False)
        raw = r.json().get("answer", "") if r.status_code == 200 else r.text
        j = jparse(raw)
        vt, act = j.get("voice_text", ""), j.get("device_action", "")
        dt = time.time() - t0

        problems = []
        if looks_broken(raw):
            problems.append("模型收到的是 null（提示词变量没取到值）")
        if not vt:
            problems.append("没解析出 voice_text")
        if want_action and act != want_action:
            problems.append(f"动作期望 {want_action}，实际 {act}")
        if want_word and want_word not in vt:
            problems.append(f"答复里没有“{want_word}”")
        if forbid_none and "查不到" in vt:
            problems.append("答了“查不到”（知识库没检索到）")

        was_flaky = False
        if problems:
            # 平台侧的模型与检索都有随机性 → 失败先重试一次，但把"偶发"**显式标注**出来：
            # 既不掩盖问题（同一句反复偶发就该加固知识库），也不让偶发红灯干扰判断。
            # 判据：重试才过 = 偶发；两次都挂同一处 = 必现，必须改提示词或知识库。
            time.sleep(1.0)
            problems, vt, act, code, dt = run_case(q, want_action, want_word,
                                                   forbid_none)
            was_flaky = True
            if not problems:
                flaky += 1

        ok = not problems
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        tag = "PASS（偶发：第 2 次才过）" if (ok and was_flaky) else ("PASS" if ok else "FAIL")
        print(f"\n[{tag}] {name}    HTTP {r.status_code}  {dt:.1f}s")
        print(f"        问: {q}")
        print(f"        答: {vt[:110] if vt else raw[:110]}")
        print(f"        动作: {act}")
        for p in problems:
            print(f"        ⚠ {p}")
    except Exception as e:
        failed += 1
        print(f"\n[FAIL] {name}    异常 {type(e).__name__}: {str(e)[:110]}")

print()
print("=" * 96)
print(f"平台端汇总：通过 {passed} 项，失败 {failed} 项，其中偶发 {flaky} 项（重试才过）")
if flaky:
    print("提示：偶发＝平台侧检索/模型有随机性。同一句反复偶发，就该加固知识库（补文件 / 调 TopK）")
print("=" * 96)
