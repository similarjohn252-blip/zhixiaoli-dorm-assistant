"""语音出入口：百度 TTS / edge-tts 兜底 / 百度 ASR（三者共用一份 access_token 缓存）。"""
import asyncio
import time

import requests

from . import trace
from .config import (BAIDU_API_KEY, BAIDU_AUE, BAIDU_CHUNK, BAIDU_SECRET_KEY,
                     TTS_ENGINE, TTS_PER, TTS_PIT, TTS_SPD, TTS_VOL)

# 百度 access_token 缓存（有效期 30 天）—— 模块级可变状态，只在本模块内使用
_baidu_token = {"value": None, "expire_at": 0.0}

# ================= TTS：百度 =================
def _get_baidu_token():
    now = time.time()
    if _baidu_token["value"] and now < _baidu_token["expire_at"]:
        return _baidu_token["value"]
    r = requests.get(
        "https://aip.baidubce.com/oauth/2.0/token",
        params={
            "grant_type": "client_credentials",
            "client_id": BAIDU_API_KEY,
            "client_secret": BAIDU_SECRET_KEY,
        },
        timeout=10,
    )
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"百度取 token 失败: {data}")
    _baidu_token["value"] = token
    _life = int(data.get("expires_in", 2592000))
    _baidu_token["expire_at"] = now + _life - 3600
    return token


def baidu_tts(text, out_path):
    """调用百度短文本在线合成，分段拼接后写入 out_path。"""
    token = _get_baidu_token()
    audio = bytearray()
    chunks = [text[i:i + BAIDU_CHUNK]
              for i in range(0, len(text), BAIDU_CHUNK)] or [text]
    for chunk in chunks:
        r = requests.get(
            "https://tsn.baidu.com/text2audio",
            params={
                "tex": chunk,
                "tok": token,
                "cuid": "ESP32",
                "ctp": 1,
                "lan": "zh",
                # ⚠️ 2026-10-15：这四个原来写死在代码里 ✗ → 改成 config 里的旋钮 ✓
                #    想换音色只改 .env（或 config.py 的默认值）✓ 见 tools\试听音色.py ✓
                "spd": TTS_SPD,   # 语速
                "pit": TTS_PIT,   # 音调
                "vol": TTS_VOL,   # 音量
                "per": TTS_PER,   # 0 度小美 / 1 度小宇 / 3 度逍遥 / 4 度丫丫（精品音库需购买 ✗）
                "aue": BAIDU_AUE,  # 3 = mp3, 6 = wav(16k/单声道/16位 PCM)
            },
            timeout=20,
        )
        ctype = r.headers.get("Content-Type", "")
        if "audio" not in ctype:
            raise RuntimeError(f"百度 TTS 返回非音频: {r.text[:200]}")
        audio.extend(r.content)

    with open(out_path, "wb") as f:
        f.write(bytes(audio))
    return out_path


# ================= TTS：edge-tts（兜底，当前会被微软 403） =================
def edge_tts_generate(text, out_path):
    import edge_tts  # 延迟导入，库有问题也不影响启动

    async def _run():
        await edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(out_path)

    asyncio.run(_run())
    return out_path


def synthesize(text, out_path):
    """返回实际使用的引擎名；全部失败则抛异常。"""
    engines = {"baidu": [baidu_tts], "edge": [edge_tts_generate]}.get(
        TTS_ENGINE, [baidu_tts, edge_tts_generate]
    )
    last_err = None
    for fn in engines:
        try:
            fn(text, out_path)
            return fn.__name__
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[TTS] {fn.__name__} 失败: "
                  f"{type(e).__name__}: {e}", flush=True)
    raise RuntimeError(f"所有 TTS 引擎都失败，最后一个错误: {last_err}")


# ================= ASR：百度短语音识别（标准版） =================
def baidu_asr(wav_bytes):
    """16k/单声道/16位 的 wav 或 pcm 字节流 -> 文本。

    实测两种调用方式（raw body / JSON+base64）都可用，这里用 raw，省一次 base64。
    dev_pid: 1537=普通话(纯中文)  1536=普通话(带简单英文)  1737=英语
    """
    token = _get_baidu_token()  # 和 TTS 同一个 app 的 token
    # 链路追踪：把"识别"这一段单独计时（它是整条链路里最容易慢的一段 ✓）
    with trace.timed("ASR"):
        r = requests.post(
            "https://vop.baidu.com/server_api",
            params={"dev_pid": 1537, "cuid": "ESP32", "token": token},
            headers={"Content-Type": "audio/wav; rate=16000"},
            data=wav_bytes,
            timeout=30,
        )
    j = r.json()
    if j.get("err_no") != 0:
        # 常见: 3301 音质太差/没听清, 3302 鉴权失败, 3303 服务端问题, 3305 音频过长
        raise RuntimeError(
            f"百度 ASR 失败: err_no={j.get('err_no')} msg={j.get('err_msg')}")
    return (j.get("result") or [""])[0]
