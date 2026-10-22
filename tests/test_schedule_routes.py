"""课表接口的端到端测试（2026-10-22）

用 Flask 的测试客户端跑，每个测试用临时数据库，不碰真实的 提醒.db。
"""
import io
import os
from datetime import datetime

import pytest

from server import reminders
from server.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "DB_PATH", str(tmp_path / "t.db"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_schedule_empty_initially(client):
    r = client.get("/v1/schedule")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["count"] == 0
    assert j["items"] == []
    assert j["week1_monday"] == "2026-08-31"
    assert j["early_minutes"] == 20


def test_schedule_save_and_read_back(client):
    payload = {
        "week1_monday": "2026-08-31",
        "early_minutes": 15,
        "items": [
            {"weekday": 3, "period": 1, "name": "线性代数", "venue": "弘远楼B0321",
             "weeks": [8]},
            {"weekday": 4, "period": 5, "name": "嵌入式系统开发", "venue": "D0425",
             "weeks": [8]},
        ],
    }
    r = client.post("/v1/schedule/save", json=payload)
    assert r.status_code == 200
    assert r.get_json()["saved"] == 2

    j = client.get("/v1/schedule").get_json()
    assert j["count"] == 2
    assert j["early_minutes"] == 15
    assert {it["name"] for it in j["items"]} == {"线性代数", "嵌入式系统开发"}
    assert j["items"][0]["id"] == "t1"


def test_schedule_save_drops_bad_items(client):
    payload = {"items": [
        {"weekday": 3, "period": 1, "name": "好的课", "venue": "", "weeks": [8]},
        {"weekday": 9, "period": 1, "name": "星期几不对", "venue": "", "weeks": [8]},
        {"weekday": 3, "period": 2, "name": "节次不对", "venue": "", "weeks": [8]},
        {"weekday": 3, "period": 1, "name": "", "venue": "", "weeks": [8]},
        {"weekday": 3, "period": 1, "name": "没周次", "venue": "", "weeks": []},
    ]}
    r = client.post("/v1/schedule/save", json=payload)
    assert r.get_json()["saved"] == 1
    j = client.get("/v1/schedule").get_json()
    assert j["count"] == 1
    assert j["items"][0]["name"] == "好的课"


def test_reminders_empty_when_no_schedule(client):
    r = client.get("/v1/reminders?hours=48")
    assert r.status_code == 200
    j = r.get_json()
    assert j["items"] == []
    assert "now" in j


def test_alarm_add_and_show_up(client):
    # 加一个很久以后的闹钟，确保在 upcoming 的窗口里
    at = datetime.now().replace(microsecond=0)
    from datetime import timedelta
    at = at + timedelta(minutes=30)
    r = client.post("/v1/alarm", json={"at": at.strftime("%Y-%m-%d %H:%M"),
                                       "say": "该喝水了"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["id"] == 1

    got = client.get("/v1/reminders?hours=2").get_json()
    assert len(got["items"]) == 1
    assert got["items"][0]["kind"] == "alarm"
    assert got["items"][0]["say"] == "该喝水了"
    assert 0 <= got["items"][0]["in_seconds"] <= 2 * 3600


def test_alarm_bad_time_rejected(client):
    r = client.post("/v1/alarm", json={"at": "明天早上", "say": "x"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


# ---------- 上传 ----------

REAL_PDF = r"C:\Users\ASUS\Downloads\王鹏超(2026-2027-1)课表 (1).pdf"


@pytest.mark.skipif(not os.path.exists(REAL_PDF), reason="真课表 PDF 不在本机")
def test_upload_real_pdf(client):
    with open(REAL_PDF, "rb") as f:
        data = f.read()
    r = client.post("/v1/schedule/upload",
                    data={"file": (io.BytesIO(data), "课表.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["count"] >= 15
    # 上传不保存
    assert client.get("/v1/schedule").get_json()["count"] == 0
    # 每条都有 id
    assert all("id" in it for it in j["items"])


def test_upload_rejects_non_pdf(client):
    r = client.post("/v1/schedule/upload",
                    data={"file": (io.BytesIO(b"hello"), "a.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    assert "PDF" in r.get_json()["error"]


def test_upload_without_file(client):
    r = client.post("/v1/schedule/upload", data={},
                    content_type="multipart/form-data")
    assert r.status_code == 400


# ---------- 上传 + 保存 + 提醒 全链路 ----------

@pytest.mark.skipif(not os.path.exists(REAL_PDF), reason="真课表 PDF 不在本机")
def test_full_chain_upload_save_remind(client):
    with open(REAL_PDF, "rb") as f:
        data = f.read()
    up = client.post("/v1/schedule/upload",
                     data={"file": (io.BytesIO(data), "课表.pdf")},
                     content_type="multipart/form-data").get_json()
    assert up["ok"]

    saved = client.post("/v1/schedule/save", json={
        "week1_monday": "2026-08-31",
        "early_minutes": 20,
        "items": up["items"],
    }).get_json()
    assert saved["saved"] >= 15

    # 10/21 那天应该正好四场
    got = reminders.upcoming(48, now=datetime(2026, 10, 21, 0, 0))
    assert len(got["items"]) == 7          # 周三四场 + 周四五场里的部分
    first = got["items"][0]
    assert first["kind"] == "class"
    assert "线性代数" in first["say"]
    assert first["at"] == "2026-10-21T08:10:00"
