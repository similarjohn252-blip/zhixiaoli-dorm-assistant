"""上台前扫一眼（2026-10-19）

用途：正式演示/答辩前一分钟跑一次，10~20 秒内回答一个问题 ——
      "现在这套东西，是活的还是死的？"

为什么不是三秒：平台那一条要真问一次（5~15 秒），省不掉。
但前面四项加起来不到 3 秒，所以最坏情况是先看到"本地全活"，再等平台那一条。

四项检查：
 ① 服务端活着吗（/v1/health 版本号）
 ② 板子在线吗（/v1/board）
 ③ 四个固定话术（报修 / 请假 / 报修进度 / 自习室）—— 全在本地，平台挂了也照样答
 ④ 平台通不通（问一句校训）—— 这一条通，说明知识库、时间、天气三个工具都活着

⭐ 为什么第 ④ 条用"校训"而不是"天气"：
   天气工具一旦失败是【整个工作流 400】，不分问的是什么。
   所以问任何一句走平台的问题都能测出它；校训最短最稳。

退出码：0 = 全通，可以上台；2 = 有问题。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("ZHIXIAOLI_PROBE_BASE", "http://127.0.0.1:5000")
problems = []


def say(ok, label, detail=""):
    print(f"  {'OK ' if ok else 'BAD'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        problems.append(label)


def call(path, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    t_all = time.time()
    print("=" * 62)
    print("  上台前扫一眼")
    print("=" * 62)

    # ① 服务端
    try:
        h = call("/v1/health", timeout=5)
        say(True, "服务端", "版本 " + str(h.get("version")))
    except Exception as e:
        say(False, "服务端", type(e).__name__)
        print("\n  服务端都没起来，后面不用测了：cd E:\\天翼\\code && E:\\Python\\python.exe run.py")
        return 2

    # ② 板子
    try:
        b = call("/v1/board", timeout=8)
        if b.get("online"):
            say(True, "板子", "在线，音量 " + str(b.get("volume")))
        else:
            say(False, "板子", "离线（连热点、看板子屏幕的 IP）")
    except Exception as e:
        say(False, "板子", type(e).__name__)

    # ③ 四个固定话术（本地，平台挂了也不影响）
    fixed = [
        ("报修指路", "我们宿舍的灯坏了，要报修", "后勤报修"),
        ("请假指路", "我想请假", "本科生请假"),
        ("报修进度", "我报修的进度怎么样了", "知行理工"),
        ("自习室指路", "我想查自习室", "空教室"),
    ]
    for label, q, must in fixed:
        t0 = time.time()
        try:
            j = call("/v1/chat", {"query": q}, timeout=20)
            vt = j.get("voice_text") or ""
            dt = time.time() - t0
            say(must in vt and j.get("device_action") == "LED_GREEN_ON",
                label, f"{dt:.1f}s")
        except Exception as e:
            say(False, label, type(e).__name__)

    # ④ 平台（这一条通 = 知识库/时间/天气都在）
    t0 = time.time()
    try:
        j = call("/v1/chat", {"query": "咱们学校的校训是什么"}, timeout=40)
        vt = j.get("voice_text") or ""
        dt = time.time() - t0
        src = j.get("source") or ""
        if "明德笃行" in vt:
            say(True, "平台", f"{dt:.1f}s，来源 {src}")
        elif "本地回退" in src:
            say(False, "平台", f"{dt:.1f}s 超时后退回本地（答案会是泛泛的）")
        else:
            say(False, "平台", f"{dt:.1f}s，答复不是校训：{vt[:40]}")
    except Exception as e:
        say(False, "平台", type(e).__name__)
        print("        ⚠ 平台报错时先看【平台 → 工作流运行日志】；")
        print("          如果红的是 WeatherTool，就是天气插件把整个工作流拖死了。")

    dt_all = time.time() - t_all
    print("=" * 62)
    if problems:
        print(f"  [{len(problems)} 项有问题]  " + "、".join(problems))
        print("  ⭐ 提醒：四个固定话术是本地的 —— 就算平台挂了，")
        print("     报修/请假/进度/自习室照样能答，答辩时可以这么说。")
    else:
        print(f"   全通  用时 {dt_all:.1f}s  可以上台")
    print("=" * 62)
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
