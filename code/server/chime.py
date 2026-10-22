"""提醒用的"叮咚"提示音，以及"提示音 + 人声"拼成一个文件（2026-10-22）

设计文档第 4 节第 1 条：播报先放一段提示音再说话。

⭐ 为什么在服务端拼成一个文件，而不是让板子放两段：
   板子放两段要管"第一段放完再放第二段"，中间还容易断。
   服务端拼好，板子只放一个 URL，逻辑最简单、最不容易出错。

⭐ 采样率必须和 TTS 输出一致（16k 单声道 16bit），
   不然拼出来的文件板子只会放前一段 —— 实测过这种情况。

用法：
    from . import chime
    path = chime.make_reminder_wav("20 分钟后你有线性代数，在弘远楼B0321")
"""
import os
import wave

import numpy as np

SAMPLE_RATE = 16000


def make_chime(path=None, sample_rate=SAMPLE_RATE):
    """生成"叮咚"：两声，A5 然后 E5，各 0.22 秒，带淡入淡出。

    返回 wav 文件的绝对路径。
    """
    sr = sample_rate
    dur = 0.22
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)

    def tone(freq):
        sig = np.sin(2 * np.pi * freq * t)
        # 淡入淡出，避免"咔"的一声
        fade = int(sr * 0.02)
        env = np.ones(n)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        return sig * env * 0.5

    first = tone(880.0)     # A5
    gap = np.zeros(int(sr * 0.05))
    second = tone(659.25)   # E5
    sig = np.concatenate([first, gap, second])

    data = (sig * 32767).astype(np.int16)

    if path is None:
        from .config import AUDIO_DIR
        path = os.path.join(AUDIO_DIR, "chime.wav")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())
    return path


def _read_wav(path):
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        width = w.getsampwidth()
        sr = w.getframerate()
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16), nch, width, sr


def join_wavs(paths, out_path, sample_rate=SAMPLE_RATE):
    """把几个 wav 首尾接起来，中间插 0.15 秒静音。

    ⚠️ 只处理 16bit 单声道；采样率不一致的直接重采样成第一个的 ——
       板子对音频格式很挑，格式不对就是不出声，而且没有报错。
    """
    chunks = []
    silence = np.zeros(int(sample_rate * 0.15), dtype=np.int16)
    target_sr = sample_rate

    for i, p in enumerate(paths):
        if not p or not os.path.exists(p):
            continue
        data, nch, width, sr = _read_wav(p)
        if width != 2:
            # 8bit / 32bit 的就跳过，不硬转（转错了更难查）
            continue
        if nch == 2:
            data = data.reshape(-1, 2).mean(axis=1).astype(np.int16)
        if sr != target_sr:
            # 简单线性重采样
            ratio = target_sr / float(sr)
            idx = np.arange(0, len(data), 1.0 / ratio)
            idx = idx[idx < len(data) - 1].astype(int)
            if len(idx) == 0:
                continue
            data = data[idx]
        if i > 0:
            chunks.append(silence)
        chunks.append(data)

    if not chunks:
        return None

    out = np.concatenate(chunks)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(target_sr)
        w.writeframes(out.tobytes())
    return out_path


def make_reminder_wav(say, out_path, tts_maker=None, chime_path=None):
    """提示音 + 人声，拼成一个文件。返回路径。

    say        —— 要播报的话
    out_path   —— 拼好的文件放哪儿
    tts_maker  —— 生成语音的函数（默认用 music.make_tts），测试时可传假的
    """
    if not say:
        return chime_path or make_chime()

    if tts_maker is None:
        from .music import make_tts as tts_maker  # noqa: F811

    try:
        voice = tts_maker(say)
    except Exception:
        voice = None

    # ⚠️ music.make_tts 返回的是【URL】不是本地路径（http://…/audio/xxx.wav）✗
    #    直接拿去做 os.path.exists 会失败，然后悄悄退回成"只有叮咚"——
    #    不报错、听着也像那么回事，最容易漏 ✗ 这里必须先转成本地路径。
    if voice and "://" in str(voice):
        from .config import AUDIO_DIR
        fname = os.path.basename(str(voice).split("?")[0].rstrip("/"))
        cand = os.path.join(AUDIO_DIR, fname)
        voice = cand if os.path.exists(cand) else None

    if not voice or not os.path.exists(voice):
        return chime_path or make_chime()

    ding = chime_path or make_chime()
    return join_wavs([ding, voice], out_path)
