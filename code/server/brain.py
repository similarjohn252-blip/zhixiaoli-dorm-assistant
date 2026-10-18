"""大脑：云端（智小理平台）优先，失败或降级时用本地保底规则。

由原来的三节合成：大脑1（Dify 接口与降级检测）＋ 大脑2（本地规则与动作映射）
＋ 意图与合成入口（handle_query，文字/语音两个入口共用）。
"""
import json
import os
import re
import time

import requests

from . import memory
from .config import (
    BRAIN, CLOUD_INPUT_MAX, CLOUD_KEY, CLOUD_MULTITURN, CLOUD_TIMEOUT,
    CLOUD_URL, CLOUD_USER, CLOUD_VERIFY,
)
from . import tickets, trace
from .music import MUSIC_ENABLE, handle_music, make_tts, music_request

# ================= 大脑 1：智小理平台（Dify 接口） =================
# 多轮对话靠 conversation_id：平台每次都会回一个新的，带回去才能延续上下文。
# ⚠️ 实测这块平台的多轮是坏的：带 conversation_id 时它的 workflow 会 400
#    （template:2: function "query" not defined）。所以这里做了自动降级：
#    一旦带上下文失败，就不再每轮都去撞，改成单轮对话；平台修好后 POST /v1/reset 即可恢复。
_cloud_conv = {"id": "", "multiturn_ok": True}
# 平台"答非所问"故障：实测提示词变量没取到值时，模型收到的是字符串 null，
# 于是对任何问题都只回一句自我介绍。识别到就切本地规则，隔几分钟再试探一次。
_cloud_state = {"degraded": False, "checked_at": 0.0}
RECHECK_SEC = float(os.environ.get("ZHIXIAOLI_CLOUD_RECHECK", "300"))
_DEGRADED_MARKS = ("user input is", "user message is", "no actual question",
                   "there's no query", "no user query")


def _cloud_is_degraded(raw_answer):
    """模型收到 null 时的特征（它会在思考里抱怨输入是 null、没有 query）。"""
    a = (raw_answer or "").lower()
    return "null" in a and any(m in a for m in _DEGRADED_MARKS)


