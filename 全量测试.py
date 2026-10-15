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
        # 记忆库也必须显式隔离 ✓ —— 否则测试会连着开发机上那份真 记忆.db 跑 ✗
        "ZHIXIAOLI_MEMORY_DB": os.path.join(AUDIO, "记忆.db"),
        # 工单台账也必须隔离 ✗ —— 否则测试会往**真实台账**里塞垃圾工单
        "ZHIXIAOLI_TICKETS_DB": os.path.join(AUDIO, "工单.db"),
        # ① 音频 URL 必须指回本机 ✗：.env 里 PUBLIC_HOST 现在是校园网地址，
        #    测试从那个地址下载音频会超时（实测踩到 ✓）
        "ZHIXIAOLI_PUBLIC_HOST": "127.0.0.1",
        # ② 测试不测鉴权：清空 token，否则 memory/device 接口全是 401（实测踩到 ✓）
        "ZHIXIAOLI_API_TOKEN": "",
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
    # ⚠️ 坑（2026-10-15 实测踩到 ✗）：/v1/memory 自 2026-10-14 起**需要 token** ✗
    #    不带 token 会拿到 401，而 401 的 JSON 里没有 facts → 这条检查会**永远**喊"是旧版" ✓
    #    注意：测试自己的环境变量里把 token 清空了（那是给临时实例 5010 用的 ✗），
    #    所以这里要**直接读项目根的 .env** 拿真实 token ✓
    token = ""
    try:
        for _ln in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                        encoding="utf-8"):
            if _ln.startswith("ZHIXIAOLI_API_TOKEN="):
                token = _ln.split("=", 1)[1].strip()
                break
    except Exception:
        pass
    _hdr = {"Authorization": f"Bearer {token}"} if token else {}
    j = requests.get("http://127.0.0.1:5000/v1/memory", headers=_hdr, timeout=6).json()
    if "facts" in j:
        rec("5000 上的服务端是新版", True,
            f"已知信息 {j.get('fact_count')} 条" + ("（已带 token ✓）" if token else ""))
    elif not token:
        rec("5000 上的服务端是新版", False, "是旧版，请 Ctrl+C 后重启 Flask")
    else:
        rec("5000 上的服务端是新版", False,
            f"带 token 也没有 facts 字段：{str(j)[:80]}")
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
FLAKY = []          # 记录"第一次没中、重试才过"的用例（平台检索实测有随机性）


def case_platform(q, want_action, want_word, forbid_chabudao):
    """跑一次平台用例，返回 (ok, detail)。"""
    try:
        j, raw = ask_platform(q)
    except Exception as e:
        return False, f"异常 {type(e).__name__}: {str(e)[:60]}"
    vt, act = j.get("voice_text", ""), j.get("device_action", "")
    ok, detail = bool(vt), f"动作={act} 回复={vt[:34]}"
    if want_action and act != want_action:
        ok, detail = False, f"期望动作 {want_action}，实际 {act}"
    if want_word and want_word not in vt:
        ok, detail = False, f"回答里没有 {want_word}：{vt[:34]}"
    if forbid_chabudao and "查不到" in vt:
        ok, detail = False, f"答了“查不到”（检索没命中）：{vt[:34]}"
    return ok, detail


