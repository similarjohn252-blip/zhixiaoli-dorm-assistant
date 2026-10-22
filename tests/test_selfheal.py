"""失准自愈（2026-10-18 新增 ✓）—— 第一次答不上来 → 用【裸问题】再问一次 ✓

为什么需要 ✗：
  知识库检索实测约 **13% 随机失准** ✓，而我们测出原因之一是
  【把背景/历史拼进 query 会稀释检索】✗（5 条事实时，"校训"这种临界条目会被顶出阈值 ✓）
→ 所以：**注入过上下文时**答不上来才重试 ✓；纯问题不重试（省 4~5 秒 ✓）；最多重试一次 ✗
"""
from server import brain, memory


def _patch(monkeypatch, replies):
    """让 ask_cloud 按顺序返回 replies ✓ 并记录每次收到的 query（用来断言"第二次是裸问题" ✓）

    ⚠️ 必须先强制 **云端分支** ✗：单测环境里 `BRAIN=local`（conftest 为了让测试不联网 ✓）
       → 否则 `handle_query` 直接走 `local_reply` ✓ 平台根本不会被调用 ✓（我第一版就栽在这 ✗：
         4 条测试全挂，`seen` 是空的 ✓ 而回复正好是 local_reply 的默认话术 ✓✓）
    """
    seen = []
    monkeypatch.setattr(brain, "BRAIN", "cloud")      # ← 关键 ✓
    # ⚠️ 还要清掉【短期记忆】✗：`_finish()` 会调 `remember_turn()` ✓ →
    #    前一条测试记下的对话会留在 `memory._history` 里 ✓ →
    #    于是这条测试里 `build_query` 会**注入历史**✗ → 就触发了重问 ✓
    #    （我第一版就栽在这：第 3 条测试断言"不该重试"却重试了 ✓ 典型的测试间串味 ✓）
    memory._history.clear()

    def fake(q):
        seen.append(q)
        i = len(seen) - 1
        return replies[i] if i < len(replies) else ("我在呢，有什么可以帮你的吗？", "NONE")

    monkeypatch.setattr(brain, "ask_cloud", fake)
    monkeypatch.setattr(brain, "make_tts", lambda t: "")
    return seen


def test_selfheal_retries_with_bare_question(monkeypatch):
    """⭐ 第一次带上下文答不上来 → 第二次必须是【裸问题】✓ 而且用第二次的回答 ✓"""
    memory._memory["facts"] = [
        {"id": 1, "ts": "", "text": "用户住 3 栋 502", "kind": "private"},
        {"id": 2, "ts": "", "text": "用户叫小李", "kind": "profile"},
    ]
    memory._memory["events"] = []
    seen = _patch(monkeypatch, [("这个我暂时查不到", "NONE"),
                                ("咱们学校的校训是明德笃行、自强日新。", "NONE")])

    r = brain.handle_query("校训是什么")

    assert len(seen) == 2, f"应该重问一次 ✓ 实际问了 {len(seen)} 次"
    assert seen[1] == "校训是什么", f"第二次必须是裸问题 ✗ 实际={seen[1]!r}"
    assert "明德" in r["voice_text"], f"应该用第二次的回答 ✗ 实际={r['voice_text']!r}"
    assert "自愈" in r["source"], f"来源该标出来 ✓ 实际={r['source']!r}"


def test_selfheal_retries_even_without_context(monkeypatch):
    """⭐ 纯问题（没注入上下文）**也要重试** ✓ —— 因为失准是【随机】的 ✓

    ⚠️ 2026-10-18 修正 ✗：第一版我只对"注入了上下文"的情况重试 ✓
       （想法是"纯问题没被稀释，重试白等 4~5 秒"✗）
       → 闸门立刻用「纯问题答不上来」证明了这是个盲区 ✗：
         检索本身约 13% 随机 ✗，同一句话再问一次就是给它第二次机会 ✓
       → 现在两种情况都重试 ✓，只是重试的内容不同（有上下文→裸问题 ✓；纯问题→原问题 ✓）
    """
    memory._memory["facts"] = []
    memory._memory["events"] = []
    seen = _patch(monkeypatch, [("这个我暂时查不到", "NONE"),
                                ("咱们学校的校训是明德笃行、自强日新。", "NONE")])

    r = brain.handle_query("校训是什么")

    assert len(seen) == 2, f"纯问题失准也要重问一次 ✓ 实际问了 {len(seen)} 次"
    assert seen[1] == "校训是什么", f"纯问题重问的就是原问题 ✓ 实际={seen[1]!r}"
    assert "明德" in r["voice_text"], f"应该用重问的回答 ✗ 实际={r['voice_text']!r}"


def test_selfheal_retries_at_most_once(monkeypatch):
    """⭐ 最多重试**一次** ✗ —— 不能变成"答不上来就狂问" ✓"""
    memory._memory["facts"] = [{"id": 1, "ts": "", "text": "用户住 3 栋 502",
                                "kind": "private"}]
    memory._memory["events"] = []
    seen = _patch(monkeypatch, [("查不到", "NONE"), ("还是查不到", "NONE")])

    r = brain.handle_query("校训是什么")

    assert len(seen) == 2, f"最多两次（1 次原始 + 1 次重问）✗ 实际 {len(seen)} 次"
    assert "查不到" in r["voice_text"]      # 两次都不行 → 保留原话 ✓ 别编 ✗


def test_selfheal_survives_retry_exception(monkeypatch):
    """重问时抛异常 → 忽略它 ✓ 用第一次的回答（别把已有的弄丢 ✗）"""
    memory._memory["facts"] = [{"id": 1, "ts": "", "text": "用户住 3 栋 502",
                                "kind": "private"}]
    memory._memory["events"] = []
    calls = []

    def flaky(q):
        calls.append(q)
        if len(calls) == 1:
            return ("这个我暂时查不到", "NONE")
        raise RuntimeError("第二次网络抖了")

    monkeypatch.setattr(brain, "BRAIN", "cloud")
    monkeypatch.setattr(brain, "ask_cloud", flaky)
    monkeypatch.setattr(brain, "make_tts", lambda t: "")

    r = brain.handle_query("校训是什么")
    assert "查不到" in r["voice_text"], f"应该保留第一次的回答 ✗ 实际={r['voice_text']!r}"