def _clean_voice(text):
    """清洗模型回复：平台偶尔会吐聊天模板标记，或把我的注入块回声出来。
    这些既不能念给用户听，也不能显示在屏幕上。"""
    t = str(text or "")
    # 1) 砍掉被回声的注入块（从第一个标记开始全部丢弃）
    for mark in ("【已知信息】", "【最近对话】", "【当前问题】", "(背景：", "（背景："):
        i = t.find(mark)
        if i > 0:
            t = t[:i]
    # 2) 去掉聊天模板标记
    t = re.sub(r"</?(?:assistant|user|system|human|ai)>", " ", t, flags=re.I)
    # 3) 收掉多余空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _cloud_request(query, conv_id):
    """真正发一次请求，返回 (voice_text, device_action)。非 200 抛异常。"""
    # 平台是聊天模板（<user>…</user>），换行和方括号会被它当成标记 → 统一压成单行再发
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    # 按应用声明的必填输入变量传（inputs.query），同时保留对话入口的 query
    send_q = query[:CLOUD_INPUT_MAX]
    if len(query) > CLOUD_INPUT_MAX:
        print(f"[云端] 输入超过应用声明的 {CLOUD_INPUT_MAX} 字上限，已截断", flush=True)
    body = {"inputs": {"query": send_q}, "query": send_q,
            "response_mode": "blocking", "user": CLOUD_USER}
    if conv_id:
        body["conversation_id"] = conv_id

    t0 = time.time()
    r = requests.post(
        CLOUD_URL,
        headers={"Authorization": f"Bearer {CLOUD_KEY}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=CLOUD_TIMEOUT,
        verify=CLOUD_VERIFY,
    )
    dt = time.time() - t0
    if r.status_code != 200:
        raise RuntimeError(f"平台返回 HTTP {r.status_code}: {r.text[:200]}")

    j = r.json()
    if j.get("conversation_id"):
        _cloud_conv["id"] = j["conversation_id"]

    answer = (j.get("answer") or "").strip()
    # 平台故障检测：变量没取到值时模型收到的是 null，只会打招呼 —— 立刻切本地规则
    if _cloud_is_degraded(answer):
        _cloud_state["degraded"] = True
        _cloud_state["checked_at"] = time.time()
        print("[云端] ⚠️ 平台没把用户输入传给模型（模型收到的是 null）——"
              "本轮改用本地规则；修好平台后 POST /v1/reset，或等几分钟自动重试",
              flush=True)
        # ⚠️ 关键：这一轮**不能**把平台那句"我收到的是 null"当回答播出去 ✗
        # （故障演练实测：以前会把 'The user input is "null" ...' 原样念给用户听 ✓）
        # 抛出去让 handle_query 走"回退本地规则"分支 —— 日志里那句话才名副其实 ✓
        raise RuntimeError("平台退化：模型收到的是 null，本轮改用本地规则")
    # 平台会先吐一段 <think>…</think> 推理过程，用户不需要听，去掉
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
    print(f"[云端] {dt:.1f}s 会话={'有' if conv_id else '无'} "
          f"答复={answer[:120]!r}", flush=True)

    # 平台约定：答复的最后是一个 JSON {"voice_text": "...", "device_action": "..."}
    voice_text, action = "", "NONE"
    s, e = answer.rfind("{"), answer.rfind("}")
    if 0 <= s < e:
        try:
            inner = json.loads(answer[s:e + 1])
            voice_text = str(inner.get("voice_text") or "")
            action = str(inner.get("device_action") or "NONE")
        except Exception:
            # JSON 不合法时退一步用正则捞
            m = re.search(r'"voice_text"\s*:\s*"([^"]*)"', answer)
            if m:
                voice_text = m.group(1)
            m = re.search(r'"device_action"\s*:\s*"([^"]*)"', answer)
            if m:
                action = m.group(1)
    if not voice_text:
        voice_text = answer  # 平台没按约定回 JSON，就把整段念出来
    return _clean_voice(voice_text), action


def ask_cloud(query):
    """问智小理平台。默认不传 conversation_id（上下文我们自己拼），
    要交给平台就把 ZHIXIAOLI_CLOUD_MULTITURN 设成 1（它那份历史有 bug，不推荐）。"""
    # 平台处于"收到 null"的故障状态时先别再问它（隔几分钟自动重试一次）
    if _cloud_state["degraded"]:
        if time.time() - _cloud_state["checked_at"] < RECHECK_SEC:
            raise RuntimeError("平台故障中（模型收到的是 null），暂用本地规则")
        _cloud_state["degraded"] = False
        print("[云端] 到点重试平台…", flush=True)

    conv_id = _cloud_conv["id"] if (
        CLOUD_MULTITURN and _cloud_conv["multiturn_ok"]) else ""
    try:
        return _cloud_request(query, conv_id)
    except Exception as e:
        if conv_id:
            print(f"[云端] 带上下文失败，自动降级为单轮: {e}", flush=True)
            _cloud_conv["multiturn_ok"] = False   # 不再每轮都白撞一次
            _cloud_conv["id"] = ""
            return _cloud_request(query, "")
        raise

# ================= 大脑 2：本地保底规则（断网也能演示） =================
# 用户明确要办事务时，平台的答复可能不办事（改提示词/知识库之后就出现过），
# 这两张表用来"保证有硬件动作 + 答非所问就换成本地话术"。
_ACTION_RULES = (
    (("会议室", "订会议", "预约会议"), "LED_GREEN_ON",
     "好的，已经帮你预约好会议室啦。"),
    (("图书馆", "借书", "还书"), "LED_BLUE_ON",
     "好的，已经帮你查到图书馆的信息啦。"),
    (("闹钟", "叫我起床", "提醒我"), "LED_BLUE_ON",
     "好的，闹钟已经帮你设好啦。"),
)
# 这些词说明用户在问学校的事（平台故障时用来决定要不要说"知识库连不上"）
_SCHOOL_WORDS = ("校训", "校区", "学校", "学院", "规章", "制度", "学生手册",
                 "宿舍", "食堂", "教务处", "辅导员", "校历", "校车", "图书馆")

# ================= 报修工单（工程债 C1：把"假装办事"换成"真产出"）=================
# ⚠️ 2026-10-15 实测出的漏网 ✗：原来靠 12 个整词，只能覆盖 **16 句里的 6 句（37%）** ✗
#    漏掉的全是"换个说法"：空调不制冷 ✓ 网连不上 ✓ 插座没电 ✓ 窗户关不上 ✓
#    有点吵 ✓ 宿舍太脏了 ✓ 热水器不出热水 ✓ 风扇不转 ✓ 灯泡闪 ✓ 床板断了 ✓
#  → 改成**组合判定**：出现【设备词】且出现【故障特征】才算报修 ✓✓
#     比整词覆盖广 ✓，又比"见词就报修"稳 ✓ ——
#     不会把"宿舍规定里说不能用大功率电器"这种**规定类问题**误判成报修 ✗
_REPAIR_WORDS = ("报修", "维修", "修一下", "修修", "叫师傅", "派师傅")
# 设备/设施（宿舍里真会坏的）
_THINGS = ("灯", "灯泡", "电灯", "空调", "风扇", "热水器", "水龙头", "水管", "下水",
           "厕所", "马桶", "淋浴", "花洒", "暖气", "门", "门锁", "窗", "窗户", "床",
           "床板", "桌子", "椅子", "插座", "开关", "电线", "洗衣机", "阳台", "柜子",
           "网", "网络", "宽带", "路由器", "wifi", "WiFi")
# 故障特征（**故意写具体短语** ✗ 不用一个"不"字 —— 免得"不舒服"被当成报修 ✓）
_FAULTS = ("坏了", "不亮", "不转", "不制冷", "不热", "不出水", "不出热水", "不通",
           "不上", "连不上", "上不了", "关不上", "打不开", "漏水", "漏电", "堵了",
           "堵住", "断了", "裂了", "松了", "闪", "跳闸", "停了", "没电", "没水",
           "没网", "没热水", "太脏", "很脏", "臭", "响", "噪音", "摇", "晃",
           "不通电", "很吵", "太吵")
# 有些故障**本身**就说明要报修，不必再提设备 ✓
# （2026-10-15 实测漏网：有点吵 ✓ 宿舍太脏了 ✓ —— 它们句子里根本没有"设备词" ✓）
_STANDALONE = ("有点吵", "太吵", "很吵", "太脏", "很脏", "很臭", "停电了")
_PROGRESS_WORDS = ("进度", "修好", "来人", "什么时候来", "多久能修", "单号", "工单",
                   "修得怎么样", "修了吗")


def _is_repair(query) -> bool:
    """这句话是不是"要报修" ✓

    三条任一成立即可 ✓：
      ① 显式说了 报修/维修/叫师傅 ✓
      ② 出现【设备词】且出现【故障特征】✓（主力规则 ✓）
      ③ 出现【本身就说明问题的说法】✓（有点吵 / 太脏 / 停电了 ✓）
    """
    q = query or ""
    if any(w in q for w in _REPAIR_WORDS):
        return True
    if any(w in q for w in _STANDALONE):
        return True
    return any(t in q for t in _THINGS) and any(f in q for f in _FAULTS)


def _room_in(text) -> str:
    """从**任意一句话**里提房间号 ✓（例如用户答"3 栋 502" ✓）

    ⚠️ 正则要允许**中间有空格**："3 栋 502" —— 上一版写成「栋后面不含空白」，
       结果只截出"3 栋"、把 502 丢了（自测当场抓到 ✓）
    """
    m = re.search(r"(\d+\s*(?:栋|号楼|公寓|舍)\s*\d{0,4}|\d{3,5}\s*室?)", text or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _guess_room() -> str:
    """从长期记忆里猜房间号（"用户住 3 栋 502" → "3 栋 502"）。

    为什么不问平台：房间号是我们自己记的事实（memory），问平台反而会编 ✗
    """
    for f in reversed(memory._memory.get("facts", [])):
        room = _room_in(f)
        if room:
            return room
    return ""


# ================= 补槽位：报修缺房间号就先问一句（2026-10-15 新增）=================
# 起因 ✗：`_guess_room()` 只在**记忆**里找房间 ✓ → 记忆是空的时候，
#   原来会直接建一张**房间为空**的工单 ✗（用户说了半天，工单上却没地址 ✓）
#   → 与其建一张残缺的单，不如**先问一句** ✓ 然后在下一句里补上 ✓✓
# 这是"智能体该有的样子"里最便宜的一种：**缺什么就问什么** ✓
#
# 设计取舍 ✓：
#   · 状态放**模块级**（单用户、单设备 ✓ 够用 ✓ 不引数据库 ✓）
#   · 带 **120 秒超时** ✓ —— 免得十分钟后一句无关的话被当成"补房间" ✗
#   · 追问时**不建单** ✓、**不碰平台** ✓（本地决定 ✓ 断网也能用 ✓✓）
_PENDING_TTL = 120.0
_pending = {"kind": "", "note": "", "ts": 0.0}


def _finish(query, reply, action, source):
    """统一收尾：写记忆 + 合成音频 + 组装返回 + 打一行链路总览。

    handle_query 的正常出口都走这里 —— 避免"新加一个分支就忘了记记忆/合成音频" ✗
    """
    memory.remember_turn(query, reply)
    with trace.timed("TTS"):
        audio_url = make_tts(reply)
    trace.log("chat", query=query, reply=reply, action=action, source=source,
              total=trace.total(), timings=trace.timings())
    trace.summary(f"动作={action} 来源={source}")
    return {
        "voice_text": reply,
        "device_action": action,
        "source": source,
        "audio_url": audio_url,
        "memory": len(memory._memory["facts"]),
        "history": len(memory._history),
        # 链路追踪（工程债 B4）：id 与各段耗时随响应回给板子/网页 ✓
        # 于是"用户侧现象"与"服务端日志"能对上；板子串口也会打这个 id ✓
        "request_id": trace.rid(),
        "timings": trace.timings(),
    }


def _action_guard(query, reply, action):
    """返回 (reply, action, source_override)。平台没办事时本地兜底，正常时原样返回。"""
    q = query or ""
    for words, act, local_text in _ACTION_RULES:
        if not any(w in q for w in words):
            continue
        final_action = act if (not action or action == "NONE") else action
        # 平台故障中，或答复里根本没提这件事（只是把"会议室"念在能力清单里）→ 本地兜底
        if _cloud_state["degraded"] or not any(w in (reply or "") for w in words):
            print(f"[动作] 平台答复没办事，本地兜底：{local_text}", flush=True)
            return local_text, final_action, "本地兜底"
        return reply, final_action, None
    return reply, action, None


def local_reply(query):
    # 断网时也能回答"我叫什么" —— 证明记忆是真存下来的
    if (("我叫什么" in query or "我是谁" in query or "你记得我" in query)
            and memory._memory["facts"]):
        return "你跟我说过：" + "；".join(memory._memory["facts"][-3:]), "NONE"
    # 报修：本地也该说人话（故障演练实测：以前只说"我在呢，有什么可以帮你的吗" ✗，
    # 用户明明在报修，答非所问很突兀；真工单由 handle_query 那边建 ✓）
    if _is_repair(query):
        return "好的，已经帮你登记报修啦，师傅会尽快联系你。", "LED_GREEN_ON"
    if "会议室" in query:
        return "好的，已经帮你预约好会议室啦。", "LED_GREEN_ON"
    if "图书馆" in query or "借书" in query:
        return "好的，已经帮你查到图书馆的信息啦。", "LED_BLUE_ON"
    if "闹钟" in query or "叫我起床" in query:
        return "好的，闹钟已经帮你设好啦。", "LED_BLUE_ON"
    if not query:
        return "我没听清，你再说一遍好吗？", "NONE"
    if _cloud_state["degraded"] and any(w in query for w in _SCHOOL_WORDS):
        # 平台故障、用户又问的是学校信息时说人话，别拿"我在呢"敷衍
        return ("后台知识库暂时连不上，学校信息这类问题我过会儿再答你；"
                "预约会议室、查图书馆、放歌这些现在都能用。"), "NONE"
    return "我在呢，有什么可以帮你的吗？", "NONE"

# ================= 失准自愈（2026-10-18 新增 ✓）=================
# 为什么需要 ✗：知识库检索实测约 **13% 随机失准** ✓（15 次里 2 次答"查不到" ✓）
#   而且我们**测出过原因之一** ✓：把「背景/历史」拼进 query 会**稀释检索** ✗
#   （实测：5 条事实时，"校训"这种临界条目最容易被顶出阈值 ✓）
# → 所以：**第一次答不上来时，用【裸问题】再问一次** ✓✓（裸问题检索最稳 ✓）
#   两条硬规矩 ✗：
#     ① **只在注入过上下文时才重试** ✓（enriched != query ✓）——
#        纯问题本来就没被稀释，重试只会白等 4~5 秒 ✗
#     ② **最多重试一次** ✓ —— 不能变成"答不上来就狂问" ✗
_WEAK_REPLY = ("查不到", "没查到", "没有找到", "找不到", "暂时没有",
               "没有相关信息", "无法回答", "不太清楚", "不确定")


def _is_weak(reply) -> bool:
    """这句话像不像「我查不到」✓（用来触发自愈 ✓ 不是用来判断"答得好不好" ✗）"""
    r = (reply or "").strip()
    return bool(r) and any(w in r for w in _WEAK_REPLY)


# ================= 意图 + 合成（文字入口和语音入口共用） =================
def handle_query(query):
    """返回 dict：voice_text / device_action / audio_url /
    source / memory / history。"""
    query = (query or "").strip()

    # 1) 先从这句话里提取长期记忆（"我叫小李"、"记住…"）
    with trace.timed("记忆"):
        for fact in memory.extract_facts(query):
            memory.add_fact(fact)

    # 2) 先判"问报修进度" —— **必须排在报修前面** ✗
    #    否则"我报修的进度怎么样了"会因为含"报修"两个字被当成新报修，
    #    凭空多建一张垃圾工单，进度还查成了那张新的（烟测实测踩到 ✓）
    if any(w in query for w in _PROGRESS_WORDS):
        with trace.timed("查台账"):
            t = tickets.newest()
        if t is not None:
            room = f"，{t['room']}" if t["room"] else ""
            reply = (f"你的报修单 {t['id']}（{t['category']}{room}）现在是"
                     f"「{tickets.status_text(t['status'])}」。")
            return _finish(query, reply, tickets.action_for(t["status"]), "本地台账")

    # 2.5) ⭐ 补槽位：上一句在报修、但没房间号 → 我们追问了一句 ✓
    #      这一句如果给了房间号，就补上并**建单** ✓✓（必须排在进度/报修判断之前 ✓）
    if _pending["kind"] == "repair" and (time.time() - _pending["ts"]) < _PENDING_TTL:
        room = _room_in(query)
        if room:
            with trace.timed("建单"):
                t = tickets.create(room=room,
                                   category=tickets.guess_category(_pending["note"]),
                                   note=_pending["note"] + " ｜ " + query)
            _pending.update(kind="", note="", ts=0.0)
            print(f"[工单] 补上房间号后建单 {t['id']} 房间={room}", flush=True)
            return _finish(
                query,
                f"好的，{room} 的报修已经登记好啦，工单号 {t['id']}，师傅会尽快联系你。",
                "LED_GREEN_ON", "本地补槽位")

    # 3) 报修 → **真的建一张工单**（工程债 C1：把"假装办事"换成"真产出"）
    #    注意：这一支**不提前返回** —— 答案措辞仍交给平台（它说得更自然），
    #    我们只负责"真建单"，最后把**真实工单号**补进回答 ✓
    repair_ticket = None
    if _is_repair(query):
        room = _guess_room()
        if not room:
            # ⭐ 缺房间号 → **先问，不建单** ✓（否则就是一张没地址的废单 ✗）
            _pending.update(kind="repair", note=query, ts=time.time())
            print(f"[追问] 报修但没房间号，先问一句：{query}", flush=True)
            return _finish(query, "好的，请问是哪个房间？比如「3 栋 502」。",
                           "NONE", "本地追问")
        with trace.timed("建单"):
            repair_ticket = tickets.create(room=room,
                                           category=tickets.guess_category(query),
                                           note=query)
        print(f"[工单] 新建 {repair_ticket['id']} 房间={repair_ticket['room'] or '未登记'} "
              f"类别={repair_ticket['category']}", flush=True)

    # 4) 放歌类请求单独走音乐分支：云端给不了音频
    if MUSIC_ENABLE:
        song, strict = music_request(query)
        if song is not None:
            with trace.timed("音乐"):
                result = handle_music(song, query, strict)
            if result is not None:
                # 音乐分支自己已经合成过音频 → 这里只补链路字段，**不重复 TTS** ✗
                result["request_id"] = trace.rid()
                result["timings"] = trace.timings()
                trace.log("music", query=query, total=trace.total(),
                          timings=trace.timings())
                trace.summary("放歌")
                return result

    # 5) 带着【已知信息 + 最近对话】去问大脑
    enriched = memory.build_query(query)
    if BRAIN == "local":
        with trace.timed("本地"):
            reply, action = local_reply(query)
        source = "本地规则"
    else:
        try:
            with trace.timed("平台"):
                reply, action = ask_cloud(enriched)
            source = "云端平台"
            # ⭐ 失准自愈：回答像「查不到」→ 重问一次 ✓
            #    ⚠️ 2026-10-18 更新 ✗：**两种情况都要重试**，但重试的东西不同 ✓
            #      ① 注入过上下文（enriched != query）→ 用【裸问题】✓
            #         （治的是「上下文稀释」这个**确定性**原因 ✓ 实测 5 条事实时临界条目会被顶出阈值 ✓）
            #      ② 纯问题（enriched == query）→ 用【同一个问题】✓
            #         （治的是「随机失准」这个**概率**原因 ✓ —— 检索本身约 13% 随机 ✗
            #           同一句话再问一次，等于给它第二次机会 ✓）
            #    我第一版只做了 ①✗ → 闸门立刻用「纯问题答不上来」这个场景证明了漏 □
            if _is_weak(reply):
                retry_q = query if enriched == query else query
                how = "原问题" if enriched == query else "裸问题"
                print(f"[自愈] 第一次像「查不到」→ 用{how}重问一次 ✓", flush=True)
                try:
                    with trace.timed("自愈重问"):
                        reply2, action2 = ask_cloud(retry_q)
                    if not _is_weak(reply2):
                        reply, action = reply2, action2
                        source = "云端平台·自愈"
                        trace.log("selfheal", ok=True, how=how, query=query[:60])
                        print("[自愈] 重问这次答出来了 ✓", flush=True)
                    else:
                        trace.log("selfheal", ok=False, how=how, query=query[:60])
                        print("[自愈] 重问还是没答出来（知识库里可能真的没有 ✓）", flush=True)
                except Exception as e2:      # 重问失败就忽略 ✓ 别把第一次的回答也弄丢 ✗
                    print(f"[自愈] 重问失败（忽略，用第一次的回答）: {type(e2).__name__}", flush=True)
            # 平台要是答非所问/没下发动件动作，这里按用户原话补上
            reply, action, override = _action_guard(query, reply, action)
            if override:
                source = override
        except Exception as e:
            print(f"[云端] 调用失败，回退本地规则: {type(e).__name__}: {e}", flush=True)
            with trace.timed("本地"):
                reply, action = local_reply(query)
            source = "本地回退"

    # 6) 这一轮如果是报修，把**真实工单号**补进回答，并把动作钉成"受理"= 绿灯
    if repair_ticket:
        reply = (f"{reply}（工单号 {repair_ticket['id']}，{repair_ticket['category']}，"
                 f"{tickets.status_text(repair_ticket['status'])}）")
        action = "LED_GREEN_ON"
        source = f"{source}·建单"

    return _finish(query, reply, action, source)
