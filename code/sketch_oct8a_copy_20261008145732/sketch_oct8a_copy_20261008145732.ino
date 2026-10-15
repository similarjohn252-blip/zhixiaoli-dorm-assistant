#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "Audio.h"          // 音频库：负责从 HTTP 拉音频流 + 解码 + 推 I2S
#include "driver/i2s_std.h" // 麦克风走独立的 I2S 输入通道（IDF 5.x 新 API）
#include <Wire.h>           // SSD1306 走硬件 I2C
#include <U8g2lib.h>        // OLED 驱动 + 中文字库
#include <WebServer.h>      // 给网页一个入口：POST /profile、POST /volume、GET /status
#include <Preferences.h>    // NVS：把网页设过的音量记住，重启不忘

// ================= 全局对象 =================
WebServer server(80);       // 网页数据入口（端口 80）
Preferences prefs;          // NVS（非易失存储）：目前只存音量

// 音量默认值：与改动前一致（原来是写死的 21）
static const uint8_t DEFAULT_VOLUME = 21;

// ================= 配置区域 =================
// 网络凭据与服务器地址在 secrets.h 里（.gitignore 已排除，不入库）
// 首次使用：复制 secrets.example.h → secrets.h，填入真实值
#include "secrets.h"

const char* ssid     = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// 服务端入口（SERVER_BASE 形如 "http://192.168.137.1:5000"）
const char* apiUrl   = SERVER_BASE "/v1/chat";    // 相邻字符串字面量在编译期拼接，零开销
const char* voiceUrl = SERVER_BASE "/v1/voice";
#define SERVER_HOST_HINT SERVER_HOST              // OLED 提示用
const char* apiKey   = CLOUD_API_KEY;             // 仅"直连云端"那条路用到
const char* userId   = DEVICE_USER;

// 定时发固定测试指令：0=关（现在靠麦克风触发），1=开（没接麦克风时用它验证播放链路）
#define AUTO_TEST_QUERY 0
// 上面开关打开时，每隔多久发一次测试指令（毫秒）。音频播放期间会自动跳过。
#define QUERY_INTERVAL_MS 15000

// ================= 麦克风（I2S 输入）配置 =================
// 常见 I2S MEMS 麦克风（INMP441 / ICS-43434 / MSM261S4030H0）接线一致：
//   VDD -> 3V3      GND -> GND      L/R -> GND(选左声道)
//   SCK -> MIC_SCK  WS  -> MIC_WS   SD  -> MIC_SD
// ⚠️ 麦克风用 I2S_NUM_1，功放用 I2S_NUM_0，两者互不干扰（ESP32-S3 有两个 I2S）
#define MIC_SCK 15
#define MIC_WS  16
#define MIC_SD  17
#define MIC_LR_LEFT 1        // L/R 接 GND 时为 1（左声道）；接 3V3 就改成 0
#define MIC_GAIN 2           // 实测 ×4 时峰值撞满 32768（削顶），降到 2；声音偏小再加大
#define MIC_SILENCE_PEAK 2200 // 实测底噪 ~1130、说话 ~4900（增益 2 之后），取中间偏下

// 录音参数：百度识别要求 16kHz / 单声道 / 16 位
#define REC_SAMPLE_RATE 16000
#define REC_MAX_SECONDS 6    // 单次最长录多久
#define REC_SILENCE_MS  1200 // 说完后静音这么久就自动停止
#define BUTTON_PIN 0         // 板子上的 BOOT 键（按下接地），按一下开始说话

// ================= OLED 显示屏配置（SSD1306 128x64，4 针）=================
// 模块丝印（常见顺序）: GND / VCC / SCL / SDA
//   GND -> GND     VCC -> 3V3     SCL -> IO47     SDA -> IO21
// ⚠️ 必须显式指定引脚：ESP32-S3 的默认 I2C 是 IO8/IO9，而这两个脚在你这块板子上被拉到了 3V3
#define OLED_SCL 47
#define OLED_SDA 21
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA);
bool   oledReady  = false;
String oledStatus = "启动中";
String oledHeard  = "";
String oledReply  = "";
String oledFoot   = "";
String oledLastDrawn = "";
bool   oledStale  = false;   // true = 屏幕上的"听到/回复"是上一轮的，标签要写成"上次"

// ================= 功放 I2S 配置 =================
#define I2S_BCLK 5
#define I2S_LRC  6
#define I2S_DIN  7
Audio audio; // 创建音频对象

String conversation_id = "";
// 放歌时服务端给的第二段音频（先播报、播完自动接着放歌）
String pendingAudioUrl = "";

