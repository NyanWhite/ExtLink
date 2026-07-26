# device_collector.py
# Windows 设备信息收集器 - 支持 WebSocket 上报（无IP版本）

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

# ==================== 配置 ====================
CONFIG = {
    "deviceId": None,  # 将在初始化时设置
    "wsServer": "localhost:32767",
    "updateInterval": 5,  # 数据上传间隔（秒）
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
        
        # Windows API 获取前台窗口
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        
        # 获取前台窗口句柄
        hwnd = user32.GetForegroundWindow()
        if hwnd == 0:
            return {"packageName": "Unknown", "activity": "Unknown", "source": "none"}
        
        # 获取窗口标题
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            window_title = buff.value
        else:
            window_title = "Unknown"
        
        # 获取进程ID
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        # 获取进程名称
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
        
        # 尝试获取CPU型号
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
    """获取存储信息"""
    try:
        partitions = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                partitions.append({
                    "mountpoint": partition.mountpoint,
                    "total": usage.total,
                    "used": usage.used,
                    "available": usage.free,
                    "usagePercent": round(usage.percent, 1),
                    "filesystem": partition.fstype
                })
            except:
                pass
        
        # 系统盘（C:）作为主要存储
        main_storage = {"total": -1, "used": -1, "available": -1, "usagePercent": -1}
        for p in partitions:
            if p["mountpoint"].startswith("C:"):
                main_storage = p
                break
        if main_storage["total"] == -1 and partitions:
            main_storage = partitions[0]
        
        return main_storage
    except:
        return {"total": -1, "used": -1, "available": -1, "usagePercent": -1}

# ==================== 网络信息 ====================
class NetworkStats:
    def __init__(self):
        self.last_rx = 0
        self.last_tx = 0
        self.last_time = 0
        self.initialized = False

    def get_network_stats(self, interval_seconds):
        """获取网络统计信息（不包含IP地址）"""
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
            # 获取网络接口信息
            net_io = psutil.net_io_counters()
            current_rx = net_io.bytes_recv
            current_tx = net_io.bytes_sent
            current_time = time.time()
            
            # 获取网络连接状态
            try:
                # 检查是否有网络连接
                socket.create_connection(("8.8.8.8", 53), timeout=1)
                result["isConnected"] = True
            except:
                result["isConnected"] = False
            
            # 尝试判断网络类型（WiFi/以太网）
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
            
            # 总流量
            result["totalRx"] = current_rx
            result["totalTx"] = current_tx
            result["totalRxStr"] = format_bytes(current_rx)
            result["totalTxStr"] = format_bytes(current_tx)
            
            # 计算间隔流量和速度
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
    """格式化速度"""
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
    """格式化字节数"""
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
    """获取Top进程"""
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
        
        # 按CPU使用率排序
        processes.sort(key=lambda x: x['cpuPercent'], reverse=True)
        return processes[:10]  # 返回前10个
    except:
        return []

# ==================== 数据收集器 ====================
class DeviceCollector:
    def __init__(self):
        self.device_id = get_device_id()
        self.network_stats = NetworkStats()
        self.send_count = 0
        self.is_running = True
        self.ws = None
        self.ws_connected = False
        
        # 初始化配置
        CONFIG["deviceId"] = self.device_id
        
        # 收集固定设备信息
        self.device_info = self._collect_device_info()
        
        logger.info("=" * 60)
        logger.info(f"📱 Windows 设备收集器")
        logger.info("=" * 60)
        logger.info(f"📋 设备ID: {self.device_id}")
        logger.info(f"🏭 制造商: {self.device_info.get('manufacturer', 'Unknown')}")
        logger.info(f"📦 系统: {self.device_info.get('os', {}).get('system', 'Unknown')} {self.device_info.get('os', {}).get('release', 'Unknown')}")
        logger.info(f"📐 屏幕: {self.device_info.get('screenWidth', -1)}x{self.device_info.get('screenHeight', -1)}")
        logger.info(f"⏱️ 上传间隔: {CONFIG['updateInterval']}秒")
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

    def collect_all_data(self):
        """收集所有数据"""
        data = {
            "deviceId": CONFIG["deviceId"],
            "timestamp": int(time.time() * 1000),
            "permissionLevel": 2  # Windows 通常有较高权限
        }

        # 基础设备信息
        if CONFIG["collectBasicInfo"]:
            data["device"] = self.device_info

        # 电池信息
        if CONFIG["collectBattery"]:
            data["battery"] = get_battery_info()

        # 前台应用
        if CONFIG["collectForegroundApp"]:
            data["foreground"] = get_foreground_app()

        # 内存信息
        if CONFIG["collectMemory"]:
            data["memory"] = get_memory_info()

        # CPU信息
        if CONFIG["collectCpuInfo"]:
            data["cpu"] = get_cpu_info()

        # 存储信息
        if CONFIG["collectStorageInfo"]:
            data["storage"] = get_storage_info()

        # 网络信息（不包含IP）
        if CONFIG["collectNetwork"]:
            net_stats = self.network_stats.get_network_stats(CONFIG["updateInterval"])
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

        # 进程信息
        if CONFIG["collectProcesses"]:
            data["processes"] = get_top_processes()

        return data

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

            # 发送初始数据
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
                    data = json.loads(message)
                    # 处理服务器消息（如果有）
                    pass
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

    async def send_periodic_data(self):
        """定期发送数据"""
        if not self.is_running:
            return

        if self.ws_connected and self.ws:
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

        if self.is_running:
            asyncio.get_event_loop().call_later(
                CONFIG["updateInterval"],
                lambda: asyncio.create_task(self.send_periodic_data())
            )

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
    
    # 启动定期发送
    if collector.is_running:
        logger.info(f"⏱️ 开始定期发送 (间隔 {CONFIG['updateInterval']} 秒)...")
        asyncio.get_event_loop().call_later(
            CONFIG["updateInterval"],
            lambda: asyncio.create_task(collector.send_periodic_data())
        )
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ 已启动!")
    logger.info("按 Ctrl+C 停止")
    logger.info("=" * 60)
    
    try:
        # 保持运行
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