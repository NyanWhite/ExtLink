# ws_server.py
# 实时推送版 + 终端命令 + 配置文件心跳参数

import asyncio
import json
import logging
import time
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional, Set
import websockets

# 配置日志（后续会根据配置文件调整级别）
logging.basicConfig(
    level=logging.DEBUG,
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
    "log_level": 0,          # 0=DEBUG, 1=INFO, 2=WARNING, 3=ERROR
    "ping_interval": 30,     # WebSocket 心跳间隔（秒）
    "ping_timeout": 60       # WebSocket 心跳超时（秒）
}

def load_config():
    """强制加载外部配置文件，若文件不存在或格式错误则抛出异常"""
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"配置文件 {CONFIG_FILE} 不存在，请创建并配置。")
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件 {CONFIG_FILE} 格式错误: {e}")
    # 补全缺失的键
    for key in DEFAULT_CONFIG:
        if key not in config:
            logger.warning(f"配置项 '{key}' 缺失，使用默认值: {DEFAULT_CONFIG[key]}")
            config[key] = DEFAULT_CONFIG[key]
    return config

def set_log_level(level_code: int):
    """根据等级代码设置日志级别"""
    level_map = {0: logging.DEBUG, 1: logging.INFO, 2: logging.WARNING, 3: logging.ERROR}
    level = level_map.get(level_code, logging.DEBUG)
    logging.getLogger().setLevel(level)
    logger.info(f"日志级别设置为: {logging.getLevelName(level)} (代码 {level_code})")

# 加载配置，若失败则退出
try:
    CONFIG = load_config()
except (FileNotFoundError, ValueError) as e:
    logger.error(f"配置加载失败: {e}")
    sys.exit(1)

# 设置日志级别
set_log_level(CONFIG.get("log_level", 0))