// ================= OLED：初始化 + 刷新 =================
// 初始化时会顺手扫一遍 I2C 总线，把地址打出来 —— 屏幕不亮时一眼看出是"没接上"还是"地址不对"
bool oledInit() {
    Wire.begin(OLED_SDA, OLED_SCL);
    delay(50);
    uint8_t addr = 0;
    Serial.print("[OLED] I2C 扫描:");
    for (uint8_t a = 0x08; a < 0x78; a++) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) {
            Serial.printf(" 0x%02X", a);
            if (!addr) addr = a;
        }
    }
    if (!addr) {
        Serial.println(" 没找到任何 I2C 设备 —— 检查 GND/VCC/SCL/SDA 四根线");
        return false;
    }
    Serial.println();
    oled.setI2CAddress(addr << 1);   // U8g2 要的是 8 位地址
    oled.begin();
    Serial.printf("[OLED] 初始化完成，地址 0x%02X\n", addr);
    return true;
}

// 内容没变就不重复刷屏（硬件 I2C 刷一屏约 20ms，帮音频任务省点时间）
void oledRefresh(bool force = false) {
    if (!oledReady) return;
    String sig = oledStatus + "|" + oledHeard + "|" + oledReply + "|" + oledFoot;
    if (!force && sig == oledLastDrawn) return;
    oledLastDrawn = sig;

    oled.clearBuffer();
    oled.setFont(u8g2_font_wqy12_t_gb2312);   // 12px 中文字库（ASCII 也包含在内）
    oled.drawUTF8(0, 12, oledStatus.c_str());
    oled.drawUTF8(0, 28, (String(oledStale ? "上次听到: " : "听到: ") + oledHeard).c_str());
    oled.drawUTF8(0, 44, (String(oledStale ? "上次回复: " : "回复: ") + oledReply).c_str());
    oled.drawUTF8(0, 60, oledFoot.c_str());
    oled.sendBuffer();
}

// ================= 主程序入口 =================
void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("--- ESP32-S3 智小理终端启动 ---");

    // 把音频库的内部日志/事件接到串口上。
    // 默认它们走 ESP-IDF 的 log_e/log_w，被 Arduino 核心的 Core Debug Level=None 全吞掉了，
    // 所以"没声音"时串口什么都没有。挂上回调后日志会直接进这里打印。
    // 前提：IDE 里「工具 → Core Debug Level」要有级别（Debug=4），否则库里在入口就 return 了。
    Audio::audio_info_callback = [](Audio::msg_t m) {
        static String lastMsg;
        const char* tag = m.s ? m.s : "?";
        const char* msg = m.msg ? m.msg : "";
        // 这两类日志是音频任务里"每次循环"都打的，而且都在解码热路径上：
        // 115200 波特率下每行约 9ms，打日志本身就会把音频任务拖垮，所以直接过滤掉。
        if (strstr(msg, "calculateVolumeLimits")) return;
        if (strstr(msg, "m_audioDataReadPtr")) return;
        // 其余日志连续重复的只打第一条
        String cur = String(tag) + " " + msg;
        if (cur == lastMsg) return;
        lastMsg = cur;
        Serial.printf("[AUDIO] %s\n", cur.c_str());
    };
    Serial.println("音频库日志回调已挂载");

    // 0. 先点亮 OLED，之后所有状态都能在屏幕上看
    oledReady = oledInit();
    if (oledReady) {
        oledStatus = "智小理 启动中";
        oledFoot   = "正在初始化…";
        oledRefresh(true);
    }

    // 1. 初始化功放 I2S 引脚
    //    ⚠️ 这个库(ESP32-audioI2S v4)强制要求 PSRAM：psramInit() 失败时 setPinout() 直接返回 false，
    //       而且默认一行日志都不打 —— 现场表现就是"什么都没报错，就是没声音"。
    //       所以这里必须看返回值，别再像以前那样忽略它。
    bool audioOk = audio.setPinout(I2S_BCLK, I2S_LRC, I2S_DIN);
    Serial.printf("setPinout() -> %s\n", audioOk ? "OK" : "失败！(PSRAM 未启用，或板子根本没有 PSRAM)");
    Serial.printf("PSRAM: found=%d  size=%u  free=%u\n", psramFound(), ESP.getPsramSize(), ESP.getFreePsram());

    //   音量：默认步数 21，曲线 dB = -112t³ + 172t² - 60（t = vol/21）
    //     vol=5 -> -52 dB（几乎无声）  vol=15 -> -13 dB  vol=18 -> -4 dB  vol=21 -> 0 dB
    //   ⚠️ 必须先用 prefs.begin() 打开命名空间：
    //      否则 getInt 静默返回默认值、putInt 静默失败（不报错！表现就是"设置生效、重启就忘"）
    bool nvsOk = prefs.begin("zhixiaoli", false);
    int bootVol = nvsOk ? (int)prefs.getInt("volume", DEFAULT_VOLUME)
                        : (int)DEFAULT_VOLUME;
    audio.setVolume((uint8_t)constrain(bootVol, 0, 21));
    Serial.printf("音量: %u / %u（NVS %s；网页设置过就记住）\n",
                  audio.getVolume(), audio.getVolumeSteps(),
                  nvsOk ? "已打开" : "打开失败 ✗");

    // 2. 初始化麦克风（和功放互不干扰的第二个 I2S 通道）
    bool micOk = micInit();
    Serial.printf("micInit() -> %s   (SCK=IO%d WS=IO%d SD=IO%d, L/R 接 GND)\n",
                  micOk ? "OK" : "失败！", MIC_SCK, MIC_WS, MIC_SD);
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    if (micOk) Serial.printf("按一下 BOOT 键(IO%d) 就能说话\n", BUTTON_PIN);

    // 3. 连接 Wi-Fi
    //    注意：这里会一直等下去，一整块日志里只有一串点就等于"卡在这一步"，后面的代码一行都没跑。
