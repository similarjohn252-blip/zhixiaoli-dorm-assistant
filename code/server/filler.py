"""填充语：说话之前的语气词，和"还在查"的等待提示（2026-10-27）

需求：按键松手后，板子先说一句「嗯」；如果答案迟迟不来，再说「我查一下」这类。

为什么要服务端管这件事 ✗ 而不是直接把音频编进固件：
  · 编进固件要占 flash（现在已用 72%），改文案就得重新编译、重新烧板
  · 服务端管：改文案只改这里的一个列表，板子上电时把音频缓存下来即可
  · 而且换音色、调语速都不用碰固件

规格：edge-tts 原生 24kHz 单声道 PCM_16 WAV（板上实测能播 ✓），语速默认 +8%
      （语气词说慢了显拖沓；可用 ZHIXIAOLI_FILLER_RATE 调）

⚠️ 生成后**必须裁掉头尾静音** ✗ —— edge 会在两端塞很长的空白：
   实测「好的」原长 1.10 秒，裁完 0.48 秒（省 57%）；三句填充语平均省 33%。
   不裁的话语气词要 1 秒才说完，听起来像卡了一下 ✓
"""
import array
import json
import os
import re
import threading
import wave

from .config import AUDIO_DIR

# ---------------- 文案 ----------------
# 语气词：松手后立刻说，负责"它听见了"这一下（越短越好）
# 2026-10-27 定：去掉单独一个「嗯」（0.34 秒，实测听着太短、像卡了一下），
#   保留「嗯嗯」（0.47 秒）顶上 —— 它既有"嗯"的感觉，又够长听清。
FILLER_HI_TEXT = ("好的", "嗯嗯", "行", "好嘞", "哦好的")

# 填充语：答案还没到时轮着说（三句，已和用户确认）
FILLER_FILL_TEXT = ("嗯，我查一下", "稍等，我看看", "这个我得查查")

def _env_texts(name, default):
    raw = os.environ.get(name)
    if not raw:
        return tuple(default)
    got = tuple(x.strip() for x in raw.split(",") if x.strip())
    return got or tuple(default)


FILLER_HI_TEXT = _env_texts("ZHIXIAOLI_FILLER_HI", FILLER_HI_TEXT)
FILLER_FILL_TEXT = _env_texts("ZHIXIAOLI_FILLER_FILL", FILLER_FILL_TEXT)

# 语速（百分比）。+8% 是实测听起来最不拖沓的值
try:
    FILLER_RATE = int(os.environ.get("ZHIXIAOLI_FILLER_RATE", "8"))
except ValueError:
    FILLER_RATE = 8

MANIFEST = os.path.join(AUDIO_DIR, "filler_manifest.json")

# 静音门限（16bit 下）与首尾保留余量：留 60ms，别把起音的辅音切掉
SILENCE_RMS = 600
PAD_MS = 60

_lock = threading.Lock()


def _slug(text):
    """文件名只用 ASCII：中文文件名在 HTTP 头里会被转义，板子那边不一定认。"""
    keep = re.sub(r"[^0-9a-zA-Z]", "", text)
    return keep or str(abs(hash(text)) % 100000)


def _file_for(kind, idx, text):
    return os.path.join(AUDIO_DIR, f"filler_{kind}{idx:02d}_{_slug(text)}.wav")


# ---------------- 裁静音 ----------------
def trim_silence(path, thresh=SILENCE_RMS, pad_ms=PAD_MS):
    """就地裁掉头尾静音。返回 (原秒数, 新秒数)。失败则原样返回。

    ⚠️ 为什么不用 audioop ✗ —— Python 3.13 把它删了（PEP 594），
       本项目跑在 3.13 上，import 直接 ModuleNotFoundError（实测踩到 ✓）。
       所以用 array('h') 自己算 RMS ✓
    """
    try:
        with wave.open(path, "rb") as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            params = w.getparams()
            raw = w.readframes(w.getnframes())
    except Exception:
        return 0.0, 0.0
    if sw != 2 or not raw:
        return 0.0, 0.0

    before = len(raw) / float(ch * sw) / float(sr)
    win = max(1, int(sr * 0.015)) * ch * sw
    first = last = None
    for i in range(0, len(raw) - win + 1, win):
        seg = array.array("h", raw[i:i + win])
        if not seg:
            continue
        energy = (sum(s * s for s in seg) / len(seg)) ** 0.5
        if energy > thresh:
            if first is None:
                first = i
            last = i + win
    if first is None:                     # 整段都是静音 → 不动它
        return before, before

    pad = int(sr * pad_ms / 1000.0) * ch * sw
    a, b = max(0, first - pad), min(len(raw), last + pad)
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(params.nchannels)
            w.setsampwidth(params.sampwidth)
            w.setframerate(params.framerate)
            w.writeframes(raw[a:b])
    except Exception:
        return before, before
    after = (b - a) / float(ch * sw) / float(sr)
    return before, after