DATA_DIR = CONFIG.get("data_dir", "device_data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    logger.info(f"📁 数据存储目录: {DATA_DIR}")
except Exception as e:
    logger.error(f"❌ 创建数据目录失败: {e}")
    DATA_DIR = "."


class DeviceDataServer:
    def __init__(self):
        self.device_clients: Dict[str, websockets.WebSocketServerProtocol] = {}
        self.web_clients: Set[websockets.WebSocketServerProtocol] = set()
        self.device_data: Dict[str, Dict] = {}
        self.device_last_seen: Dict[str, float] = {}
        self.device_info: Dict[str, Dict] = {}
        self.total_messages = 0

        self.running = False
        self.websocket_server = None
        self.stop_event = asyncio.Event()

    async def start(self):
        """启动 WebSocket 服务器，阻塞直到收到停止信号"""
        if self.running:
            logger.warning("服务器已经在运行，忽略")
            return

        # 重置停止事件，避免上次的信号影响
        self.stop_event.clear()
        self.running = True

        ws_port = CONFIG.get("ws_port", 32767)
        host = CONFIG.get("host", "0.0.0.0")
        ping_interval = CONFIG.get("ping_interval", 30)
        ping_timeout = CONFIG.get("ping_timeout", 60)

        try:
            self.websocket_server = await websockets.serve(
                self.handle_client,
                host,
                ws_port,
                ping_interval=ping_interval,
                ping_timeout=ping_timeout,
                max_size=10 * 1024 * 1024
            )
            logger.info(f"🌐 WebSocket服务器启动在 {host}:{ws_port}")
            logger.info(f"   Ping间隔: {ping_interval}s, Ping超时: {ping_timeout}s")
        except Exception as e:
            self.running = False
            raise e

        # 等待停止信号或服务器关闭
        stop_task = asyncio.create_task(self.stop_event.wait())
        close_task = asyncio.create_task(self.websocket_server.wait_closed())
        await asyncio.wait(
            [stop_task, close_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        # 取消未完成的任务
        for task in (stop_task, close_task):
            if not task.done():
                task.cancel()

        # 关闭服务器
        if self.websocket_server:
            self.websocket_server.close()
            await self.websocket_server.wait_closed()

        self.running = False
        logger.info("WebSocket 服务器已停止")

    async def stop(self):
        """停止服务器，等待完全关闭"""
        if not self.running:
            logger.debug("服务器已经停止")
            return

        logger.info("正在停止 WebSocket 服务器...")
        self.stop_event.set()
        # 等待 start 完全退出
        while self.running:
            await asyncio.sleep(0.05)
        # 确保服务器完全关闭
        if self.websocket_server:
            self.websocket_server.close()
            await self.websocket_server.wait_closed()
        logger.info("WebSocket 服务器已完全停止")

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol):
        """处理新连接（区分设备或网页）"""
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.warning("收到非JSON消息，关闭连接")
                await websocket.close()
                return

            if 'deviceId' in data:
                await self.handle_device(websocket, data)
            elif data.get('type') == 'web':
                await self.handle_web(websocket)
            else:
                logger.warning("未知连接类型，关闭")
                await websocket.close()
        except asyncio.TimeoutError:
            logger.warning("连接超时，未收到初始消息")
            await websocket.close()
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"处理连接时出错: {e}")

    async def handle_device(self, websocket: websockets.WebSocketServerProtocol, init_data: dict):
        """处理设备客户端"""
        client_id = init_data.get('deviceId', str(id(websocket)))
        self.device_clients[client_id] = websocket
        self.device_last_seen[client_id] = time.time()

        if client_id not in self.device_info:
            self.device_info[client_id] = {
                "first_seen": datetime.now().isoformat(),
                "device_model": init_data.get('device', {}).get('model', 'Unknown'),
                "device_manufacturer": init_data.get('device', {}).get('manufacturer', 'Unknown'),
            }
        logger.info(f"📱 设备 {client_id} 连接成功")

        try:
            await websocket.send(json.dumps({
                "type": "welcome",
                "timestamp": int(time.time() * 1000),
                "message": "连接成功! 等待数据接收..."
            }))
        except:
            pass

        try:
            async for message in websocket:
                await self.process_message(client_id, message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"🔴 设备 {client_id} 连接断开")
        finally:
            self.device_clients.pop(client_id, None)
            self.device_last_seen.pop(client_id, None)

    async def handle_web(self, websocket: websockets.WebSocketServerProtocol):
        """处理网页客户端"""
        self.web_clients.add(websocket)
        logger.info(f"🌐 网页客户端连接 (当前 {len(self.web_clients)} 个)")
        await self.send_stats_to_web(websocket)
        try:
            await websocket.wait_closed()
        except:
            pass
        finally:
            self.web_clients.discard(websocket)
            logger.info(f"🌐 网页客户端断开 (剩余 {len(self.web_clients)} 个)")

    async def process_message(self, client_id: str, message: str):
        """处理设备发来的消息"""
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

            await self.broadcast_stats()
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误: {e}")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")

    async def handle_full_data(self, client_id: str, data: Dict[str, Any]):
        logger.debug(f"📥 收到设备 {client_id} 的完整数据")

    async def handle_partial_data(self, client_id: str, data: Dict[str, Any]):
        logger.debug(f"📥 收到设备 {client_id} 的增量数据")

    def save_data_to_file(self, client_id: str, data: Dict[str, Any]):
        try:
            filename = f"device_{client_id}.json"
            filepath = os.path.join(DATA_DIR, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ 保存数据失败: {e}")

    def get_device_stats(self) -> Dict[str, Any]:
        """构建当前设备状态统计（用于推送）"""
        now = time.time()
        stats = {
            "total_clients": len(self.device_clients),
            "active_devices": len([c for c, t in self.device_last_seen.items() if now - t < 300]),
            "total_messages": self.total_messages,
            "devices": {}
        }

        for client_id, data in self.device_data.items():
            last_seen = self.device_last_seen.get(client_id, 0)
            info = self.device_info.get(client_id, {})
            data_timestamp = data.get('timestamp', 0)
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
                } if network else None,
                "battery": data.get('battery', {}),
                "foreground": data.get('foreground', {}),
                "memory": data.get('memory', {}),
                "storage": data.get('storage', {}),
                "screen": data.get('screen', {}),
                "location": data.get('location', {}),
                "sensors": data.get('sensors', {})
            }

        return stats

    async def send_stats_to_web(self, websocket: websockets.WebSocketServerProtocol):
        try:
            stats = self.get_device_stats()
            await websocket.send(json.dumps(stats))
        except Exception as e:
            logger.error(f"发送状态到网页失败: {e}")

    async def broadcast_stats(self):
        if not self.web_clients:
            return
        stats = self.get_device_stats()
        data = json.dumps(stats)
        for ws in list(self.web_clients):
            try:
                await ws.send(data)
            except:
                self.web_clients.discard(ws)


