"""配置路径的纯逻辑（B3 快测试层 ✓）。

重点是那次**差点埋进去的雷** ✗：
  `.env` 里若写 `ZHIXIAOLI_MEMORY_DB=`（空串），
  `os.environ.get(name, default)` 会老老实实返回**空串**（不是默认值 ✗）
  → `sqlite3.connect("")` 直接炸 ✓
  → 所以必须用 `os.environ.get(name) or 默认值` ✓，并**用一个子进程把它测出来** ✓
"""
import os
import subprocess
import sys
import tempfile

from server import config

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_memory_db_is_set_and_db_suffix():
    assert config.MEMORY_DB, "MEMORY_DB 不能为空 ✗"
    assert config.MEMORY_DB.endswith(".db")


def test_memory_db_override_is_honored():
    """conftest 里显式指到了临时目录 ✓ —— 这条同时证明"隔离真的生效" ✓"""
    assert config.MEMORY_DB == os.environ["ZHIXIAOLI_MEMORY_DB"]


def test_empty_env_value_derives_instead_of_crashing():
    """空串场景必须自动推导成 .db，而不是炸 ✗（子进程里验，避免污染本进程 ✓）"""
    tmp = tempfile.mkdtemp(prefix="zxlempty_")
    env = dict(os.environ)
    env["ZHIXIAOLI_MEMORY_DB"] = ""
    env["ZHIXIAOLI_MEMORY_FILE"] = os.path.join(tmp, "记忆.json")
    code = (
        "import sys; sys.path.insert(0, r'" + os.path.join(CODE_DIR, "code") + "'); "
        "from server import config; "
        "print(config.MEMORY_DB)"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert r.returncode == 0, r.stderr
    got = (r.stdout or "").strip().splitlines()[-1]
    assert got.endswith("记忆.db") and os.path.join(tmp, "") in got + os.sep
