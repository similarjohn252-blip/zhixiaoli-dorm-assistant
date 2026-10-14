"""自测：放歌触发词（music_request）到底认哪些说法。

背景（实测）：走 /v1/music 一切正常，但走 /v1/chat 时
  「放一首周杰伦的歌」「放七里香」「来首歌听听」全都落到云端 ✗
于是被 v2 的"只做宿舍"提示词拦住 → 聊天里事实上放不了歌。
本脚本直接调 music_request()，把"认不认、抽出的搜索词是什么"一次打清楚。

用法：E:\\Python\\python.exe tools\\自测_音乐触发.py
"""
import sys

sys.path.insert(0, r"E:\天翼\code")

from server import music  # noqa: E402

# 演示时会说的、以及用户可能说的各种说法
CASES = [
    "放一首周杰伦的歌",
    "放一首告白气球",
    "放七里香",
    "来首歌听听",
    "放歌",
    "我想听周杰伦",
    "给我放首歌吧",
    "来一段音乐",
    "放一首歌",
    "告白气球",
    "周杰伦",
    "宿舍能用电磁炉吗",     # 反例：不该被当成点歌
]

print("=== music_request() 触发情况 ===")
for q in CASES:
    try:
        song, strict = music.music_request(q)
    except Exception as e:
        print(f"  {q:<18} 异常 {type(e).__name__}: {e}")
        continue
    if song is None:
        mark = "✗ 不认（会落到云端）"
        extra = ""
    else:
        mark = "✓ 认"
        extra = f"  搜索词='{song}'  strict={strict}"
    print(f"  {q:<18} {mark}{extra}")

print()
print("=== 触发词表（前 12 条）===")
for t in music._MUSIC_TRIGGERS[:12]:
    print("   ", t)
