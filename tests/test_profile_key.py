"""口令的自动化测试（2026-10-22 方案一：用户自己设）

要点：
· 没设之前，档案接口 401 且带 need_setup，网页据此显示"设一个"
· 设置/重置**只允许本机**（127.0.0.1）—— 否则会场 WiFi 下别人先设就被劫持了
· 库里存的是加盐哈希，不是明文（否则拷走 记忆.db 就等于拿到口令）
"""
import pytest

from server import profile_key


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_key, "MEMORY_DB", str(tmp_path / "k.db"))
    monkeypatch.setattr(profile_key, "PROFILE_KEY", "")   # 模拟 .env 没设
    yield


def test_starts_unset():
    assert profile_key.is_set() is False
    assert profile_key.check("") is False
    assert profile_key.check("1234") is False


def test_set_then_check():
    ok, _msg = profile_key.set_key("4321")
    assert ok is True
    assert profile_key.is_set() is True
    assert profile_key.check("4321") is True
    assert profile_key.check("1111") is False


def test_not_stored_as_plaintext(tmp_path):
    profile_key.set_key("8765")
    raw = open(str(tmp_path / "k.db"), "rb").read()
    assert b"8765" not in raw, "库里不该有明文口令"


@pytest.mark.parametrize("bad", ["", "123", "123456789", "12 34", "密码1234"])
def test_rejects_bad_key(bad):
    ok, msg = profile_key.set_key(bad)
    assert ok is False
    assert "4 到 8 位" in msg


def test_reset_clears():
    profile_key.set_key("4321")
    ok, _ = profile_key.reset()
    assert ok is True
    assert profile_key.is_set() is False
    assert profile_key.check("4321") is False


def test_env_takes_priority(monkeypatch):
    """在 .env 里固定了口令时，网页上不能改。"""
    monkeypatch.setattr(profile_key, "PROFILE_KEY", "8888")
    assert profile_key.is_set() is True
    assert profile_key.check("8888") is True
    ok, msg = profile_key.set_key("1234")
    assert ok is False and "env" in msg.lower()


def test_state_shape():
    st = profile_key.state()
    assert st["key_set"] is False
    assert st["from_env"] is False
    assert st["setup_allowed"] is True


def test_banner_mentions_where_to_set():
    assert "127.0.0.1" in profile_key.banner() or "还没有口令" in profile_key.banner()


# ---------------- 接口层：只允许本机设置 ----------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from server import app as app_mod
    from server import memory
    monkeypatch.setattr(memory, "_memory", {"facts": [], "events": []})
    monkeypatch.setattr(memory, "MEMORY_DB", str(tmp_path / "m.db"))
    monkeypatch.setattr(profile_key, "MEMORY_DB", str(tmp_path / "k.db"))
    monkeypatch.setattr(profile_key, "PROFILE_KEY", "")
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
REMOTE = {"REMOTE_ADDR": "10.0.0.5"}


def test_state_endpoint_needs_no_key(client):
    r = client.get("/v1/profile/state", environ_base=LOCAL)
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["key_set"] is False
    assert j["can_setup_here"] is True


def test_profile_says_need_setup_when_unset(client):
    r = client.get("/v1/profile", environ_base=LOCAL)
    assert r.status_code == 401
    j = r.get_json()
    assert j["need_setup"] is True and j["setup_allowed"] is True


def test_set_key_only_from_local(client):
    r = client.post("/v1/profile/key", json={"key": "4321"}, environ_base=REMOTE)
    assert r.status_code == 403
    assert "服务端那台电脑" in r.get_json()["error"]

    r = client.post("/v1/profile/key", json={"key": "4321"}, environ_base=LOCAL)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert profile_key.check("4321") is True


def test_after_set_profile_works(client):
    client.post("/v1/profile/key", json={"key": "4321"}, environ_base=LOCAL)
    r = client.get("/v1/profile", headers={"X-Profile-Key": "4321"}, environ_base=REMOTE)
    assert r.status_code == 200
    r = client.get("/v1/profile", headers={"X-Profile-Key": "0000"}, environ_base=REMOTE)
    assert r.status_code == 401
    assert r.get_json()["need_setup"] is False


def test_reset_from_local(client):
    client.post("/v1/profile/key", json={"key": "4321"}, environ_base=LOCAL)
    r = client.post("/v1/profile/key", json={"action": "reset"}, environ_base=LOCAL)
    assert r.status_code == 200
    assert profile_key.is_set() is False
