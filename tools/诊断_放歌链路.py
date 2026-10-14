"""进程内直接跑 handle_query，判断"放歌失效"到底出在代码还是出在运行中的服务端。

为什么要这样测：/v1/chat 上放歌不生效，但 /v1/music 正常 ✗ ——
可能原因有两个：① 代码逻辑坏了 ② 5000 上那个 Flask 进程是旧代码（改完没重启）。
在进程内直接调用 handle_query()，就绕开了"运行中的服务端"，一次分清 ✓

用法：E:\\Python\\python.exe tools\\诊断_放歌链路.py
"""
import os
import sys

os.environ["ZHIXIAOLI_MEMORY_FILE"] = os.path.join(
    os.environ.get("TEMP", "."), "放歌诊断_记忆.json")   # 别污染真实记忆
sys.path.insert(0, r"E:\天翼\code")

from server import brain, music  # noqa: E402

print("=== 0) 开关与依赖 ===")
print(f"  MUSIC_ENABLE = {music.MUSIC_ENABLE}   （False 的话聊天里根本不会走音乐分支 ✗）")
print(f"  HANDLE_MUSIC = {callable(music.handle_music)}")

for q in ("放一首周杰伦的歌", "放七里香", "放一首告白气球"):
    print(f"\n=== 查询: {q} ===")
    song, strict = music.music_request(q)
    print(f"  music_request -> song={song!r} strict={strict}")
    if song is None:
        print("  → 不认，会落到云端 ✗")
        continue
    r = music.handle_music(song, q, strict)
    if r is None:
        print("  → handle_music 返回 None（交给云端）✗")
    else:
        print(f"  → handle_music: 动作={r.get('device_action')} "
              f"来源={r.get('source')} 歌曲={'有' if r.get('audio_url2') else '无'} "
              f"回复={str(r.get('voice_text'))[:40]}")

print("\n=== 端到端（进程内跑 handle_query，这才真正反映当前代码）===")
for q in ("放一首周杰伦的歌", "放七里香"):
    try:
        r = brain.handle_query(q)
        print(f"  {q}")
        print(f"    动作={r.get('device_action')}  来源={r.get('source')}  "
              f"歌曲={'有 ✓' if r.get('audio_url2') else '无 ✗'}")
        print(f"    回复={str(r.get('voice_text'))[:60]}")
    except Exception as e:
        print(f"  {q} 异常 {type(e).__name__}: {e}")
