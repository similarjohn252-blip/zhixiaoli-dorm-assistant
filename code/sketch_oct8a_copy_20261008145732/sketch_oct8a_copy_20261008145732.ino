#include <WiFi.h>
// 2026-10-27 去掉 <WiFiClientSecure.h>：接唤醒后 3MB 的 APP 分区差 214KB 装不下，
//   而 apiUrl 指向的是我们自己的 http 服务端，那条 https 分支本来就是死代码。
//   去掉它等于不再链接整个 mbedTLS —— 这是最大的一块省法（实测减了 ~1MB）。
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "Audio.h"          // 音频库：负责从 HTTP 拉音频流 + 解码 + 推 I2S
#include "ESP_I2S.h"       // I2SClass：麦克风改走这个（ESP_SR 只认 I2SClass&）
#include "ESP_SR.h"        // 语音唤醒（芯片内置 Hi ESP 模型）
#include "esp_partition.h"  // 查 model 分区在不在（2026-10-29 加的防线，见 wakeWordInit）
#include "driver/i2s_std.h" // slot/位宽等枚举仍从这里来
// 2026-10-27：Wire 与 U8g2 一起去掉了（屏幕不要了）。
//   U8g2 连同 198KB 的 gb2312 中文字体不再被链接 —— 这是让唤醒装得下的关键。
#include <WebServer.h>      // 给网页一个入口：POST /profile、POST /volume、GET /status
#include <Preferences.h>    // NVS：把网页设过的音量记住，重启不忘

// ================= 全局对象 =================
WebServer server(80);       // 网页数据入口（端口 80）
Preferences prefs;          // NVS（非易失存储）：目前只存音量

// 音量默认值：与改动前一致（原来是写死的 21）
static const uint8_t DEFAULT_VOLUME = 21;

// ================= 配置区域 =================
// 两份配置，各管一摊 ✓（工程债 B1 尾巴：参数以前散在这个文件里 —— 顶部一批、第 700 行又一批 ✗）
//   · config.h  = 硬件与调参（**全队一致** ✓，跟代码入库 ✓）
//   · secrets.h = 网络与密钥（**每人不同** ✗，.gitignore 已排除 ✓）
// 首次使用：复制 secrets.example.h → secrets.h，填入真实值
#include "secrets.h"
#include "config.h"

const char* ssid     = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// 服务端入口（SERVER_BASE 形如 "http://192.168.137.1:5000"）
const char* apiUrl   = SERVER_BASE "/v1/chat";    // 相邻字符串字面量在编译期拼接，零开销
const char* voiceUrl = SERVER_BASE "/v1/voice";
#define SERVER_HOST_HINT SERVER_HOST              // OLED 提示用
const char* apiKey   = CLOUD_API_KEY;             // 仅"直连云端"那条路用到
const char* userId   = DEVICE_USER;

// 引脚与调参见 config.h ✓（工程债 B1 尾巴：原来散在这里，现在集中到一处 ✓）
// 下面这些是**由配置派生的变量/对象**，不是配置本身 ✓
// 屏幕对象已删（2026-10-27）。下面这些字符串保留：它们现在只喂串口日志，
//   排障时照样能看到"现在在干嘛、听到什么、回了什么"。
bool   oledReady  = false;   // 永远是 false —— 让所有"if (oledReady)"的绘制分支自然跳过
String oledStatus = "启动中";
String oledHeard  = "";
String oledReply  = "";
String oledFoot   = "";
unsigned long volBarUntil = 0;   // 音量条显示到什么时候（0 = 不显示 ✓）
String oledLastDrawn = "";
bool   oledStale  = false;   // true = 屏幕上的"听到/回复"是上一轮的，标签要写成"上次"
Audio audio; // 创建音频对象

String conversation_id = "";
// 放歌时服务端给的第二段音频（先播报、播完自动接着放歌）
String pendingAudioUrl = "";

// ================= 屏幕：已去掉（2026-10-27）=================
// 原来的 oledInit() 会扫 I2C 总线、oledRefresh() 会画四行中文 —— 都删了。
// 省下来的：U8g2 库 + 198KB 的 gb2312 中文字体（实测字体 202,690 字节）。
// 状态改成打串口：`[状态]` 开头的行就是原来屏幕上那几行。
bool oledInit() {
    Serial.println("[屏幕] 已移除（2026-10-27 决定），状态只走串口");
    return false;
}

void oledRefresh(bool force = false) {
    static String last = "";
    String sig = oledStatus + "|" + oledHeard + "|" + oledReply + "|" + oledFoot;
    if (!force && sig == last) return;      // 内容没变就不重复打
    last = sig;
    Serial.printf("[状态] %s", oledStatus.c_str());
    if (oledHeard.length()) Serial.printf(" ｜ 听到: %s", oledHeard.c_str());
    if (oledReply.length()) Serial.printf(" ｜ 回复: %s", oledReply.c_str());
    if (oledFoot.length())  Serial.printf(" ｜ %s", oledFoot.c_str());
    Serial.println();
}

