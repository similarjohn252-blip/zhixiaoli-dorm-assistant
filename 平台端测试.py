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

# ⚠️ 2026-10-18：拒答短语必须写全 ✗ —— 原来只查"查不到"✗，
#   漏了"我这里没有/没查到"✗，结果【实际在拒答、脚本却判 PASS】✗（当天真发生过 ✓）
REFUSAL = ("查不到", "没查到", "我这里没有", "我这儿没有", "没有准数", "没有查到")

# (用例, 问题, 期望的 device_action(None=不校验), 答复里必须出现的词(None=不校验), 是否禁止出现"查不到")
CASES = [
    ("知识库 · 校训", "咱们学校的校训是什么", None, "明德", True),
    ("知识库 · 宿舍管理规定", "宿舍管理有什么要求", None, "电器", True),
    ("知识库 · 手册·留宿", "留宿他人会有什么处理", None, "留宿", True),
    ("宿舍 · 报修登记", "我们宿舍的灯坏了，要报修", "LED_GREEN_ON", None, False),
    ("宿舍 · 报修进度", "宿舍报修什么时候能来人修", "LED_BLUE_ON", None, False),
    ("宿舍 · 违规提醒", "宿舍能用电磁炉吗", "LED_RED_ON", None, False),
    ("宿舍 · 晚归报备", "我今晚晚点回宿舍", "LED_BLUE_ON", None, False),
    # ⭐ v3 新增（2026-10-18）：这些用例守的是"不要一律说不知道"✓
    #    ① 通用知识：v2 会说"我只负责宿舍"✗ → v3 必须正常答 ✓
    ("v3 · 通用知识（算术）", "一加一等于几", "NONE", "二", False),
    #    ② 实时信息：模型不知道/查不到 → 必须如实说没有 ✓ **不许编**✗
    #       （用例名里带「不许编」→ 主循环会额外检查有没有冒具体日期/天气 ✓）
    ("v3 · 实时信息-日期（不许编）", "今天几号", "NONE", None, False),
    ("v3 · 实时信息-天气（不许编）", "今天天气怎么样", "NONE", None, False),
    #    ③ 校园信息但知识库没有 → 说没有 + **给出可走的下一步** ✓（只说"查不到"不算过 ✗）
    # ⚠️ 2026-10-18 改判据 ✗：原先要求出现"辅导员"（因为知识库当时没有晚归规定 ✓）
    #    知识库接上后手册里就有 ✓ 模型直接答规定 ✓ 不提辅导员 ✓ → 旧判据会误判 ✗
    #    新判据 ✓：**不能拒答**（说了"这条我这里没有/查不到"才算失败 ✗）
    ("v3 · 校园问题要真答（不许拒答）", "咱们学校新出的晚归规定怎么算", "NONE", None, True),
    #    ④ 办不了的事 → 明确说办不了 + 指到正确地方 ✓
    ("超范围 · 办不了+指路", "帮我订个会议室", "NONE", "教务处", False),
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
    if forbid_none and any(w in vt for w in REFUSAL):
        # ⚠️ 2026-10-18：判太严也会错 ✗ —— 部分回答 + 承认缺的部分 + 给路子，
        #   正是 v3 要的正确行为 ✓ 只有"整句只是拒绝、关键内容没给"才算失败 ✗
        if not (want_word and want_word in vt):
            problems.append("完全拒答（知识库明明有内容 ✗）")
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
        if forbid_none and any(w in vt for w in REFUSAL):
            # 同上：有拒答措辞但关键内容给了 → 通过 ✓
            if not (want_word and want_word in vt):
                problems.append("完全拒答（知识库明明有内容 ✗）")
        # ⭐ v3：「实时信息不许编」—— 模型不知道日期/天气，冒出来就是编的 ✗
        if "不许编" in name:
            import re as _re
            if _re.search(r"\d{1,2}\s*[月号日]", vt) or any(w in vt for w in ("℃", "气温", "晴", "阴", "下雨")):
                problems.append("答出了具体的日期/天气 —— 这是编的 ✗")

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

# 退出码必须反映成败 ✗ —— 原来恒为 0，靠 $LASTEXITCODE 判断的人/CI 会被误导
# （全量闸门实测踩到：脚本打印"失败 N 项"却退出 0 ✓）
# 用 SystemExit 以免再引入一次 import ✓
raise SystemExit(0 if failed == 0 else 2)
