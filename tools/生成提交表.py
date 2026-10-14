"""照模板填写《作品提交表》，输出成新文件（不覆盖原模板）。

定位：自由选题 · 管理服务智能体 · 智能应用
作品：智小理·宿舍事务语音助手（宿舍后勤服务场景）
"""
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

SRC = r"C:\Users\ASUS\Downloads\2026 年“天翼 AI”杯重庆高校人工智能大赛作品提交表.docx"
DST = r"E:\天翼\作品提交表_已填.docx"

# ---------------- 要填的内容 ----------------
NAME = "智小理·宿舍事务语音助手"
DIRECTION = "自由选题（管理服务智能体 · 后勤与校园生活/宿舍事务服务）"
BRIEF = (
    "面向高校宿舍场景的语音智能体，让宿舍琐事一句话解决。学生按住终端按键说话即可："
    "①问宿舍管理规定（违禁电器、晚归报备、住宿调整等），答案检索自《学生宿舍管理规定》"
    "知识库、有出处不编造；②一句话发起报修，系统自动判断类别、生成带工单号的报修单，"
    "屏幕显示受理结果、指示灯同步亮起；③支持多轮追问与长期记忆（记住楼栋房间）。"
    "终端由 ESP32-S3＋麦克风＋功放＋OLED 组成，成本约百元。"
)
TECH = """一、选题与痛点
宿管与辅导员每天被重复问题包围（违禁电器、晚归报备、住宿调整），同一句话要答几十遍；报修靠微信群刷屏、容易漏单。本项目只做宿舍这一件事，把它做透。

二、系统架构
语音终端（ESP32-S3）→ 电脑服务端（Python/Flask，负责语音识别、意图路由、语音合成、工单落库）→ 智小理平台（工作流＋知识库检索）→ 音频回传终端播放，同时驱动屏幕与指示灯。
终端与电脑同一热点内通信；评委可通过网页入口用文字或录音体验同一套能力（无需硬件）。

三、关键实现
1) 语音链路：终端以 I2S 采集 16kHz/16bit 单声道 WAV 上传，服务端调用百度短语音识别；回答经百度语音合成成 WAV 回传，终端用 I2S 播放。双 I2S 通道分离（功放通道 0、麦克风通道 1），录音增益与静音阈值按现场实测标定，说话停 1.2 秒自动结束。
2) 知识库与 RAG：在智小理平台建“宿舍事务知识库”，上传《学生宿舍管理规定》《后勤服务指南》《报修流程》等文档；工作流采用“知识检索节点（相似度阈值 0.2、TopK 3~5）＋大模型节点”，把命中的原文注入提示词，要求模型只依据资料回答、查不到就如实说明，杜绝编造。
3) 意图路由与硬件动作：提示词按“业务办理→学校信息→闲聊”三级优先判断。业务类（报修/住宿调整）不再只做口头应答，而是生成真实工单（含工单号、时间、类别、房间号）写入台账，并让终端点亮对应指示灯（受理=绿、处理中=蓝、驳回=红）；信息类一律不动作。
4) 记忆：短期记忆保留最近 3 轮问答并随问题一起提交，实现“那明天呢”这类追问；长期记忆把用户主动告知的信息（楼栋、房间、偏好）落到本地文件，重启后仍有效。
5) 工程细节：OLED 实时显示“听到/回复/来源/记忆条数”；平台故障时自动切本地规则并如实提示，保证演示不中断；提供端到端自测脚本。

四、已完成的验证
端到端闭环跑通：说话→识别→知识库作答→语音播报→屏幕＋指示灯，平均 4~8 秒；知识库问答经 API 实测可答出规定原文；音频格式与参数经脚本校验合规；平台侧 7 项自动化测试全部通过。

五、边界与下一步
① 对接真实业务系统需学校开放接口与授权，当前以“生成工单台账＋指示灯反馈”实现可验证的闭环；② 隐私边界：语音经百度识别、知识文档托管在智小理平台，服务端不留存音频、工单仅存房间号与故障类别；③ 下一步接入后勤系统、增加唤醒词。"""
USAGE = (
    "方式一（推荐，无需硬件）：打开作品地址中的网页入口，直接输入文字或录音提问，"
    "即可看到回答、听到语音播报，并看到本条指令对应的硬件动作。"
    "方式二（硬件终端）：接通电源后终端自动联网，按住板载 BOOT 键说话，"
    "屏幕显示识别结果与回答，喇叭播报，指示灯同步亮起。"
    "建议体验三句：①“宿舍能用大功率电器吗”（知识库问答）②“我们宿舍的灯坏了，要报修”（生成工单＋绿灯）"
    "③“那明天能来人修吗”（多轮追问）。"
    "环境要求：终端与电脑处于同一 2.4GHz 热点；网页入口只需校园网。"
)
ADDRESS = "【待填：发布后填写网页体验入口】代码与工单示例可放网盘/仓库链接"

