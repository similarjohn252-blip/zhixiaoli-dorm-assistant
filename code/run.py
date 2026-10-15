"""启动入口：打印自检横幅 → 开始监听。

用法：E:\\Python\\python.exe run.py
"""
from server import preflight

# ⚠️ 顺序很重要：**先只读 .env 做第一道自检**（此时不导入别的 server 模块 ✗）
#    因为 config 在 import 时就会对缺失的必需变量 fail-fast → 那只能"一次报一个" ✗
#    在这里拦住，就能一次把缺的**全列出来** ✓
if not preflight.check_env_only()[0]:
    raise SystemExit(1)

from server import memory
from server.app import app
from server.config import (
    AUDIO_DIR, BRAIN, CLOUD_URL, HISTORY_TURNS, MEMORY_FILE, PORT,
    PUBLIC_HOST, PUBLIC_PORT, TTS_ENGINE, TTS_FORMAT,
)
from server.music import MUSIC_COUNTRY, MUSIC_DIR, MUSIC_ENABLE


def main():
    # 完整自检：端口占用 / 目录可写 / 对外地址 / 磁盘空间（有硬问题就不给启动 ✓）
    # env_checked=True：第一道已在文件顶部做过，这里不重复 ✓
    ok, _hard, _soft = preflight.run(env_checked=True)
    if not ok:
        raise SystemExit(1)
    print(f"音频目录: {AUDIO_DIR}")
    print(f"TTS 后端: {TTS_ENGINE}  格式: {TTS_FORMAT}")
    _brain = "智小理平台 " + CLOUD_URL if BRAIN != "local" else "仅本地规则"
    print(f"大脑: {BRAIN}  ({_brain})")
    print(f"记忆: {MEMORY_FILE}")
    print(f"      已知信息 {len(memory._memory['facts'])} 条，"
          f"短期记忆保留最近 {HISTORY_TURNS} 轮")
    print(f"找资源: 音乐 {'开' if MUSIC_ENABLE else '关'}"
          f"  (iTunes 试听 30 秒, 地区 {MUSIC_COUNTRY})")
    print(f"        下载到 {MUSIC_DIR}")
    print(f"监听: 0.0.0.0:{PORT}  （ESP32 下载地址 "
          f"http://{PUBLIC_HOST}:{PUBLIC_PORT}/audio/...）")
    print("语音入口: POST /v1/voice (ESP32 上传录音)")
    print("          POST /v1/chat (文字)   POST /v1/reset (新会话)")
    print("记忆接口: GET/POST /v1/memory (查看/添加)   POST /v1/memory/clear (清空)")
    app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
    