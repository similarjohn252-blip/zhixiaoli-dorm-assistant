"""给提醒准备音频（2026-10-22）

① 让 /v1/reminders 返回 audio_url
② 配套的板子模拟器要用它

⭐ 只在"快到点了"才生成（默认 10 分钟内）：
   一学期二十来个提醒，每周都要生成一次。提前全生成等于每次保存课表都调二十次 TTS，
   慢且浪费。按需生成 + 按 id 缓存，第一次轮询到就生成，之后直接用。

⭐ 文件名用提醒 id：
   课表提醒的 id 是 c+上课时间（c20261021083000），闹钟是 a+序号。
   同一个提醒反复出现在轮询里，id 不变，所以缓存命中。
"""
import os

from . import chime

AUDIO_PREFIX = "remind_"


def audio_path_for(item_id, audio_dir):
    safe = "".join(ch for ch in str(item_id) if ch.isalnum() or ch in "_-")
    return os.path.join(audio_dir, AUDIO_PREFIX + safe + ".wav")


def ensure_audio(item_id, say, audio_dir, tts_maker=None):
    """生成"叮咚 + 人声"，已存在就直接用。返回文件名（不是完整 URL）。"""
    path = audio_path_for(item_id, audio_dir)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return os.path.basename(path)
    try:
        os.makedirs(audio_dir, exist_ok=True)
        got = chime.make_reminder_wav(say, path, tts_maker=tts_maker)
        if got and os.path.exists(got):
            return os.path.basename(got)
    except Exception as e:
        print("[提醒音频] 生成失败：" + type(e).__name__ + ": " + str(e)[:80], flush=True)
    return ""


def decorate(items, audio_dir, base_url="", window_seconds=600, tts_maker=None):
    """给快到的那些提醒加上 audio_url，其余不动。返回改过的 items（原地改）。

    window_seconds —— 还有多少秒之内才生成音频，默认 600（10 分钟）
    base_url       —— 完整的服务地址，比如 http://192.168.137.1:5000/
    """
    if not base_url:
        return items
    base = base_url if base_url.endswith("/") else base_url + "/"
    for it in items:
        try:
            left = int(it.get("in_seconds", 999999))
        except (ValueError, TypeError):
            continue
        if left > window_seconds:
            continue
        name = ensure_audio(it.get("id"), it.get("say", ""), audio_dir, tts_maker)
        if name:
            it["audio_url"] = base + "audio/" + name
    return items