PENDING = "【待填】"


def set_text(cell, text, size=10.5, align=None, indent=True, bold=False):
    """把文字写进单元格：保留原有段落样式，只改文字与基本字体。"""
    cell.text = ""
    p = cell.paragraphs[0]
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "宋体"
    rpr = run._element.get_or_add_rPr()
    rf = rpr.get_or_add_rFonts()
    rf.set(qn("w:eastAsia"), "宋体")
    if indent and "\n" not in text and len(text) > 30:
        p.paragraph_format.first_line_indent = Pt(size * 2)
    return run


def find_row(tb, label):
    for row in tb.rows:
        if (row.cells[0].text or "").strip().replace(" ", "") == label:
            return row
    return None


d = Document(SRC)
tb = d.tables[0]

# 团队信息：保持模板原样（留空给团队自己填）——注意 队长手机号 是合并单元格，
# 若往 cells[5] 写东西会把标签覆盖掉，所以这里整段不碰。

# 作品信息
row = find_row(tb, "作品名称")
set_text(row.cells[1], NAME, indent=False)
# 选题方向：标签在第 4 列（row.cells 每次返回新对象，必须用 enumerate）
for i, c in enumerate(row.cells):
    if "选题方向" in (c.text or ""):
        set_text(row.cells[i + 1], DIRECTION, indent=False)
        break

row = find_row(tb, "作品简介")
set_text(row.cells[1], BRIEF, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
row = find_row(tb, "设计思路与技术实现")
set_text(row.cells[1], TECH, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
row = find_row(tb, "作品使用说明")
set_text(row.cells[1], USAGE, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
row = find_row(tb, "作品地址")
set_text(row.cells[1], ADDRESS, indent=False)

# 在表格后追加提示段（提交前删除）
d.add_paragraph()
tip = d.add_paragraph()
r = tip.add_run("【提交前删除本段】① 团队信息（队伍名称/队长姓名/学号/手机号/成员/指导教师）与作品地址请按实际情况填写；"
                "② 作品截图 3-5 张，建议：终端全貌（ESP32+麦克风+功放+OLED+指示灯）/ "
                "按 BOOT 说话时屏幕显示“听到·回复·来源·记忆N”/ 知识库问答原文（宿舍管理规定）/ "
                "报修工单台账（工单号·类别·房间号）/ 网页体验入口界面。")
r.font.size = Pt(10.5)
r.font.name = "宋体"
r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

d.save(DST)

print("已生成:", DST)
for tag, txt, limit in (("作品简介", BRIEF, 200), ("设计思路与技术实现", TECH, 1000),
                        ("作品使用说明", USAGE, 300)):
    n = len(re.sub(r"\s", "", txt))
    flag = "OK" if n <= limit else f"超了 {n - limit} 字！"
    print(f"  {tag}: {n} 字 / 上限 {limit}  → {flag}")
