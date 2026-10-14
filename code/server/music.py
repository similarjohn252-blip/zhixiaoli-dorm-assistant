"""找资源放歌：搜歌 → 打分 → 下载 → 补 m4a 容器 → 交给板子播。

音源：Apple 官方 30 秒试听接口（免费、无需 key、直链是 m4a/AAC）。
"""
import hashlib
import os
import traceback

import requests

from . import memory
from .config import AUDIO_DIR, AUDIO_FILENAME, PUBLIC_HOST, PUBLIC_PORT
from .speech import synthesize

# ================= 找资源：音乐（上网搜歌 → 下载到本地 → 交给板子播） =================
# 架构：电脑做桥。搜索和下载都在电脑上完成，只把「局域网 HTTP 地址」给 ESP32，
#       所以板子不用访问外网、不用 TLS，音源也能随时换。
# 音源：Apple 官方 30 秒试听接口（免费、不需要 key，直链是 m4a/AAC）。
#       已确认音频库能解 m4a：Audio.cpp:757 有 AACDecoder，:2778 有完整 MP4 盒子解析。
MUSIC_ENABLE = os.environ.get("ZHIXIAOLI_MUSIC", "1") == "1"
# 实测：带 country=cn 去搜"周杰伦"会返回 0 条，不带地区（默认美区）反而能搜到中文歌，
# 所以默认留空＝不传地区；要用特定区就自己设，搜不到会自动去掉地区再搜一次。
MUSIC_COUNTRY = os.environ.get("ZHIXIAOLI_MUSIC_COUNTRY", "")
MUSIC_DIR = os.path.join(AUDIO_DIR, "music")

_MUSIC_TRIGGERS = ("放歌", "放一首", "放个歌", "放首歌", "来一首", "来首", "放首",
                   "听歌", "听一首", "听首", "放音乐", "听音乐", "播放", "我想听", "我要听")
_MUSIC_STOPWORDS = ("帮我", "请", "给我", "我想", "我要", "能不能", "可以", "一下", "现在",
                    "播放一首", "播放", "放一首", "放一个", "放个", "放首", "来一首", "来一个",
                    "来首", "听一首", "听首", "放歌", "听歌", "放音乐", "听音乐",
                    "的歌", "歌曲", "首歌", "的音乐")
# 宽松说法（"放七里香""听稻香"）容易误伤这些常见词和疑问句，命中就绝不走音乐分支
_NOT_MUSIC_WORDS = ("放心", "放假", "放学", "放在", "放到", "放这", "放那", "放弃", "放大",
                    "方便", "放好", "放开", "播放器", "听说", "听话", "听写")
_QUESTION_WORDS = ("怎么", "为什么", "什么", "吗", "呢", "哪", "谁", "多少", "几",
                   "是不是", "有没有", "何时", "几点")
_COVER_WORDS = ("cover", "翻唱", "版", "piano", "instrumental", "伴奏", "纯音乐",
                "remix", "karaoke", "tribute", "dj ")


def music_request(query):
    """判断是不是"放歌"。返回 (搜索词, 是否明确说法)。

    明确说法：放一首X / 放首歌 / 来一首X / 我想听X / 播放X …
    宽松说法：放X / 来X / 听X / 播X（很短、不像问句、不是"放心/放假"这类词）
              —— 宽松这种如果搜不到，就放弃音乐分支，交给云端正常回答，不会瞎回"没找到"。
    """
    q = (query or "").strip()
    if not q:
        return None, False

    strict = any(t in q for t in _MUSIC_TRIGGERS)
    loose = False
    if not strict and len(q) <= 14 and q[0] in "放来听播":
        if (not any(w in q for w in _NOT_MUSIC_WORDS)
                and not any(w in q for w in _QUESTION_WORDS)):
            loose = True
    if not (strict or loose):
        return None, False

    term = q
    for w in _MUSIC_STOPWORDS:
        term = term.replace(w, "")
    term = term.strip(" 的了，,。.！!？?、")
    if term[:1] in ("放", "来", "听", "播"):      # 宽松说法剩下的开头动词也去掉
        term = term[1:].strip(" 的了，,。.！!？?、")
    if term in ("歌", "音乐", "歌曲", "曲子", "一首", "首", "个", "点"):   # 只说了"放首歌"
        term = ""
    return term, strict