// ================= 主程序入口 =================
// 打断重说的两个开关（必须定义在 loop() 之前 —— 它们是在按键分支里用 #if 判断的）
// ⚠️ 2026-10-29 又踩了一次同一个坑 ✗ —— 这几个 #define 原来写在文件后面（麦克风那段之后），
//    而 loop() 里用 #if 判断它们：预处理是按文件顺序走的，**后面的定义对前面的代码不可见** ✗，
//    未定义 = 0 → 整块代码被静默编译掉（表现是"改了代码、体积一字节没变"）。
//    所以凡是 loop() 里 #if 用到的，都必须定义在这里（loop 之前）。
#define USE_WAKE_WORD        1     // 0 = 不链接 ESP_SR（分区不对时救急用）
#define WAKE_HEARTBEAT_MS    6000  // 每 6 秒打一行"我在等 Hi ESP"，排查用；改 0 关掉

#define BARGE_ACK            1   // 1 = 打断后先说一句语气词；0 = 直接开始录

// 填充语的时长表与状态 —— 定义在文件后面「填充语」那一段，
// 但 loop() 里的打断流程要用，所以先声明（它们在那边不能是 static，否则链接属性冲突）
extern unsigned long fillerHiMs[];
extern unsigned long fillerFillMs[];
extern int fillerHiCount;
extern int fillerFillCount;
extern unsigned long fillerBusyUntil;
bool fillerPlay(const char* kind, int idx);
// ⚠️ 实的开关：填充语的发声目前还是占位实现（只打日志、不出声），
//    所以此刻"等它说完"等于白等。等板子到了把 fillerPlay() 填上（SPIFFS + audio 库），
//    把这个改成 1，打断就会真的先说"好的"再听你说。
#define FILLER_AUDIBLE       1   // 2026-10-29：fillerPlay 已实现，可以真的等它说完

// 唤醒状态（定义放前面：loop() 比麦克风那一段早，变量必须先声明）
static volatile bool wakeRequested = false;   // 事件回调只置标志，重活留给主循环
static bool wakeWordReady = false;            // 唤醒是否在跑
static bool wakeWordPaused = false;           // 当前是否被 pause 了

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("--- ESP32-S3 智小理终端启动 ---");

    // 电源灯：一上电就亮，直到断电（唯一的灯，只表示"我通电了"）
    pinMode(LED_POWER_PIN, OUTPUT);
    digitalWrite(LED_POWER_PIN, HIGH);

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
    bool wakeOk = micOk && wakeWordInit();   // 唤醒要麦克风先就绪
    // ⚠️ 2026-10-29 实测踩到的真崩溃 ✗ —— 这行原来有 5 个占位符却只给 4 个参数：
    //    "唤醒 -> %s" 没有对应实参，于是把 MIC_SCK(=15) 当成 char* 去解引用 →
    //    Guru Meditation: LoadProhibited，EXCVADDR=0x0f，板子无限重启。
    //    教训：改 printf 的格式串时，**参数必须一起加** ✗（编译器不报错，只在运行时炸）
    Serial.printf("micInit() -> %s   唤醒 -> %s   (SCK=IO%d WS=IO%d SD=IO%d)\n",
                  micOk ? "OK" : "失败！", wakeOk ? "就绪" : "跳过/失败",
                  MIC_SCK, MIC_WS, MIC_SD);
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

    // ⚠️ 2026-10-19 加 ✗：ESP32 默认开 modem sleep ✓
    //    闲置几分钟后射频进省电，链路其实已经死了 ✗
    //    但 WiFi.status() **仍然报「已连接」**✗ → loop 里那个 8 秒重连守护【永远不触发】✗
    //    表现就是：重启后能用 ✓ 闲置一会儿再按就「上传失败」✗（实测就是这个 ✓）
    //    → 关掉省电模式 ✓（代价：功耗略高 ✓ 但我们是 USB 供电 ✓ 无所谓 ✓）
    WiFi.setSleep(false);
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
            volBarUntil = millis() + VOL_BAR_MS;      // 让屏幕把音量条显示一会儿 ✓
            oledRefresh(true);
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

    // ---- 开机提示：原来那块大字画面随屏幕一起删了（2026-10-27）----
    // 现在只打串口 + 电源灯常亮，足够判断"起来了没有"。
    Serial.printf("【开机】Wi-Fi 已连，IP %s，电源灯常亮\n", WiFi.localIP().toString().c_str());

    // 填充语清单要等 WiFi 通了才取得到（只取 URL 和时长，不下载音频本体）
    if (wakeOk) fillerFetch();

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

    // 【唤醒】事件回调只置标志；重活放主循环（回调跑在 SR 自己的任务里，
    //   在那儿做 HTTP 会卡住它的喂数节奏）。
    if (wakeRequested) {
        wakeRequested = false;
        if (audio.isRunning()) {
            Serial.println("【唤醒】正在播报，这次先不理");
        } else {
            oledStatus = "我在";
            oledFoot   = "听到 Hi ESP";
            oledRefresh(true);
            listenAndReply();        // 复用按键那条路 —— 别写第二套流程
        }
    }

    // 【唤醒让路】播放期间闭嘴，免得把喇叭声当成唤醒词（播完自动恢复）
    if (audio.isRunning()) {
        wakeWordPause();
    } else {
        wakeWordResume();
    }

