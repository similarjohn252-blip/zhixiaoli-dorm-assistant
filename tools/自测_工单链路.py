"""自测：报修与进度的回答（2026-10-22 改写）

⚠️ 原来这个脚本叫「自测_工单链路」，测的是"报修真的生成工单号、写进台账"。
   2026-10-19 起改成了指路话术 —— 不再替你建单，只告诉你去哪办、怎么办。
   所以原来那套断言（回答带 BX 开头的工单号、动作为绿灯、状态是已受理）全部不再成立，
   全量闸门从 10-19 起一直是红的，只是没人去看。

现在测的是改完之后的行为：
  ① 长期记忆：自然说法也要记住住哪
  ② 报修 → 指路话术，且**不**建工单
  ③ 问进度 → 指路话术，且**不**建工单（这是当年的回归护栏，仍然值得守）
  ④ 两句都是绿灯动作（受理=绿）

用法：E:\\Python\\python.exe tools\\自测_工单链路.py
"""
import os
import sys
import tempfile

# 用临时库/临时记忆，绝不碰真实数据 ✓（必须在 import server 之前设好）
_TMP = tempfile.gettempdir()
os.environ["ZHIXIAOLI_TICKETS_DB"] = os.path.join(_TMP, "自测_报修.db")
os.environ["ZHIXIAOLI_MEMORY_FILE"] = os.path.join(_TMP, "自测_报修记忆.json")
for _suf in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["ZHIXIAOLI_TICKETS_DB"] + _suf)
    except Exception:
        pass
try:
    os.remove(os.environ["ZHIXIAOLI_MEMORY_FILE"])
except Exception:
    pass

sys.path.insert(0, r"E:\天翼\code")

from server import brain, tickets  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}{('  —— ' + detail) if detail else ''}")
    if not cond:
        fails.append(name)


print("=== 1) 长期记忆：自然说法也要记住住哪 ===")
brain.handle_query("我叫小李，我住 3 栋 502")
check("记住房间号", "3 栋 502" in " ".join(brain.memory._memory["facts"]),
      f"facts={brain.memory._memory['facts']}")

print("\n=== 2) 报修 → 指路话术（**不**建工单）===")
n_before = len(tickets.latest(50))
r1 = brain.handle_query("我们宿舍的灯坏了，要报修")
n_after = len(tickets.latest(50))
print(f"    回复：{r1['voice_text']}")
print(f"    动作={r1['device_action']}  来源={r1['source']}")
check("说了去哪办（知行理工 + 后勤报修）",
      "知行理工" in r1["voice_text"] and "后勤报修" in r1["voice_text"],
      r1["voice_text"][:60])
check("不再给工单号（改指路之后不该有）", "BX" not in r1["voice_text"],
      r1["voice_text"][:60])
check("没新建工单（2026-10-19 起的约定）", n_after == n_before,
      f"{n_before} → {n_after}")
check("动作是绿灯（受理/指路成功）", r1["device_action"] == "LED_GREEN_ON")
check("来源标成本地固定话术", r1["source"] == "本地固定话术", r1["source"])

print("\n=== 3) 问进度 → 指路话术，也不建单 ===")
n_before = len(tickets.latest(50))
r2 = brain.handle_query("我报修的进度怎么样了")
n_after = len(tickets.latest(50))
print(f"    回复：{r2['voice_text']}")
check("说了进度去哪看", "知行理工" in r2["voice_text"] and "进度" in r2["voice_text"],
      r2["voice_text"][:60])
check("含『报修』的问进度也不建单（回归护栏）", n_after == n_before,
      f"{n_before} → {n_after}")

print("\n=== 4) 台账接口本身还在（代码保留，只是不再自动触发）===")
check("tickets.newest() 能调用", tickets.newest() is None)

print()
if fails:
    print(f"✗ 失败 {len(fails)} 项：" + "、".join(fails))
    sys.exit(2)
print("✓ 全部通过（报修 = 指路话术，不替你建单）")