# ==================== HTML页面（前端使用 WebSocket） ====================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>📱 设备数据监控 (实时)</title>
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
        .placeholder-section {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 16px;
            min-height: 200px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #667788;
            font-size: 16px;
            text-align: center;
            border: 1px dashed rgba(100, 200, 255, 0.15);
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
        .connection-status {
            font-size: 14px;
            color: #8899aa;
        }
        .connection-status .connected { color: #00ff88; }
        .connection-status .disconnected { color: #ff4444; }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div>
            <h1><span>Ext</span>Link</h1>
            <div style="font-size:13px;color:#8899aa;margin-top:4px;">
                <span class="connection-status" id="connStatus">⏳ 连接中...</span>
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
            <p>等待数据...</p>
        </div>
    </div>
</div>

<script>
    var cardStates = {};
    var ws = null;
    var reconnectTimer = null;

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

    function renderDevices(data) {
        var container = document.getElementById('devicesContainer');
        if (!container) return;

        var deviceKeys = Object.keys(data.devices || {});
        document.getElementById('totalClients').textContent = data.total_clients || 0;

        if (deviceKeys.length === 0) {
            container.innerHTML = `
                <div class="full-width empty-state">
                    <div class="icon">📡</div>
                    <p>暂无设备连接</p>
                </div>
            `;
            return;
        }

        var html = '';
        for (var clientId in data.devices) {
            var dev = data.devices[clientId];
            var isOnline = dev.last_seen !== null;

            var battery = dev.battery || {};
            var batteryLevel = battery.level !== undefined ? battery.level : '?';
            var batteryClass = batteryLevel >= 50 ? 'good' : (batteryLevel >= 20 ? 'warning' : 'danger');

            var foreground = dev.foreground || {};
            var fgName = foreground.packageName || 'Unknown';

            var memory = dev.memory || {};
            var memoryTotal = memory.total || 0;
            var memoryUsed = memory.used || 0;
            var memoryMB = memoryTotal > 0 ? (memoryTotal / 1024 / 1024).toFixed(0) : '?';
            var memoryUsedMB = memoryUsed > 0 ? (memoryUsed / 1024 / 1024).toFixed(0) : '?';
            var memoryPercent = memory.usagePercent || '?';

            var storage = dev.storage || {};
            var storageTotal = storage.total || 0;
            var storageUsed = storage.used || 0;
            var storageGB = storageTotal > 0 ? (storageTotal / 1024 / 1024 / 1024).toFixed(1) : '?';
            var storageUsedGB = storageUsed > 0 ? (storageUsed / 1024 / 1024 / 1024).toFixed(1) : '?';
            var storagePercent = storage.usagePercent || '?';

            var screen = dev.screen || {};
            var screenStatus = screen.isOn ? '🟢 亮屏' : '🔴 熄屏';
            var brightness = screen.brightness || '?';

            var location = dev.location || {};
            var hasLocation = location.hasLocation || false;
            var locationStr = hasLocation ? location.latitude.toFixed(4) + ', ' + location.longitude.toFixed(4) : '未获取';

            var sensors = dev.sensors || {};
            var sensorCount = Object.keys(sensors).length;

            var network = dev.network || {};
            var netType = network.type || '未知';
            var netConnected = network.isConnected || false;
            var netDownSpeed = network.downSpeedStr || '0 B/s';
            var netUpSpeed = network.upSpeedStr || '0 B/s';
            var netIntervalRx = network.intervalRxStr || '0 B';
            var netIntervalTx = network.intervalTxStr || '0 B';
            var netTotalRx = network.totalRxStr || '0 B';
            var netTotalTx = network.totalTxStr || '0 B';
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

            var netIcon = netConnected ? '✅' : '❌';
            var netStatusClass = netConnected ? 'connected' : 'disconnected';
            var netTypeDisplay = netType;
            if (netTypeDetail) netTypeDisplay += ' (' + netTypeDetail + ')';
            if (netSignalLevel) netTypeDisplay += ' 信号: ' + netSignalLevel;

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
                                <div class="data-item">
                                    <div class="label">📡 传感器数据</div>
                                    <div class="value">${sensorCount} 个</div>
                                    <div class="sub">传感器总数</div>
                                </div>
                            </div>
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
                            <div class="placeholder-section">
                                📊 点击右侧数据查看详细历史记录
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

    function connectWebSocket() {
        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsUrl = protocol + '//' + window.location.hostname + ':' + {{WS_PORT}};
        ws = new WebSocket(wsUrl);

        ws.onopen = function() {
            document.getElementById('connStatus').innerHTML = '🟢 已连接 (实时)';
            document.getElementById('connStatus').className = 'connection-status connected';
            ws.send(JSON.stringify({type: 'web'}));
            if (reconnectTimer) {
                clearInterval(reconnectTimer);
                reconnectTimer = null;
            }
        };

        ws.onmessage = function(event) {
            try {
                var data = JSON.parse(event.data);
                renderDevices(data);
                // 恢复展开状态
                for (var id in cardStates) {
                    if (cardStates[id]) {
                        var bodyId = 'body-' + id.replace(/[^a-zA-Z0-9]/g, '_');
                        var body = document.getElementById(bodyId);
                        var toggle = document.getElementById('toggle-' + bodyId);
                        if (body) {
                            body.classList.add('open');
                            if (toggle) toggle.classList.add('open');
                        }
                    }
                }
            } catch (e) {
                console.error('解析数据失败:', e);
            }
        };

        ws.onclose = function() {
            document.getElementById('connStatus').innerHTML = '🔴 连接断开 (重连中...)';
            document.getElementById('connStatus').className = 'connection-status disconnected';
            if (!reconnectTimer) {
                reconnectTimer = setInterval(function() {
                    if (ws.readyState !== WebSocket.OPEN && ws.readyState !== WebSocket.CONNECTING) {
                        connectWebSocket();
                    }
                }, 3000);
            }
        };

        ws.onerror = function(err) {
            console.error('WebSocket错误:', err);
        };
    }

    connectWebSocket();
    console.log('📱 DeviceMonitor (实时推送) 已启动');
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


# ==================== 终端命令处理 ====================
async def stdin_reader(server: DeviceDataServer):
    loop = asyncio.get_event_loop()
    help_text = """
可用命令:
  .reload_conf        - 重新加载配置文件并重启服务器
  .force_update       - 强制所有设备上传数据（暂未实现）
  .log_level <level>  - 设置日志级别 (0=DEBUG, 1=INFO, 2=WARNING, 3=ERROR)
  .help               - 显示此帮助信息
  .exit               - 退出服务器
"""
    print(help_text)
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            cmd = line.strip()
            if not cmd:
                continue
            if cmd == '.help':
                print(help_text)
            elif cmd == '.exit':
                logger.info("收到退出命令，正在关闭服务器...")
                asyncio.get_event_loop().stop()
                break
            elif cmd == '.reload_conf':
                await handle_reload(server)
            elif cmd == '.force_update':
                logger.info("🔧 .force_update 命令已收到（暂未实现）")
            elif cmd.startswith('.log_level'):
                parts = cmd.split()
                if len(parts) == 2:
                    try:
                        level = int(parts[1])
                        if level in (0, 1, 2, 3):
                            await handle_log_level(level)
                        else:
                            print("日志级别必须是 0, 1, 2, 3")
                    except ValueError:
                        print("日志级别必须是数字")
                else:
                    print("用法: .log_level <0|1|2|3>")
            else:
                print(f"未知命令: {cmd}，输入 .help 查看帮助")
        except Exception as e:
            logger.error(f"终端命令处理错误: {e}")

async def handle_reload(server: DeviceDataServer):
    global CONFIG
    logger.info("🔄 正在重新加载配置并重启服务器...")
    await server.stop()  # 等待停止完成
    try:
        new_config = load_config()
        CONFIG = new_config
        log_level = CONFIG.get('log_level', 0)
        set_log_level(log_level)
        logger.info("✅ 配置文件已重新加载")
    except Exception as e:
        logger.error(f"❌ 重载配置失败: {e}")
    # 设置重启标志
    asyncio.get_event_loop().call_soon(setattr, sys.modules[__name__], 'need_restart', True)

need_restart = False

async def handle_log_level(level: int):
    global CONFIG
    CONFIG['log_level'] = level
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=2, ensure_ascii=False)
        set_log_level(level)
        logger.info(f"✅ 日志级别已设置为 {level}，并保存到配置文件")
    except Exception as e:
        logger.error(f"❌ 保存配置失败: {e}")


# ==================== 主程序 ====================
async def main():
    global need_restart
    from aiohttp import web

    server = DeviceDataServer()
    ws_port = CONFIG.get("ws_port", 32767)
    web_port = CONFIG.get("web_port", 8080)
    host = CONFIG.get("host", "0.0.0.0")

    logger.info("=" * 80)
    logger.info("📱 设备数据接收服务器 v9.5 (完整稳定版)")
    logger.info("=" * 80)
    logger.info(f"🌐 WebSocket: ws://{host}:{ws_port}")
    logger.info(f"🌐 Web界面: http://{host}:{web_port}")
    logger.info(f"📁 数据目录: {DATA_DIR}")
    logger.info("📝 配置文件: server_config.json")
    logger.info("💡 在终端输入 .help 查看命令")
    logger.info("=" * 80)

    # 启动 Web 服务
    try:
        async def stats_handler(request):
            return web.json_response(server.get_device_stats())

        async def devices_handler(request):
            return web.json_response(server.device_data)

        app = web.Application()
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

    stdin_task = asyncio.create_task(stdin_reader(server))

    ws_task = None
    need_restart = True  # 首次启动

    while True:
        if need_restart:
            # 如果已有任务，先确保它完全停止
            if ws_task is not None:
                if not ws_task.done():
                    ws_task.cancel()
                    try:
                        await ws_task
                    except asyncio.CancelledError:
                        pass
                    except:
                        pass
                ws_task = None
            # 强制停止服务器（防止残留）
            await server.stop()
            need_restart = False
            # 启动新服务器
            try:
                ws_task = asyncio.create_task(server.start())
                logger.info("WebSocket 服务器启动任务已创建")
            except Exception as e:
                logger.error(f"创建启动任务失败: {e}")
                await asyncio.sleep(5)
                need_restart = True
                continue

        # 检查任务状态
        if ws_task is not None and ws_task.done():
            try:
                await ws_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"WebSocket 服务器异常退出: {e}")
            ws_task = None
            logger.info("等待 5 秒后尝试重启...")
            await asyncio.sleep(5)
            need_restart = True
            continue

        await asyncio.sleep(0.2)

    stdin_task.cancel()
    await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 服务器已停止")
    except Exception as e:
        logger.error(f"❌ 服务器运行错误: {e}")
        import traceback
        traceback.print_exc()