#if USE_STATIC_IP
    // 固定 IP：演示时 IP 不再变（想改回自动分配：把 secrets.h 里 USE_STATIC_IP 设为 0）
    WiFi.mode(WIFI_STA);
    WiFi.config(IPAddress(STATIC_IP), IPAddress(STATIC_GATEWAY),
                IPAddress(STATIC_SUBNET));
    Serial.println("使用固定 IP（配置见 secrets.h）");
#endif
    WiFi.begin(ssid, password);
    unsigned long wifiTick = millis();
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        if (millis() - wifiTick > 5000) {
            wifiTick = millis();
            Serial.printf("\n[Wi-Fi] 仍未连上 (status=%d)。请检查：\n", WiFi.status());
            Serial.printf("        1) 电脑「移动热点」是否已打开（睡眠会自动关闭）\n");
            Serial.printf("        2) SSID 是否一字不差: \"%s\"（Windows 热点名字后面的数字会变）\n", ssid);
            Serial.printf("        3) 热点频段必须是 2.4GHz —— 设成 5GHz 时 ESP32 只会报 NO_AP_FOUND\n");
        }
    }
    Serial.println("\nWi-Fi 连接成功!");
    Serial.printf("本机 IP: %s\n", WiFi.localIP().toString().c_str());
    server.on("/profile", HTTP_POST, []() {
        String body = server.arg("plain");
        Serial.println("【网页收到个人信息】: " + body);
        server.send(200, "application/json", "{\"status\":\"ok\"}");
    });
    // 读当前音量（不改变任何东西）：浏览器/命令行随时能看
    server.on("/volume", HTTP_GET, []() {
        char buf[64];
        snprintf(buf, sizeof(buf), "{\"volume\":%u,\"steps\":%u}",
                 audio.getVolume(), audio.getVolumeSteps());
        server.send(200, "application/json", buf);
    });
    server.on("/volume", HTTP_POST, []() {
        String body = server.arg("plain");
        Serial.println("【网页收到音量控制】: " + body);
        // 真正应用音量：解析 {"volume": n}
        // 取值范围 0~21（音频库默认 21 档；固件原本写死的 18 就在这个范围里）
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, body);
        if (!err && doc["volume"].is<int>()) {
            int v = constrain(doc["volume"].as<int>(), 0, 21);
            audio.setVolume((uint8_t)v);
            Serial.printf("【音量已设为 %d】\n", audio.getVolume());
            prefs.putInt("volume", (int)audio.getVolume());   // 存进 NVS：重启不忘
        } else {
            Serial.println("【音量未改动】body 不是合法 JSON 或缺 volume 字段");
        }
        server.send(200, "application/json", "{\"status\":\"ok\"}");
    });
    server.on("/status", HTTP_GET, []() {
        server.send(200, "application/json", "{\"status\":\"online\"}");
    });
    // CORS 预检：网页用 fetch 发 POST(application/json) 之前，浏览器会先发一个 OPTIONS，
    // 它必须拿到 2xx，否则浏览器直接拦掉 POST（板子根本收不到 ✗）。
    // 注意：核心的 enableCORS(true) 只负责加响应头，不处理 OPTIONS —— 实测预检返回 404 ✗
    server.on("/profile", HTTP_OPTIONS, []() {
        server.send(200, "application/json", "{}");
    });
    server.on("/volume", HTTP_OPTIONS, []() {
        server.send(200, "application/json", "{}");
    });
    server.begin();
    server.enableCORS(true);   // 允许网页跨源 fetch（否则浏览器会拦掉 POST，板子根本收不到）
    Serial.println("WebServer 已启动，端口 80（已开 CORS）");
    oledStatus = "待机 按 BOOT 说话";
    oledFoot   = "IP " + WiFi.localIP().toString();
    oledRefresh(true);
}

