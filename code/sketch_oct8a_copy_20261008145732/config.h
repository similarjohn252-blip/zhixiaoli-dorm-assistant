#pragma once
// ============================================================================
// 硬件与调参（工程债 B1 尾巴）
//
// 以前这些**散在 .ino 里** ✗ —— 顶部一批（麦克风/按键/OLED/I2S）、
// 第 700 行又一批（LED），改一个参数要在 700 行文件里翻找 ✓
// 现在集中到本文件一处 ✓
//
// 与 secrets.h 的分工（别搞混 ✓）：
//   · config.h  = 硬件与调参 —— **所有人一样** ✓，跟着代码入库 ✓
//   · secrets.h = 网络与密钥 —— **每人不同** ✗，已被 .gitignore 排除 ✓
//
// 改完任意一项 → 重新烧录即可生效 ✓
// ============================================================================

// ---- 定时测试开关（不接麦克风时验证"文字→合成→播放"）----
// 0 = 关（正常靠麦克风/按键触发）；1 = 开
#define AUTO_TEST_QUERY 0
// 上面开关打开时，每隔多久发一次测试指令（毫秒）。音频播放期间会自动跳过。
#define QUERY_INTERVAL_MS 15000

// ---- 麦克风（I2S 输入，用 I2S_NUM_1）----
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

// ---- 录音参数：百度识别要求 16kHz / 单声道 / 16 位 ----
#define REC_SAMPLE_RATE 16000
#define REC_MAX_SECONDS 6    // 单次最长录多久
#define REC_SILENCE_MS  1200 // 说完后静音这么久就自动停止

// ---- 说话按键 ----
#define BUTTON_PIN 0         // 板子上的 BOOT 键（按下接地），按一下开始说话

// ---- OLED（SSD1306 128x64，4 针，硬件 I2C）----
// 模块丝印（常见顺序）: GND / VCC / SCL / SDA
//   GND -> GND     VCC -> 3V3     SCL -> IO47     SDA -> IO21
// ⚠️ 必须显式指定引脚：ESP32-S3 的默认 I2C 是 IO8/IO9，而这两个脚在你这块板子上被拉到了 3V3
#define OLED_SCL 47
#define OLED_SDA 21

// ---- 功放 I2S（MAX98357，用 I2S_NUM_0）----
#define I2S_BCLK 5
#define I2S_LRC  6
#define I2S_DIN  7

// ---- 三颗 LED：真实硬件动作（工程债 C2）----// 语义：绿 = 已受理 / 蓝 = 处理中 / 红 = 警示
//       NONE = 全灭（这一轮没有硬件动作） / PLAY_MUSIC = 不动灯（保留上一状态）
// 接线：GPIO --(220Ω 电阻；蓝灯建议 100Ω)--> LED 长脚(阳极)；LED 短脚(阴极) --> GND
// ⚠️ 引脚号按你实际接线改这三行即可；避开 IO33~37（本板 N16R8 的 OPI PSRAM 占用）
// ⚠️ 截至 2026-10-15：**灯尚未接线、颜色未验证** ✗（代码与编译已就绪 ✓）
#define LED_GREEN_PIN 4
#define LED_BLUE_PIN  12
#define LED_RED_PIN   13

// ---- 屏幕显示（2026-10-15 加：拍照片/录视频更好看，也更好排障 ✓）----
#define BOOT_SPLASH_MS 1600    // 开机大字画面停留多久（足够拍照 ✓）
#define VOL_BAR_MS     2500    // 音量条显示多久（网页调音量时看得见 ✓）
#define SHOW_WIFI_RSSI 1       // 1 = 右上角画 Wi-Fi 信号格 ✓（演示时一眼看出还连着网 ✓）
