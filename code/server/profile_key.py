"""网页「用户档案」的口令（2026-10-22 改版：改成用户自己设）

之前的做法有两个毛病 ✗：
  ① 口令只打印在服务端黑窗口里 —— 用户（学生本人、或者评委让他演示时）根本看不到
  ② 没在 .env 里设的话，每次重启随机生成一个新的 —— 彩排记的，正式演示一重启就进不去

现在的规则（按"方案一：用户自己设"）：
  · 第一次访问时，**在服务端那台电脑上**（127.0.0.1）打开网页，会看到"设置口令"
  · 设完存进库（PBKDF2 加盐哈希，不存明文），以后一直用它，重启不变
  · 忘了就在同一台电脑上点"重置口令"，不需要旧口令 ——
    能坐在那台电脑前的人，本来就等于这台机器的拥有者 ✓
  · 只允许本机设置/重置：否则会场 WiFi 下别人先打开网页就把口令设成他自己的了 ✗
  · 想固定成某个值，就在 .env 里设 ZHIXIAOLI_PROFILE_KEY，那时网页上不允许改（环境变量优先）

⚠️ 为什么不存明文：口令是 4~8 位，库里明文放着等于没设 ——
   谁拷走 记忆.db 就等于拿到口令 ✓
"""
import hashlib
import os
import re
import secrets
import sqlite3

from .config import MEMORY_DB, PROFILE_KEY

_ITER = 100_000
_KEY_PAT = re.compile(r"^[0-9A-Za-z]{4,8}$")


def _db():
    os.makedirs(os.path.dirname(MEMORY_DB) or ".", exist_ok=True)
    c = sqlite3.connect(MEMORY_DB, timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return c


def _get(k):
    try:
        c = _db()
        try:
            row = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return row[0] if row else ""
        finally:
            c.close()
    except Exception:
        return ""


def _set(k, v):
    c = _db()
    try:
        with c:
            c.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    finally:
        c.close()


def from_env():
    """口令是不是由 .env 指定的（那样网页上就不让改）。"""
    return bool(PROFILE_KEY)


def is_set():
    if from_env():
        return True
    return bool(_get("profile_hash"))


def _hash(key, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"),
                               bytes.fromhex(salt_hex), _ITER).hex()


def check(key):
    """口令对不对。"""
    key = str(key or "")
    if not key:
        return False
    if from_env():
        return secrets.compare_digest(key, PROFILE_KEY)
    salt = _get("profile_salt")
    want = _get("profile_hash")
    if not salt or not want:
        return False
    return secrets.compare_digest(_hash(key, salt), want)


def valid(key):
    """4~8 位数字或字母 —— 太短没意义，太长演示时懒得输。"""
    return bool(_KEY_PAT.match(str(key or "")))


def set_key(key):
    """设一个新口令（覆盖旧的）。返回 (ok, 说明)。"""
    if from_env():
        return False, "口令由 .env 的 ZHIXIAOLI_PROFILE_KEY 指定，网页上不能改"
    key = str(key or "")
    if not valid(key):
        return False, "口令要 4 到 8 位，只能数字或字母"
    salt = secrets.token_hex(16)
    _set("profile_salt", salt)
    _set("profile_hash", _hash(key, salt))
    return True, "设好了"


def reset():
    """清掉口令（回到"未设置"）。只有本机才该允许调用。"""
    if from_env():
        return False, "口令由 .env 指定，改 .env 才能换"
    try:
        c = _db()
        try:
            with c:
                c.execute("DELETE FROM meta WHERE k IN ('profile_salt','profile_hash')")
        finally:
            c.close()
    except Exception as e:
        return False, "清不掉：" + type(e).__name__
    return True, "已重置，重新设置一个吧"


def state():
    """给网页决定显示什么。"""
    return {
        "key_set": is_set(),
        "from_env": from_env(),
        "setup_allowed": (not from_env()),
    }


def banner():
    """启动横幅那一行：说清现在是什么状态、去哪设。"""
    if from_env():
        return "[档案] 口令取 .env 的 ZHIXIAOLI_PROFILE_KEY（网页上不可改）"
    if is_set():
        return ("[档案] 口令已设置。忘了的话，在服务端那台电脑上打开 "
                "http://127.0.0.1:5000/ 点「重置口令」")
    return ("[档案] 还没有口令 —— 在服务端那台电脑上打开 http://127.0.0.1:5000/，"
            "页面上会让你设一个 4~8 位")
