# device_collector_ForDesktop.py
# Windows 设备信息收集器 - 支持 WebSocket 上报（配置由服务器动态下发）

import asyncio
import json
import logging
import time
import platform
import socket
import psutil
import wmi
import subprocess
import re
import sys
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

# ==================== 固定配置（仅 deviceId 和 wsServer） ====================
DEVICE_ID = None  # 将在初始化时设置
FIXED_CONFIG = {
    "wsServer": "localhost:91"
    # deviceId 动态设置
}

# 最终配置（由固定配置 + 服务器下发合并而成）
CONFIG = {}


def init_config():
    """初始化配置：设置固定值，并赋予默认采集参数（等待服务器下发更新）"""
    global CONFIG, DEVICE_ID
    DEVICE_ID = get_device_id()
    CONFIG = {
        "deviceId": DEVICE_ID,
        "wsServer": FIXED_CONFIG["wsServer"],
        # 默认采集参数（后续可由服务器覆盖）
        "updateInterval": 1,
        "collectBasicInfo": True,
        "collectBattery": True,
        "collectForegroundApp": True,
        "collectMemory": True,
        "collectCpuInfo": True,
        "collectStorageInfo": True,
        "collectNetwork": True,
        "collectProcesses": True
    }


# ==================== 获取设备信息 ====================
def get_device_id():
    """获取设备名称作为设备ID"""
    try:
        return socket.gethostname()
    except:
        return "WINDOWS_" + str(int(time.time()))


def get_windows_version():
    """获取Windows版本"""
    try:
        return platform.version()
    except:
        return "Unknown"


def get_os_info():
    """获取操作系统信息"""
    try:
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor()
        }
    except:
        return {
            "system": "Windows",
            "release": "Unknown",
            "version": "Unknown",
            "machine": "Unknown",
            "processor": "Unknown"
        }


def get_system_manufacturer():
    """获取系统制造商"""
    try:
        c = wmi.WMI()
        for item in c.Win32_ComputerSystem():
            return item.Manufacturer or "Unknown"
    except:
        return "Unknown"


def get_system_model():
    """获取系统型号"""
    try:
        c = wmi.WMI()
        for item in c.Win32_ComputerSystem():
            return item.Model or "Unknown"
    except:
        return "Unknown"


