"""「记住…」的两种走向 + 档案接口（2026-10-22）

第一部分：整句只是交代 → 本地直出确认，不碰平台；
          半交代半提问 → 照常走平台，不能被一句确认吞掉问题。
第二部分：网页档案接口的鉴权与增删改查。
"""
import pytest

from server import brain, memory


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(memory, "MEMORY_FILE", str(tmp_path / "t.json"))
    monkeypatch.setattr(memory, "_memory", {"facts": [], "events": []})
    monkeypatch.setattr(memory, "_history", [])
    monkeypatch.setattr(brain, "BRAIN", "cloud")
    monkeypatch.setattr(brain, "make_tts", lambda t: "")
    brain._pending.update(kind="", note="", ts=0.0)
    yield


def _no_cloud(monkeypatch):
    def boom(q):
        raise AssertionError("这句不该走平台：" + q)
    monkeypatch.setattr(brain, "ask_cloud", boom)


def test_plain_remember_is_local(monkeypatch):
    _no_cloud(monkeypatch)
    r = brain.handle_query("记住我英语基础不好")
    assert r["source"] == "本地档案"
    assert "记住" in r["voice_text"]
    assert [f["text"] for f in memory._memory["facts"]] == ["用户英语基础不好"]


def test_private_remember_says_it_wont_be_spoken(monkeypatch):
    _no_cloud(monkeypatch)
    r = brain.handle_query("记住我学号是12500000001")
    assert r["source"] == "本地档案"
    assert "不会在回答里念出来" in r["voice_text"]
    assert memory._memory["facts"][0]["kind"] == "private"


def test_remember_plus_question_goes_to_platform(monkeypatch):
    """「记住…，高数怎么复习」不能被一句"好的记住了"吞掉问题 ✓"""
    seen = []

    def fake(q):
        seen.append(q)
        return ("高数建议先过一遍极限和导数。", "NONE")

    monkeypatch.setattr(brain, "ask_cloud", fake)
    r = brain.handle_query("记住我英语基础不好，高数怎么复习")
    assert seen, "应该问了平台"
    assert r["source"] != "本地档案"
    # 档案还是要记住 ✓
    assert any("英语" in f["text"] for f in memory._memory["facts"])


def test_plain_sentence_writes_nothing(monkeypatch):
    _no_cloud(monkeypatch)
    monkeypatch.setattr(brain, "ask_cloud", lambda q: ("嗯。", "NONE"))
    brain.handle_query("我住 3 栋 502")
    assert memory._memory["facts"] == []


# ---------------- 网页档案接口 ----------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from server import app as app_mod
    monkeypatch.setattr(memory, "MEMORY_DB", str(tmp_path / "t2.db"))
    monkeypatch.setattr(memory, "MEMORY_FILE", str(tmp_path / "t2.json"))
    monkeypatch.setattr(memory, "_memory", {"facts": [], "events": []})
    monkeypatch.setattr(app_mod, "_PROFILE_KEY", "test123")
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


KEY = {"X-Profile-Key": "test123"}


def test_profile_requires_key(client):
    r = client.get("/v1/profile")
    assert r.status_code == 401
    assert r.get_json()["need_key"] is True


def test_profile_wrong_key(client):
    r = client.get("/v1/profile", headers={"X-Profile-Key": "nope"})
    assert r.status_code == 401


def test_profile_list_empty(client):
    j = client.get("/v1/profile", headers=KEY).get_json()
    assert j["ok"] is True and j["count"] == 0 and j["limit"] > 0


def test_profile_add_list_update_delete_clear(client):
    j = client.post("/v1/profile", json={"action": "add", "text": "我大二计算机专业"},
                    headers=KEY).get_json()
    assert j["ok"] is True and j["count"] == 1

    j = client.get("/v1/profile", headers=KEY).get_json()
    assert j["items"][0]["kind"] == "profile"
    assert "计算机" in j["items"][0]["shown"]

    j = client.post("/v1/profile", json={"action": "update", "text": "我大二计算机专业",
                                        "new": "我大三计算机专业"}, headers=KEY).get_json()
    assert j["ok"] is True
    assert memory._memory["facts"][0]["text"] == "我大三计算机专业"

    j = client.post("/v1/profile", json={"action": "delete", "text": "我大三计算机专业"},
                    headers=KEY).get_json()
    assert j["ok"] is True and memory._memory["facts"] == []

    memory.add_fact("一条档案")
    j = client.post("/v1/profile", json={"action": "clear"}, headers=KEY).get_json()
    assert j["cleared"] == 1 and memory._memory["facts"] == []


def test_profile_masks_private_in_list(client):
    client.post("/v1/profile", json={"action": "add", "text": "我学号是12500000001"},
                headers=KEY)
    j = client.get("/v1/profile", headers=KEY).get_json()
    assert "12500000001" not in j["items"][0]["shown"]
    assert j["items"][0]["kind"] == "private"


def test_profile_rejects_too_long(client):
    r = client.post("/v1/profile", json={"action": "add", "text": "长" * 61}, headers=KEY)
    assert r.status_code == 400


def test_profile_bad_action(client):
    r = client.post("/v1/profile", json={"action": "nope", "text": "x"}, headers=KEY)
    assert r.status_code == 400