for q, want_action, want_word, forbid_chabudao in cases:
    ok, detail = case_platform(q, want_action, want_word, forbid_chabudao)
    if not ok:
        # 平台检索实测有约 13% 的随机失准（见 tools\测_知识库稳定性.py）：
        # 失败就再试 ✓ —— 2026-10-15 实测遇到**连续两次都没命中**，
        # 于是闸门红了一次 ✗（而那不是我们的代码问题 ✓），所以最多试 3 次；
        # 但"偶发"必须**显式记下来** ✓：既不掩盖真实退化，也不把平台随机性误判成故障 ✓
        for attempt in (2, 3):
            time.sleep(1.0)
            ok2, detail2 = case_platform(q, want_action, want_word, forbid_chabudao)
            if ok2:
                FLAKY.append(q)
                ok, detail = True, detail2 + f"（偶发：第 {attempt} 次才过）"
                break
            ok, detail = ok2, detail2
    rec(f"平台 · {q}", ok, detail)

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
            # 链路追踪（工程债 B4）：响应里必须带 request_id 与各段耗时 ✓
            rec(f"chat · {tag} 链路 id 与耗时",
                bool(j.get("request_id")) and isinstance(j.get("timings"), dict),
                f"rid={j.get('request_id')} timings={j.get('timings')}")
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
        # 知识库偶发失准（实测约 13%）→ 重试一次并标注为偶发
        if "明德" not in vt:
            time.sleep(1.0)
            j2 = ask_local("/v1/voice", data=data,
                           headers={"Content-Type": "audio/wav; rate=16000"}).json()
            if "明德" in (j2.get("voice_text") or ""):
                vt = j2["voice_text"]
                FLAKY.append("voice · 答出知识库内容")
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

    # ---- 报修工单（C1：真工单，不是话术）----
    try:
        tk = requests.get(LOCAL + "/v1/tickets", timeout=15).json()
        rec("ticket · 台账可读", "tickets" in tk and "stats" in tk,
            f"{len(tk.get('tickets') or [])} 张 stats={tk.get('stats')}")

        c = ask_local("/v1/chat", {"query": "我们宿舍的灯坏了，要报修"}).json()
        rec("ticket · 报修回答带真实工单号", "BX" in (c.get("voice_text") or ""),
            (c.get("voice_text") or "")[-40:])

        n1 = len(requests.get(LOCAL + "/v1/tickets", timeout=10).json()
                 .get("tickets") or [])
        ask_local("/v1/chat", {"query": "那明天能来人修吗"})
        n2 = len(requests.get(LOCAL + "/v1/tickets", timeout=10).json()
                 .get("tickets") or [])
        rec("ticket · 问进度不新建工单（回归护栏）", n1 == n2, f"{n1} → {n2}")

        tid = ((requests.get(LOCAL + "/v1/tickets", timeout=10).json()
                .get("tickets") or [{}])[0]).get("id")
        if tid:
            s = ask_local(f"/v1/tickets/{tid}", {"status": "doing"}).json()
            rec("ticket · 改状态并回读", s.get("ok") is True,
                str((s.get("ticket") or {}).get("status")))
    except Exception as e:
        rec("ticket · 工单链路", False, f"异常 {type(e).__name__}: {str(e)[:60]}")

    # ---- 设备代理：网页不直连板子，改走 /v1/device/*（文档第 11 节）----
    # 断言只看"结构"，不要求板子一定在线 —— 否则这条测试会随硬件在场与否而时好时坏 ✗
    try:
        d = requests.get(LOCAL + "/v1/device/status", timeout=15).json()
        rec("device · 状态代理", "online" in d,
            f"online={d.get('online')} {d.get('error') or d.get('detail')}")
        v = requests.get(LOCAL + "/v1/device/volume", timeout=15).json()
        rec("device · 读音量代理", ("volume" in v) or ("ok" in v),
            f"volume={v.get('volume')} steps={v.get('steps')} {v.get('error', '')}")
        w2 = ask_local("/v1/device/volume", {"volume": 15}).json()
        rec("device · 写音量代理", "ok" in w2,
            f"ok={w2.get('ok')} volume={w2.get('volume')} {w2.get('error', '')}")
        p = ask_local("/v1/device/profile", {"name": "回归", "room": "3 栋 502"}).json()
        rec("device · 个人信息代理", "ok" in p, f"ok={p.get('ok')} {p.get('error', '')}")
    except Exception as e:
        rec("device · 代理接口", False, f"异常 {type(e).__name__}: {str(e)[:60]}")

    # ---- 健康自检 + 版本自报（演示前一条命令看能不能上台）----
    try:
        h = requests.get(LOCAL + "/v1/health", timeout=15).json()
        rec("health · 结构完整",
            all(k in h for k in ("ok", "version", "checks")),
            f"ok={h.get('ok')} 版本={h.get('version')} "
            f"检查项={list((h.get('checks') or {}).keys())}")
        rec("health · 自报版本",
            bool(h.get("version")) and h.get("version") != "unknown",
            f"版本={h.get('version')} 启动={h.get('started_at')} uptime={h.get('uptime_s')}s")
    except Exception as e:
        rec("health · 健康自检", False, f"异常 {type(e).__name__}: {str(e)[:60]}")

    # ---- 兼容性护栏：老字段一字不变地还能用（我们对前端的承诺，文档 10.1 铁律 1）----
    try:
        c = ask_local("/v1/chat", {"query": "你好呀"}).json()
        rec("兼容 · /v1/chat 字段未变",
            all(k in c for k in ("voice_text", "device_action", "source", "audio_url")),
            f"字段={sorted(c.keys())}")
    except Exception as e:
        rec("兼容 · /v1/chat", False, f"异常 {type(e).__name__}: {str(e)[:60]}")
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
# ⚠️ 必须给一个**独立的构建目录** ✗ —— 2026-10-15 实测踩到（而且是偶发 ✗）：
#    arduino-cli 默认在 %LOCALAPPDATA%\arduino\sketches\<hash>\ 里编译，
#    而 **Arduino IDE 用的是同一个缓存目录** ✗ → 两边一撞就是
#    "open ...\build.options.json: Access is denied"（退出码 1）
#    给它自己的目录后，IDE 在不在跑都不影响闸门 ✓
FW_BUILD = os.path.join(os.environ.get("TEMP") or "/tmp", "zhixiaoli_fwbld")


