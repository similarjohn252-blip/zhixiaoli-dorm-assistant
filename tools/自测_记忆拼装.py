"""自测：记忆拼装（build_query）的"按需注入"行为。

为什么单独测它：实测（tools\\诊断_上下文影响.py，3 轮 × 6 变体）——
给知识库问题拼长前缀，会让校训这类"临界"条目的检索随机失准 ✗
所以规则定为"**问题自己能说清，就不拼上下文**"。

这条规则很容易在后续改动中被无意破坏，用几行断言钉住它，
比每次跑端到端快得多（这是"契约测试"的最小形态）。

用法：E:\\Python\\python.exe tools\\自测_记忆拼装.py
"""
import sys

sys.path.insert(0, r"E:\天翼\code")

from server import memory  # noqa: E402


def check(q, should_inject):
    memory._memory["facts"] = ["用户叫小李", "用户住 3 栋 502"]
    memory._history[:] = [("我们宿舍的灯坏了", "好的，已经帮你登记报修啦")]
    out = memory.build_query(q)
    injected = out.strip() != q.strip()
    good = injected == should_inject
    print(f"  {'✓' if good else '✗'} {'注入' if injected else '不注入':4s} ← {q}")
    if injected:
        print(f"          实际发送: {out}")
    return good


print("=== build_query 按需注入自测 ===")
ok = True
ok &= check("咱们学校的校训是什么", False)        # 知识库类：不拼 → 检索最稳
ok &= check("宿舍能用电磁炉吗", False)            # 8 字，自己说得清 → 不该注入
ok &= check("我今晚晚点回宿舍", False)            # 8 字，同上（阈值边界用例）
ok &= check("我们宿舍的灯坏了，要报修", False)     # 自洽的完整句子
ok &= check("那明天能来人修吗", True)             # 指代词 → 必须拼上下文
ok &= check("还有呢", True)                       # 太短 → 必须拼上下文
print()
print("全部通过 ✓" if ok else "有断言失败 ✗")
sys.exit(0 if ok else 2)
