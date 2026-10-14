import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")   # 读脚本旁边的 .env，别写死绝对路径

# -*- coding: utf-8 -*-
"""全量自测（自带临时服务端，不用你手动启动任何东西）

跑法：在你的终端里执行
    E:\\Python\\python.exe E:\\天翼\\_全量测试.py

它会：
  1) 在 5010 端口起一份**独立的**临时服务端（自己的音频目录和记忆文件，不碰你的演示数据）
  2) 测平台 API 直连（和板子同一条路）：校训 / 学生手册 / 办事意图的 LED 动作 / 闲聊
  3) 测本地服务端全接口：/v1/chat、/v1/voice（假录音走完整语音链路）、/v1/music、/v1/memory
  4) 检查板子要的音频格式（WAV 头、m4a 的 ftyp 品牌）
  5) 编译一遍固件，确认没改坏
  6) 打印汇总，并把临时服务端关掉
"""
import os
import subprocess
import sys
import tempfile
import time
import wave

import requests
import urllib3

urllib3.disable_warnings()

PLATFORM = "https://aiagent.cqut.edu.cn:31134"
KEY = os.environ["ZHIXIAOLI_CLOUD_KEY"]
LOCAL = "http://127.0.0.1:5010"
SERVER_PY = r"E:\天翼\code\run.py"
ARDUINO_CLI = (r"E:\Arduino\Arduino IDE\resources\app\lib\backend"
               r"\resources\arduino-cli.exe")
SKETCH = r"E:\天翼\code\sketch_oct8a_copy_20261008145732"
FQBN = ("esp32:esp32:esp32s3:UploadSpeed=921600,USBMode=hwcdc,FlashSize=16M,"
        "PartitionScheme=app3M_fat9M_16MB,DebugLevel=none,PSRAM=opi")
BAIDU_KEY = os.environ["BAIDU_API_KEY"]
BAIDU_SECRET = os.environ["BAIDU_SECRET_KEY"]

TMP = os.path.join(tempfile.gettempdir(), "全量测试")
AUDIO = os.path.join(TMP, "audio")
os.makedirs(AUDIO, exist_ok=True)

passed, failed = 0, 0
proc = None


def rec(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))


def skip(name, detail=""):
    """不适用 / 环境性提示：不计入失败。

    为什么要这一档：把"没在跑"这种环境状态报成 FAIL，会淹没真正的失败 ✗
    """
    print(f"  [SKIP] {name}" + (f"  —— {detail}" if detail else ""))


def jparse(ans):
    i = ans.rfind("{")
    if i < 0:
        return {}
    try:
        import json
        return json.loads(ans[i:ans.rfind("}") + 1])
    except Exception:
        return {}


def server_up():
    try:
        requests.get(LOCAL + "/v1/memory", timeout=3)
        return True
    except Exception:
        return False


