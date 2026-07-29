# ws_server.py
# 模块化存储 + 历史记录滑动窗口 + 可配置模块 + 多指标折线图（字段级智能单位转换 + 图表容器复用防重置）
# 新增：电池充满/耗尽时间估算（基于 current, capacity, level）

import asyncio
import json
import logging
import time
import os
import sys
import configparser
import gzip
from datetime import datetime
from typing import Dict, Any, Optional, Set, List
import websockets
from aiohttp import web

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== 配置文件 ====================
CONFIG_FILE = "server_config.json"
DEFAULT_CONFIG = {
    "ws_port": 91,
    "web_port": 80,
    "host": "0.0.0.0",
    "data_dir": "device_data",
    "log_level": 0,
    "ping_interval": 30,
    "ping_timeout": 60,
    "save_history": False,
    "history_length": "1mo",
    "data_modules": [
        "battery",
        "network",
        "foreground",
        "screen",
        "sensors",
        "location",
        "memory",
        "storage"
    ]
}


def parse_history_length(length_str: str) -> int:
    if not length_str:
        return 3600
    length_str = length_str.strip().lower()
    try:
        if length_str.endswith('s'):
            return int(length_str[:-1])
        elif length_str.endswith('m'):
            return int(length_str[:-1]) * 60
        elif length_str.endswith('h'):
            return int(length_str[:-1]) * 3600
        elif length_str.endswith('mo'):
            return int(length_str[:-2]) * 30 * 24 * 3600
        elif length_str.endswith('y'):
            return int(length_str[:-1]) * 365 * 24 * 3600
        else:
            return int(length_str)
    except ValueError:
        return 3600


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"配置文件 {CONFIG_FILE} 不存在，请创建并配置。")
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件 {CONFIG_FILE} 格式错误: {e}")
    for key in DEFAULT_CONFIG:
        if key not in config:
            logger.warning(f"配置项 '{key}' 缺失，使用默认值: {DEFAULT_CONFIG[key]}")
            config[key] = DEFAULT_CONFIG[key]
    if not isinstance(config.get('data_modules'), list):
        config['data_modules'] = DEFAULT_CONFIG['data_modules']
    return config


def set_log_level(level_code: int):
    level_map = {0: logging.DEBUG, 1: logging.INFO, 2: logging.WARNING, 3: logging.ERROR}
    level = level_map.get(level_code, logging.DEBUG)
    logging.getLogger().setLevel(level)
    logger.info(f"日志级别设置为: {logging.getLevelName(level)} (代码 {level_code})")


# 加载配置
try:
    CONFIG = load_config()
except (FileNotFoundError, ValueError) as e:
    logger.error(f"配置加载失败: {e}")
    sys.exit(1)

set_log_level(CONFIG.get("log_level", 0))

DATA_MODULES = CONFIG.get('data_modules', DEFAULT_CONFIG['data_modules'])