def _compile_once():
    return subprocess.run([ARDUINO_CLI, "compile", "--fqbn", FQBN, "--no-color",
                           "--build-path", FW_BUILD, SKETCH],
                          capture_output=True, text=True, timeout=600)


try:
    r = _compile_once()
    # 2026-10-15 实测踩到 ✗：**同一句编译命令偶发退出 1**（手动立刻重跑又好 ✓）——
    # 大概是校园网 / 库索引 / IDE 抢占之类的瞬时问题 ✓。
    # 按本项目惯例：**重试一次，但把"偶发"显式记下来**（既不掩盖，也不误判成必现故障 ✓）
    flaky_compile = False
    if r.returncode != 0:
        time.sleep(2)
        r2 = _compile_once()
        if r2.returncode == 0:
            flaky_compile = True
            r = r2
    line = [x for x in r.stdout.splitlines() if "Sketch uses" in x]
    rec("固件编译通过", r.returncode == 0,
        (line[0].strip() if line else f"退出码 {r.returncode}")
        + ("（偶发：第 2 次才过 ✓）" if flaky_compile else ""))
    if r.returncode != 0:
        # 报错可能在 stdout 也可能在 stderr ✗ —— 两边都打，别再让人猜 ✓
        for tag, s in (("stdout", r.stdout), ("stderr", r.stderr)):
            if s and s.strip():
                print(f"     [{tag}] " + "\n     ".join(s.strip().splitlines()[-10:]))
except Exception as e:
    rec("固件编译", False, f"{type(e).__name__}: {str(e)[:80]}")

# ============================ 收尾 ============================
if proc:
    proc.terminate()
    print("\n（临时服务端已关闭）")

print()
print("=" * 70)
print(f"汇总：通过 {passed} 项，失败 {failed} 项，其中偶发 {len(FLAKY)} 项（重试才过）")
for _f in FLAKY:
    print(f"  偶发：{_f}")
if FLAKY:
    print("提示：偶发＝平台侧检索有随机性。同一句反复偶发，就该加固知识库（补文件 / 调 TopK）")
print("=" * 70)
print("把这段输出整段发我，我逐条判读。")
sys.exit(0 if failed == 0 else 2)
