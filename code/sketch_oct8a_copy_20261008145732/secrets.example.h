#pragma once
// 复制成 secrets.h 后填入真实值（secrets.h 已被 .gitignore 排除）
#pragma once
#define WIFI_SSID      "your-2.4g-hotspot"
#define WIFI_PASSWORD  "your-password"
#define SERVER_BASE    "http://192.168.137.1:5000"
#define SERVER_HOST    "192.168.137.1"
#define CLOUD_API_KEY  ""
#define DEVICE_USER    "esp32_stu_001"

// 固定 IP（可选）：=1 启用后板子 IP 固定不变；=0 用热点/路由器自动分配
// 注意选一个不容易被 DHCP 分出去的号，避免和别的设备撞车
#define USE_STATIC_IP   0
#define STATIC_IP       192, 168, 137, 39
#define STATIC_GATEWAY  192, 168, 137, 1
#define STATIC_SUBNET   255, 255, 255, 0