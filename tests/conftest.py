"""pytest 的公共准备（B3：给"纯逻辑"补一层**快**的测试，秒级、不联网 ✓）。

⚠️ 必须在**任何 server 模块被导入之前**改掉环境变量 ✗ ——
   因为 `config.py` 在 import 时就读取环境变量 ✓，
   而 `memory.py` 在 import 时就会 `load_memory()`（**真的会去开数据库** ✗）
   → 不先改，测试就会碰开发机上那份**真记忆 / 真台账** ✓（这个坑本项目踩过 ✓）
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "code"))          # 让 `import server.*` 可用 ✓

# ⚠️ 临时目录必须放在**项目内** ✗ ——
#    第一版用了 tempfile.mkdtemp()（落在 %TEMP%，在工作区之外 ✓），
#    实测撞上 `PermissionError: [WinError 5] 拒绝访问` ✗ ——
#    受限沙箱/CI 里根本建不出子目录 ✓。放项目内：任何模式都能写 ✓，
#    也更接近 CI 的形态 ✓（跑完自动删 ✓，且已 gitignore ✓）
TMP = os.path.join(ROOT, ".pytest_tmp")
shutil.rmtree(TMP, ignore_errors=True)                  # 上次跑剩下的先清掉 ✓
os.makedirs(TMP, exist_ok=True)
os.environ.update({
    "ZHIXIAOLI_AUDIO_DIR": os.path.join(TMP, "语音"),
    "ZHIXIAOLI_MEMORY_FILE": os.path.join(TMP, "记忆.json"),
    "ZHIXIAOLI_MEMORY_DB": os.path.join(TMP, "记忆.db"),
    "ZHIXIAOLI_TICKETS_DB": os.path.join(TMP, "工单.db"),
    "ZHIXIAOLI_LOG_FILE": os.path.join(TMP, "链路.jsonl"),
    "ZHIXIAOLI_MUSIC_DIR": os.path.join(TMP, "music"),
    "ZHIXIAOLI_BRAIN": "local",            # 绝不联网 ✓
    "ZHIXIAOLI_API_TOKEN": "",             # 单元层不测鉴权 ✓
    "ZHIXIAOLI_BOARD_HOST": "127.0.0.1",   # 别去连真板子 ✓
})
os.makedirs(os.environ["ZHIXIAOLI_AUDIO_DIR"], exist_ok=True)

# 收尾不删目录 ✓ —— 写了 pytest_sessionfinish 钩子，但实测它**没生效** ✗
# （目录还在 ✓）。与其留一段"看起来会清理"的死代码 ✗，不如老实说：
#   · 每次跑测试**开始时**会先清空它 ✓（见上面的 rmtree）
#   · 它已被 .gitignore 排除 ✓，体积也只有几十 KB ✓
# 教训：**跑一次就知道的谎，别留着** ✓（本项目已经栽过好几次 ✓）