void loop() {
    // 【核心】必须一直、尽快地调用 audio.loop()，否则网络音频会卡顿/断续。
    // 原来这里写 delay(15000)，等于每 15 秒把喂音频数据的节奏一起卡死，所以改成非阻塞计时。
    audio.loop();

    // 【网页入口】非阻塞，尽量放在 audio.loop() 之后，别抢音频喂数据的节奏。
    server.handleClient();

    // 播放状态每秒报一次。重点看「播放进度」有没有在往前走：
    //   进度在涨   -> 解码器确实在吐 PCM，问题只剩功放/喇叭侧
    //   进度一直 0 -> 流开了但没解码，问题在网络/解码这一侧
    // 注意：采样率/位深/声道报的是 I2S 输出配置，不能用来判断有没有在解码。
    static unsigned long lastAudioReportMs = 0;
    static bool wasRunning = false;
    if (audio.isRunning()) {
        if (!wasRunning) {
            wasRunning = true;
            Serial.println("【音频】流已打开，开始观察解码进度");
        }
        if (millis() - lastAudioReportMs >= 1000) {
            lastAudioReportMs = millis();
            Serial.printf("【音频】进度=%u秒 总时长=%u秒 输出=%uHz/%u位/%u声道 文件=%u字节 输入缓冲=%u/%u\n",
                          audio.getAudioCurrentTime(), audio.getAudioFileDuration(),
                          audio.getSampleRate(), audio.getBitsPerSample(), audio.getChannels(),
                          audio.getFileSize(), audio.inBufferFilled(), audio.getInBufferSize());
            oledStatus = "播放中 " + String(audio.getAudioCurrentTime()) + "s/" +
                         String(audio.getAudioFileDuration()) + "s";
            oledRefresh();
        }
    } else if (wasRunning) {
        wasRunning = false;
        Serial.println("【音频】播放结束");
        if (pendingAudioUrl != "") {
            // 先播报、再放歌：上一段放完自动接上歌曲
            String next = pendingAudioUrl;
            pendingAudioUrl = "";
            Serial.println("【接着播放歌曲】: " + next);
            bool ok = audio.connecttohost(next.c_str());
            Serial.printf("connecttohost() -> %s\n", ok ? "OK" : "失败（网络/解码器问题）");
            oledStatus = ok ? "放歌中…" : "放歌失败";
        } else {
            oledStatus = "待机 按 BOOT 说话";
        }
        oledRefresh(true);
    }

    // ---- Wi-Fi 掉了要自己往回连（2026-10-15 实测踩到 ✗）----
    //   服务端长时间不可达 / 电脑热点抖过一次之后，板子会**一直掉在网外** ✗ ——
    //   原代码只在按键时打印一句，**从不重连** ✓（ESP32 带静态 IP 时自动重连不可靠）
    //   每 8 秒试一次，别做成忙等 ✓
    static unsigned long lastWifiTry = 0;
    if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > 8000) {
        lastWifiTry = millis();
        Serial.printf("【Wi-Fi】掉线（status=%d），正在重连…\n", WiFi.status());
        WiFi.reconnect();
        oledStatus = "Wi-Fi 掉线，重连中";
        oledFoot   = "看电脑热点开着吗";
        oledRefresh(true);
    }

    // ---- 按一下 BOOT 键：说一句话 → 上传识别 → 播报回复 ----
    static bool lastBtn = HIGH;
    bool btn = digitalRead(BUTTON_PIN);
    bool pressed = (lastBtn == HIGH && btn == LOW);   // 只在按下的那一瞬间触发
    lastBtn = btn;
    if (pressed) {
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("【麦克风】Wi-Fi 没连上，先解决网络再说话");
            // 屏幕也要说实话 ✗ —— 原来只打串口，屏幕还留着上一次的"上传失败"，
            // 会让人以为还是服务端的问题（2026-10-15 实测被误导 ✓）
            oledStatus = "Wi-Fi 没连上";
            oledFoot   = "电脑热点开着吗";
            oledRefresh(true);
        } else if (audio.isRunning()) {
            Serial.println("【麦克风】正在播报，等它说完再按");
        } else {
            listenAndReply();
            lastBtn = digitalRead(BUTTON_PIN);  // 录音/播报期间按键状态可能变了，重新同步
        }
    }

