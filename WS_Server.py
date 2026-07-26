# ws_server.py
# 修复版 - 去掉3D显示，传感器数据全量展示 + 网络监控显示

import asyncio
import json
import logging
import time
import os
from datetime import datetime
from typing import Dict, Any, Optional
import websockets

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== 配置文件 ====================
CONFIG_FILE = "server_config.json"

DEFAULT_CONFIG = {
    "ws_port": 32767,
    "web_port": 8080,
    "host": "0.0.0.0",
    "data_dir": "device_data",
    "enable_3d_view": True
}

def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                for key in DEFAULT_CONFIG:
                    if key not in config:
                        config[key] = DEFAULT_CONFIG[key]
                return config
    except Exception as e:
        logger.warning(f"加载配置文件失败: {e}")
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    return DEFAULT_CONFIG

CONFIG = load_config()

DATA_DIR = CONFIG.get("data_dir", "device_data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    logger.info(f"📁 数据存储目录: {DATA_DIR}")
except Exception as e:
    logger.error(f"❌ 创建数据目录失败: {e}")
    DATA_DIR = "."


class DeviceDataServer:
    def __init__(self):
        self.clients: Dict[str, websockets.WebSocketServerProtocol] = {}
        self.device_data: Dict[str, Dict] = {}
        self.device_last_seen: Dict[str, float] = {}
        self.device_info: Dict[str, Dict] = {}
        self.total_messages = 0
        self.running = False
        
    async def start(self):
        self.running = True
        ws_port = CONFIG.get("ws_port", 32767)
        host = CONFIG.get("host", "0.0.0.0")
        logger.info(f"🌐 WebSocket服务器启动在 {host}:{ws_port}")

        async with websockets.serve(
            self.handle_client,
            host,
            ws_port,
            ping_interval=30,
            ping_timeout=60,
            max_size=10 * 1024 * 1024
        ):
            await asyncio.Future()

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol):
        client_id = None

        try:
            message = await websocket.recv()
            try:
                data = json.loads(message)
                client_id = data.get('deviceId', str(id(websocket)))

                if client_id not in self.device_info:
                    self.device_info[client_id] = {
                        "first_seen": datetime.now().isoformat(),
                        "device_model": data.get('device', {}).get('model', 'Unknown'),
                        "device_manufacturer": data.get('device', {}).get('manufacturer', 'Unknown'),
                    }

                logger.info(f"📱 设备 {client_id} 连接成功")
            except json.JSONDecodeError:
                client_id = str(id(websocket))
                logger.warning(f"客户端 {client_id} 发送了无效的JSON")
                return

            self.clients[client_id] = websocket
            self.device_last_seen[client_id] = time.time()

            await websocket.send(json.dumps({
                "type": "welcome",
                "timestamp": int(time.time() * 1000),
                "message": "连接成功! 等待数据接收..."
            }))

            async for message in websocket:
                await self.process_message(client_id, message)

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"🔴 设备 {client_id} 连接断开")
        except Exception as e:
            logger.error(f"❌ 处理客户端 {client_id} 时出错: {e}")
        finally:
            if client_id and client_id in self.clients:
                del self.clients[client_id]
                if client_id in self.device_last_seen:
                    del self.device_last_seen[client_id]

    async def process_message(self, client_id: str, message: str):
        try:
            data = json.loads(message)
            data_type = data.get('dataType', 'unknown')
            self.total_messages += 1

            self.device_last_seen[client_id] = time.time()
            self.device_data[client_id] = data

            if 'device' in data:
                self.device_info[client_id] = {
                    "first_seen": self.device_info.get(client_id, {}).get("first_seen", datetime.now().isoformat()),
                    "device_model": data['device'].get('model', 'Unknown'),
                    "device_manufacturer": data['device'].get('manufacturer', 'Unknown'),
                    "screen_width": data['device'].get('screenWidth', 1080),
                    "screen_height": data['device'].get('screenHeight', 2400),
                    "last_update": datetime.now().isoformat()
                }

            self.save_data_to_file(client_id, data)

            if data_type == 'full':
                await self.handle_full_data(client_id, data)
            elif data_type == 'diff':
                await self.handle_partial_data(client_id, data)

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误: {e}")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")

    async def handle_full_data(self, client_id: str, data: Dict[str, Any]):
        logger.info(f"📥 收到设备 {client_id} 的完整数据")

    async def handle_partial_data(self, client_id: str, data: Dict[str, Any]):
        logger.info(f"📥 收到设备 {client_id} 的增量数据")

    def save_data_to_file(self, client_id: str, data: Dict[str, Any]):
        try:
            filename = f"device_{client_id}.json"
            filepath = os.path.join(DATA_DIR, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ 保存数据失败: {e}")

    def get_device_stats(self) -> Dict[str, Any]:
        now = time.time()
        stats = {
            "total_clients": len(self.clients),
            "active_devices": len([c for c, t in self.device_last_seen.items() if now - t < 300]),
            "total_messages": self.total_messages,
            "devices": {}
        }

        for client_id, data in self.device_data.items():
            last_seen = self.device_last_seen.get(client_id, 0)
            info = self.device_info.get(client_id, {})
            data_timestamp = data.get('timestamp', 0)
            
            # 提取网络信息
            network = data.get('network', {})
            
            stats["devices"][client_id] = {
                "last_seen": datetime.fromtimestamp(last_seen).isoformat() if last_seen else None,
                "data_timestamp": data_timestamp,
                "data_fields": len(data),
                "has_battery": 'battery' in data,
                "has_foreground": 'foreground' in data,
                "has_location": 'location' in data and data.get('location', {}).get('hasLocation', False),
                "has_sensors": 'sensors' in data,
                "has_network": 'network' in data,
                "permission_level": data.get('permissionLevel', 'unknown'),
                "device_model": info.get('device_model', 'Unknown'),
                "device_manufacturer": info.get('device_manufacturer', 'Unknown'),
                "screen_width": info.get('screen_width', 1080),
                "screen_height": info.get('screen_height', 2400),
                "first_seen": info.get('first_seen', 'Unknown'),
                # 网络信息
                "network": {
                    "type": network.get('type', '未知'),
                    "detail": network.get('detail', ''),
                    "isConnected": network.get('isConnected', False),
                    "isWifi": network.get('isWifi', False),
                    "isMobile": network.get('isMobile', False),
                    "ip": network.get('ip', '未知'),
                    "signalLevel": network.get('signalLevel'),
                    "networkType": network.get('networkType'),
                    "downSpeed": network.get('downSpeed', 0),
                    "upSpeed": network.get('upSpeed', 0),
                    "downSpeedStr": network.get('downSpeedStr', '0 B/s'),
                    "upSpeedStr": network.get('upSpeedStr', '0 B/s'),
                    "intervalRx": network.get('intervalRx', 0),
                    "intervalTx": network.get('intervalTx', 0),
                    "intervalRxStr": network.get('intervalRxStr', '0 B'),
                    "intervalTxStr": network.get('intervalTxStr', '0 B'),
                    "totalRx": network.get('totalRx', 0),
                    "totalTx": network.get('totalTx', 0),
                    "totalRxStr": network.get('totalRxStr', '0 B'),
                    "totalTxStr": network.get('totalTxStr', '0 B')
                } if network else None
            }

        return stats


# ==================== HTML页面 ====================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>📱 设备数据监控</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #0a0e17; color: #e0e0e0; padding: 20px; }
        .container { max-width: 1600px; margin: 0 auto; }
        
        .header { 
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            padding: 25px 30px; 
            border-radius: 16px;
            margin-bottom: 20px;
            border: 1px solid rgba(100, 200, 255, 0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }
        .header h1 { font-size: 24px; font-weight: 600; }
        .header h1 span { color: #00d4ff; }
        .header .stats-info { display: flex; gap: 30px; flex-wrap: wrap; }
        .header .stats-info .stat-item { text-align: center; }
        .header .stats-info .stat-item .num { font-size: 28px; font-weight: 700; color: #00d4ff; }
        .header .stats-info .stat-item .label { font-size: 12px; color: #8899aa; }
        
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media (max-width: 1200px) { .grid { grid-template-columns: 1fr; } }
        
        .card {
            background: rgba(20, 30, 50, 0.9);
            border-radius: 16px;
            border: 1px solid rgba(100, 200, 255, 0.08);
            overflow: hidden;
            transition: all 0.3s ease;
        }
        .card:hover { border-color: rgba(100, 200, 255, 0.2); }
        .card.offline { opacity: 0.5; }
        
        .card-header {
            padding: 15px 20px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: rgba(0, 0, 0, 0.2);
            user-select: none;
            transition: background 0.2s;
            flex-wrap: wrap;
            gap: 8px;
        }
        .card-header:hover { background: rgba(0, 100, 200, 0.1); }
        .card-header .device-name {
            font-size: 16px;
            font-weight: 600;
        }
        .card-header .device-name .model { color: #00d4ff; }
        .card-header .device-status {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 13px;
            flex-wrap: wrap;
        }
        .card-header .device-status .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }
        .card-header .device-status .dot.online { background: #00ff88; box-shadow: 0 0 10px #00ff8866; }
        .card-header .device-status .dot.offline { background: #ff4444; }
        .card-header .toggle-icon { font-size: 18px; transition: transform 0.3s; display: inline-block; }
        .card-header .toggle-icon.open { transform: rotate(180deg); }
        
        .card-body { display: none; padding: 20px; }
        .card-body.open { display: block; }
        
        .data-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 10px;
        }
        .data-item {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px 14px;
            border-radius: 8px;
            border-left: 3px solid #00d4ff;
        }
        .data-item .label { font-size: 11px; color: #8899aa; text-transform: uppercase; letter-spacing: 0.5px; }
        .data-item .value { font-size: 15px; font-weight: 600; margin-top: 2px; }
        .data-item .value.good { color: #00ff88; }
        .data-item .value.warning { color: #ffaa00; }
        .data-item .value.danger { color: #ff4444; }
        .data-item .sub { font-size: 11px; color: #667788; margin-top: 2px; }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #8899aa;
        }
        .empty-state .icon { font-size: 48px; margin-bottom: 16px; }
        
        .full-width { grid-column: 1 / -1; }
        
        .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
        
        .sensor-section {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 16px;
            min-height: 200px;
        }
        .sensor-section .section-title {
            font-size: 13px;
            color: #8899aa;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .sensor-section .section-title .count {
            background: rgba(0, 200, 255, 0.15);
            padding: 0 10px;
            border-radius: 12px;
            font-size: 12px;
            color: #00d4ff;
        }
        
        .sensor-grid-full {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 8px;
        }
        .sensor-item-full {
            background: rgba(0, 0, 0, 0.25);
            padding: 10px 14px;
            border-radius: 8px;
            border-left: 3px solid #00d4ff;
            display: flex;
            flex-direction: column;
        }
        .sensor-item-full .sensor-name {
            font-size: 11px;
            color: #8899aa;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .sensor-item-full .sensor-value {
            font-size: 15px;
            font-weight: 600;
            color: #00d4ff;
            margin-top: 2px;
            font-family: monospace;
        }
        .sensor-item-full .sensor-value.good { color: #00ff88; }
        .sensor-item-full .sensor-value.warning { color: #ffaa00; }
        .sensor-item-full .sensor-value.danger { color: #ff4444; }
        
        .no-sensor {
            color: #556677;
            font-size: 13px;
            text-align: center;
            padding: 30px 0;
        }
        
        .card-footer {
            padding: 10px 20px;
            background: rgba(0, 0, 0, 0.15);
            border-top: 1px solid rgba(100, 200, 255, 0.05);
            font-size: 12px;
            color: #556677;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }
        .card-footer .update-time {
            color: #8899aa;
        }
        .card-footer .update-time .time-value {
            color: #00d4ff;
            font-family: monospace;
        }

        /* 网络状态指示 */
        .network-status {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 12px;
            background: rgba(0, 0, 0, 0.3);
        }
        .network-status .connected { color: #00ff88; }
        .network-status .disconnected { color: #ff4444; }
        
        .speed-display {
            display: flex;
            gap: 16px;
            font-size: 13px;
            font-family: monospace;
        }
        .speed-display .down { color: #00d4ff; }
        .speed-display .up { color: #ffaa00; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div>
            <h1><span>Ext</span>Link</h1>
            <div style="font-size:13px;color:#8899aa;margin-top:4px;">
            </div>
        </div>
        <div class="stats-info">
            <div class="stat-item">
                <div class="num" id="totalClients">0</div>
                <div class="label">在线设备</div>
            </div>
        </div>
    </div>

    <div class="grid" id="devicesContainer">
        <div class="full-width empty-state">
            <div class="icon">📡</div>
        </div>
    </div>
</div>

<script>
    // ==================== 状态管理 ====================
    var deviceDataCache = {};
    var deviceStats = { total_clients: 0, total_messages: 0 };
    var cardStates = {};
    var isUpdating = false;
    var pendingUpdate = false;
    
    // 传感器中文名称映射
    var sensorNameMap = {
        'accelerometer': '加速度计',
        'gyroscope': '陀螺仪',
        'magnetic_field': '磁力计',
        'gravity': '重力',
        'linear_acceleration': '线性加速度',
        'orientation': '方向',
        'light': '光线',
        'proximity': '距离',
        'ambient_temperature': '环境温度',
        'pressure': '压力',
        'relative_humidity': '相对湿度',
        'temperature': '温度',
        'step_counter': '计步器',
        'heart_rate': '心率',
        'battery': '电池'
    };

    // ==================== 格式化工具 ====================
    function formatSensorValue(val) {
        if (val === undefined || val === null) return 'N/A';
        if (typeof val === 'object') {
            if (val.x !== undefined && val.x !== null) {
                return val.x.toFixed(2) + ', ' + val.y.toFixed(2) + ', ' + val.z.toFixed(2);
            } else if (val.value !== undefined && val.value !== null) {
                return val.value.toFixed(1);
            }
            return JSON.stringify(val).substring(0, 40);
        }
        return String(val);
    }

    function getSensorValueClass(val) {
        if (val === undefined || val === null) return '';
        if (typeof val === 'object') {
            if (val.value !== undefined) {
                var num = parseFloat(val.value);
                if (!isNaN(num)) {
                    if (num > 80) return 'good';
                    if (num > 30) return 'warning';
                    return 'danger';
                }
            }
        }
        return '';
    }
    
    function formatTimestamp(ts) {
        if (!ts || ts <= 0) return '未知';
        try {
            var d = new Date(ts);
            if (isNaN(d.getTime())) return '无效时间';
            return d.toLocaleString('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        } catch (e) {
            return '无效时间';
        }
    }

    // ==================== UI渲染 ====================
    function renderDevices(data) {
        var container = document.getElementById('devicesContainer');
        if (!container) return;

        var deviceKeys = Object.keys(data.devices || {});

        if (deviceKeys.length === 0) {
            container.innerHTML = `
                <div class="full-width empty-state">
                    <div class="icon">📡</div>
                </div>
            `;
            return;
        }

        var html = '';

        for (var clientId in data.devices) {
            var dev = data.devices[clientId];
            var detail = deviceDataCache[clientId] || {};
            var isOnline = dev.last_seen !== null;

            var battery = detail.battery || {};
            var batteryLevel = battery.level !== undefined ? battery.level : '?';
            var batteryClass = batteryLevel >= 50 ? 'good' : (batteryLevel >= 20 ? 'warning' : 'danger');

            var foreground = detail.foreground || {};
            var fgName = foreground.packageName || 'Unknown';

            var memory = detail.memory || {};
            var memoryTotal = memory.total || 0;
            var memoryUsed = memory.used || 0;
            var memoryMB = memoryTotal > 0 ? (memoryTotal / 1024 / 1024).toFixed(0) : '?';
            var memoryUsedMB = memoryUsed > 0 ? (memoryUsed / 1024 / 1024).toFixed(0) : '?';
            var memoryPercent = memory.usagePercent || '?';

            var storage = detail.storage || {};
            var storageTotal = storage.total || 0;
            var storageUsed = storage.used || 0;
            var storageGB = storageTotal > 0 ? (storageTotal / 1024 / 1024 / 1024).toFixed(1) : '?';
            var storageUsedGB = storageUsed > 0 ? (storageUsed / 1024 / 1024 / 1024).toFixed(1) : '?';
            var storagePercent = storage.usagePercent || '?';

            var screen = detail.screen || {};
            var screenStatus = screen.isOn ? '🟢 亮屏' : '🔴 熄屏';
            var brightness = screen.brightness || '?';

            var location = detail.location || {};
            var hasLocation = location.hasLocation || false;
            var locationStr = hasLocation ? location.latitude.toFixed(4) + ', ' + location.longitude.toFixed(4) : '未获取';

            var sensors = detail.sensors || {};

            // ===== 网络信息 =====
            var network = dev.network || {};
            var netType = network.type || '未知';
            var netConnected = network.isConnected || false;
            var netDownSpeed = network.downSpeedStr || '0 B/s';
            var netUpSpeed = network.upSpeedStr || '0 B/s';
            var netIntervalRx = network.intervalRxStr || '0 B';
            var netIntervalTx = network.intervalTxStr || '0 B';
            var netTotalRx = network.totalRxStr || '0 B';
            var netTotalTx = network.totalTxStr || '0 B';
            var netIp = network.ip || '未知';
            var netDetail = network.detail || '';
            var netSignalLevel = network.signalLevel || '';
            var netTypeDetail = network.networkType || '';

            var deviceModel = dev.device_model || clientId;
            var deviceManufacturer = dev.device_manufacturer || '';
            var screenWidth = dev.screen_width || 1080;
            var screenHeight = dev.screen_height || 2400;
            
            var dataTimestamp = dev.data_timestamp || 0;
            var updateTimeStr = formatTimestamp(dataTimestamp);

            var safeId = clientId.replace(/[^a-zA-Z0-9]/g, '_');
            var bodyId = 'body-' + safeId;
            var cardId = 'device-' + safeId;

            var isOpen = cardStates[clientId] || false;
            var toggleClass = isOpen ? 'open' : '';

            // ===== 构建传感器列表 =====
            var sensorKeys = Object.keys(sensors);
            var sensorHtml = '';
            
            var priorityOrder = ['accelerometer', 'gyroscope', 'magnetic_field', 'gravity', 'linear_acceleration', 'orientation', 'light', 'proximity', 'ambient_temperature', 'pressure', 'relative_humidity'];
            var sortedKeys = [];
            for (var i = 0; i < priorityOrder.length; i++) {
                if (sensors[priorityOrder[i]]) {
                    sortedKeys.push(priorityOrder[i]);
                }
            }
            for (var i = 0; i < sensorKeys.length; i++) {
                if (sortedKeys.indexOf(sensorKeys[i]) === -1) {
                    sortedKeys.push(sensorKeys[i]);
                }
            }

            if (sortedKeys.length > 0) {
                for (var i = 0; i < sortedKeys.length; i++) {
                    var key = sortedKeys[i];
                    var val = sensors[key];
                    if (!val) continue;
                    
                    var displayVal = formatSensorValue(val);
                    var valueClass = getSensorValueClass(val);
                    var label = sensorNameMap[key] || key;
                    
                    sensorHtml += `
                        <div class="sensor-item-full">
                            <span class="sensor-name">${label}</span>
                            <span class="sensor-value ${valueClass}">${displayVal}</span>
                        </div>
                    `;
                }
            } else {
                sensorHtml = '<div class="no-sensor">📡 暂无传感器数据</div>';
            }

            var sensorCount = sortedKeys.length;

            // ===== 网络状态图标 =====
            var netIcon = netConnected ? '✅' : '❌';
            var netStatusClass = netConnected ? 'connected' : 'disconnected';
            var netTypeDisplay = netType;
            if (netTypeDetail) {
                netTypeDisplay += ' (' + netTypeDetail + ')';
            }
            if (netSignalLevel) {
                netTypeDisplay += ' 信号: ' + netSignalLevel;
            }

            html += `
            <div class="card ${isOnline ? '' : 'offline'}" id="${cardId}">
                <div class="card-header" onclick="toggleCard('${bodyId}', '${clientId}')">
                    <div class="device-name">
                        <span class="model">${deviceModel}</span>
                        <span style="font-size:13px;color:#8899aa;margin-left:8px;">${deviceManufacturer}</span>
                        <span style="font-size:12px;color:#556677;margin-left:8px;">${screenWidth}×${screenHeight}</span>
                    </div>
                    <div class="device-status">
                        <span>🔋 ${batteryLevel}%</span>
                        <span>📱 ${fgName.length > 15 ? fgName.substring(0, 15)+'...' : fgName}</span>
                        <span class="network-status">
                            <span class="${netStatusClass}">${netIcon}</span>
                            ${netType}
                        </span>
                        <span>
                            <span class="dot ${isOnline ? 'online' : 'offline'}"></span>
                            ${isOnline ? '在线' : '离线'}
                        </span>
                        <span class="toggle-icon ${toggleClass}" id="toggle-${bodyId}">▼</span>
                    </div>
                </div>
                <div class="card-body ${toggleClass}" id="${bodyId}">
                    <div class="two-col">
                        <div>
                            <div class="data-grid">
                                <div class="data-item">
                                    <div class="label">🔋 电池</div>
                                    <div class="value ${batteryClass}">${batteryLevel}%</div>
                                    <div class="sub">${battery.charging ? '⚡ 充电中' : '🔌 未充电'}</div>
                                </div>
                                <div class="data-item">
                                    <div class="label">💾 内存</div>
                                    <div class="value">${memoryUsedMB} / ${memoryMB} MB</div>
                                    <div class="sub">使用 ${memoryPercent}%</div>
                                </div>
                                <div class="data-item">
                                    <div class="label">💾 存储</div>
                                    <div class="value">${storageUsedGB} / ${storageGB} GB</div>
                                    <div class="sub">使用 ${storagePercent}%</div>
                                </div>
                                <div class="data-item">
                                    <div class="label">📱 前台应用</div>
                                    <div class="value" style="font-size:13px;">${fgName}</div>
                                </div>
                                <div class="data-item">
                                    <div class="label">🖥️ 屏幕</div>
                                    <div class="value">${screenStatus}</div>
                                    <div class="sub">亮度: ${brightness}</div>
                                </div>
                                <div class="data-item">
                                    <div class="label">📍 位置</div>
                                    <div class="value" style="font-size:13px;">${locationStr}</div>
                                    <div class="sub">${hasLocation ? '✅ GPS定位' : '❌ 无定位'}</div>
                                </div>
                            </div>
                            <!-- 网络详细信息 -->
                            <div class="data-grid" style="margin-top:10px;">
                                <div class="data-item" style="border-left-color:#00d4ff;">
                                    <div class="label">🌐 网络</div>
                                    <div class="value" style="font-size:14px;">${netTypeDisplay}</div>
                                    <div class="sub">${netDetail ? '| ' + netDetail : ''}</div>
                                </div>
                                <div class="data-item" style="border-left-color:#00d4ff;">
                                    <div class="label">⬇️ 下载速度</div>
                                    <div class="value good">${netDownSpeed}</div>
                                    <div class="sub">间隔流量: ${netIntervalRx}</div>
                                </div>
                                <div class="data-item" style="border-left-color:#ffaa00;">
                                    <div class="label">⬆️ 上传速度</div>
                                    <div class="value warning">${netUpSpeed}</div>
                                    <div class="sub">间隔流量: ${netIntervalTx}</div>
                                </div>
                                <div class="data-item" style="border-left-color:#667788;">
                                    <div class="label">📊 总流量</div>
                                    <div class="value" style="font-size:13px;">⬇️ ${netTotalRx} / ⬆️ ${netTotalTx}</div>
                                </div>
                            </div>
                        </div>
                        <div>
                            <div class="sensor-section">
                                <div class="section-title">
                                    📡 传感器数据
                                    <span class="count">${sensorCount} 个</span>
                                </div>
                                <div class="sensor-grid-full">
                                    ${sensorHtml}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="card-footer">
                    <span class="update-time">🕐 数据更新: <span class="time-value">${updateTimeStr}</span></span>
                    <span>📊 权限等级: ${dev.permission_level || 'unknown'}</span>
                </div>
            </div>
            `;
        }

        container.innerHTML = html;
    }

    // ==================== 折叠切换 ====================
    function toggleCard(bodyId, clientId) {
        var body = document.getElementById(bodyId);
        var toggle = document.getElementById('toggle-' + bodyId);
        if (body) {
            var isOpen = body.classList.contains('open');
            if (isOpen) {
                body.classList.remove('open');
                if (toggle) toggle.classList.remove('open');
                cardStates[clientId] = false;
            } else {
                body.classList.add('open');
                if (toggle) toggle.classList.add('open');
                cardStates[clientId] = true;
            }
        }
    }

    // ==================== API调用 ====================
    function updateStats() {
        if (isUpdating) {
            pendingUpdate = true;
            return;
        }
        isUpdating = true;
        pendingUpdate = false;
        
        fetch('/api/stats')
            .then(response => response.json())
            .then(data => {
                deviceStats = data;
                document.getElementById('totalClients').textContent = data.total_clients || 0;

                var currentStates = {};
                for (var id in cardStates) {
                    currentStates[id] = cardStates[id];
                }

                renderDevices(data);

                for (var id in currentStates) {
                    if (currentStates[id]) {
                        var bodyId = 'body-' + id.replace(/[^a-zA-Z0-9]/g, '_');
                        var body = document.getElementById(bodyId);
                        var toggle = document.getElementById('toggle-' + bodyId);
                        if (body) {
                            body.classList.add('open');
                            if (toggle) toggle.classList.add('open');
                            cardStates[id] = true;
                        }
                    }
                }

                isUpdating = false;
                if (pendingUpdate) {
                    pendingUpdate = false;
                    updateStats();
                }
            })
            .catch(err => {
                console.error('Error:', err);
                isUpdating = false;
                if (pendingUpdate) {
                    pendingUpdate = false;
                    updateStats();
                }
            });
    }

    function fetchDeviceData() {
        fetch('/api/devices')
            .then(response => response.json())
            .then(data => {
                deviceDataCache = data;
            })
            .catch(err => console.error('Error:', err));
    }

    // ==================== 启动 ====================
    setInterval(updateStats, 5000);
    setInterval(fetchDeviceData, 3000);
    updateStats();
    fetchDeviceData();

    console.log('📱 DeviceMonitor 已启动');
</script>
</body>
</html>
"""


def get_html():
    ws_port = CONFIG.get("ws_port", 32767)
    return HTML_PAGE.replace("{{WS_PORT}}", str(ws_port))


async def web_handler(request):
    from aiohttp import web
    return web.Response(body=get_html().encode('utf-8'), content_type='text/html')


async def main():
    import sys

    try:
        from aiohttp import web
        HAS_AIOHTTP = True
    except ImportError:
        HAS_AIOHTTP = False
        logger.warning("⚠️ aiohttp未安装，Web界面将不可用")
        logger.warning("💡 安装命令: pip install aiohttp")
        web = None

    server = DeviceDataServer()

    ws_port = CONFIG.get("ws_port", 32767)
    web_port = CONFIG.get("web_port", 8080)
    host = CONFIG.get("host", "0.0.0.0")

    logger.info("=" * 80)
    logger.info("📱 设备数据接收服务器 v8.0 (网络监控版)")
    logger.info("=" * 80)
    logger.info(f"🌐 WebSocket: ws://{host}:{ws_port}")
    logger.info(f"🌐 Web界面: http://{host}:{web_port}")
    logger.info(f"📁 数据目录: {DATA_DIR}")
    logger.info("📝 配置文件: server_config.json")
    logger.info("=" * 80)

    ws_task = asyncio.create_task(server.start())

    if HAS_AIOHTTP:
        try:
            async def stats_handler(request):
                return web.json_response(server.get_device_stats())

            async def devices_handler(request):
                return web.json_response(server.device_data)

            app = web.Application()
            app['server'] = server

            app.router.add_get('/', web_handler)
            app.router.add_get('/api/stats', stats_handler)
            app.router.add_get('/api/devices', devices_handler)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host, web_port)
            await site.start()
            logger.info(f"✅ Web界面: http://localhost:{web_port}")
        except Exception as e:
            logger.warning(f"⚠️ Web界面启动失败: {e}")

    try:
        await ws_task
    except KeyboardInterrupt:
        logger.info("🛑 服务器正在关闭...")
        server.running = False
    finally:
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 服务器已停止")
    except Exception as e:
        logger.error(f"❌ 服务器运行错误: {e}")
        import traceback
        traceback.print_exc()