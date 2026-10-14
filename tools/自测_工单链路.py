"""自测：报修工单链路（C1 —— 把"假装办事"换成"真产出"）。

为什么要有它：这条链路里有两个**很容易复发**的坑，靠人记不住 ✓
  ① 房间号要能从**自然说法**里记下来（"我住 3 栋 502"，不带"记住"）——否则工单房间是空的
  ② **问进度不能建新单** ✗ ——"我报修的进度怎么样了"里含"报修"两个字，
     如果报修分支排在前面，会凭空多建一张垃圾工单（烟测实测踩到 ✓）

用法：E:\\Python\\python.exe tools\\自测_工单链路.py
"""
import os
import sys
import tempfile

# 用临时库/临时记忆，绝不碰真实数据 ✓（必须在 import server 之前设好）
_TMP = tempfile.gettempdir()
os.environ["ZHIXIAOLI_TICKETS_DB"] = os.path.join(_TMP, "自测_工单.db")
os.environ["ZHIXIAOLI_MEMORY_FILE"] = os.path.join(_TMP, "自测_工单记忆.json")
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
r0 = brain.handle_query("我叫小李，我住 3 栋 502")
check("记住房间号", "3 栋 502" in " ".join(brain.memory._memory["facts"]),
      f"facts={brain.memory._memory['facts']}")

print("\n=== 2) 报修 → 真的生成工单，并写进回答 ===")
r1 = brain.handle_query("我们宿舍的灯坏了，要报修")
print(f"    回复：{r1['voice_text']}")
print(f"    动作={r1['device_action']}  来源={r1['source']}")
t1 = tickets.newest()
check("回答里带真实工单号", "BX" in r1["voice_text"], r1["voice_text"][:60])
check("动作为绿灯（已受理）", r1["device_action"] == "LED_GREEN_ON")
check("状态是已受理", "已受理" in r1["voice_text"])
check("类别认成照明", t1["category"] == "照明", str(t1["category"]))
check("房间号来自长期记忆", t1["room"] == "3 栋 502", str(t1["room"]))

print("\n=== 3) 问进度 → 查台账（且**不能**新建工单 ✗）===")
n_before = len(tickets.latest(50))
r2 = brain.handle_query("那明天能来人修吗")
n_after = len(tickets.latest(50))
print(f"    回复：{r2['voice_text']}")
check("查到了同一张单", t1["id"] in r2["voice_text"], r2["voice_text"][:60])
check("没新建垃圾工单（回归护栏）", n_after == n_before,
      f"{n_before} → {n_after}")

n_before = len(tickets.latest(50))
r3 = brain.handle_query("我报修的进度怎么样了")
n_after = len(tickets.latest(50))
print(f"    回复：{r3['voice_text']}")
check("含'报修'的问进度也不新建单（回归护栏）", n_after == n_before,
      f"{n_before} → {n_after}")

print("\n=== 4) 改状态 → 动作与文案跟着变 ===")
check("改状态成功", tickets.set_status(t1["id"], "doing"))
r4 = brain.handle_query("我报修的进度怎么样了")
print(f"    回复：{r4['voice_text']}  动作={r4['device_action']}")
check("文案显示处理中", "处理中" in r4["voice_text"])
check("动作变蓝灯", r4["device_action"] == "LED_BLUE_ON")
check("状态映射：驳回=红灯",
      tickets.action_for("rejected") == "LED_RED_ON")

print("\n=== 5) 统计 ===")
print(f"    {tickets.stats()}")

print()
if fails:
    print(f"✗ 失败 {len(fails)} 项：" + "、".join(fails))
    sys.exit(2)
print("✓ 全部通过（报修 = 真工单，不是话术）")