# 中文歌手名 → iTunes 里的英文名：带上英文名搜，原唱命中率高得多（否则中文热歌常搜到翻唱）
_ARTIST_ALIAS = {
    "周杰伦": "Jay Chou", "林俊杰": "JJ Lin", "陈奕迅": "Eason Chan",
    "邓紫棋": "G.E.M.", "薛之谦": "Joker Xue", "刘若英": "Rene Liu",
    "周深": "Zhou Shen", "李荣浩": "Li Ronghao", "毛不易": "Mao Buyi",
    "张杰": "Jason Zhang", "汪苏泷": "Silence Wang", "许嵩": "Vae",
    "田馥甄": "Hebe Tien", "五月天": "Mayday", "张学友": "Jacky Cheung",
    "王菲": "Faye Wong", "孙燕姿": "Stefanie Sun", "梁静茹": "Fish Leong",
    "蔡依林": "Jolin Tsai", "陶喆": "David Tao", "王力宏": "Leehom Wang",
}


def _artist_alias_hits(term):
    """搜索词里出现的中文歌手名，返回对应的英文名列表。"""
    return [en for zh, en in _ARTIST_ALIAS.items() if zh in term]


def _score_result(it, term):
    """给搜索结果打分：优先原唱、扣掉翻唱/钢琴版。"""
    name = (it.get("trackName") or "").lower()
    artist = (it.get("artistName") or "").lower()
    t = term.lower()
    score = 0
    aliases = [a.lower() for a in _artist_alias_hits(term)]
    if artist and (artist in t or t in artist):
        score += 3                                    # 搜索词里直接带了歌手名
    if any(a and (a in artist or artist in a) for a in aliases):
        score += 3                                    # 中文歌手名对上了英文歌手（原唱）
    if name and name in t:
        score += 2                                    # 歌名匹配
    if any(w in name for w in _COVER_WORDS):
        score -= 2                                    # 翻唱/钢琴版/伴奏往后排
    return score


def search_music(term):
    """搜 30 秒试听。返回 (歌名, 歌手, 试听地址)；找不到返回 None。"""
    # 搜索时补上歌手的英文名（"周杰伦的七里香" → "周杰伦的七里香 Jay Chou"）
    query_term = term
    aliases = _artist_alias_hits(term)
    if aliases:
        query_term = term + " " + " ".join(aliases)

    tries = []
    if MUSIC_COUNTRY:
        tries.append({"term": query_term, "media": "music", "limit": 10,
                      "country": MUSIC_COUNTRY})
    # 去掉地区再试一次
    tries.append({"term": query_term, "media": "music", "limit": 10})
    if query_term != term:
        # 补英文名还搜不到就用原词
        tries.append({"term": term, "media": "music", "limit": 10})
    for params in tries:
        try:
            r = requests.get("https://itunes.apple.com/search",
                             params=params, timeout=12)
            r.raise_for_status()
            items = [it for it in (r.json().get("results") or [])
                     if it.get("previewUrl")]
            if items:
                best = max(items, key=lambda it: _score_result(it, term))
                return ((best.get("trackName") or "").strip(),
                        (best.get("artistName") or "").strip(),
                        best["previewUrl"])
        except Exception as e:
            print(f"[音乐] 搜索出错: {type(e).__name__}: {e}", flush=True)
    return None