#if USE_WAKE_WORD
    // 【唤醒心跳】每 6 秒说一次"我在等" —— 专门用来排查"喊了没反应"：
    //   有心跳 = 固件认为唤醒任务在跑（问题在音量/口音/模型）
    //   没心跳 = 唤醒根本没上岗（问题在初始化或 pause 卡住）
    static unsigned long lastWakeBeat = 0;
    if (WAKE_HEARTBEAT_MS && millis() - lastWakeBeat > WAKE_HEARTBEAT_MS) {
        lastWakeBeat = millis();
        if (!wakeWordReady) {
            Serial.println("【唤醒心跳】唤醒没启用（分区/初始化问题）");
        } else if (wakeWordPaused) {
            Serial.println("【唤醒心跳】当前被 pause 着（在录音或播报）");
        } else {
            Serial.println("【唤醒心跳】我在等 Hi ESP…");
        }
    }
#endif

    // 【提醒】3 秒问一次服务端，到点本地播报（2026-10-22）
    // 两个函数内部都做了"正在播报就跳过"，不会抢音频的节奏 ✓
    pollReminders();
    checkReminders();

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
        } else {
            // 【打断重说】正在播报时按一下 = 立刻停下，重新听你说（2026-10-27 要求）
            if (audio.isRunning()) {
                Serial.println("【打断】停止播报，重新录音");
                audio.stopSong();
                // 必须顺手清掉 pendingAudioUrl：它是在"播放结束"那一刻才接上的
                // （见 loop 里的 wasRunning 分支）—— 不清的话，打断反而会立刻开始放歌 ✗
                if (pendingAudioUrl != "") {
                    Serial.println("【打断】顺带取消待播的歌曲");
                    pendingAudioUrl = "";
                }
                delay(80);   // 让解码器把最后一帧吐完，别和新录音抢 I2S
            }
#if BARGE_ACK
            // 停下之后先应一声，再听你说 —— 让用户知道"它听见你按了"，
            // 而不是按完一片安静、过了两秒才突然开始录音。
            // 用「语气词」那几条（好的 / 嗯嗯 / 行 / 好嘞 / 哦好的）：它们本来就是干这个的。
            if (fillerHiCount > 0) {
                int h = (int)random(fillerHiCount);
                if (fillerPlay("hi", h)) {
#if FILLER_AUDIBLE
                    // 真的出声的时候要等它说完（而且这段时间别让唤醒插嘴）
                    wakeWordPause();
                    while (millis() < fillerBusyUntil) {
                        audio.loop();
                        delay(10);
                    }
                    wakeWordResume();
#else
                    Serial.printf("【打断】应一声：hi%d（%lu ms，占位不出声）\n",
                                  h, fillerHiMs[h]);
#endif
                }
            }
#endif
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
// 2026-10-27：麦克风从 i2s_std 的裸 handle 改成 I2SClass ——
//   ESP_SR.begin() 收的是 I2SClass&，而 i2s_std 拿的是 handle，两者不能共用一个口。
//   它和唤醒共用这一路 I2S：唤醒时由 SR 的填充回调读，录音时我们自己读（靠 pause 让路）。
static I2SClass micI2s;
// 上一次录音的峰值：留在屏幕上，一眼看出麦克风到底有没有在工作
int micLastPeak = 0;

// 麦克风用的 I2S 槽（L/R 接 GND 为左声道）
#define MIC_SLOT (MIC_LR_LEFT ? I2S_STD_SLOT_LEFT : I2S_STD_SLOT_RIGHT)

// ================= 语音唤醒（2026-10-27）=================
// 用的是芯片内置模型，唤醒词 **Hi ESP**（英文）。
//   ⚠️ 中文唤醒词做不到 ✗ —— 核心预编译库里只有 CONFIG_SR_WN_WN9_HIESP，
//      中文命令词是 CONFIG_SR_MN_CN_NONE。要中文得从源码重编整个核心，那是天级的活。
// 我们**只开唤醒、不传命令词**：唤醒之后那句话照旧走录音 → 百度识别 → 平台 → TTS。
// ⚠️ 2026-10-27 暂停在 0：接上唤醒之后体积超了 3MB APP 分区（3,360,491 / 3,145,728，超 215 KB），
//    编译到最后一步 ld 报 "text section exceeds available space"。
//    代码本身是完整的（麦克风已迁到 I2SClass、事件/让路都写了），只是**装不下**。
//    改回 1 之前必须先腾出 ~215 KB，两条路：
//      ① 换小一号的中文字体：gb2312 198 KB → gb2312a 107 KB，省 91 KB（还差 124 KB）
//      ② 自定义分区表，把 APP 从 3MB 提到 ~3.6MB（模型 3.9MB + SPIFFS 5MB 仍放得下 16MB）
//    现在 = 0：不链接 ESP_SR，体积回到能装下的水平，固件随时可以烧。
#define WAKE_PAUSE_SETTLE_MS 40   // pause 之后等多久再读麦克风（原因见 wakeWordPause）
#define WAKE_DEBOUNCE_MS     1500 // 唤醒连发时只认第一次
#define WAKE_VERBOSE         1

// 这三个变量在文件前部声明（Arduino 只自动生成函数原型，不生成变量声明，
// 而 loop() 比这里早 —— 当初放这儿编译报的是 "'wakeRequested' was not declared"）

// SR 的事件回调（跑在 SR 自己的任务里，绝不能在这里做重活）
void onSrEvent(sr_event_t event, int command_id, int phrase_id) {
    static unsigned long lastWakeMs = 0;
    switch (event) {
        case SR_EVENT_WAKEWORD:
        case SR_EVENT_WAKEWORD_CHANNEL:
            // 同一次唤醒可能连发两个事件（AFE 在相邻两帧都判命中）→ 1.5 秒内只认一次
            if (lastWakeMs && (millis() - lastWakeMs) < WAKE_DEBOUNCE_MS) {
                Serial.println("【唤醒】连发，忽略");
                break;
            }
            lastWakeMs = millis();
            Serial.println("【唤醒】听到 Hi ESP ✓");
            wakeRequested = true;
            break;
        case SR_EVENT_TIMEOUT:
            Serial.println("【唤醒】超时，回到等待唤醒");
            ESP_SR.setMode(SR_MODE_WAKEWORD);
            break;
        default:
            Serial.printf("【唤醒】事件 %d（我们不处理）\n", (int)event);
            break;
    }
}

// 让唤醒暂时闭嘴（录音要用麦克风，播放时又怕把喇叭声当成唤醒词）
void wakeWordPause() {
#if USE_WAKE_WORD
    if (wakeWordReady && !wakeWordPaused) {
        if (ESP_SR.pause()) {
            wakeWordPaused = true;
            // ⚠️ sr_pause() 只是置了个事件位（esp32-hal-sr.c:452），**不等喂数据任务停** ✗
            //    它的填充回调此刻可能正卡在 i2s->readBytes() 里 → 立刻自己读会抢数据。
            //    等一小会儿让它收尾（一个 AFE 帧约 16~32ms，40ms 够）。
            //    🚧 上板要盯一件事：录音第一个字有没有被切掉；若有，把这个值加到 80。
            delay(WAKE_PAUSE_SETTLE_MS);
        }
    }
#endif
}

void wakeWordResume() {
#if USE_WAKE_WORD
    if (wakeWordReady && wakeWordPaused) {
        // 唤醒后 AFE 内部会切模式（官方的例子是切到命令词模式）；
        // 我们只做唤醒，所以每次重新上岗都显式切回 WAKEWORD，
        // 否则下一次说 Hi ESP 可能没反应。
        // ⚠️ 2026-10-29：顺序改成「先 resume、再 setMode」—— pause 期间切模式可能被吞。
        //
        // ⚠️⚠️ 2026-10-29 第二次踩坑（板子上表现为"成功一次，后面就不行了"）✗：
        //     上一版把 wakeWordPaused = false 这一行**改丢了** ✗ →
        //     resume() 明明成功了，标志却一直是 true →
        //     心跳一直报「当前被 pause 着」，而且每个循环都去 resume 一次，状态机被搅乱。
        //     **凡是"设置标志"的代码，改这段必须回头确认标志有没有被清** ✓
        if (ESP_SR.resume()) {
            ESP_SR.setMode(SR_MODE_WAKEWORD);
            wakeWordPaused = false;          // ← 这一行不能少
            if (WAKE_VERBOSE) Serial.println("【唤醒】重新上岗，等 Hi ESP");
        }
    }
#endif
}

bool wakeWordInit() {
#if USE_WAKE_WORD
    // ⚠️ 2026-10-29 实测教训（板子上真踩了）✗ ——
    //    分区方案不是 esp_sr_16 时，**ESP_SR.begin() 不会安全返回**：
    //    库里的 MODEL_LOADER 先打 "Can not find model in partition table"，
    //    紧接着空指针崩（Guru Meditation: LoadProhibited）→ **无限重启** ✗，
    //    板子直接变砖，连按键都不通。
    //    所以调它之前自己查一遍分区表：没有 model 分区就**跳过唤醒**，
    //    让板子正常启动、按键那条路照旧能用 ✓（失败要降级，不能连累整机）
    const esp_partition_t *mp = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "model");
    if (mp == nullptr) {
        Serial.println("【唤醒】✗ 分区表里没有 model 分区 —— 跳过唤醒，按键照旧可用");
        Serial.println("【唤醒】  修法：Tools → Partition Scheme 选 "
                       "ESP SR 16M (3MB APP/6MB SPIFFS/3.9MB MODEL)，然后整片重烧");
        Serial.println("【唤醒】  ⚠️ 只烧 app 不够 —— 分区表和 3.19MB 模型都得烧进去");
        return false;
    }
    Serial.printf("【唤醒】model 分区在 0x%06X，%u 字节\n",
                  (unsigned)mp->address, (unsigned)mp->size);

    ESP_SR.onEvent(onSrEvent);
    // sr_commands 传 nullptr/0：只要唤醒，不要命令词
    if (!ESP_SR.begin(micI2s, nullptr, 0, SR_CHANNELS_MONO, SR_MODE_WAKEWORD, "M")) {
        Serial.println("【唤醒】ESP_SR.begin 失败（分区方案是 esp_sr_16 吗？）");
        return false;
    }
    wakeWordReady = true;
    Serial.println("【唤醒】就绪，说 Hi ESP（英文）试试");
    return true;
#else
    return false;
#endif
}