DATA_DIR = CONFIG.get("data_dir", "device_data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    logger.info(f"📁 数据存储目录: {DATA_DIR}")
    logger.info(f"📦 数据模块: {', '.join(DATA_MODULES)}")
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

        self.device_ini_cache: Dict[str, Dict] = {}

        # 历史记录缓存（用于精确去重）
        self.last_history_entry: Dict[str, Dict[str, Any]] = {}

    async def start(self):
        if self.running:
            logger.warning("服务器已经在运行，忽略")
            return

        self.stop_event.clear()
        self.running = True

        ws_port = CONFIG.get("ws_port", 91)
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

        stop_task = asyncio.create_task(self.stop_event.wait())
        close_task = asyncio.create_task(self.websocket_server.wait_closed())
        await asyncio.wait(
            [stop_task, close_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for task in (stop_task, close_task):
            if not task.done():
                task.cancel()

        if self.websocket_server:
            self.websocket_server.close()
            await self.websocket_server.wait_closed()

        self.running = False
        logger.info("WebSocket 服务器已停止")

    async def stop(self):
        if not self.running:
            return
        logger.info("正在停止 WebSocket 服务器...")
        self.stop_event.set()
        while self.running:
            await asyncio.sleep(0.05)
        if self.websocket_server:
            self.websocket_server.close()
            await self.websocket_server.wait_closed()
        logger.info("WebSocket 服务器已完全停止")

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol):
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
        except Exception as e:
            logger.error(f"处理连接时出错: {e}")

    async def handle_device(self, websocket: websockets.WebSocketServerProtocol, init_data: dict):
        client_id = init_data.get('deviceId', str(id(websocket)))
        self.device_clients[client_id] = websocket
        self.device_last_seen[client_id] = time.time()

        # 清空该设备的历史缓存，确保新连接的第一条数据被记录
        self.last_history_entry.pop(client_id, None)

        if client_id not in self.device_info:
            self.device_info[client_id] = {
                "first_seen": datetime.now().isoformat(),
                "device_model": init_data.get('device', {}).get('model', 'Unknown'),
                "device_manufacturer": init_data.get('device', {}).get('manufacturer', 'Unknown'),
            }
        logger.info(f"📱 设备 {client_id} 连接成功")

        self._ensure_device_dir(client_id)
        self.device_ini_cache[client_id] = self._load_device_ini(client_id)

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
            self.device_ini_cache.pop(client_id, None)

    async def handle_web(self, websocket: websockets.WebSocketServerProtocol):
        self.web_clients.add(websocket)
        logger.info(f"🌐 网页客户端连接 (当前 {len(self.web_clients)} 个)")
        await self.send_stats_to_web(websocket)
        try:
            async for message in websocket:
                # Web客户端不再发送history_request，全部改为HTTP
                pass
        except:
            pass
        finally:
            self.web_clients.discard(websocket)
            logger.info(f"🌐 网页客户端断开 (剩余 {len(self.web_clients)} 个)")

    def _extract_value(self, module: str, data: dict) -> Optional[float]:
        return None

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

            await self.broadcast_stats()
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")

    async def handle_full_data(self, client_id: str, data: Dict[str, Any]):
        logger.debug(f"📥 收到设备 {client_id} 的完整数据")

    async def handle_partial_data(self, client_id: str, data: Dict[str, Any]):
        logger.debug(f"📥 收到设备 {client_id} 的增量数据")

    def _ensure_device_dir(self, client_id: str) -> str:
        device_dir = os.path.join(DATA_DIR, client_id)
        os.makedirs(device_dir, exist_ok=True)
        hs_dir = os.path.join(device_dir, "hs")
        os.makedirs(hs_dir, exist_ok=True)
        return device_dir

    def _load_device_ini(self, client_id: str) -> Dict:
        device_dir = os.path.join(DATA_DIR, client_id)
        ini_file = os.path.join(device_dir, "historySet.ini")

        default_sections = ['device'] + DATA_MODULES
        default_config = {}
        for sec in default_sections:
            default_config[sec] = {'save_history': -1, 'history_length': -1}

        if not os.path.exists(ini_file):
            config = configparser.ConfigParser()
            for sec, values in default_config.items():
                config[sec] = {k: str(v) for k, v in values.items()}
            with open(ini_file, 'w', encoding='utf-8') as f:
                config.write(f)
            logger.debug(f"📝 创建历史配置文件: {ini_file}")
            return default_config

        config = configparser.ConfigParser()
        config.read(ini_file, encoding='utf-8')
        result = {}
        for sec in default_sections:
            result[sec] = {}
            for key in default_config[sec].keys():
                try:
                    val = config.get(sec, key)
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                    result[sec][key] = val
                except:
                    result[sec][key] = default_config[sec][key]
        return result

    def _get_module_history_settings(self, client_id: str, module: str):
        global_save = CONFIG.get('save_history', False)
        global_length_str = CONFIG.get('history_length', '1h')
        global_seconds = parse_history_length(global_length_str)

        ini = self.device_ini_cache.get(client_id, {})
        mod_cfg = ini.get(module, {})

        mod_save = mod_cfg.get('save_history', -1)
        mod_length = mod_cfg.get('history_length', -1)

        final_save = global_save if mod_save == -1 else bool(mod_save)
        if mod_length == -1:
            final_seconds = global_seconds
        else:
            final_seconds = parse_history_length(str(mod_length))

        return final_save, final_seconds

    def _clean_history_file(self, history_file: str, max_age_seconds: int):
        if not os.path.exists(history_file):
            return
        
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - (max_age_seconds * 1000)

        try:
            lines = []
            open_func = gzip.open if history_file.endswith('.gz') else open
            with open_func(history_file, 'rt', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get('timestamp', 0) >= cutoff_ms:
                            lines.append(line)
                    except:
                        continue
            write_open = gzip.open if history_file.endswith('.gz') else open
            with write_open(history_file, 'wt', encoding='utf-8') as f:
                if lines:
                    f.write('\n'.join(lines) + '\n')
                else:
                    f.write('')
        except Exception as e:
            logger.error(f"清理历史文件失败 {history_file}: {e}")

    def _save_history_entry(self, client_id: str, module: str, entry: dict):
        last_data = self.last_history_entry.get(client_id, {}).get(module)
        current_data = entry.get('data')
        if current_data is not None and last_data is not None and current_data == last_data:
            logger.debug(f"⏭️  跳过重复历史记录: {client_id}/{module}")
            return

        save_flag, max_seconds = self._get_module_history_settings(client_id, module)
        if not save_flag:
            return

        device_dir = os.path.join(DATA_DIR, client_id)
        hs_dir = os.path.join(device_dir, "hs")
        os.makedirs(hs_dir, exist_ok=True)
        history_file = os.path.join(hs_dir, f"{module}.history.gz")

        try:
            with gzip.open(history_file, 'at', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"写入历史记录失败 {module}: {e}")
            return

        self.last_history_entry.setdefault(client_id, {})[module] = current_data
        self._clean_history_file(history_file, max_seconds)

    def save_data_to_file(self, client_id: str, data: Dict[str, Any]):
        try:
            # 根据充电状态反转电池电流符号
            if 'battery' in data and isinstance(data['battery'], dict):
                if 'current' in data['battery']:
                    cur = data['battery']['current']
                    if isinstance(cur, (int, float)):
                        charging = data['battery'].get('charging', False)
                        if charging:
                            # 充电时：取正（绝对值）
                            data['battery']['current'] = abs(cur)
                        else:
                            # 放电时：取负（负绝对值）
                            data['battery']['current'] = -abs(cur)

            device_dir = self._ensure_device_dir(client_id)

            if 'device' in data:
                device_file = os.path.join(device_dir, "device_info.json")
                with open(device_file, 'w', encoding='utf-8') as f:
                    json.dump(data['device'], f, indent=2, ensure_ascii=False)

            timestamp = data.get('timestamp', int(time.time() * 1000))

            for module in DATA_MODULES:
                if module in data and data[module] is not None:
                    module_file = os.path.join(device_dir, f"{module}.json")
                    with open(module_file, 'w', encoding='utf-8') as f:
                        json.dump(data[module], f, indent=2, ensure_ascii=False)

                    save_flag, _ = self._get_module_history_settings(client_id, module)
                    if save_flag:
                        entry = {
                            "timestamp": timestamp,
                            "data": data[module]
                        }
                        self._save_history_entry(client_id, module, entry)

            timestamp_file = os.path.join(device_dir, "last_update.json")
            with open(timestamp_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "last_update": datetime.now().isoformat(),
                    "timestamp": timestamp
                }, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"❌ 保存数据失败 (设备 {client_id}): {e}")

    def get_device_stats(self) -> Dict[str, Any]:
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

            # ---------- 电池估算 ----------
            battery_estimate = None
            battery_data = data.get('battery', {})
            if isinstance(battery_data, dict):
                level = battery_data.get('level')
                current_raw = battery_data.get('current')   # 原始单位：µA
                capacity_raw = battery_data.get('capacity') # 原始单位：mAh
                charging = battery_data.get('charging', False)
                if (level is not None and current_raw is not None and capacity_raw is not None
                        and capacity_raw > 0):
                    current_mA = current_raw
                    if charging and current_mA > 0:
                        # 充电：计算充满所需时间
                        remaining_mAh = capacity_raw * (100 - level) / 100.0
                        if remaining_mAh > 0:
                            hours = remaining_mAh / current_mA
                            seconds = hours * 3600
                            battery_estimate = {
                                'type': 'charging',
                                'time_to_full_seconds': seconds,
                                'full_time': int((time.time() + seconds) * 1000)
                            }
                        else:
                            # 已充满
                            battery_estimate = {'type': 'charged', 'time_to_full_seconds': 0}
                    elif not charging and current_mA < 0:
                        # 放电：计算耗尽所需时间
                        remaining_mAh = capacity_raw * level / 100.0
                        if remaining_mAh > 0:
                            hours = remaining_mAh / abs(current_mA)
                            seconds = hours * 3600
                            battery_estimate = {
                                'type': 'discharging',
                                'time_to_empty_seconds': seconds,
                                'empty_time': int((time.time() + seconds) * 1000)
                            }
                        else:
                            battery_estimate = {'type': 'empty', 'time_to_empty_seconds': 0}

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
                "battery_estimate": battery_estimate,   # 新增
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

    # ========== HTTP API 历史数据 ==========
    async def http_history(self, request):
        device_id = request.query.get('device_id')
        module = request.query.get('module')
        if not device_id or not module:
            return web.json_response({"error": "缺少 device_id 或 module"}, status=400)

        history_file_gz = os.path.join(DATA_DIR, device_id, "hs", f"{module}.history.gz")
        history_file = os.path.join(DATA_DIR, device_id, "hs", f"{module}.history")
        if not os.path.exists(history_file_gz) and not os.path.exists(history_file):
            data = {"deviceId": device_id, "module": module, "timestamps": [], "series": {}}
            json_str = json.dumps(data, ensure_ascii=False)
            compressed = gzip.compress(json_str.encode('utf-8'))
            return web.Response(
                body=compressed,
                headers={'Content-Encoding': 'gzip', 'Content-Type': 'application/json'}
            )

        entries = []
        target_file = history_file_gz if os.path.exists(history_file_gz) else history_file
        open_func = gzip.open if target_file.endswith('.gz') else open
        try:
            with open_func(target_file, 'rt', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get('timestamp')
                        data = entry.get('data')
                        if ts is not None and data is not None:
                            entries.append((ts, data))
                    except:
                        continue
        except Exception as e:
            logger.error(f"读取历史数据失败 {target_file}: {e}")
            data = {"deviceId": device_id, "module": module, "timestamps": [], "series": {}}
            json_str = json.dumps(data, ensure_ascii=False)
            compressed = gzip.compress(json_str.encode('utf-8'))
            return web.Response(
                body=compressed,
                headers={'Content-Encoding': 'gzip', 'Content-Type': 'application/json'}
            )

        if not entries:
            data = {"deviceId": device_id, "module": module, "timestamps": [], "series": {}}
            json_str = json.dumps(data, ensure_ascii=False)
            compressed = gzip.compress(json_str.encode('utf-8'))
            return web.Response(
                body=compressed,
                headers={'Content-Encoding': 'gzip', 'Content-Type': 'application/json'}
            )

        all_fields = set()
        for _, data in entries:
            for key, value in data.items():
                if isinstance(value, (int, float, bool)):
                    all_fields.add(key)

        fields = sorted(list(all_fields))
        timestamps = [ts for ts, _ in entries]
        series = {}
        for field in fields:
            values = []
            for _, data in entries:
                val = data.get(field)
                if isinstance(val, bool):
                    val = int(val)
                if isinstance(val, (int, float)):
                    values.append(val)
                else:
                    values.append(None)
            series[field] = values

        data = {
            "deviceId": device_id,
            "module": module,
            "timestamps": timestamps,
            "series": series
        }
        json_str = json.dumps(data, ensure_ascii=False)
        compressed = gzip.compress(json_str.encode('utf-8'))
        return web.Response(
            body=compressed,
            headers={'Content-Encoding': 'gzip', 'Content-Type': 'application/json'}
        )


# ==================== HTML页面（字段级智能单位转换 + 图表容器复用 + 电池估算） ====================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>📱 设备数据监控 (智能单位转换)</title>
    <script src="/static/echarts.min.js"></script>
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
        .connection-status { font-size: 14px; color: #8899aa; }
        .connection-status .connected { color: #00ff88; }
        .connection-status .disconnected { color: #ff4444; }

        .device-row {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            align-items: stretch;
            background: rgba(20, 30, 50, 0.9);
            border-radius: 16px;
            border: 1px solid rgba(100, 200, 255, 0.08);
            padding: 16px;
            transition: border-color 0.3s;
        }
        .device-row:hover { border-color: rgba(100, 200, 255, 0.2); }
        .device-row.offline { opacity: 0.6; }

        .device-card {
            flex: 0 0 420px;
            min-width: 320px;
            display: flex;
            flex-direction: column;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(100,200,255,0.1);
            cursor: pointer;
        }
        .card-header .device-name { font-size: 16px; font-weight: 600; }
        .card-header .device-name .model { color: #00d4ff; }
        .card-header .device-status { display: flex; align-items: center; gap: 12px; font-size: 13px; flex-wrap: wrap; }
        .card-header .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
        .card-header .dot.online { background: #00ff88; box-shadow: 0 0 10px #00ff8866; }
        .card-header .dot.offline { background: #ff4444; }
        .card-body { padding-top: 10px; flex: 1; }
        .data-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 8px;
        }
        .data-item {
            background: rgba(0, 0, 0, 0.3);
            padding: 8px 12px;
            border-radius: 6px;
            border-left: 3px solid #00d4ff;
            cursor: pointer;
            transition: background 0.2s;
        }
        .data-item:hover { background: rgba(0, 100, 200, 0.15); }
        .data-item .label { font-size: 10px; color: #8899aa; text-transform: uppercase; letter-spacing: 0.5px; }
        .data-item .value { font-size: 14px; font-weight: 600; margin-top: 2px; }
        .data-item .value.good { color: #00ff88; }
        .data-item .value.warning { color: #ffaa00; }
        .data-item .value.danger { color: #ff4444; }
        .data-item .sub { font-size: 11px; color: #667788; margin-top: 2px; }
        .card-footer {
            margin-top: 10px;
            padding-top: 8px;
            border-top: 1px solid rgba(100,200,255,0.05);
            font-size: 12px;
            color: #8899aa;
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
        }
        .card-footer .time-value { color: #00d4ff; font-family: monospace; }

        .chart-wrapper {
            flex: 1;
            min-height: 350px;
            min-width: 300px;
            background: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 8px;
            position: relative;
        }
        .chart-wrapper .chart-placeholder {
            display: flex;
            align-items: center;
            justify-content: center;
            color: #667788;
            font-size: 16px;
            text-align: center;
            height: 100%;
            min-height: 280px;
            border: 1px dashed rgba(100,200,255,0.15);
            border-radius: 8px;
        }
        .chart-container {
            width: 100%;
            height: 100%;
            min-height: 350px;
        }

        @media (max-width: 900px) {
            .device-row { flex-wrap: wrap; }
            .device-card { flex: 1 1 100%; }
            .chart-wrapper { flex: 1 1 100%; min-height: 250px; }
        }
        .empty-state { text-align: center; padding: 60px 20px; color: #8899aa; }
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

    <div id="devicesContainer">
        <div class="empty-state">
            <div class="icon" style="font-size:48px;">📡</div>
            <p>等待数据...</p>
        </div>
    </div>
</div>

<script>
    // ==================== 全局状态 ====================
    var ws = null;
    var reconnectTimer = null;
    var cardStates = {};
    var chartInstances = {};

    // ==================== 格式化 ====================
    function formatTimestamp(ts) {
        if (!ts || ts <= 0) return '未知';
        try {
            var d = new Date(ts);
            if (isNaN(d.getTime())) return '无效时间';
            return d.toLocaleString('zh-CN', {
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
        } catch (e) { return '无效时间'; }
    }
    function formatTimestampShort(ts) {
        if (!ts || ts <= 0) return '';
        try {
            var d = new Date(ts);
            return d.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } catch (e) { return ''; }
    }

    // ==================== 智能单位转换（按字段名） ====================
    function transformField(module, field, values) {
        // 返回 { data: 转换后的数组, unit: 单位字符串 }
        var raw = values;
        if (module === 'battery') {
            // 电压 mV → V
            if (field === 'voltage') {
                return { data: raw.map(v => v !== null ? v / 1000 : null), unit: ' V' };
            }
            // 电流 µA → mA (假设原始是µA)
            if (field === 'current') {
                return { data: raw.map(v => v !== null ? v / 1000 : null), unit: ' mA' };
            }
            // 容量 mAh → Ah
            if (field === 'capacity') {
                return { data: raw.map(v => v !== null ? v / 1000 : null), unit: ' Ah' };
            }
            // 电量百分比，不缩放
            if (field === 'level') {
                return { data: raw, unit: ' %' };
            }
            // 温度 0.1°C → °C
            if (field === 'temperature') {
                return { data: raw.map(v => v !== null ? v / 10 : null), unit: ' °C' };
            }
        }
        // 网络模块特殊处理（由上层统一单位，此处原样返回）
        if (module === 'network' && (field === 'downSpeed' || field === 'upSpeed')) {
            return { data: raw, unit: '' }; // 单位由外层统一处理
        }
        // 其他字段：自动数值缩放（KB/MB/GB）
        return autoScaleByValue(raw);
    }

    function autoScaleByValue(values) {
        var maxVal = 0;
        for (var i=0; i<values.length; i++) {
            var v = Math.abs(values[i]);
            if (v > maxVal) maxVal = v;
        }
        if (maxVal === 0) return { data: values, unit: '' };
        var scale = 1;
        var unit = '';
        if (maxVal > 1024*1024*1024) { scale = 1/(1024*1024*1024); unit = ' GB'; }
        else if (maxVal > 1024*1024) { scale = 1/(1024*1024); unit = ' MB'; }
        else if (maxVal > 1024) { scale = 1/1024; unit = ' KB'; }
        if (maxVal > 1024) {
            var scaled = values.map(function(v) { return v !== null ? v * scale : null; });
            return { data: scaled, unit: unit };
        } else {
            return { data: values, unit: '' };
        }
    }

    // 网络流量统一单位：确保 downSpeed 和 upSpeed 使用相同单位 (KB/s 或 MB/s)
    function transformNetworkFields(fields, seriesData) {
        // fields 是包含 downSpeed, upSpeed 等的数组
        var downRaw = seriesData['downSpeed'] || [];
        var upRaw = seriesData['upSpeed'] || [];
        // 计算最大值，决定单位
        var maxVal = 0;
        var allVals = downRaw.concat(upRaw);
        for (var i=0; i<allVals.length; i++) {
            var v = Math.abs(allVals[i]);
            if (v > maxVal) maxVal = v;
        }
        var scale = 1;
        var unit = ' B/s';
        if (maxVal > 1024*1024) { scale = 1/(1024*1024); unit = ' MB/s'; }
        else if (maxVal > 1024) { scale = 1/1024; unit = ' KB/s'; }
        // 如果最大值小于1024，保持 B/s
        var transformed = {};
        fields.forEach(function(field) {
            var raw = seriesData[field] || [];
            if (field === 'downSpeed' || field === 'upSpeed') {
                var scaled = raw.map(function(v) { return v !== null ? v * scale : null; });
                transformed[field] = { data: scaled, unit: unit };
            } else {
                // 其他字段（如 signalLevel 等）单独处理
                var result = autoScaleByValue(raw);
                transformed[field] = { data: result.data, unit: result.unit };
            }
        });
        return transformed;
    }

    // ==================== 图表操作 ====================
    function initChart(deviceId) {
        var containerId = 'chart-' + deviceId.replace(/[^a-zA-Z0-9]/g, '_');
        var container = document.getElementById(containerId);
        if (!container) return null;
        var chart = echarts.init(container, 'dark');
        chartInstances[deviceId] = { chart: chart, currentModule: null, seriesData: { timestamps: [], fields: {} } };
        window.addEventListener('resize', function() { chart.resize(); });
        return chart;
    }

    function renderChart(deviceId, module, data) {
        var inst = chartInstances[deviceId];
        if (!inst) {
            var chart = initChart(deviceId);
            if (!chart) return;
            inst = chartInstances[deviceId];
        }
        var chart = inst.chart;
        inst.currentModule = module;
        inst.seriesData = {
            timestamps: data.timestamps || [],
            fields: data.series || {}
        };

        var timestamps = inst.seriesData.timestamps;
        var fields = Object.keys(inst.seriesData.fields);
        if (!timestamps.length || !fields.length) {
            chart.clear();
            chart.setOption({
                title: { text: '暂无历史数据', left: 'center', top: 'center', textStyle: { color: '#667788', fontSize: 16, fontWeight: 'normal' } }
            });
            chart.resize();
            return;
        }

        // ---- 智能转换 ----
        var transformedData = {};
        var seriesOptions = [];

        if (module === 'network') {
            // 网络模块特殊处理：统一 downSpeed/upSpeed 单位
            var netTransformed = transformNetworkFields(fields, inst.seriesData.fields);
            fields.forEach(function(field) {
                var item = netTransformed[field];
                if (item) {
                    transformedData[field] = item;
                } else {
                    // fallback
                    var raw = inst.seriesData.fields[field];
                    var result = autoScaleByValue(raw);
                    transformedData[field] = result;
                }
            });
            // 构建 series
            fields.forEach(function(field) {
                var item = transformedData[field];
                seriesOptions.push({
                    name: field + item.unit,
                    type: 'line',
                    data: item.data,
                    smooth: true,
                    symbol: 'circle',
                    symbolSize: 3,
                    connectNulls: true
                });
            });
        } else {
            // 其他模块：逐字段转换
            fields.forEach(function(field) {
                var raw = inst.seriesData.fields[field];
                var result = transformField(module, field, raw);
                transformedData[field] = result;
                seriesOptions.push({
                    name: field + result.unit,
                    type: 'line',
                    data: result.data,
                    smooth: true,
                    symbol: 'circle',
                    symbolSize: 3,
                    connectNulls: true
                });
            });
        }

        // 图例数据（带单位）
        var legendData = fields.map(function(field) {
            return field + (transformedData[field] ? transformedData[field].unit : '');
        });

        var timeLabels = timestamps.map(function(ts) { return formatTimestampShort(ts); });

        var option = {
            tooltip: {
                trigger: 'axis',
                formatter: function(params) {
                    var ts = timestamps[params[0].dataIndex];
                    var html = formatTimestamp(ts) + '<br/>';
                    params.forEach(function(p) {
                        html += p.marker + ' ' + p.seriesName + ': ' + p.value + '<br/>';
                    });
                    return html;
                }
            },
            legend: {
                data: legendData,
                textStyle: { color: '#8899aa' },
                type: 'scroll',
                top: 0,
                left: 'center'
            },
            grid: { left: '5%', right: '5%', bottom: '25%', top: '15%', containLabel: true },
            xAxis: {
                type: 'category',
                data: timeLabels,
                axisLabel: { rotate: 30, interval: Math.max(1, Math.floor(timeLabels.length / 20)), color: '#8899aa', fontSize: 10 },
                axisLine: { lineStyle: { color: '#334455' } }
            },
            yAxis: {
                type: 'value',
                axisLabel: { color: '#8899aa' },
                splitLine: { lineStyle: { color: '#1a2a3a', type: 'dashed' } }
            },
            series: seriesOptions,
            dataZoom: [{
                type: 'slider',
                start: 0,
                end: 100,
                height: 20,
                bottom: 5,
                borderColor: '#1a2a3a',
                fillerColor: 'rgba(0, 212, 255, 0.1)',
                handleStyle: { color: '#00d4ff' },
                textStyle: { color: '#8899aa' }
            }]
        };
        chart.setOption(option, true);
        chart.resize();
    }

    function updateChartNewPoint(deviceId, module, timestamp, newData) {
        var inst = chartInstances[deviceId];
        if (!inst) return;
        if (inst.currentModule !== module) return;

        var seriesData = inst.seriesData;
        var fields = Object.keys(seriesData.fields);
        if (fields.length === 0) return;

        var lastTs = seriesData.timestamps[seriesData.timestamps.length - 1];
        if (lastTs === timestamp) return;

        seriesData.timestamps.push(timestamp);
        fields.forEach(function(field) {
            var val = newData[field];
            if (typeof val === 'boolean') val = val ? 1 : 0;
            if (typeof val === 'number') {
                seriesData.fields[field].push(val);
            } else {
                seriesData.fields[field].push(null);
            }
        });

        // 重新转换并更新
        var timestamps = seriesData.timestamps;
        var fields2 = Object.keys(seriesData.fields);
        var transformedData = {};
        var seriesOptions = [];

        if (module === 'network') {
            var netTransformed = transformNetworkFields(fields2, seriesData.fields);
            fields2.forEach(function(field) {
                var item = netTransformed[field];
                if (item) {
                    transformedData[field] = item;
                } else {
                    var raw = seriesData.fields[field];
                    var result = autoScaleByValue(raw);
                    transformedData[field] = result;
                }
            });
            fields2.forEach(function(field) {
                var item = transformedData[field];
                seriesOptions.push({
                    name: field + item.unit,
                    type: 'line',
                    data: item.data,
                    smooth: true,
                    symbol: 'circle',
                    symbolSize: 3,
                    connectNulls: true
                });
            });
        } else {
            fields2.forEach(function(field) {
                var raw = seriesData.fields[field];
                var result = transformField(module, field, raw);
                transformedData[field] = result;
                seriesOptions.push({
                    name: field + result.unit,
                    type: 'line',
                    data: result.data,
                    smooth: true,
                    symbol: 'circle',
                    symbolSize: 3,
                    connectNulls: true
                });
            });
        }

        var legendData = fields2.map(function(field) {
            return field + (transformedData[field] ? transformedData[field].unit : '');
        });
        var timeLabels = timestamps.map(function(ts) { return formatTimestampShort(ts); });

        inst.chart.setOption({
            xAxis: { data: timeLabels },
            series: seriesOptions,
            legend: { data: legendData }
        }, false);
        inst.chart.resize();
    }

    function loadHistory(deviceId, module) {
        var inst = chartInstances[deviceId];
        if (!inst) {
            initChart(deviceId);
            inst = chartInstances[deviceId];
            if (!inst) return;
        }
        var chart = inst.chart;
        chart.clear();
        chart.setOption({
            title: { text: '加载中...', left: 'center', top: 'center', textStyle: { color: '#667788', fontSize: 16 } }
        });
        chart.resize();

        fetch('/api/history?device_id=' + encodeURIComponent(deviceId) + '&module=' + encodeURIComponent(module))
            .then(res => res.json())
            .then(data => {
                if (data.series) {
                    renderChart(deviceId, module, data);
                } else {
                    renderChart(deviceId, module, { timestamps: [], series: {} });
                }
            })
            .catch(err => {
                console.error('加载历史失败:', err);
                renderChart(deviceId, module, { timestamps: [], series: {} });
            });
    }

    function onDataItemClick(deviceId, module) {
        loadHistory(deviceId, module);
    }

    // ==================== 渲染设备（复用图表容器 + 电池估算） ====================
    function renderDevices(stats) {
        var container = document.getElementById('devicesContainer');
        if (!container) return;

        var deviceKeys = Object.keys(stats.devices || {});
        document.getElementById('totalClients').textContent = stats.total_clients || 0;

        if (deviceKeys.length === 0) {
            container.innerHTML = '<div class="empty-state"><div style="font-size:48px;">📡</div><p>暂无设备连接</p></div>';
            for (var id in chartInstances) {
                if (chartInstances[id].chart) chartInstances[id].chart.dispose();
            }
            chartInstances = {};
            return;
        }

        // 保存旧图表容器
        var savedCharts = {};
        var existingRows = container.querySelectorAll('.device-row');
        existingRows.forEach(function(row) {
            var cid = row.getAttribute('data-client-id');
            if (!cid) return;
            var chartContainer = row.querySelector('.chart-container');
            if (chartContainer) {
                chartContainer.remove();
                savedCharts[cid] = chartContainer;
            }
        });

        container.innerHTML = '';

        // 辅助函数：格式化时长
        function formatDuration(seconds) {
            if (!seconds || seconds < 0) return '--:--';
            if (seconds < 60) return '0分钟';
            var hours = Math.floor(seconds / 3600);
            var minutes = Math.floor((seconds % 3600) / 60);
            if (hours > 0) {
                return hours + '小时' + minutes + '分钟';
            } else {
                return minutes + '分钟';
            }
        }
        // 辅助函数：格式化到达时间
        function formatArrivalTime(ts) {
            if (!ts) return '--:--';
            var d = new Date(ts);
            var now = new Date();
            var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            var tomorrow = new Date(today.getTime() + 86400000);
            var target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
            var prefix = '';
            if (target.getTime() === today.getTime()) prefix = '今天 ';
            else if (target.getTime() === tomorrow.getTime()) prefix = '明天 ';
            else {
                return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
            }
            return prefix + d.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        }

        var html = '';
        for (var i = 0; i < deviceKeys.length; i++) {
            var clientId = deviceKeys[i];
            var dev = stats.devices[clientId];
            var isOnline = dev.last_seen !== null;

            var battery = dev.battery || {};
            var batteryLevel = battery.level !== undefined ? battery.level : '?';
            var batteryClass = batteryLevel >= 50 ? 'good' : (batteryLevel >= 20 ? 'warning' : 'danger');

            // 电池估算
            var batteryEst = dev.battery_estimate || null;
            var batterySub = battery.charging ? '⚡ 充电中' : '🔌 未充电';
            if (batteryEst) {
                if (batteryEst.type === 'charging') {
                    var secs = batteryEst.time_to_full_seconds;
                    if (secs > 0) {
                        var dur = formatDuration(secs);
                        var arrival = formatArrivalTime(batteryEst.full_time);
                        batterySub += ' - 距离充满 ' + dur + ' - 到达时间 ' + arrival;
                    } else {
                        batterySub += ' - 已充满';
                    }
                } else if (batteryEst.type === 'discharging') {
                    var secs = batteryEst.time_to_empty_seconds;
                    if (secs > 0) {
                        var dur = formatDuration(secs);
                        var arrival = formatArrivalTime(batteryEst.empty_time);
                        batterySub += ' - 距离耗尽 ' + dur + ' - 到达时间 ' + arrival;
                    } else {
                        batterySub += ' - 已耗尽';
                    }
                } else if (batteryEst.type === 'charged') {
                    batterySub += ' - 已充满';
                } else if (batteryEst.type === 'empty') {
                    batterySub += ' - 已耗尽';
                }
            }

            var foreground = dev.foreground || {};
            var fgTitle = foreground.windowTitle || foreground.packageName || 'Unknown';
            var fgApp = foreground.packageName || 'Unknown';

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
            var chartId = 'chart-' + safeId;

            var isOpen = cardStates[clientId] || false;
            var toggleIcon = isOpen ? '▲' : '▼';

            var netIcon = netConnected ? '✅' : '❌';
            var netStatusClass = netConnected ? 'connected' : 'disconnected';
            var netTypeDisplay = netType;
            if (netTypeDetail) netTypeDisplay += ' (' + netTypeDetail + ')';
            if (netSignalLevel) netTypeDisplay += ' 信号: ' + netSignalLevel;

            var dataItems = [
                { label: '🔋 电池', value: batteryLevel + '%', sub: batterySub, cls: batteryClass, module: 'battery' },
                { label: '💾 内存', value: memoryUsedMB + ' / ' + memoryMB + ' MB', sub: '使用 ' + memoryPercent + '%', module: 'memory' },
                { label: '💾 存储', value: storageUsedGB + ' / ' + storageGB + ' GB', sub: '使用 ' + storagePercent + '%', module: 'storage' },
                { label: '📱 前台', value: fgTitle, sub: fgApp, module: 'foreground' },
                { label: '🖥️ 屏幕', value: screenStatus, sub: '亮度: ' + brightness, module: 'screen' },
                { label: '📍 位置', value: locationStr, sub: hasLocation ? '✅ GPS' : '❌ 无定位', module: 'location' },
                { label: '📡 传感器', value: sensorCount + ' 个', module: 'sensors' }
            ];
            var dataItemsHtml = '';
            for (var j = 0; j < dataItems.length; j++) {
                var item = dataItems[j];
                var clickAttr = item.module ? ` onclick="onDataItemClick('${clientId}', '${item.module}')"` : '';
                dataItemsHtml += `
                    <div class="data-item"${clickAttr}>
                        <div class="label">${item.label}</div>
                        <div class="value ${item.cls || ''}">${item.value}</div>
                        <div class="sub">${item.sub}</div>
                    </div>
                `;
            }

            var netItemsHtml = `
                <div class="data-item" style="border-left-color:#00d4ff;">
                    <div class="label">🌐 网络</div>
                    <div class="value" style="font-size:14px;">${netTypeDisplay}</div>
                    <div class="sub">${netDetail ? '| ' + netDetail : ''}</div>
                </div>
                <div class="data-item" style="border-left-color:#00d4ff;" onclick="onDataItemClick('${clientId}', 'network')">
                    <div class="label">⬇️ 下载</div>
                    <div class="value good">${netDownSpeed}</div>
                    <div class="sub">间隔: ${netIntervalRx}</div>
                </div>
                <div class="data-item" style="border-left-color:#ffaa00;" onclick="onDataItemClick('${clientId}', 'network')">
                    <div class="label">⬆️ 上传</div>
                    <div class="value warning">${netUpSpeed}</div>
                    <div class="sub">间隔: ${netIntervalTx}</div>
                </div>
                <div class="data-item" style="border-left-color:#667788;" onclick="onDataItemClick('${clientId}', 'network')">
                    <div class="label">📊 总流量</div>
                    <div class="value" style="font-size:13px;">⬇️ ${netTotalRx} / ⬆️ ${netTotalTx}</div>
                </div>
            `;

            var rowClass = isOnline ? '' : 'offline';
            html += `
            <div class="device-row ${rowClass}" data-client-id="${clientId}" id="row-${safeId}">
                <div class="device-card">
                    <div class="card-header" onclick="toggleCard('${bodyId}', '${clientId}')">
                        <div class="device-name">
                            <span class="model">${deviceModel}</span>
                            <span style="font-size:13px;color:#8899aa;margin-left:8px;">${deviceManufacturer}</span>
                            <span style="font-size:12px;color:#556677;margin-left:8px;">${screenWidth}×${screenHeight}</span>
                        </div>
                        <div class="device-status">
                            <span>🔋 ${batteryLevel}%</span>
                            <span>📱 ${fgTitle.length > 15 ? fgTitle.substring(0,15)+'...' : fgTitle}</span>
                            <span class="network-status">
                                <span class="${netStatusClass}">${netIcon}</span>
                                ${netType}
                            </span>
                            <span><span class="dot ${isOnline ? 'online' : 'offline'}"></span>${isOnline ? '在线' : '离线'}</span>
                            <span style="font-size:18px;transition:transform 0.3s;">${toggleIcon}</span>
                        </div>
                    </div>
                    <div class="card-body" id="${bodyId}" style="display:${isOpen ? 'block' : 'none'};">
                        <div class="data-grid">
                            ${dataItemsHtml}
                        </div>
                        <div class="data-grid" style="margin-top:10px;">
                            ${netItemsHtml}
                        </div>
                        <div class="card-footer">
                            <span>🕐 更新: <span class="time-value">${updateTimeStr}</span></span>
                            <span>📊 权限: ${dev.permission_level || 'unknown'}</span>
                        </div>
                    </div>
                </div>
                <div class="chart-wrapper">
                    <div id="${chartId}" class="chart-container"></div>
                </div>
            </div>
            `;
        }
        container.innerHTML = html;

        // 恢复保存的图表容器
        var newRows = container.querySelectorAll('.device-row');
        newRows.forEach(function(row) {
            var cid = row.getAttribute('data-client-id');
            if (!cid) return;
            var saved = savedCharts[cid];
            if (saved) {
                var wrapper = row.querySelector('.chart-wrapper');
                if (wrapper) {
                    wrapper.innerHTML = '';
                    wrapper.appendChild(saved);
                    var inst = chartInstances[cid];
                    if (inst && inst.chart) {
                        inst.chart.resize();
                    }
                }
            } else {
                var placeholder = row.querySelector('.chart-container');
                if (placeholder) {
                    placeholder.innerHTML = '<div class="chart-placeholder">📊 点击左侧数据项查看趋势</div>';
                }
            }
        });

        // 清理不存在的图表实例
        var currentIds = {};
        newRows.forEach(function(row) {
            var cid = row.getAttribute('data-client-id');
            if (cid) currentIds[cid] = true;
        });
        for (var id in chartInstances) {
            if (!currentIds[id]) {
                if (chartInstances[id].chart) chartInstances[id].chart.dispose();
                delete chartInstances[id];
            }
        }

        // 恢复已有图表的 option
        for (var id in chartInstances) {
            var inst = chartInstances[id];
            if (inst.currentModule && inst.seriesData.timestamps.length > 0) {
                var containerId = 'chart-' + id.replace(/[^a-zA-Z0-9]/g, '_');
                var el = document.getElementById(containerId);
                if (el && inst.chart) {
                    inst.chart.resize();
                }
            }
        }
    }

    function toggleCard(bodyId, clientId) {
        var body = document.getElementById(bodyId);
        if (!body) return;
        var isOpen = body.style.display !== 'none';
        body.style.display = isOpen ? 'none' : 'block';
        cardStates[clientId] = !isOpen;
        var row = document.getElementById('row-' + clientId.replace(/[^a-zA-Z0-9]/g, '_'));
        if (row) {
            var icon = row.querySelector('.card-header span:last-child');
            if (icon) icon.textContent = isOpen ? '▼' : '▲';
        }
        setTimeout(function() {
            var inst = chartInstances[clientId];
            if (inst && inst.chart) inst.chart.resize();
        }, 100);
    }

    // ==================== WebSocket ====================
    function connectWebSocket() {
        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsUrl = protocol + '//' + window.location.hostname + ':' + {{WS_PORT}};
        ws = new WebSocket(wsUrl);

        ws.onopen = function() {
            document.getElementById('connStatus').innerHTML = '🟢 已连接 (实时)';
            document.getElementById('connStatus').className = 'connection-status connected';
            ws.send(JSON.stringify({type: 'web'}));
            if (reconnectTimer) { clearInterval(reconnectTimer); reconnectTimer = null; }
        };

        ws.onmessage = function(event) {
            try {
                var msg = JSON.parse(event.data);
                if (msg.type !== 'history_response') {
                    renderDevices(msg);
                    for (var clientId in msg.devices) {
                        var inst = chartInstances[clientId];
                        if (!inst || !inst.currentModule) continue;
                        var devData = msg.devices[clientId];
                        if (!devData) continue;
                        var moduleData = devData[inst.currentModule];
                        if (!moduleData) continue;
                        var timestamp = devData.data_timestamp || Date.now();
                        updateChartNewPoint(clientId, inst.currentModule, timestamp, moduleData);
                    }
                }
            } catch (e) {
                console.error('解析消息失败:', e);
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

        ws.onerror = function(err) { console.error('WebSocket错误:', err); };
    }

    connectWebSocket();
    console.log('📱 DeviceMonitor (智能单位转换, 图表容器复用, 电池估算) 已启动');
</script>
</body>
</html>
"""


def get_html():
    ws_port = CONFIG.get("ws_port", 91)
    return HTML_PAGE.replace("{{WS_PORT}}", str(ws_port))


async def web_handler(request):
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
    global CONFIG, DATA_MODULES
    logger.info("🔄 正在重新加载配置并重启服务器...")
    await server.stop()
    try:
        new_config = load_config()
        CONFIG = new_config
        log_level = CONFIG.get('log_level', 0)
        set_log_level(log_level)
        DATA_MODULES = CONFIG.get('data_modules', DEFAULT_CONFIG['data_modules'])
        logger.info("✅ 配置文件已重新加载")
        logger.info(f"📦 数据模块: {', '.join(DATA_MODULES)}")
    except Exception as e:
        logger.error(f"❌ 重载配置失败: {e}")
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
    server = DeviceDataServer()
    ws_port = CONFIG.get("ws_port", 91)
    web_port = CONFIG.get("web_port", 80)
    host = CONFIG.get("host", "0.0.0.0")

    logger.info("=" * 80)
    logger.info("📱 设备数据接收服务器 v15.3 (智能单位转换 + 电池估算)")
    logger.info("=" * 80)
    logger.info(f"🌐 WebSocket: ws://{host}:{ws_port}")
    logger.info(f"🌐 Web界面: http://{host}:{web_port}")
    logger.info(f"📁 数据存储目录: {DATA_DIR}")
    logger.info("📁 存储结构: device_data/<deviceId>/<module>.json + hs/<module>.history.gz")
    logger.info("📝 全局配置: server_config.json")
    logger.info("📝 设备配置: device_data/<deviceId>/historySet.ini")
    logger.info(f"📦 数据模块: {', '.join(DATA_MODULES)}")
    logger.info("📄 静态资源: /static/echarts.min.js")
    logger.info("💡 在终端输入 .help 查看命令")
    logger.info("=" * 80)

    app = web.Application()
    app.router.add_get('/', web_handler)
    app.router.add_get('/api/stats', server.http_history)
    app.router.add_get('/api/devices', lambda r: web.json_response(server.device_data))
    app.router.add_get('/api/history', server.http_history)
    app.router.add_static('/static', 'static')

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, web_port)
    await site.start()
    logger.info(f"✅ Web界面: http://localhost:{web_port}")

    stdin_task = asyncio.create_task(stdin_reader(server))

    ws_task = None
    need_restart = True

    while True:
        if need_restart:
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
            await server.stop()
            need_restart = False
            try:
                ws_task = asyncio.create_task(server.start())
                logger.info("WebSocket 服务器启动任务已创建")
            except Exception as e:
                logger.error(f"创建启动任务失败: {e}")
                await asyncio.sleep(5)
                need_restart = True
                continue

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