#if AUTO_TEST_QUERY
    // 不想接麦克风时，用它验证「文字 -> 合成 -> 播放」这一段
    static bool firstQueryDone = false;
    static unsigned long lastQueryMs = 0;
    if (WiFi.status() == WL_CONNECTED && !audio.isRunning()) {
        if (!firstQueryDone || millis() - lastQueryMs >= QUERY_INTERVAL_MS) {
            firstQueryDone = true;
            lastQueryMs = millis();
            sendToZhiXiaoLi("帮我订个会议室");
        }
    }
#endif
}

// ================= 服务端响应的统一解析 + 播放 =================
// /v1/chat（文字）和 /v1/voice（语音）返回的结构一样，解析和播放都走这里
void handleServerResponse(String response) {
    DynamicJsonDocument responseDoc(response.length() + 1024);
    DeserializationError error = deserializeJson(responseDoc, response);

    if (error) {
        Serial.print("【外层JSON解析失败】: ");
        Serial.println(error.c_str());
        Serial.println("【原始响应】: " + response.substring(0, 300));
        return;
    }

    // 语音入口会多带一个字段：它到底听到了什么
    if (responseDoc.containsKey("asr_text")) {
        String heard = responseDoc["asr_text"].as<String>();
        if (heard != "") {
            Serial.println("【听到】: " + heard);
            oledHeard = heard;
        }
    }
    if (responseDoc.containsKey("error")) {
        String err = responseDoc["error"].as<String>();
        Serial.println("【服务端提示】: " + err);
        oledStatus = "服务端报错";
        oledFoot   = err.substring(0, 24);   // 常见：NotFound = 服务端没重启，接口不存在
        oledRefresh(true);
    }

    // 服务端会告诉我们这次是谁答的：云端平台 / 本地回退 / 本地规则
    String src = "";
    if (responseDoc.containsKey("source")) {
        src = responseDoc["source"].as<String>();
        if (src != "") Serial.println("【回答来源】: " + src);
    }

    // 记忆：长期已知信息条数 + 本轮带上的上下文轮数
    int memCount = responseDoc["memory"] | 0;
    int histCount = responseDoc["history"] | 0;
    if (memCount > 0 || histCount > 0) {
        Serial.printf("【记忆】已知信息 %d 条，本轮上下文 %d 轮\n", memCount, histCount);
    }

    // 链路 id（工程债 B4）：出问题时把这一串发给服务端，
    // 一条命令（tools\链路日志.py <id>）就能串出本次请求的全链路与各段耗时 ✓
    String reqId = responseDoc["request_id"] | "";
    if (reqId != "") Serial.println("【链路ID】: " + reqId);

    String voiceText = "";
    String deviceAction = "";
    String audioUrl = "";

    // 【智能判断】如果包含 answer，说明是云端；否则是本地
    // 每次响应先清掉"待播歌曲"，避免上一首残留到下一轮
    pendingAudioUrl = "";

    if (responseDoc.containsKey("answer")) {
        // ----- 云端逻辑 -----
        // 【重要】多轮对话：必须把平台返回的 conversation_id 带回去，否则平台每轮都当成新会话
        if (responseDoc.containsKey("conversation_id")) {
            String cid = responseDoc["conversation_id"].as<String>();
            if (cid != "" && cid != conversation_id) {
                conversation_id = cid;
                Serial.println("【会话ID】: " + cid);
            }
        }
        String answerText = responseDoc["answer"].as<String>();
        Serial.println("【云端原始answer】: " + answerText);

        int startIndex = answerText.lastIndexOf('{');
        int endIndex = answerText.lastIndexOf('}');

        if (startIndex != -1 && endIndex != -1 && endIndex > startIndex) {
            String cleanJson = answerText.substring(startIndex, endIndex + 1);
            DynamicJsonDocument innerDoc(cleanJson.length() + 512);
            DeserializationError innerError = deserializeJson(innerDoc, cleanJson);

            if (!innerError) {
                voiceText = innerDoc["voice_text"].as<String>();
                deviceAction = innerDoc["device_action"].as<String>();
            }
        }
    } else {
        // ----- 本地 Flask 逻辑 -----
        voiceText = responseDoc["voice_text"].as<String>();
        deviceAction = responseDoc["device_action"].as<String>();
        if (responseDoc.containsKey("audio_url")) {
            audioUrl = responseDoc["audio_url"].as<String>();
        }
        // 放歌：服务端会多给一个 audio_url2（歌曲本体），第一段播完自动接上
        if (responseDoc.containsKey("audio_url2")) {
            pendingAudioUrl = responseDoc["audio_url2"].as<String>();
            if (pendingAudioUrl != "") Serial.println("【待播歌曲】: " + pendingAudioUrl);
        }
    }

    if (voiceText == "") {
        Serial.println("【未提取到有效内容】");
        oledStatus = "响应里没有内容";
        oledFoot   = "看串口日志";
        oledRefresh(true);
        return;
    }

    Serial.println("【语音播报内容】: " + voiceText);
    Serial.println("【硬件控制指令】: " + deviceAction);
    ledSet(deviceAction);   // ← 真实点灯（C2）：绿=已受理 / 蓝=处理中 / 红=警示

    oledReply = voiceText;
    oledStale = false;   // 这一轮真的成功了，标签恢复成"听到/回复"
    // 第四行：来源 · 硬件动作 · 记忆条数（动作名缩写，才塞得进 128px）
    oledFoot = (src != "") ? src : String("本地");
    if (deviceAction == "LED_GREEN_ON")                    oledFoot += " · 绿灯";
    else if (deviceAction == "LED_BLUE_ON")                oledFoot += " · 蓝灯";
    else if (deviceAction == "LED_RED_ON")                 oledFoot += " · 红灯";
    else if (deviceAction == "PLAY_MUSIC")                 oledFoot += " · 音乐";
    else if (deviceAction != "" && deviceAction != "NONE") oledFoot += " · " + deviceAction;
    if (memCount > 0) oledFoot += " · 记忆" + String(memCount);
    oledRefresh(true);

    // 【重要】播放音频（本地服务器返回 audio_url 才能播，云端需要额外调 TTS）
    if (audioUrl == "" && pendingAudioUrl != "") {
        // 播报没合成出来，但歌曲有了：直接放歌，别让这次请求白跑
        audioUrl = pendingAudioUrl;
        pendingAudioUrl = "";
    }
    if (audioUrl != "") {
        Serial.println("【开始播放音频】: " + audioUrl);
        // 交给 I2S 去拉这段音频，loop() 里的 audio.loop() 会持续喂数据
        bool started = audio.connecttohost(audioUrl.c_str());
        Serial.printf("connecttohost() -> %s\n", started ? "OK" : "失败（URL/网络/解码器问题）");
        oledStatus = started ? "播放中…" : "播放失败";
        oledRefresh(true);
    }
}