def start_server():
    global proc
    if server_up():
        print("  （5010 上已有服务端，直接用它）")
        return True
    env = os.environ.copy()
    env.update({
        "ZHIXIAOLI_PORT": "5010",
        "ZHIXIAOLI_PUBLIC_PORT": "5010",
        "ZHIXIAOLI_AUDIO_DIR": AUDIO,
        "ZHIXIAOLI_MEMORY_FILE": os.path.join(AUDIO, "记忆.json"),
        # 子进程 stdout 强制 UTF-8，否则 server.log 会写成乱码，排障时读不了
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    print("  正在起临时服务端（独立数据目录）…")
    proc = subprocess.Popen([sys.executable, SERVER_PY], env=env,
                            stdout=open(os.path.join(TMP, "server.log"), "w",
                                        encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    for _ in range(30):
        time.sleep(0.5)
        if server_up():
            return True
    return False


def ask_platform(q):
    r = requests.post(
        PLATFORM + "/v1/chat-messages",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"inputs": {"query": q}, "query": q, "response_mode": "blocking",
              "user": "regress"},
        timeout=45, verify=False)
    ans = r.json().get("answer", "")
    return jparse(ans), ans


def ask_local(path, payload=None, data=None, headers=None, timeout=180):
    url = LOCAL + path
    if data is not None:
        return requests.post(url, data=data, headers=headers or {}, timeout=timeout)
    return requests.post(url, json=payload or {}, timeout=timeout)


def check_wav(c):
    return c[:4] == b"RIFF" and c[8:12] == b"WAVE"


def check_m4a(c):
    if c[4:8] != b"ftyp":
        return False, "不是 MP4/M4A"
    brand = c[8:12]
    if brand not in (b"M4A ", b"isom", b"mp42"):
        return False, f"ftyp 品牌 {brand!r} 不合规"
    if b"mp4a" not in c[:300000] or b"esds" not in c[:300000]:
        return False, "缺 mp4a/esds"
    return True, "品牌 M4A ✓"


def download(url):
    return requests.get(url.replace("192.168.137.1", "127.0.0.1"), timeout=90,
                        headers={"Range": "bytes=0-"}).content


def fake_recording(text):
    tok = requests.get("https://aip.baidubce.com/oauth/2.0/token", timeout=15, params={
        "grant_type": "client_credentials", "client_id": BAIDU_KEY,
        "client_secret": BAIDU_SECRET}).json()["access_token"]
    r = requests.get("https://tsn.baidu.com/text2audio", timeout=20, params={
        "tex": text, "tok": tok, "cuid": "regress", "ctp": 1, "lan": "zh",
        "spd": 5, "pit": 5, "vol": 5, "per": 0, "aue": 6})
    path = os.path.join(TMP, "fake.wav")
    with open(path, "wb") as f:
        f.write(r.content)
    with wave.open(path) as w:
        return path, w.getframerate(), w.getnchannels()


# ============================ 开始 ============================
print("=" * 70)
print("0. 检查你正在运行的服务端（5000）是不是最新代码")
print("=" * 70)
try:
    j = requests.get("http://127.0.0.1:5000/v1/memory", timeout=6).json()
    if "facts" in j:
        rec("5000 上的服务端是新版", True, f"已知信息 {j.get('fact_count')} 条")
    else:
        rec("5000 上的服务端是新版", False, "是旧版，请 Ctrl+C 后重启 Flask")
except Exception as e:
    # 环境性提示：本脚本自己会起临时实例（5010），所以这里不算失败。
    # 关键是报出**真实原因**，而不是笼统地说"没在监听" ✗
    skip("5000 上没有服务端在跑（不影响本测试）",
         f"{type(e).__name__}: {str(e)[:70]} —— 演示前记得单独启动它")

print()
print("=" * 70)
print("A. 平台 API 直连（和板子走同一条路）")
print("=" * 70)
cases = [
    ("咱们学校的校训是什么", None, "明德", True),
    ("宿舍管理有什么要求", None, "电器", True),
    ("留宿他人会有什么处理", None, "留宿", True),
    ("我们宿舍的灯坏了，要报修", "LED_GREEN_ON", None, False),
    ("宿舍能用电磁炉吗", "LED_RED_ON", None, False),
    ("帮我订个会议室", "NONE", None, False),
    ("你好呀", "NONE", None, False),
]
for q, want_action, want_word, forbid_chabudao in cases:
    try:
        j, raw = ask_platform(q)
        vt, act = j.get("voice_text", ""), j.get("device_action", "")
        ok, detail = bool(vt), f"动作={act} 回复={vt[:34]}"
        if want_action and act != want_action:
            ok, detail = False, f"期望动作 {want_action}，实际 {act}"
        if want_word and want_word not in vt:
            ok, detail = False, f"回答里没有 {want_word}：{vt[:34]}"
        if forbid_chabudao and "查不到" in vt:
            ok, detail = False, f"答了“查不到”（检索没命中）：{vt[:34]}"
        rec(f"平台 · {q}", ok, detail)
    except Exception as e:
        rec(f"平台 · {q}", False, f"异常 {type(e).__name__}: {str(e)[:60]}")

print()
print("=" * 70)
print("B. 本地服务端（临时实例，独立数据目录）")
print("=" * 70)
if start_server():
    rec("临时服务端启动", True, LOCAL)
    for q, tag in (("咱们学校的校训是什么", "知识库"),
                   ("我们宿舍的灯坏了，要报修", "宿舍报修")):
        try:
            j = ask_local("/v1/chat", {"query": q}).json()
            rec(f"chat · {tag} 字段齐全",
                all(k in j for k in ("voice_text", "device_action", "source")),
                f"source={j.get('source')} 动作={j.get('device_action')}")
            au = j.get("audio_url") or ""
            rec(f"chat · {tag} 播报是 WAV", bool(au) and check_wav(download(au)),
                au[-22:] if au else "无音频")
        except Exception as e:
            rec(f"chat · {tag}", False, f"异常 {type(e).__name__}: {str(e)[:60]}")

    try:
        path, rate, ch = fake_recording("咱们学校的校训是什么")
        with open(path, "rb") as f:
            data = f.read()
        j = ask_local("/v1/voice", data=data,
                      headers={"Content-Type": "audio/wav; rate=16000"}).json()
        asr, vt = j.get("asr_text", ""), j.get("voice_text", "")
        rec("voice · 识别出“校训”", "校训" in asr, f"识别={asr}")
        rec("voice · 答出知识库内容", "明德" in vt, f"回复={vt[:34]}")
    except Exception as e:
        rec("voice · 假录音链路", False, f"异常 {type(e).__name__}: {str(e)[:60]}")

    try:
        j = ask_local("/v1/music", {"query": "放一首告白气球"}).json()
        u2 = j.get("audio_url2") or ""
        ok, why = check_m4a(download(u2)) if u2 else (False, "没返回歌曲地址")
        rec("music · 歌曲格式板子可播", ok, why)
    except Exception as e:
        rec("music · 放歌", False, f"异常 {type(e).__name__}: {str(e)[:60]}")

    try:
        m = requests.get(LOCAL + "/v1/memory", timeout=10).json()
        rec("memory · 读接口", "facts" in m, f"已知信息 {m.get('fact_count')} 条")
        w = ask_local("/v1/memory", {"text": "回归测试条目"}).json()
        rec("memory · 写接口", bool(w.get("added")), str(w.get("added")))
        ask_local("/v1/memory/clear")
    except Exception as e:
        rec("memory · 接口", False, f"异常 {type(e).__name__}: {str(e)[:60]}")
else:
    # 起不来时直接把日志末尾打出来，省掉"你去翻日志"这一步
    try:
        with open(os.path.join(TMP, "server.log"), encoding="utf-8",
                  errors="replace") as f:
            tail = " / ".join(f.read().splitlines()[-3:])[:400]
    except Exception:
        tail = "（日志读不到）"
    rec("临时服务端启动", False, tail or "（日志是空的）")

print()
print("=" * 70)
print("C. 固件编译")
print("=" * 70)
try:
    r = subprocess.run([ARDUINO_CLI, "compile", "--fqbn", FQBN, "--no-color", SKETCH],
                       capture_output=True, text=True, timeout=600)
    line = [x for x in r.stdout.splitlines() if "Sketch uses" in x]
    rec("固件编译通过", r.returncode == 0,
        line[0].strip() if line else f"退出码 {r.returncode}")
    if r.returncode != 0:
        print("     " + "\n     ".join(r.stdout.splitlines()[-8:]))
except Exception as e:
    rec("固件编译", False, f"{type(e).__name__}: {str(e)[:80]}")

# ============================ 收尾 ============================
if proc:
    proc.terminate()
    print("\n（临时服务端已关闭）")

print()
print("=" * 70)
print(f"汇总：通过 {passed} 项，失败 {failed} 项")
print("=" * 70)
print("把这段输出整段发我，我逐条判读。")
sys.exit(0 if failed == 0 else 2)