# ---------------- 生成 ----------------
def _default_tts(text, out_path):
    """默认用 edge-tts（语速按 FILLER_RATE 临时覆盖），失败退回百度。"""
    from . import speech
    old = getattr(speech, "TTS_EDGE_RATE", None)
    try:
        speech.TTS_EDGE_RATE = FILLER_RATE
        return speech.edge_tts_generate(text, out_path)
    except Exception as e:
        print(f"[填充语] edge 失败（{type(e).__name__}: {e}），退回百度", flush=True)
        return speech.baidu_tts(text, out_path)
    finally:
        if old is not None:
            speech.TTS_EDGE_RATE = old


def _plan():
    """按顺序列出该有哪些文件（先语气词，再填充语）。"""
    out = []
    for i, text in enumerate(FILLER_HI_TEXT, 1):
        out.append({"id": f"hi{i:02d}", "kind": "hi", "text": text,
                    "file": _file_for("hi", i, text)})
    for i, text in enumerate(FILLER_FILL_TEXT, 1):
        out.append({"id": f"fill{i:02d}", "kind": "fill", "text": text,
                    "file": _file_for("fill", i, text)})
    return out


def _duration(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def items():
    """读清单（不生成）。给 /v1/fillers 用。"""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def ensure(tts_maker=None, force=False):
    """确保清单里的每一句都有音频。返回清单。

    tts_maker 可注入（测试用 —— 别在测试里真去打 edge-tts）。
    """
    with _lock:
        maker = tts_maker or _default_tts
        plan = _plan()
        old = {x["id"]: x for x in items()}
        out = []
        made = 0
        for item in plan:
            path = item["file"]
            prev = old.get(item["id"])
            fresh = (prev and prev.get("text") == item["text"]
                     and os.path.exists(path) and not force)
            if not fresh:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    maker(item["text"], path)
                    made += 1
                except Exception as e:
                    print(f"[填充语] 生成失败 {item['text']}：{type(e).__name__}: {e}",
                          flush=True)
                    if prev:
                        out.append(prev)      # 保住旧的那份，别把清单搞空
                    continue
                before, after = trim_silence(path)
                if before and after:
                    print(f"[填充语] {item['text']}：{before:.2f}s → {after:.2f}s", flush=True)
            dur = _duration(path)
            out.append({
                "id": item["id"], "kind": item["kind"], "text": item["text"],
                "file": os.path.basename(path),
                "url": "/audio/" + os.path.basename(path),
                "duration": round(dur, 2),
            })
        try:
            os.makedirs(AUDIO_DIR, exist_ok=True)
            with open(MANIFEST, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
            # 清掉不再用的旧文件（改了文案之后，上一版的文件会变成孤儿）
            keep = {x["file"] for x in out}
            for fn in os.listdir(AUDIO_DIR):
                if fn.startswith("filler_") and fn.endswith(".wav") and fn not in keep:
                    try:
                        os.remove(os.path.join(AUDIO_DIR, fn))
                    except Exception:
                        pass
        except Exception as e:
            print(f"[填充语] 清单写不进去：{type(e).__name__}: {e}", flush=True)
        if made:
            print(f"[填充语] 这次生成了 {made} 条", flush=True)
        return out


load_summary = None        # 让别的模块能拿到上次 ensure 的摘要（可选）


def summary_text():
    """给启动横幅的一行摘要。"""
    got = items()
    if not got:
        return "[填充语] 还没生成（首次请求 /v1/fillers 时会生成）"
    hi = [x for x in got if x.get("kind") == "hi"]
    fill = [x for x in got if x.get("kind") == "fill"]
    total = sum(x.get("duration") or 0 for x in got)
    return (f"[填充语] 语气词 {len(hi)} 条 + 填充语 {len(fill)} 条，"
            f"合计 {total:.1f} 秒音频")