// ================= 麦克风：采集 → 组 WAV → 上传识别 =================
static i2s_chan_handle_t micHandle = NULL;
// 上一次录音的峰值：留在屏幕上，一眼看出麦克风到底有没有在工作
int micLastPeak = 0;

bool micInit() {
    i2s_chan_config_t chanCfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    chanCfg.auto_clear = true;
    esp_err_t rc = i2s_new_channel(&chanCfg, NULL, &micHandle);
    if (rc != ESP_OK) {
        Serial.printf("【麦克风】i2s_new_channel 失败: %s\n", esp_err_to_name(rc));
        return false;
    }

    i2s_std_config_t stdCfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(REC_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = (gpio_num_t)MIC_SCK,
            .ws   = (gpio_num_t)MIC_WS,
            .dout = I2S_GPIO_UNUSED,
            .din  = (gpio_num_t)MIC_SD,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
#if MIC_LR_LEFT
    stdCfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;   // L/R 接 GND
#else
    stdCfg.slot_cfg.slot_mask = I2S_STD_SLOT_RIGHT;  // L/R 接 3V3
#endif
    rc = i2s_channel_init_std_mode(micHandle, &stdCfg);
    if (rc != ESP_OK) {
        Serial.printf("【麦克风】init_std_mode 失败: %s\n", esp_err_to_name(rc));
        return false;
    }
    return true;
}

// 44 字节标准 WAV 头（PCM）
void writeWavHeader(uint8_t* b, uint32_t pcmBytes, uint32_t rate) {
    uint32_t fileSize = pcmBytes + 36, byteRate = rate * 2, sub1 = 16;
    uint16_t fmt = 1, ch = 1, align = 2, bits = 16;
    memcpy(b,      "RIFF", 4);     memcpy(b + 4,  &fileSize, 4);
    memcpy(b + 8,  "WAVEfmt ", 8); memcpy(b + 16, &sub1, 4);
    memcpy(b + 20, &fmt, 2);       memcpy(b + 22, &ch, 2);
    memcpy(b + 24, &rate, 4);      memcpy(b + 28, &byteRate, 4);
    memcpy(b + 32, &align, 2);     memcpy(b + 34, &bits, 2);
    memcpy(b + 36, "data", 4);     memcpy(b + 40, &pcmBytes, 4);
}

// 录一句话，返回"WAV 头 + PCM"的整块内存（用完必须 free），长度写进 outLen；失败返回 nullptr
uint8_t* recordWav(size_t* outLen) {
    *outLen = 0;
    const size_t maxSamples = (size_t)REC_SAMPLE_RATE * REC_MAX_SECONDS;
    const size_t wavCap = maxSamples * 2 + 44;
    uint8_t* buf = (uint8_t*)ps_malloc(wavCap);   // 优先放 PSRAM（6 秒约 192KB，内部 RAM 放不下）
    if (!buf) buf = (uint8_t*)malloc(wavCap);
    if (!buf) { Serial.println("【录音】内存不足"); return nullptr; }
    if (i2s_channel_enable(micHandle) != ESP_OK) {
        free(buf);
        Serial.println("【录音】麦克风通道打不开");
        return nullptr;
    }

    int16_t* pcm = (int16_t*)(buf + 44);
    static int32_t block[256];          // 每次读 256 帧（16ms）
    size_t samples = 0;
    bool speechSeen = false;
    uint32_t silenceFrom = 0, lastPrint = 0, t0 = millis();
    int peakAll = 0;

    Serial.printf("【录音】开始说话，最多 %d 秒（说完静音 %.1f 秒自动结束）…\n",
                  REC_MAX_SECONDS, REC_SILENCE_MS / 1000.0);

    size_t readFails = 0, bytesIn = 0;   // 用来区分"读回来是 0 字节"和"读回来一堆 0"
    while (samples < maxSamples) {
        size_t got = 0;
        if (i2s_channel_read(micHandle, block, sizeof(block), &got, 1000) != ESP_OK) { readFails++; continue; }
        bytesIn += got;

        int blockPeak = 0;
        size_t n = got / 4;
        for (size_t i = 0; i < n && samples < maxSamples; i++) {
            // 麦克风是 24 位数据放在 32 位槽的高位，右移 16 位得到标准 16 位
            int32_t s = (block[i] >> 16) * MIC_GAIN;
            if (s > 32767) s = 32767; else if (s < -32768) s = -32768;
            pcm[samples++] = (int16_t)s;
            int a = abs((int)s);
            if (a > blockPeak) blockPeak = a;
        }
        if (blockPeak > peakAll) peakAll = blockPeak;

        // 简易静音检测：说过话之后连续静音就提前结束，不用干等满 6 秒
        if (blockPeak > MIC_SILENCE_PEAK) {
            speechSeen = true;
            silenceFrom = 0;
        } else if (speechSeen) {
            if (silenceFrom == 0) silenceFrom = millis();
            else if (millis() - silenceFrom > REC_SILENCE_MS) break;
        }

        if (millis() - lastPrint >= 500) {
            lastPrint = millis();
            Serial.printf("  录音中 %.1fs  峰值=%d（一直是 0 说明麦克风没接好/声道选错）\n",
                          (millis() - t0) / 1000.0, blockPeak);
            oledStatus = "录音中 " + String((millis() - t0) / 1000.0, 1) + "s 峰值" + String(blockPeak);
            oledFoot   = "说完静音 1.2 秒自动结束";
            oledRefresh();
        }
    }
    i2s_channel_disable(micHandle);   // 录完就关，免得把喇叭的声音又收回来

    size_t pcmBytes = samples * 2;
    writeWavHeader(buf, pcmBytes, REC_SAMPLE_RATE);
    *outLen = pcmBytes + 44;
    Serial.printf("【录音】结束: %.1f 秒 / %u 字节, 全程最大峰值=%d%s\n",
                  samples / (float)REC_SAMPLE_RATE, (unsigned)*outLen, peakAll,
                  speechSeen ? "" : "  <- 没听到说话声");
    // 0 字节 = I2S 通道根本没吐数据；非 0 却峰值 0 = 数据线一直是死零。两者病因完全不同。
    Serial.printf("【录音】麦克风共读到 %u 字节, 读取失败 %u 次\n", (unsigned)bytesIn, (unsigned)readFails);
    micLastPeak = peakAll;
    return buf;
}

// 按一下按钮的完整动作：录 → 传 → 播
void listenAndReply() {
    size_t wavLen = 0;
    uint8_t* wav = recordWav(&wavLen);
    if (!wav) return;
    if (wavLen < 44 + REC_SAMPLE_RATE / 2) {   // 不到 0.5 秒，别浪费一次识别
        Serial.println("【录音】太短，取消上传");
        free(wav);
        return;
    }

    HTTPClient http;
    static WiFiClient plainClient;
    http.begin(plainClient, voiceUrl);
    http.setTimeout(25000);   // 上传 + 百度识别 + 百度合成，留足时间
    http.addHeader("Content-Type", "audio/wav; rate=16000");

    oledStatus = "识别中…";
    oledFoot   = "峰值" + String(micLastPeak) + "  " + String((unsigned)wavLen) + " 字节";
    oledRefresh(true);

    Serial.printf("【上传】%u 字节 -> %s\n", (unsigned)wavLen, voiceUrl);
    int code = http.POST(wav, wavLen);
    if (code <= 0) {   // 连不上就再试一次：Wi-Fi 抖动很常见，重试一次能省掉一次按键
        Serial.println("【上传】第一次没连上，1 秒后重试…");
        delay(1000);
        http.end();
        http.begin(plainClient, voiceUrl);
        http.addHeader("Content-Type", "audio/wav; rate=16000");
        code = http.POST(wav, wavLen);
    }
    Serial.printf("【HTTP返回码】: %d\n", code);

    if (code == 200) {
        handleServerResponse(http.getString());
    } else if (code > 0) {
        String raw = http.getString();
        Serial.println("【服务端非200，原始响应】: " + raw.substring(0, 300));
        oledStatus = "服务端 " + String(code);
        oledFoot   = raw.substring(0, 24);
        oledStale  = true;
        oledRefresh(true);
    } else {
        Serial.println("【上传失败】连不上服务端（IP/端口/防火墙？）");
        oledStatus = "上传失败";
        oledFoot   = "Flask 在跑吗? IP 还是 " + String(SERVER_HOST_HINT) + " 吗?";
        oledStale  = true;
        oledRefresh(true);
    }
    http.end();
    free(wav);
}

// ================= 文字入口（不用麦克风时走这条） =================
void sendToZhiXiaoLi(String userQuery) {
    HTTPClient http;
    static WiFiClient plainClient;
    static WiFiClientSecure secureClient;
    secureClient.setInsecure();

    if (String(apiUrl).startsWith("https")) {
        http.begin(secureClient, apiUrl);
    } else {
        http.begin(plainClient, apiUrl);
    }

    http.setTimeout(8000); // 首次 TTS 合成 + 跨网段往返，5 秒有时不够
    http.addHeader("Authorization", String("Bearer ") + apiKey);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<512> requestDoc;
    requestDoc["query"] = userQuery;
    requestDoc["mode"] = "blocking";           // 旧字段（平台会忽略）
    requestDoc["response_mode"] = "blocking";  // Dify 规范字段，直连校园平台时要用
    requestDoc["user"] = userId;
    if (conversation_id != "") requestDoc["conversation_id"] = conversation_id;

    String requestBody;
    serializeJson(requestDoc, requestBody);
    Serial.println("【发送请求】: " + requestBody);

    int httpResponseCode = http.POST(requestBody);
    Serial.print("【HTTP返回码】: ");
    Serial.println(httpResponseCode);

    if (httpResponseCode == 200) {
        handleServerResponse(http.getString());
    } else if (httpResponseCode > 0) {
        // 服务端出错（500 等）时往往返回的是 HTML 错误页而不是 JSON，先把原文打出来，
        // 一眼就能分清是服务端问题还是解析问题。
        Serial.println("【服务端非200，原始响应】: " + http.getString().substring(0, 300));
    } else {
        Serial.print("【请求失败，错误码】: ");
        Serial.println(httpResponseCode);
    }
    http.end();
}

// ================= 三颗 LED：真实硬件动作（工程债 C2）=================
// 语义：绿 = 已受理 / 蓝 = 处理中 / 红 = 警示
//       NONE = 全灭（这一轮没有硬件动作） / PLAY_MUSIC = 不动灯（保留上一状态）
// 接线：GPIO --(220Ω 电阻；蓝灯建议 100Ω)--> LED 长脚(阳极)；LED 短脚(阴极) --> GND
// ⚠️ 引脚号按你实际接线改这三行即可；避开 IO33~37（本板 N16R8 的 OPI PSRAM 占用）
#define LED_GREEN_PIN 4
#define LED_BLUE_PIN  12
#define LED_RED_PIN   13

void ledSet(const String &action) {
    // 每次都设一遍 pinMode：幂等、无害，顺便省掉"忘了在 setup 里初始化"的风险 ✓
    pinMode(LED_GREEN_PIN, OUTPUT);
    pinMode(LED_BLUE_PIN, OUTPUT);
    pinMode(LED_RED_PIN, OUTPUT);

    if (action == "PLAY_MUSIC") return;   // 放歌不动灯

    digitalWrite(LED_GREEN_PIN, action == "LED_GREEN_ON" ? HIGH : LOW);
    digitalWrite(LED_BLUE_PIN,  action == "LED_BLUE_ON"  ? HIGH : LOW);
    digitalWrite(LED_RED_PIN,   action == "LED_RED_ON"   ? HIGH : LOW);
}