def get_screen_info():
    """获取屏幕信息"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        return {
            "width": width,
            "height": height,
            "density": 96  # Windows 默认 DPI
        }
    except:
        return {"width": -1, "height": -1, "density": -1}


# ==================== 电池信息 ====================
def get_battery_info():
    """获取电池信息"""
    result = {
        "level": -1,
        "charging": False,
        "temperature": -1,
        "voltage": -1,
        "health": "Unknown"
    }
    try:
        battery = psutil.sensors_battery()
        if battery:
            result["level"] = int(battery.percent)
            result["charging"] = battery.power_plugged
    except:
        pass
    return result


# ==================== 前台应用（Windows） ====================
def get_foreground_app():
    """获取前台应用"""
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        
        hwnd = user32.GetForegroundWindow()
        if hwnd == 0:
            return {"packageName": "Unknown", "activity": "Unknown", "source": "none"}
        
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            window_title = buff.value
        else:
            window_title = "Unknown"
        
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        process_name = "Unknown"
        try:
            if pid.value > 0:
                process = psutil.Process(pid.value)
                process_name = process.name()
        except:
            pass
        
        return {
            "packageName": process_name,
            "activity": window_title,
            "source": "win32",
            "pid": pid.value,
            "windowTitle": window_title
        }
    except Exception as e:
        logger.debug(f"获取前台应用失败: {e}")
        return {"packageName": "Unknown", "activity": "Unknown", "source": "error"}


# ==================== 内存信息 ====================
def get_memory_info():
    """获取内存信息"""
    try:
        mem = psutil.virtual_memory()
        return {
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
            "usagePercent": round(mem.percent, 1)
        }
    except:
        return {"total": -1, "used": -1, "available": -1, "usagePercent": -1}


# ==================== CPU信息 ====================
def get_cpu_info():
    """获取CPU信息"""
    try:
        cpu_info = {
            "cores": psutil.cpu_count(logical=True),
            "physicalCores": psutil.cpu_count(logical=False),
            "model": "Unknown",
            "usage": psutil.cpu_percent(interval=0.5)
        }
        
        try:
            output = subprocess.check_output(
                "wmic cpu get name", 
                shell=True, 
                encoding='gbk',
                errors='ignore'
            )
            lines = output.strip().split('\n')
            if len(lines) >= 2:
                cpu_info["model"] = lines[1].strip()
        except:
            pass
        
        return cpu_info
    except:
        return {"cores": -1, "model": "Unknown", "usage": -1}


# ==================== 存储信息 ====================
def get_storage_info():
    """获取存储信息（汇总所有磁盘）"""
    try:
        partitions = []
        total_size = 0
        total_used = 0
        total_free = 0
        
        for partition in psutil.disk_partitions():
            try:
                if 'cdrom' in partition.opts or 'removable' in partition.opts:
                    continue
                
                usage = psutil.disk_usage(partition.mountpoint)
                partitions.append({
                    "mountpoint": partition.mountpoint,
                    "total": usage.total,
                    "used": usage.used,
                    "available": usage.free,
                    "usagePercent": round(usage.percent, 1),
                    "filesystem": partition.fstype,
                    "device": partition.device
                })
                
                total_size += usage.total
                total_used += usage.used
                total_free += usage.free
            except:
                pass
        
        if not partitions:
            return {"total": -1, "used": -1, "available": -1, "usagePercent": -1, "partitions": []}
        
        total_percent = 0
        if total_size > 0:
            total_percent = round((total_used / total_size) * 100, 1)
        
        return {
            "total": total_size,
            "used": total_used,
            "available": total_free,
            "usagePercent": total_percent,
            "partitions": partitions,
            "partitionCount": len(partitions)
        }
    except Exception as e:
        logger.debug(f"获取存储信息失败: {e}")
        return {"total": -1, "used": -1, "available": -1, "usagePercent": -1, "partitions": []}


# ==================== 网络信息 ====================
class NetworkStats:
    def __init__(self):
        self.last_rx = 0
        self.last_tx = 0
        self.last_time = 0
        self.initialized = False

    def get_network_stats(self, interval_seconds):
        result = {
            "type": "未知",
            "detail": "未获取",
            "isConnected": False,
            "isWifi": False,
            "isMobile": False,
            "downSpeed": 0,
            "upSpeed": 0,
            "downSpeedStr": "0 B/s",
            "upSpeedStr": "0 B/s",
            "intervalRx": 0,
            "intervalTx": 0,
            "intervalRxStr": "0 B",
            "intervalTxStr": "0 B",
            "totalRx": 0,
            "totalTx": 0,
            "totalRxStr": "0 B",
            "totalTxStr": "0 B"
        }
        
        try:
            net_io = psutil.net_io_counters()
            current_rx = net_io.bytes_recv
            current_tx = net_io.bytes_sent
            current_time = time.time()
            
            try:
                socket.create_connection(("8.8.8.8", 53), timeout=1)
                result["isConnected"] = True
            except:
                result["isConnected"] = False
            
            try:
                adapters = psutil.net_if_stats()
                wifi_found = False
                for name, stats in adapters.items():
                    if stats.isup:
                        if "wi-fi" in name.lower() or "wireless" in name.lower() or "wlan" in name.lower():
                            wifi_found = True
                            break
                if wifi_found:
                    result["type"] = "WiFi"
                    result["detail"] = "WiFi"
                    result["isWifi"] = True
                elif result["isConnected"]:
                    result["type"] = "以太网"
                    result["detail"] = "以太网"
                    result["isWifi"] = False
                else:
                    result["type"] = "无网络"
            except:
                if result["isConnected"]:
                    result["type"] = "已连接"
                    result["detail"] = "已连接"
            
            result["totalRx"] = current_rx
            result["totalTx"] = current_tx
            result["totalRxStr"] = format_bytes(current_rx)
            result["totalTxStr"] = format_bytes(current_tx)
            
            if not self.initialized:
                self.last_rx = current_rx
                self.last_tx = current_tx
                self.last_time = current_time
                self.initialized = True
                return result
            
            time_diff = current_time - self.last_time
            if time_diff < 0.1:
                time_diff = 0.1
            
            rx_diff = current_rx - self.last_rx
            tx_diff = current_tx - self.last_tx
            
            if rx_diff < 0:
                rx_diff = 0
            if tx_diff < 0:
                tx_diff = 0
            
            result["intervalRx"] = rx_diff
            result["intervalTx"] = tx_diff
            result["intervalRxStr"] = format_bytes(rx_diff)
            result["intervalTxStr"] = format_bytes(tx_diff)
            
            result["downSpeed"] = rx_diff / time_diff
            result["upSpeed"] = tx_diff / time_diff
            result["downSpeedStr"] = format_speed(result["downSpeed"])
            result["upSpeedStr"] = format_speed(result["upSpeed"])
            
            self.last_rx = current_rx
            self.last_tx = current_tx
            self.last_time = current_time
            
            return result
        except Exception as e:
            logger.debug(f"获取网络统计失败: {e}")
            return result


def format_speed(bytes_per_second):
    if bytes_per_second < 0:
        return "0 B/s"
    if bytes_per_second < 1024:
        return f"{bytes_per_second:.0f} B/s"
    elif bytes_per_second < 1024 * 1024:
        return f"{bytes_per_second / 1024:.1f} KB/s"
    elif bytes_per_second < 1024 * 1024 * 1024:
        return f"{bytes_per_second / 1024 / 1024:.1f} MB/s"
    else:
        return f"{bytes_per_second / 1024 / 1024 / 1024:.1f} GB/s"


def format_bytes(bytes_val):
    if bytes_val < 0:
        return "0 B"
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / 1024 / 1024:.1f} MB"
    else:
        return f"{bytes_val / 1024 / 1024 / 1024:.2f} GB"


# ==================== 进程信息 ====================
def get_top_processes():
    try:
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                pinfo = proc.info
                if pinfo['cpu_percent'] > 0 or pinfo['memory_percent'] > 0:
                    processes.append({
                        "pid": pinfo['pid'],
                        "name": pinfo['name'] or "Unknown",
                        "cpuPercent": round(pinfo['cpu_percent'], 1),
                        "memoryPercent": round(pinfo['memory_percent'], 1)
                    })
            except:
                pass
        
        processes.sort(key=lambda x: x['cpuPercent'], reverse=True)
        return processes[:10]
    except:
        return []


# ==================== 数据收集器 ====================
class DeviceCollector:
    def __init__(self):
        init_config()  # 初始化 CONFIG
        
        self.device_id = CONFIG["deviceId"]
        self.network_stats = NetworkStats()
        self.send_count = 0
        self.is_running = True
        self.ws = None
        self.ws_connected = False
        self.config_received = False
        self.send_task = None  # 当前定时发送任务
        
        # 收集固定设备信息
        self.device_info = self._collect_device_info()
        
        logger.info("=" * 60)
        logger.info(f"📱 Windows 设备收集器 (动态配置)")
        logger.info("=" * 60)
        logger.info(f"📋 设备ID: {self.device_id}")
        logger.info(f"🏭 制造商: {self.device_info.get('manufacturer', 'Unknown')}")
        logger.info(f"📦 系统: {self.device_info.get('os', {}).get('system', 'Unknown')} {self.device_info.get('os', {}).get('release', 'Unknown')}")
        logger.info(f"📐 屏幕: {self.device_info.get('screenWidth', -1)}x{self.device_info.get('screenHeight', -1)}")
        logger.info(f"⏱️ 上传间隔: {CONFIG.get('updateInterval', 1)}秒 (默认，等待服务器下发)")
        logger.info("=" * 60)

    def _collect_device_info(self):
        """收集设备基础信息"""
        os_info = get_os_info()
        screen = get_screen_info()
        
        return {
            "model": self.device_id,
            "marketName": self.device_id,
            "manufacturer": get_system_manufacturer(),
            "os": os_info,
            "screenWidth": screen["width"],
            "screenHeight": screen["height"],
            "screenDensity": screen["density"],
            "systemModel": get_system_model(),
            "windowsVersion": get_windows_version()
        }

    def _update_config(self, server_config: Dict):
        """更新配置（只更新非固定字段）"""
        for k, v in server_config.items():
            if k not in ("deviceId", "wsServer"):
                CONFIG[k] = v
        logger.info(f"✅ 配置已更新: updateInterval={CONFIG.get('updateInterval')}, "
                    f"collectBattery={CONFIG.get('collectBattery')}, "
                    f"collectNetwork={CONFIG.get('collectNetwork')} ...")

    def collect_all_data(self):
        """收集所有数据（使用当前 CONFIG）"""
        data = {
            "deviceId": CONFIG["deviceId"],
            "timestamp": int(time.time() * 1000),
            "permissionLevel": 2
        }

        if CONFIG.get("collectBasicInfo", True):
            data["device"] = self.device_info

        if CONFIG.get("collectBattery", True):
            data["battery"] = get_battery_info()

        if CONFIG.get("collectForegroundApp", True):
            data["foreground"] = get_foreground_app()

        if CONFIG.get("collectMemory", True):
            data["memory"] = get_memory_info()

        if CONFIG.get("collectCpuInfo", True):
            data["cpu"] = get_cpu_info()

        if CONFIG.get("collectStorageInfo", True):
            data["storage"] = get_storage_info()

        if CONFIG.get("collectNetwork", True):
            net_stats = self.network_stats.get_network_stats(CONFIG.get("updateInterval", 1))
            data["network"] = {
                "type": net_stats["type"],
                "detail": net_stats["detail"],
                "isConnected": net_stats["isConnected"],
                "isWifi": net_stats["isWifi"],
                "isMobile": net_stats["isMobile"],
                "signalLevel": None,
                "networkType": None,
                "downSpeed": net_stats["downSpeed"],
                "upSpeed": net_stats["upSpeed"],
                "downSpeedStr": net_stats["downSpeedStr"],
                "upSpeedStr": net_stats["upSpeedStr"],
                "intervalRx": net_stats["intervalRx"],
                "intervalTx": net_stats["intervalTx"],
                "intervalRxStr": net_stats["intervalRxStr"],
                "intervalTxStr": net_stats["intervalTxStr"],
                "totalRx": net_stats["totalRx"],
                "totalTx": net_stats["totalTx"],
                "totalRxStr": net_stats["totalRxStr"],
                "totalTxStr": net_stats["totalTxStr"]
            }

        if CONFIG.get("collectProcesses", True):
            data["processes"] = get_top_processes()

        return data

    async def _restart_sender(self):
        """重启定时发送任务（当配置更新时调用）"""
        if self.send_task and not self.send_task.done():
            self.send_task.cancel()
            try:
                await self.send_task
            except asyncio.CancelledError:
                pass
        if self.is_running and self.ws_connected:
            self.send_task = asyncio.create_task(self._send_loop())

    async def _send_loop(self):
        """定时发送循环"""
        while self.is_running and self.ws_connected:
            try:
                data = self.collect_all_data()
                data["dataType"] = "diff"
                await self.ws.send(json.dumps(data))
                self.send_count += 1
                if self.send_count % 20 == 0:
                    logger.info(f"📤 已发送 {self.send_count} 次")
            except Exception as e:
                logger.error(f"❌ 发送失败: {e}")
                self.ws_connected = False
                asyncio.create_task(self.connect_websocket())
                break
            
            # 等待下一个间隔（动态获取间隔，可能被配置更新改变）
            interval = CONFIG.get("updateInterval", 1)
            await asyncio.sleep(interval)

    async def connect_websocket(self):
        """连接WebSocket"""
        if not self.is_running:
            return

        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
            self.ws = None

        ws_url = f"ws://{CONFIG['wsServer']}"
        logger.info(f"🔗 连接: {ws_url}")

        try:
            self.ws = await websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=60,
                max_size=10 * 1024 * 1024
            )
            self.ws_connected = True
            logger.info("✅ WebSocket 已连接")

            # 发送初始数据（full），等待配置
            try:
                data = self.collect_all_data()
                data["dataType"] = "full"
                await self.ws.send(json.dumps(data))
                self.send_count += 1
                logger.info(f"📤 发送 #{self.send_count} (完整数据)")
            except Exception as e:
                logger.error(f"❌ 发送失败: {e}")

            # 启动接收循环
            asyncio.create_task(self._receive_loop())

        except Exception as e:
            logger.error(f"❌ 连接失败: {e}")
            self.ws_connected = False
            if self.is_running:
                await asyncio.sleep(3)
                asyncio.create_task(self.connect_websocket())

    async def _receive_loop(self):
        """接收消息循环"""
        try:
            async for message in self.ws:
                try:
                    msg = json.loads(message)
                    if msg.get("type") == "welcome":
                        if "config" in msg:
                            self._update_config(msg["config"])
                            self.config_received = True
                            # 配置已更新，重启定时发送
                            await self._restart_sender()
                    # 其他消息忽略
                except:
                    pass
        except websockets.exceptions.ConnectionClosed:
            logger.info("🔴 WebSocket 断开")
            self.ws_connected = False
            if self.is_running:
                await asyncio.sleep(3)
                asyncio.create_task(self.connect_websocket())
        except Exception as e:
            logger.error(f"❌ 接收错误: {e}")
            self.ws_connected = False

    def print_data(self):
        """打印当前数据（不显示IP）"""
        data = self.collect_all_data()
        logger.info("📊 当前数据:")
        
        if "device" in data:
            logger.info(f"  📱 设备: {data['device'].get('model', 'Unknown')}")
        if "memory" in data and data["memory"].get("total", -1) > 0:
            mem = data["memory"]
            total_mb = mem["total"] / 1024 / 1024
            used_mb = mem["used"] / 1024 / 1024
            logger.info(f"  💾 内存: {used_mb:.0f}MB / {total_mb:.0f}MB ({mem.get('usagePercent', -1)}%)")
        if "cpu" in data:
            logger.info(f"  💻 CPU: {data['cpu'].get('cores', -1)}核心, 使用率 {data['cpu'].get('usage', -1)}%")
        if "network" in data:
            net = data["network"]
            logger.info(f"  🌐 网络: {net.get('type', '未知')}")
            logger.info(f"  ⬇️ 下载: {net.get('downSpeedStr', '0 B/s')} (间隔 {net.get('intervalRxStr', '0 B')})")
            logger.info(f"  ⬆️ 上传: {net.get('upSpeedStr', '0 B/s')} (间隔 {net.get('intervalTxStr', '0 B')})")
        if "foreground" in data:
            logger.info(f"  📱 前台: {data['foreground'].get('packageName', 'Unknown')}")
        if "processes" in data:
            logger.info(f"  📊 进程: {len(data['processes'])} 个")

    def stop(self):
        """停止收集器"""
        logger.info("🛑 停止收集器...")
        self.is_running = False
        if self.send_task and not self.send_task.done():
            self.send_task.cancel()
        if self.ws:
            try:
                asyncio.create_task(self.ws.close())
            except:
                pass


# ==================== 主程序 ====================
async def main():
    collector = DeviceCollector()
    
    # 测试收集
    logger.info("\n📊 测试收集...")
    collector.print_data()
    
    # 连接 WebSocket
    logger.info("\n🔗 启动 WebSocket...")
    await collector.connect_websocket()
    
    # 启动定期发送（先使用默认配置，等待服务器下发后重启）
    if collector.is_running and collector.ws_connected:
        logger.info(f"⏱️ 开始定期发送 (初始间隔 {CONFIG.get('updateInterval', 1)} 秒)...")
        collector.send_task = asyncio.create_task(collector._send_loop())
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ 已启动!")
    logger.info("按 Ctrl+C 停止")
    logger.info("=" * 60)
    
    try:
        while collector.is_running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        collector.stop()
        logger.info("✅ 已停止")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 程序已停止")
    except Exception as e:
        logger.error(f"❌ 运行错误: {e}")
        import traceback
        traceback.print_exc()
        input("按回车键退出...")