bool micInit() {
    micI2s.setTimeout(1000);
    // setPins(bclk, ws, dout, din, mclk) —— 麦克风只用到 din，dout 传 -1
    micI2s.setPins(MIC_SCK, MIC_WS, -1, MIC_SD);

    // 32 位取数：麦克风是 24 位数据放在 32 位槽的高位
    if (!micI2s.begin(I2S_MODE_STD, REC_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT,
                      I2S_SLOT_MODE_MONO, MIC_SLOT)) {
        Serial.println("【麦克风】I2SClass.begin 失败");
        return false;
    }
    // 再让库直接转成 16 位：它的实现就是 dst[i] = src[i] >> 16，
    // 与原来手写的右移完全一致（所以音量和静音门限都不用重调）
    micI2s.configureRX(REC_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO,
                       I2S_RX_TRANSFORM_32_TO_16, MIC_SLOT);
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
    // 录音期间必须让唤醒让路，否则两个读者抢同一路 I2S，录进来的会是碎片
    wakeWordPause();
    micI2s.setTimeout(1000);

    int16_t* pcm = (int16_t*)(buf + 44);
    static int16_t block[256];          // 每次读 256 帧（16ms，16 位）
    size_t samples = 0;
    bool speechSeen = false;
    uint32_t silenceFrom = 0, lastPrint = 0, t0 = millis();
    int peakAll = 0;

    Serial.printf("【录音】开始说话，最多 %d 秒（说完静音 %.1f 秒自动结束）…\n",
                  REC_MAX_SECONDS, REC_SILENCE_MS / 1000.0);

    size_t readFails = 0, bytesIn = 0;   // 用来区分"读回来是 0 字节"和"读回来一堆 0"
    while (samples < maxSamples) {
        // 库已经用 TRANSFORM_32_TO_16 转好了，读回来就是 int16
        size_t got = micI2s.readBytes((char*)block, sizeof(block));
        bytesIn += got;

        int blockPeak = 0;
        size_t n = got / 2;
        for (size_t i = 0; i < n && samples < maxSamples; i++) {
            int32_t s = (int32_t)block[i] * MIC_GAIN;
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
    wakeWordResume();                 // 录完把唤醒还回去

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
// ================= 填充语：松手先说一句，再等答案（2026-10-27）=================
// 为什么要它：原来一次请求把板子钉死在 http.POST() 里 6~13 秒，等待期间是哑的。
//   现在改成"受理 + 轮询"，等待期间主循环是自由的，可以说话。
// 服务端侧：POST /v1/voice?async=1 → {id}；GET /v1/voice/result?id=… → working/done
#define USE_ASYNC_VOICE   1     // 改成 0 就退回"一次请求拿全部"的老路
#define FILLER_MAX_FILL   3     // 最多说三句填充语（三条轮完就安静等）
#define VOICE_POLL_MS     250   // 多久问一次结果
#define FILLER_GATE_MS    6000  // final 策略：第三句压到这个时刻之后再说
#define VOICE_DEADLINE_MS 25000 // 总超时

// 填充语的 **URL + 时长** —— 上电时从 /v1/fillers 取（只取清单，不下载音频本体）。
// 为什么不写死文件名：文案一改，服务端重新生成的文件名里带哈希、会变 ✗；
// 每次上电重新取，永远不会失配 ✓
// 下面的时长是"取不到清单时"的兜底值（与 tools\填充语时序.py 模拟器同一组数）。
#define FILLER_HI_MAX   8
#define FILLER_FILL_MAX 4
String fillerHiUrl[FILLER_HI_MAX];
String fillerFillUrl[FILLER_FILL_MAX];
unsigned long fillerHiMs[FILLER_HI_MAX]     = {480, 470, 430, 490, 630, 500, 500, 500};
unsigned long fillerFillMs[FILLER_FILL_MAX] = {1220, 1330, 1110, 1200};
int fillerHiCount   = 5;
int fillerFillCount = 3;

unsigned long fillerBusyUntil = 0;   // 本句预计播完的时刻
static int  fillerPlayed = 0;        // 已经说了几句填充语

// 上电取一次清单：GET /v1/fillers → 每条 {url, duration}
void fillerFetch() {
    HTTPClient http;
    static WiFiClient client;
    http.begin(client, serverBase() + "/v1/fillers");
    http.setTimeout(8000);
    int code = http.GET();
    if (code != 200) {
        Serial.printf("【填充语】清单拉取失败 %d（填充语不会出声，只按时间占位）\n", code);
        http.end();
        return;
    }
    String body = http.getString();
    http.end();

    DynamicJsonDocument doc(3072);
    if (deserializeJson(doc, body)) {
        Serial.println("【填充语】清单解不开（不出声，只占位）");
        return;
    }
    String base = serverBase();
    int hi = 0, fl = 0;
    for (JsonVariant v : doc["hi"].as<JsonArray>()) {
        if (hi >= FILLER_HI_MAX) break;
        String u = String(v["url"] | "");
        if (!u.length()) continue;
        fillerHiUrl[hi] = base + u;
        fillerHiMs[hi]  = (unsigned long)((v["duration"] | 0.0) * 1000.0);
        hi++;
    }
    for (JsonVariant v : doc["fill"].as<JsonArray>()) {
        if (fl >= FILLER_FILL_MAX) break;
        String u = String(v["url"] | "");
        if (!u.length()) continue;
        fillerFillUrl[fl] = base + u;
        fillerFillMs[fl]  = (unsigned long)((v["duration"] | 0.0) * 1000.0);
        fl++;
    }
    if (hi) fillerHiCount = hi;
    if (fl) fillerFillCount = fl;
    Serial.printf("【填充语】清单就绪：语气词 %d 条、填充语 %d 条%s\n",
                  hi, fl, (hi && fl) ? "（会出声）" : "（拿不到 URL，只占位）");
}

// 播一句。kind："hi"（语气词）/ "fill"（填充语）。
// 返回 true = 已经开始播（或至少按时间占位），调用方据此决定要不要等它。
bool fillerPlay(const char* kind, int idx) {
    const String* url = nullptr;
    unsigned long ms = 0;
    if (strcmp(kind, "hi") == 0 && idx >= 0 && idx < fillerHiCount) {
        url = &fillerHiUrl[idx];
        ms  = fillerHiMs[idx];
    } else if (strcmp(kind, "fill") == 0 && idx >= 0 && idx < fillerFillCount) {
        url = &fillerFillUrl[idx];
        ms  = fillerFillMs[idx];
    } else {
        return false;
    }
    if (audio.isRunning()) return false;          // 别抢正在放的东西

    bool played = false;
    if (url && url->length()) {
        played = audio.connecttohost(url->c_str());
    }
    Serial.printf("【填充语】%s #%d %lu ms  %s\n", kind, idx, ms,
                  played ? "出声" : (url && url->length() ? "拉流失败（按时间占位）"
                                                         : "没有 URL（只占位）"));
    // 多留 120ms：给拉流起播和尾巴留点余量，免得被答案硬切
    fillerBusyUntil = millis() + ms + 120;
    return true;
}

// 等填充语播完（不打断它）。等待期间也喂一下音频。
static void fillerWaitIdle() {
    while (millis() < fillerBusyUntil) {
        audio.loop();
        delay(10);
    }
}

// 等到答案、说完最后一句话之后再播。返回 true 表示已经处理完（成功或已报错）。
static bool voiceAsyncAndSpeak(uint8_t* wav, size_t wavLen) {
    static WiFiClient client;
    String url = String(voiceUrl) + "?async=1";

    HTTPClient http;
    http.begin(client, url);
    http.setTimeout(10000);
    http.addHeader("Content-Type", "audio/wav; rate=16000");

    oledStatus = "上传中…";
    oledRefresh(true);

    int code = http.POST(wav, wavLen);
    if (code != 200) {
        http.end();
        Serial.printf("【异步】受理失败 code=%d，退回同步\n", code);
        return false;                       // 交给老路（同步请求）兜底
    }
    String body = http.getString();
    http.end();

    StaticJsonDocument<256> doc;
    if (deserializeJson(doc, body)) {
        Serial.println("【异步】受理返回解不开");
        return false;
    }
    String id = String(doc["id"] | "");
    if (id.length() == 0) return false;
    Serial.printf("【异步】受理号 %s\n", id.c_str());

    // ① 松手立刻说一句语气词
    unsigned long t0 = millis();
    fillerPlayed = 0;
    if (fillerHiCount > 0) {
        int h = (int)(random(fillerHiCount));
        if (fillerPlay("hi", h)) {
            fillerBusyUntil = millis() + fillerHiMs[h];
        }
    }

    // ② 轮询
    String resultUrl = serverBase() + "/v1/voice/result?id=" + id;
    unsigned long lastPoll = 0;
    unsigned long deadline = millis() + VOICE_DEADLINE_MS;

    while (millis() < deadline) {
        audio.loop();

        // 上一句说完了；还有句子没说完；策略允许 → 再说一句
        bool idle = (millis() >= fillerBusyUntil);
        if (idle && fillerPlayed < FILLER_MAX_FILL && fillerPlayed < fillerFillCount) {
            bool gateOk = true;
            if (fillerPlayed >= 2) {        // 第三句：压到 6 秒之后（final 策略）
                gateOk = (millis() - t0) >= FILLER_GATE_MS;
            }
            if (gateOk) {
                if (fillerPlay("fill", fillerPlayed)) {
                    fillerPlayed++;
                }
            }
        }

        // 该问结果了
        if (millis() - lastPoll >= VOICE_POLL_MS) {
            lastPoll = millis();
            HTTPClient q;
            static WiFiClient qclient;
            q.begin(qclient, resultUrl);
            q.setTimeout(3000);
            q.addHeader("Authorization", String("Bearer ") + apiKey);
            int qc = q.GET();
            if (qc == 200) {
                String jb = q.getString();
                StaticJsonDocument<2048> jd;
                if (!deserializeJson(jd, jb)) {
                    String state = String(jd["state"] | "");
                    if (state == "done") {
                        q.end();
                        // ⚠️ 让当前这句填充语播完再开口 ✗ 硬切听着像卡带（模拟器发现的）
                        fillerWaitIdle();
                        handleServerResponse(jb);
                        return true;
                    }
                    if (state == "gone" || state == "error") {
                        q.end();
                        Serial.println("【异步】结果丢了或出错：" + jb.substring(0, 160));
                        oledStatus = "没听清";
                        oledFoot   = "再说一次试试";
                        oledStale  = true;
                        oledRefresh(true);
                        return true;
                    }
                    // working：继续等
                }
            } else {
                Serial.printf("【异步】轮询返回 %d\n", qc);
            }
            q.end();
        }
        delay(10);
    }

    Serial.println("【异步】等答案超时");
    oledStatus = "等太久了";
    oledFoot   = "网络或平台慢，再试一次";
    oledStale  = true;
    oledRefresh(true);
    return true;
}

void listenAndReply() {
    size_t wavLen = 0;
    uint8_t* wav = recordWav(&wavLen);
    if (!wav) return;
    if (wavLen < 44 + REC_SAMPLE_RATE / 2) {   // 不到 0.5 秒，别浪费一次识别
        Serial.println("【录音】太短，取消上传");
        free(wav);
        return;
    }

#if USE_ASYNC_VOICE
    // 先试异步：等待期间可以说"我查一下"（2026-10-27）
    if (voiceAsyncAndSpeak(wav, wavLen)) {
        free(wav);
        return;
    }
    Serial.println("【异步】不可用，走同步那条路");
#endif

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
    // 只有 http（我们自己的服务端）。https 那条路连同 mbedTLS 一起去掉了，见文件头的说明。
    http.begin(plainClient, apiUrl);

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

// ================= 提醒轮询（2026-10-22）=================
// 详见 code\课表与提醒_设计.md
//
// ⭐ 板子不存真实时间 —— 它没有 RTC 也没有 NTP，服务端给的是"还有多少秒"，
//    这里换算成 millis 截止点。不用对时、不用管时区 ✓
//
// ⭐ 播报在本地做：到点直接放服务端已经拼好的那个音频（叮咚 + 人声），
//    不在这时候再去请求服务端 —— 断网了已经排好的照样响 ✓
//
// ⚠️ 一次最多记 6 条。设计上一个学期二十来个时段，但真正"未来 6 小时内"的
//    同时不会超过几条，记多了白占内存。
struct RemindItem {
    String        id;
    unsigned long dueMs;
    String        url;
    String        say;
};

static RemindItem     reminds[6];
static int            remindCount = 0;
static unsigned long  lastRemindPollMs = 0;
static const unsigned long REMIND_POLL_MS = 3000;   // 3 秒一次：语音说"十秒后提醒我"最多晚 3 秒 ✓

static String serverBase() {
    // apiUrl 形如 http://192.168.137.1:5000/v1/chat，这里切掉 /v1/…
    String b = String(apiUrl);
    int i = b.lastIndexOf("/v1/");
    if (i > 0) b = b.substring(0, i);
    return b;
}

void pollReminders() {
    if (WiFi.status() != WL_CONNECTED) return;
    if (audio.isRunning()) return;              // 播报中，别抢网络和 CPU
    if (millis() - lastRemindPollMs < REMIND_POLL_MS) return;
    lastRemindPollMs = millis();

    static WiFiClient plainClient;
    String url = serverBase() + "/v1/reminders?hours=6";
    HTTPClient http;
    http.begin(plainClient, url);
    http.setTimeout(1500);                      // 局域网够用，别拖住 loop
    http.addHeader("Authorization", String("Bearer ") + apiKey);
    int code = http.GET();
    if (code != 200) {
        http.end();
        if (code > 0) Serial.printf("【提醒】轮询返回 %d\n", code);
        return;
    }
    String body = http.getString();
    http.end();

    DynamicJsonDocument doc(4096);
    if (deserializeJson(doc, body)) {
        Serial.println("【提醒】返回的 JSON 解不开");
        return;
    }

    for (JsonVariant v : doc["items"].as<JsonArray>()) {
        String id  = v["id"]        | "";
        long   left = v["in_seconds"] | -1;
        String say = v["say"]       | "";
        String u   = v["audio_url"] | "";
        if (id == "" || left < 0 || u == "") continue;   // 没音频就没法播，等下一轮

        bool known = false;
        for (int k = 0; k < remindCount; k++) {
            if (reminds[k].id == id) { known = true; break; }
        }
        if (known) continue;                             // 已经记过了，别重复播
        if (remindCount >= 6) continue;

        reminds[remindCount].id    = id;
        reminds[remindCount].dueMs = millis() + (unsigned long)left * 1000UL;
        reminds[remindCount].url   = u;
        reminds[remindCount].say   = say;
        remindCount++;
        Serial.printf("【提醒】记下 %s，还有 %ld 秒：%s\n", id.c_str(), left, say.c_str());
    }
}

void checkReminders() {
    if (remindCount == 0) return;
    if (audio.isRunning()) return;              // 正在播报，等它放完
    unsigned long now = millis();
    for (int k = 0; k < remindCount; k++) {
        if (now < reminds[k].dueMs) continue;
        Serial.printf("【提醒】到点！%s\n", reminds[k].say.c_str());
        oledStatus = "提醒：" + reminds[k].say;
        oledFoot   = "到点了";
        oledRefresh(true);
        bool ok = audio.connecttohost(reminds[k].url.c_str());
        Serial.printf("【提醒】播报 %s\n", ok ? "开始" : "失败（网络/解码）");
        // 放完就删，一次性
        for (int j = k; j < remindCount - 1; j++) reminds[j] = reminds[j + 1];
        remindCount--;
        return;
    }
}

// ================= 电源指示灯：一颗（2026-10-27）=================
// 用户决定：三色灯不要了，只留一颗指示开关机。
// 平台的 device_action 照旧会下发，但不再驱动灯光 —— 只在串口留个记录，
// 这样"平台到底下了什么指令"仍然查得到（排障用），又不用改平台那边。
void ledSet(const String &action) {
    pinMode(LED_POWER_PIN, OUTPUT);
    digitalWrite(LED_POWER_PIN, HIGH);        // 开机就是亮的 —— 它唯一的职责
    if (action.length() && action != "NONE" && action != "PLAY_MUSIC") {
        Serial.println("[灯] 电源灯常亮；平台下发的 " + action + " 不再驱动灯光");
    }
}