def download_music(term, url):
    """下载到 AUDIO_DIR/music 并返回局域网可播地址；失败返回 ""。"""
    try:
        os.makedirs(MUSIC_DIR, exist_ok=True)
        _digest = hashlib.md5(term.encode("utf-8")).hexdigest()[:10]
        name = "music_" + _digest + ".m4a"
        path = os.path.join(MUSIC_DIR, name)
        if not (os.path.exists(path) and os.path.getsize(path) > 10000):
            data = requests.get(
                url, timeout=60, headers={"User-Agent": "Mozilla/5.0"}).content
            if len(data) < 10000:
                print(f"[音乐] 下载太小（{len(data)} 字节），放弃", flush=True)
                return ""
            # 音频库只认 ftyp 品牌 M4A /isom/mp42（Audio.cpp:2839），其它品牌就地改 4 个字节
            if (data[4:8] == b"ftyp"
                    and data[8:12] not in (b"M4A ", b"isom", b"mp42")):
                print(f"[音乐] ftyp 品牌 {data[8:12]!r} 不被音频库支持，"
                      "改成 M4A ", flush=True)
                data = data[:8] + b"M4A " + data[12:]
            with open(path, "wb") as f:
                f.write(data)
        print(f"[音乐] 就绪: {path}（{os.path.getsize(path)} 字节）", flush=True)
        return f"http://{PUBLIC_HOST}:{PUBLIC_PORT}/audio/music/{name}"
    except Exception as e:
        print(f"[音乐] 下载失败: {type(e).__name__}: {e}", flush=True)
        return ""


def make_tts(text):
    """把文本合成为 output.wav，返回可下载地址（失败返回 ""）。"""
    path = os.path.join(AUDIO_DIR, AUDIO_FILENAME)
    try:
        engine = synthesize(text, path)
        if os.path.getsize(path) > 0:
            print(f"[TTS] {engine} 合成成功: {path}", flush=True)
            return f"http://{PUBLIC_HOST}:{PUBLIC_PORT}/audio/{AUDIO_FILENAME}"
        print("[TTS] 生成了 0 字节文件，视为失败", flush=True)
    except Exception:
        print("[TTS] 合成失败，本次只回文本：", flush=True)
        traceback.print_exc()
    return ""


def handle_music(song, raw="", strict=True):
    """放歌分支：不走云端（云端给不了音频）。返回与 handle_query 相同结构的 dict，
    audio_url=播报、audio_url2=歌曲本体，板子会"先播报、再放歌"。

    返回 None = 这次交给云端正常回答（只有"宽松说法搜不到"时才会这样）。
    """
    music_url, title, artist = "", "", ""
    if not song:
        reply, action, source = "你想听谁的歌？说个歌名或者歌手就行。", "NONE", "音乐"
    else:
        found = None
        # 先用解析出来的关键词搜；搜不到再拿原句搜一次（"放七里香"这种原句也可能被模糊匹配到）
        for term in ([song, raw] if raw and raw != song else [song]):
            try:
                found = search_music(term)
            except Exception as e:
                print(f"[音乐] 搜索失败: {type(e).__name__}: {e}", flush=True)
                found = None
            if found:
                break
        if not found:
            if not strict:
                # 宽松说法又搜不到 → 很可能根本不是点歌，交给云端正常回答
                print(f"[音乐] “{raw}”宽松匹配但搜不到，交给云端处理", flush=True)
                return None
            reply, action, source = f"没找到《{song}》，换个歌名或者歌手再试试？", "NONE", "音乐"
        else:
            title, artist, purl = found
            print(f"[音乐] 搜到: {artist} - {title}", flush=True)
            music_url = download_music(song, purl)
            if music_url:
                reply, action, source = (
                    f"好的，给你放{artist}的《{title}》", "PLAY_MUSIC", "音乐")
            else:
                reply, action, source = (
                    f"找到了{artist}的《{title}》，但下载失败了，稍后再试试。",
                    "NONE", "音乐")

    print(f"[音乐] {reply}", flush=True)
    memory.remember_turn(song or "放歌", reply)

    audio_url = make_tts(reply)
    if not audio_url and music_url:      # 播报合成失败就直奔歌曲，别让这次请求白跑
        audio_url, music_url = music_url, ""

    return {
        "voice_text": reply,
        "device_action": action,
        "source": source,
        "audio_url": audio_url,
        "audio_url2": music_url,
        "memory": len(memory._memory["facts"]),
        "history": len(memory._history),
    }
