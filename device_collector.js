// device_collector.js
// 增强版 - 电池详细信息（电压/电流/容量）+ 应用名称（非包名）
// 支持 AutoJS 自身与 Shizuku 双通道获取

"auto";

console.show();

// ==================== 动态导入 Java 类 ====================
try {
    importClass(android.net.ConnectivityManager);
    importClass(android.net.NetworkInfo);
    importClass(android.net.TrafficStats);
    importClass(android.os.BatteryManager);
    importClass(android.content.Intent);
    importClass(android.content.IntentFilter);
    importClass(android.content.pm.PackageManager);
} catch(e) {
    // 部分版本可能不支持
}

// ==================== 获取设备信息 ====================
function getDeviceId() {
    var model = "UNKNOWN";
    try {
        var result = shell("getprop ro.product.model", true);
        if (result && result.output) {
            var value = result.output.trim();
            if (value && value.length > 0 && value !== "Unknown") {
                model = value;
                return model;
            }
        }
    } catch (e) {}
    try {
        if (device.model && device.model !== "Unknown" && device.model.length > 0) {
            model = device.model;
            return model;
        }
    } catch (e) {}
    return "DEV_" + Date.now().toString(36).toUpperCase();
}

function getMarketName() {
    try {
        var result = shell("getprop ro.product.marketname", true);
        if (result && result.output) {
            var value = result.output.trim();
            if (value && value.length > 0 && value !== "Unknown") {
                return value;
            }
        }
    } catch (e) {}
    return null;
}

function getManufacturer() {
    try {
        var result = shell("getprop ro.product.manufacturer", true);
        if (result && result.output) {
            var value = result.output.trim();
            if (value && value.length > 0 && value !== "Unknown") {
                return value;
            }
        }
    } catch (e) {}
    try {
        if (device.brand && device.brand !== "Unknown") {
            return device.brand;
        }
    } catch (e) {}
    return "Unknown";
}

function getAndroidVersion() {
    try {
        var result = shell("getprop ro.build.version.release", true);
        if (result && result.output) {
            var value = result.output.trim();
            if (value && value.length > 0) {
                return value;
            }
        }
    } catch (e) {}
    try {
        if (device.release) {
            return device.release;
        }
    } catch (e) {}
    return "Unknown";
}

function getSdkVersion() {
    try {
        var result = shell("getprop ro.build.version.sdk", true);
        if (result && result.output) {
            var value = result.output.trim();
            if (value && value.length > 0) {
                return parseInt(value) || -1;
            }
        }
    } catch (e) {}
    try {
        if (device.sdkInt) {
            return device.sdkInt;
        }
    } catch (e) {}
    return -1;
}

function getScreenInfo() {
    try {
        return {
            width: device.width || -1,
            height: device.height || -1,
            density: device.density || -1
        };
    } catch (e) {
        return { width: -1, height: -1, density: -1 };
    }
}

// ==================== 网络监控函数（无IP版本） ====================
function getNetworkType() {
    try {
        var cm = context.getSystemService("connectivity");
        if (!cm) {
            return { type: "未知", detail: "无法获取网络服务", isConnected: false };
        }
        
        var activeNetwork = cm.getActiveNetworkInfo();
        
        if (activeNetwork == null || !activeNetwork.isConnected()) {
            return {
                type: "无网络",
                detail: "未连接",
                isConnected: false,
                isWifi: false,
                isMobile: false
            };
        }

        var type = activeNetwork.getType();
        var typeName = activeNetwork.getTypeName();
        
        var result = {
            isConnected: true,
            type: typeName || "未知",
            detail: "",
            isWifi: false,
            isMobile: false
        };

        var TYPE_WIFI = 1;
        var TYPE_MOBILE = 0;
        
        if (type == TYPE_WIFI) {
            result.isWifi = true;
            result.isMobile = false;
            result.type = "WiFi";
            result.detail = "WiFi";
            
            try {
                var wifiManager = context.getSystemService("wifi");
                if (wifiManager) {
                    var wifiInfo = wifiManager.getConnectionInfo();
                    if (wifiInfo) {
                        try {
                            var rssi = wifiInfo.getRssi();
                            if (rssi !== undefined && rssi !== null) {
                                var level = wifiManager.calculateSignalLevel(rssi, 5);
                                result.signalLevel = level + "/5";
                            }
                        } catch(e) {}
                    }
                }
            } catch(e) {}
        }
        else if (type == TYPE_MOBILE) {
            result.isWifi = false;
            result.isMobile = true;
            result.type = "移动网络";
            result.detail = "移动网络";
            
            try {
                var tm = context.getSystemService("phone");
                if (tm) {
                    try {
                        var networkType = tm.getDataNetworkType();
                        var networkTypeNames = {
                            0: "未知",
                            1: "GPRS",
                            2: "EDGE",
                            3: "UMTS",
                            4: "CDMA",
                            5: "EVDO_0",
                            6: "EVDO_A",
                            7: "1xRTT",
                            8: "HSDPA",
                            9: "HSUPA",
                            10: "HSPA",
                            11: "IDEN",
                            12: "EVDO_B",
                            13: "LTE",
                            14: "EHRPD",
                            15: "HSPAP",
                            16: "GSM",
                            17: "TD_SCDMA",
                            18: "IWLAN",
                            19: "LTE_CA",
                            20: "NR"
                        };
                        var netName = networkTypeNames[networkType] || "未知制式";
                        result.detail += " (" + netName + ")";
                        result.networkType = netName;
                    } catch(e) {}
                }
            } catch(e) {}
        }
        else {
            result.detail = typeName + " 已连接";
        }

        return result;
    } catch (e) {
        return {
            type: "获取失败",
            detail: "错误",
            isConnected: false,
            isWifi: false,
            isMobile: false
        };
    }
}

function getNetworkSpeed() {
    try {
        var currentRx = 0;
        var currentTx = 0;
        
        try {
            if (typeof TrafficStats !== 'undefined') {
                currentRx = TrafficStats.getTotalRxBytes();
                currentTx = TrafficStats.getTotalTxBytes();
            }
        } catch(e) {}
        
        return {
            totalRx: currentRx,
            totalTx: currentTx
        };
    } catch (e) {
        return { totalRx: 0, totalTx: 0 };
    }
}

function formatSpeed(bytesPerSecond) {
    if (bytesPerSecond < 0) return "0 B/s";
    if (bytesPerSecond < 1024) {
        return bytesPerSecond.toFixed(0) + " B/s";
    } else if (bytesPerSecond < 1024 * 1024) {
        return (bytesPerSecond / 1024).toFixed(1) + " KB/s";
    } else if (bytesPerSecond < 1024 * 1024 * 1024) {
        return (bytesPerSecond / 1024 / 1024).toFixed(1) + " MB/s";
    } else {
        return (bytesPerSecond / 1024 / 1024 / 1024).toFixed(1) + " GB/s";
    }
}

function formatBytes(bytes) {
    if (bytes < 0) return "0 B";
    if (bytes < 1024) {
        return bytes.toFixed(0) + " B";
    } else if (bytes < 1024 * 1024) {
        return (bytes / 1024).toFixed(1) + " KB";
    } else if (bytes < 1024 * 1024 * 1024) {
        return (bytes / 1024 / 1024).toFixed(1) + " MB";
    } else {
        return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
    }
}

// ==================== 网络监控状态（无IP） ====================
var networkState = {
    lastRx: 0,
    lastTx: 0,
    lastTime: 0,
    initialized: false
};

function getNetworkStats(intervalSeconds) {
    var speedData = getNetworkSpeed();
    var currentRx = speedData.totalRx;
    var currentTx = speedData.totalTx;
    var currentTime = Date.now();
    var result = {
        type: "未知",
        detail: "未获取",
        isConnected: false,
        isWifi: false,
        isMobile: false,
        downSpeed: 0,
        upSpeed: 0,
        downSpeedStr: "0 B/s",
        upSpeedStr: "0 B/s",
        totalRx: currentRx,
        totalTx: currentTx,
        totalRxStr: formatBytes(currentRx),
        totalTxStr: formatBytes(currentTx),
        intervalRx: 0,
        intervalTx: 0,
        intervalRxStr: "0 B",
        intervalTxStr: "0 B"
    };

    // 获取网络类型
    var netInfo = getNetworkType();
    result.type = netInfo.type;
    result.detail = netInfo.detail;
    result.isConnected = netInfo.isConnected;
    result.isWifi = netInfo.isWifi;
    result.isMobile = netInfo.isMobile;
    if (netInfo.signalLevel) {
        result.signalLevel = netInfo.signalLevel;
    }
    if (netInfo.networkType) {
        result.networkType = netInfo.networkType;
    }

    // 计算网速
    if (!networkState.initialized) {
        networkState.lastRx = currentRx;
        networkState.lastTx = currentTx;
        networkState.lastTime = currentTime;
        networkState.initialized = true;
        return result;
    }

    var timeDiff = (currentTime - networkState.lastTime) / 1000;
    if (timeDiff < 0.1) {
        timeDiff = 0.1;
    }

    var rxDiff = currentRx - networkState.lastRx;
    var txDiff = currentTx - networkState.lastTx;

    if (rxDiff < 0) rxDiff = 0;
    if (txDiff < 0) txDiff = 0;

    result.intervalRx = rxDiff;
    result.intervalTx = txDiff;
    result.intervalRxStr = formatBytes(rxDiff);
    result.intervalTxStr = formatBytes(txDiff);

    result.downSpeed = rxDiff / timeDiff;
    result.upSpeed = txDiff / timeDiff;
    result.downSpeedStr = formatSpeed(result.downSpeed);
    result.upSpeedStr = formatSpeed(result.upSpeed);

    networkState.lastRx = currentRx;
    networkState.lastTx = currentTx;
    networkState.lastTime = currentTime;

    return result;
}

// ==================== 配置 ====================
var DEVICE_ID = getDeviceId();
var MARKET_NAME = getMarketName();
var MANUFACTURER = getManufacturer();
var ANDROID_VERSION = getAndroidVersion();
var SDK_VERSION = getSdkVersion();
var SCREEN_INFO = getScreenInfo();

var CONFIG = {
    "deviceId": DEVICE_ID,
    "wsServer": "localhost:91",
    "updateInterval": 1,  // 数据上传间隔（秒）
    "collectBasicInfo": true,
    "collectBattery": true,   // 开启以测试电池详细数据
    "collectForegroundApp": true, // 开启以测试应用名称
    "collectMemory": true,
    "collectScreenState": true,
    "collectStorageInfo": true,
    "collectCpuInfo": true,
    "collectLocation": false,
    "collectSensor": true,
    "collectProcesses": true,
    "collectPackages": true,
    "collectNetwork": true  // 网络监控开关
};

// ==================== 打印设备信息 ====================
log("=".repeat(60));
log("📱 设备信息");
log("=".repeat(60));
log("📋 设备型号: " + DEVICE_ID);
if (MARKET_NAME) {
    log("📛 市场名称: " + MARKET_NAME);
}
log("🏭 制造商: " + MANUFACTURER);
log("📦 Android版本: " + ANDROID_VERSION + " (SDK " + SDK_VERSION + ")");
log("📐 屏幕: " + SCREEN_INFO.width + "x" + SCREEN_INFO.height);
log("⏱️ 上传间隔: " + CONFIG.updateInterval + "秒");
log("=".repeat(60));

// ==================== 状态变量 ====================
var ws = null;
var wsConnected = false;
var isRunning = true;
var sendCount = 0;
var heartbeatCount = 0;

// 传感器数据缓存（用于高频采样）
var sensorCache = {
    accelerometer: null,
    gyroscope: null,
    magnetic_field: null,
    gravity: null,
    linear_acceleration: null,
    light: null,
    proximity: null,
    ambient_temperature: null,
    pressure: null,
    relative_humidity: null,
    orientation: null,
    _lastUpdate: 0
};

// 传感器监听器（持续采集）
var sensorListeners = [];

// ==================== 权限检测 ====================
function detectPermissionLevel() {
    try {
        var result = shell("id", true);
        if (result && result.output) {
            var output = result.output.trim();
            if (output.indexOf("uid=0") !== -1 || output.indexOf("root") !== -1) {
                return 3;
            }
        }
    } catch (e) {}

    try {
        var result = shell("settings get global airplane_mode_on", true);
        if (result && result.code === 0) {
            return 2;
        }
    } catch (e) {}

    try {
        if (auto.service) {
            return 1;
        }
    } catch (e) {}

    return 0;
}

// ==================== 通用Shell执行 ====================
function execShell(cmd) {
    try {
        var result = shell(cmd, true);
        if (result && result.output) {
            return result.output.trim();
        }
        return null;
    } catch (e) {
        return null;
    }
}

// ==================== Shizuku 执行（adb 模式） ====================
function execShizuku(cmd) {
    try {
        var result = shell(cmd, { adb: true });
        if (result && result.code === 0) {
            return result.stdout.toString().trim();
        } else {
            return null;
        }
    } catch (e) {
        return null;
    }
}

// ==================== 获取应用名称（通过包名） ====================
function getAppLabel(pkg) {
    if (!pkg || pkg === "Unknown" || pkg === "") return "Unknown";
    try {
        var pm = context.getPackageManager();
        var appInfo = pm.getApplicationInfo(pkg, 0);
        return pm.getApplicationLabel(appInfo).toString();
    } catch(e) {
        return "Unknown";
    }
}

// ==================== 电池详细信息（双通道） ====================

/**
 * 通过 AutoJS 自身 API 获取电池数据
 */
function getBatteryDetailsAutoJS() {
    var info = {
        voltage: null,
        current: null,
        capacity: null,
        level: null,
        temperature: null,
        health: null,
        status: null
    };
    try {
        // 使用 BatteryManager
        var batteryManager = context.getSystemService(context.BATTERY_SERVICE);
        var intent = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (intent) {
            info.level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
            var scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
            if (info.level >= 0 && scale > 0) {
                info.level = Math.round(info.level * 100 / scale);
            }
            info.voltage = intent.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1);
            info.temperature = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1);
            info.status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
            info.health = intent.getIntExtra(BatteryManager.EXTRA_HEALTH, -1);
        }
        // 尝试获取电流和容量（可能需要 Android 5.0+）
        if (batteryManager && batteryManager.getIntProperty) {
            try {
                var current = batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW);
                if (current !== 0) info.current = current;
            } catch(e) {}
            try {
                var capacity = batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER);
                if (capacity !== 0) info.capacity = capacity;
            } catch(e) {}
        }
    } catch(e) {
        log("AutoJS电池获取异常: " + e.message);
    }
    return info;
}

/**
 * 通过 Shizuku（dumpsys + sysfs）获取电池数据
 */
function getBatteryDetailsShizuku() {
    var info = {
        voltage: null,
        current: null,
        capacity: null,
        level: null,
        temperature: null,
        health: null,
        status: null
    };
    try {
        // 1. dumpsys battery
        var output = execShizuku("dumpsys battery");
        if (output) {
            var lines = output.split("\n");
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (line.indexOf("voltage") >= 0) {
                    var match = line.match(/\d+/);
                    if (match) info.voltage = parseInt(match[0]);
                } else if (line.indexOf("level") >= 0) {
                    var match = line.match(/\d+/);
                    if (match) info.level = parseInt(match[0]);
                } else if (line.indexOf("temperature") >= 0) {
                    var match = line.match(/\d+/);
                    if (match) info.temperature = parseInt(match[0]);
                } else if (line.indexOf("status") >= 0) {
                    var match = line.match(/\d+/);
                    if (match) info.status = parseInt(match[0]);
                } else if (line.indexOf("health") >= 0) {
                    var match = line.match(/\d+/);
                    if (match) info.health = parseInt(match[0]);
                }
            }
        }
        // 2. 从 sysfs 读取电流和容量
        var currentRaw = execShizuku("cat /sys/class/power_supply/battery/current_now 2>/dev/null");
        if (currentRaw && currentRaw.match(/^\d+$/)) {
            info.current = parseInt(currentRaw);
        }
        var capacityRaw = execShizuku("cat /sys/class/power_supply/battery/charge_full 2>/dev/null");
        if (capacityRaw && capacityRaw.match(/^\d+$/)) {
            info.capacity = parseInt(capacityRaw);
        }
    } catch(e) {
        log("Shizuku电池获取异常: " + e.message);
    }
    return info;
}

/**
 * 合并两种方式，优先使用 Shizuku 数据（更准确），若缺失则补充 AutoJS 数据
 */
function getBatteryDetails() {
    var autoData = getBatteryDetailsAutoJS();
    var shizukuData = getBatteryDetailsShizuku();
    
    // 合并：优先使用 shizuku 的非空值，否则使用 autoData
    var result = {};
    var keys = ["voltage", "current", "capacity", "level", "temperature", "health", "status"];
    for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        var shizVal = shizukuData[key];
        var autoVal = autoData[key];
        // 如果 shizuku 有有效值（非 null, 非 -1 或非 0 对于电流容量），则使用
        if (shizVal !== null && shizVal !== undefined && shizVal !== -1 && shizVal !== 0) {
            result[key] = shizVal;
        } else if (autoVal !== null && autoVal !== undefined && autoVal !== -1) {
            result[key] = autoVal;
        } else {
            result[key] = null;
        }
    }
    // 日志输出对比（便于调试）
    log("🔋 电池信息对比: AutoJS[电压=" + autoData.voltage + "mV, 电流=" + autoData.current + "μA, 容量=" + autoData.capacity + "mAh, 电量=" + autoData.level + "%]");
    log("🔋 Shizuku[电压=" + shizukuData.voltage + "mV, 电流=" + shizukuData.current + "μA, 容量=" + shizukuData.capacity + "mAh, 电量=" + shizukuData.level + "%]");
    return result;
}

// ==================== 获取前台应用（增强版：包含应用名称） ====================
function getForegroundApp() {
    var pkg = "Unknown";
    var act = "Unknown";
    var source = "none";

    if (auto.service) {
        try {
            if (typeof currentPackage === 'function') {
                var p = currentPackage();
                if (p && p !== "Unknown" && p !== "" && p !== null && typeof p === 'string' && p.length > 0) {
                    pkg = p;
                    source = "autojs_currentPackage";
                }
            }
        } catch (e) {
            log("⚠️ currentPackage() 调用失败: " + e.message);
        }

        if (pkg === "Unknown") {
            try {
                if (typeof app.currentPackage === 'function') {
                    var p = app.currentPackage();
                    if (p && p !== "Unknown" && p !== "" && p !== null && typeof p === 'string' && p.length > 0) {
                        pkg = p;
                        source = "autojs_app_currentPackage";
                    }
                }
            } catch (e) {}
        }

        try {
            if (typeof currentActivity === 'function') {
                var a = currentActivity();
                if (a && a !== "Unknown" && a !== "" && a !== null && typeof a === 'string' && a.length > 0) {
                    act = a;
                }
            }
        } catch (e) {}

        if (act === "Unknown") {
            try {
                if (typeof app.currentActivity === 'function') {
                    var a = app.currentActivity();
                    if (a && a !== "Unknown" && a !== "" && a !== null && typeof a === 'string' && a.length > 0) {
                        act = a;
                    }
                }
            } catch (e) {}
        }
    }

    if (pkg === "Unknown") {
        var cmds = [
            "dumpsys activity activities 2>/dev/null | grep -E 'mResumedActivity|mFocusedActivity|mCurrentFocus' | head -1",
            "dumpsys window windows 2>/dev/null | grep -E 'mCurrentFocus' | head -1",
            "dumpsys activity top 2>/dev/null | grep -E 'ACTIVITY|TASK' | head -5"
        ];

        for (var i = 0; i < cmds.length; i++) {
            try {
                var output = execShell(cmds[i]);
                if (output && output.length > 0) {
                    var match = null;
                    
                    match = output.match(/([a-zA-Z0-9._-]+)\/([a-zA-Z0-9._-]+)/);
                    if (match) {
                        pkg = match[1];
                        act = match[2] || "Unknown";
                        source = "dumpsys_" + (i + 1);
                        break;
                    }
                    
                    match = output.match(/\{[^}]*\s+([a-zA-Z0-9._-]+)\/([a-zA-Z0-9._-]+)/);
                    if (match) {
                        pkg = match[1];
                        act = match[2] || "Unknown";
                        source = "dumpsys_alt_" + (i + 1);
                        break;
                    }
                    
                    match = output.match(/([a-zA-Z0-9._-]+)/);
                    if (match && match[1] && match[1].length > 3 && match[1].indexOf('.') > 0) {
                        pkg = match[1];
                        source = "dumpsys_pkg_only_" + (i + 1);
                        break;
                    }
                }
            } catch (e) {}
        }
    }

    // 获取应用名称（标签）
    var appName = getAppLabel(pkg);

    return {
        packageName: pkg,
        activity: act,
        source: source,
        isAutoJsService: !!auto.service,
        appName: appName   // 新增字段
    };
}

// ==================== 获取GPS位置 ====================
function getLocation() {
    try {
        importPackage(android.location);
        var locationManager = context.getSystemService(context.LOCATION_SERVICE);
        var bestLocation = null;
        var providers = locationManager.getProviders(true);
        if (providers) {
            for (var i = 0; i < providers.size(); i++) {
                var provider = providers.get(i);
                try {
                    var loc = locationManager.getLastKnownLocation(provider);
                    if (loc) {
                        if (!bestLocation || loc.getAccuracy() < bestLocation.getAccuracy()) {
                            bestLocation = loc;
                        }
                    }
                } catch (e) {}
            }
        }
        if (bestLocation) {
            return {
                latitude: bestLocation.getLatitude(),
                longitude: bestLocation.getLongitude(),
                accuracy: bestLocation.getAccuracy(),
                altitude: bestLocation.getAltitude(),
                provider: bestLocation.getProvider(),
                hasLocation: true
            };
        }
        return { latitude: -1, longitude: -1, accuracy: -1, altitude: -1, provider: "none", hasLocation: false };
    } catch (e) {
        return { latitude: -1, longitude: -1, accuracy: -1, altitude: -1, provider: "error", hasLocation: false };
    }
}

// ==================== 传感器采集（持续监听） ====================
function startSensorCollection() {
    try {
        sensors.ignoresUnsupportedSensor = true;
    } catch (e) {}

    var sensorList = [
        { name: "accelerometer", key: "accelerometer", type: "3d" },
        { name: "gyroscope", key: "gyroscope", type: "3d" },
        { name: "magnetic_field", key: "magnetic_field", type: "3d" },
        { name: "gravity", key: "gravity", type: "3d" },
        { name: "linear_acceleration", key: "linear_acceleration", type: "3d" },
        { name: "orientation", key: "orientation", type: "3d" },
        { name: "light", key: "light", type: "1d" },
        { name: "proximity", key: "proximity", type: "1d" },
        { name: "ambient_temperature", key: "ambient_temperature", type: "1d" },
        { name: "pressure", key: "pressure", type: "1d" },
        { name: "relative_humidity", key: "relative_humidity", type: "1d" }
    ];

    for (var i = 0; i < sensorListeners.length; i++) {
        try {
            sensorListeners[i].unregister();
        } catch (e) {}
    }
    sensorListeners = [];

    for (var i = 0; i < sensorList.length; i++) {
        var sensor = sensorList[i];
        try {
            var instance = sensors.register(sensor.name, sensors.delay.game);
            if (instance) {
                var listener = function(key, type) {
                    return function(event, v1, v2, v3) {
                        try {
                            if (type === "3d") {
                                sensorCache[key] = { x: v1, y: v2, z: v3 };
                            } else {
                                sensorCache[key] = { value: v1 };
                            }
                            sensorCache._lastUpdate = Date.now();
                        } catch (e) {}
                    };
                }(sensor.key, sensor.type);
                
                instance.on("change", listener);
                sensorListeners.push(instance);
            }
        } catch (e) {}
    }
}

// ==================== 获取传感器数据（从缓存读取） ====================
function getSensorData() {
    var result = {};
    var keys = ["accelerometer", "gyroscope", "magnetic_field", "gravity", 
                "linear_acceleration", "orientation", "light", "proximity", 
                "ambient_temperature", "pressure", "relative_humidity"];
    for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        if (sensorCache[key] !== null && sensorCache[key] !== undefined) {
            result[key] = sensorCache[key];
        }
    }
    return result;
}

// ==================== 数据收集 ====================
function collectAllData() {
    var data = {
        deviceId: CONFIG.deviceId,
        timestamp: Date.now(),
        permissionLevel: detectPermissionLevel()
    };

    // ===== 基础设备信息 =====
    if (CONFIG.collectBasicInfo) {
        data.device = {
            model: DEVICE_ID,
            marketName: MARKET_NAME || DEVICE_ID,
            manufacturer: MANUFACTURER,
            androidVersion: ANDROID_VERSION,
            sdkVersion: SDK_VERSION,
            screenWidth: SCREEN_INFO.width,
            screenHeight: SCREEN_INFO.height,
            screenDensity: SCREEN_INFO.density
        };

        var propCmds = [
            { key: "bootloader", cmd: "getprop ro.bootloader" },
            { key: "hardware", cmd: "getprop ro.hardware" },
            { key: "board", cmd: "getprop ro.product.board" },
            { key: "product", cmd: "getprop ro.product.name" },
            { key: "fingerprint", cmd: "getprop ro.build.fingerprint" }
        ];
        var propValues = {};
        for (var i = 0; i < propCmds.length; i++) {
            var item = propCmds[i];
            if (!propValues[item.key]) {
                try {
                    var val = execShell(item.cmd);
                    if (val && val !== "Unknown" && val.length > 0) {
                        propValues[item.key] = val;
                    }
                } catch (e) {}
            }
        }
        for (var key in propValues) {
            data.device[key] = propValues[key];
        }

        try {
            if (device.getAndroidId) {
                data.device.androidId = device.getAndroidId();
            }
        } catch (e) {}
    }

    // ===== 电池信息（增强版） =====
    if (CONFIG.collectBattery) {
        var batteryDetail = getBatteryDetails();
        var battery = {
            level: batteryDetail.level !== null ? batteryDetail.level : -1,
            charging: false,  // 保留原字段，可通过status判断
            temperature: batteryDetail.temperature !== null ? batteryDetail.temperature : -1,
            voltage: batteryDetail.voltage !== null ? batteryDetail.voltage : -1,
            health: batteryDetail.health !== null ? batteryDetail.health : "Unknown",
            // 新增字段
            current: batteryDetail.current !== null ? batteryDetail.current : -1,
            capacity: batteryDetail.capacity !== null ? batteryDetail.capacity : -1,
            status: batteryDetail.status !== null ? batteryDetail.status : -1
        };
        // 判断是否充电
        if (battery.status !== -1) {
            battery.charging = (battery.status === 2 || battery.status === 5); // 2=充电中, 5=已充满
        }
        data.battery = battery;
    }

    // ===== 前台应用（含应用名称） =====
    if (CONFIG.collectForegroundApp) {
        data.foreground = getForegroundApp();
    }

    // ===== 内存信息 =====
    if (CONFIG.collectMemory) {
        var memory = { total: -1, used: -1, available: -1, usagePercent: -1 };
        try {
            var cmd = execShell("cat /proc/meminfo");
            if (cmd) {
                var lines = cmd.split('\n');
                var memTotal = -1, memAvailable = -1;
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i];
                    if (line.indexOf("MemTotal:") !== -1) {
                        var parts = line.trim().split(/\s+/);
                        if (parts.length >= 2) memTotal = parseInt(parts[1]) * 1024;
                    }
                    if (line.indexOf("MemAvailable:") !== -1) {
                        var parts = line.trim().split(/\s+/);
                        if (parts.length >= 2) memAvailable = parseInt(parts[1]) * 1024;
                    }
                }
                if (memTotal > 0) {
                    memory.total = memTotal;
                    memory.available = memAvailable > 0 ? memAvailable : 0;
                    memory.used = memory.total - memory.available;
                    if (memory.total > 0 && memory.available > 0) {
                        memory.usagePercent = ((memory.used / memory.total) * 100).toFixed(1);
                    }
                }
            }
        } catch (e) {}
        if (memory.total === -1) {
            try {
                importPackage(android.app);
                var activityManager = context.getSystemService(context.ACTIVITY_SERVICE);
                var memInfo = new ActivityManager.MemoryInfo();
                activityManager.getMemoryInfo(memInfo);
                memory.total = memInfo.totalMem || -1;
                memory.available = memInfo.availMem || -1;
                if (memory.total > 0 && memory.available > 0) {
                    memory.used = memory.total - memory.available;
                    memory.usagePercent = ((memory.used / memory.total) * 100).toFixed(1);
                }
            } catch (e) {}
        }
        data.memory = memory;
    }

    // ===== CPU信息 =====
    if (CONFIG.collectCpuInfo) {
        var cpu = { cores: -1, model: "Unknown", usage: -1 };
        try {
            var cmd = execShell("cat /proc/cpuinfo | grep -c processor");
            if (cmd) cpu.cores = parseInt(cmd.trim()) || -1;
        } catch (e) {}
        try {
            var cmd = execShell("cat /proc/cpuinfo | grep -E 'Hardware|model name' | head -1");
            if (cmd) {
                var parts = cmd.split(':');
                if (parts.length >= 2) cpu.model = parts[1].trim();
            }
        } catch (e) {}
        try {
            var cmd = execShell("top -n 1 -b | grep -E '%Cpu|CPU:' | head -1");
            if (cmd) {
                var match = cmd.match(/(\d+\.?\d*)/);
                if (match) cpu.usage = parseFloat(match[1]);
            }
        } catch (e) {}
        data.cpu = cpu;
    }

    // ===== 屏幕状态 =====
    if (CONFIG.collectScreenState) {
        var screen = { isOn: false, brightness: -1 };
        try {
            if (typeof device.isScreenOn === 'function') screen.isOn = device.isScreenOn();
        } catch (e) {}
        try {
            if (typeof device.getBrightness === 'function') screen.brightness = device.getBrightness();
        } catch (e) {}
        data.screen = screen;
    }

    // ===== 存储信息 =====
    if (CONFIG.collectStorageInfo) {
        var storage = { total: -1, used: -1, available: -1, usagePercent: -1 };
        try {
            var cmd = execShell("df -B1 /data | tail -1");
            if (cmd) {
                var parts = cmd.trim().split(/\s+/);
                if (parts.length >= 6) {
                    storage.total = parseInt(parts[1]) || -1;
                    storage.used = parseInt(parts[2]) || -1;
                    storage.available = parseInt(parts[3]) || -1;
                    if (storage.total > 0 && storage.available > 0) {
                        storage.usagePercent = ((storage.used / storage.total) * 100).toFixed(1);
                    }
                }
            }
        } catch (e) {}
        if (storage.total === -1) {
            try {
                importPackage(android.os);
                var stat = new StatFs(Environment.getDataDirectory().getPath());
                var blockSize = stat.getBlockSizeLong();
                var totalBlocks = stat.getBlockCountLong();
                var availableBlocks = stat.getAvailableBlocksLong();
                storage.total = totalBlocks * blockSize;
                storage.available = availableBlocks * blockSize;
                storage.used = storage.total - storage.available;
                if (storage.total > 0 && storage.available > 0) {
                    storage.usagePercent = ((storage.used / storage.total) * 100).toFixed(1);
                }
            } catch (e) {}
        }
        data.storage = storage;
    }

    // ===== GPS位置 =====
    if (CONFIG.collectLocation) {
        data.location = getLocation();
    }

    // ===== 传感器数据（从缓存获取） =====
    if (CONFIG.collectSensor) {
        var sensorData = getSensorData();
        if (Object.keys(sensorData).length > 0) {
            data.sensors = sensorData;
        }
    }

    // ===== 网络信息（无IP） =====
    if (CONFIG.collectNetwork) {
        var netStats = getNetworkStats(CONFIG.updateInterval);
        data.network = {
            type: netStats.type,
            detail: netStats.detail,
            isConnected: netStats.isConnected,
            isWifi: netStats.isWifi,
            isMobile: netStats.isMobile,
            signalLevel: netStats.signalLevel || null,
            networkType: netStats.networkType || null,
            downSpeed: netStats.downSpeed,
            upSpeed: netStats.upSpeed,
            downSpeedStr: netStats.downSpeedStr,
            upSpeedStr: netStats.upSpeedStr,
            intervalRx: netStats.intervalRx,
            intervalTx: netStats.intervalTx,
            intervalRxStr: netStats.intervalRxStr,
            intervalTxStr: netStats.intervalTxStr,
            totalRx: netStats.totalRx,
            totalTx: netStats.totalTx,
            totalRxStr: netStats.totalRxStr,
            totalTxStr: netStats.totalTxStr
        };
    }

    return data;
}

// ==================== WebSocket ====================
function connectWebSocket() {
    if (!isRunning) return;

    if (ws) {
        try { ws.close(1000, "重连"); } catch (e) {}
        ws = null;
    }

    var wsUrl = "ws://" + CONFIG.wsServer;
    log("🔗 连接: " + wsUrl);

    try {
        ws = web.newWebSocket(wsUrl);

        ws.on("open", function onWsOpen(res, socket) {
            if (!isRunning) return;
            log("✅ 已连接");
            wsConnected = true;

            try {
                var data = collectAllData();
                data.dataType = "full";
                ws.send(JSON.stringify(data));
                sendCount++;
                log("📤 发送 #" + sendCount + " (完整数据)");
            } catch (e) {
                log("❌ 发送失败: " + e.message);
            }
        });

        ws.on("text", function onWsText(text, socket) {});

        ws.on("close", function onWsClose(code, reason, socket) {
            if (!isRunning) return;
            log("🔴 断开: " + code);
            wsConnected = false;
            if (code !== 1000 && isRunning) {
                setTimeout(connectWebSocket, 3000);
            }
        });

        ws.on("error", function onWsError(err, socket) {});

        ws.on("failure", function onWsFailure(err, res, socket) {
            if (!isRunning) return;
            log("❌ 连接失败");
            wsConnected = false;
            setTimeout(connectWebSocket, 3000);
        });

    } catch (e) {
        log("❌ 创建失败: " + e.message);
        setTimeout(connectWebSocket, 3000);
    }
}

// ==================== 定期发送 ====================
function sendPeriodicData() {
    if (!isRunning) return;

    if (wsConnected && ws) {
        try {
            var data = collectAllData();
            data.dataType = "diff";
            ws.send(JSON.stringify(data));
            sendCount++;
            if (sendCount % 20 === 0) {
                log("📤 已发送 " + sendCount + " 次");
            }
        } catch (e) {
            log("❌ 发送失败: " + e.message);
            wsConnected = false;
            setTimeout(connectWebSocket, 3000);
        }
    }

    if (isRunning) {
        setTimeout(sendPeriodicData, CONFIG.updateInterval * 1000);
    }
}

// ==================== 全局命令 ====================
globalThis.collectData = function() {
    var data = collectAllData();
    log("📊 手动收集完成");
    if (data.memory && data.memory.total > 0) {
        var totalMB = (data.memory.total / 1024 / 1024).toFixed(0);
        var usedMB = (data.memory.used / 1024 / 1024).toFixed(0);
        log("  💾 内存: " + usedMB + "MB / " + totalMB + "MB (" + data.memory.usagePercent + "%)");
    }
    if (data.cpu && data.cpu.cores > 0) {
        log("  💻 CPU: " + data.cpu.cores + "核心, 使用率 " + data.cpu.usage + "%");
    }
    if (data.storage && data.storage.total > 0) {
        var totalGB = (data.storage.total / 1024 / 1024 / 1024).toFixed(1);
        var usedGB = (data.storage.used / 1024 / 1024 / 1024).toFixed(1);
        log("  💾 存储: " + usedGB + "GB / " + totalGB + "GB (" + data.storage.usagePercent + "%)");
    }
    if (data.foreground) {
        log("  📱 前台: " + data.foreground.packageName + " (" + data.foreground.source + ")");
        log("  📱 Activity: " + data.foreground.activity);
        log("  📱 应用名称: " + data.foreground.appName);
    }
    if (data.battery) {
        log("  🔋 电量: " + data.battery.level + "%" + (data.battery.charging ? " (充电中)" : ""));
        if (data.battery.voltage > 0) log("  🔋 电压: " + data.battery.voltage + "mV");
        if (data.battery.current > 0) log("  🔋 电流: " + data.battery.current + "μA");
        if (data.battery.capacity > 0) log("  🔋 容量: " + data.battery.capacity + "mAh");
    }
    if (data.location && data.location.hasLocation) {
        log("  📍 GPS: " + data.location.latitude + ", " + data.location.longitude);
    }
    if (data.sensors) {
        var sensorKeys = Object.keys(data.sensors);
        log("  📡 传感器: " + sensorKeys.length + "个");
        for (var i = 0; i < Math.min(sensorKeys.length, 3); i++) {
            var key = sensorKeys[i];
            var val = data.sensors[key];
            if (val && typeof val === 'object') {
                if (val.x !== undefined) {
                    log("    - " + key + ": x=" + val.x.toFixed(2) + ", y=" + val.y.toFixed(2) + ", z=" + val.z.toFixed(2));
                } else if (val.value !== undefined) {
                    log("    - " + key + ": " + val.value.toFixed(1));
                }
            }
        }
    }
    if (data.network) {
        log("  🌐 网络: " + data.network.type + (data.network.isConnected ? " ✅" : " ❌"));
        log("  ⬇️ 下载: " + data.network.downSpeedStr + " (间隔 " + data.network.intervalRxStr + ")");
        log("  ⬆️ 上传: " + data.network.upSpeedStr + " (间隔 " + data.network.intervalTxStr + ")");
        log("  📊 总下载: " + data.network.totalRxStr + " | 总上传: " + data.network.totalTxStr);
    }
    return data;
};

globalThis.sendData = function() {
    if (wsConnected && ws) {
        try {
            var data = collectAllData();
            data.dataType = "full";
            ws.send(JSON.stringify(data));
            log("📤 手动发送成功");
        } catch (e) {
            log("❌ 发送失败: " + e.message);
        }
    } else {
        log("⚠️ 未连接");
    }
};

globalThis.status = function() {
    var level = detectPermissionLevel();
    var names = ["无", "AutoJS", "Shizuku", "Root"];
    log("=".repeat(60));
    log("📊 状态:");
    log("  📋 设备: " + CONFIG.deviceId);
    if (MARKET_NAME) {
        log("  📛 市场名: " + MARKET_NAME);
    }
    log("  🔑 权限: " + (names[level] || "未知"));
    log("  🔗 连接: " + (wsConnected ? "✅" : "❌"));
    log("  🌐 服务器: " + CONFIG.wsServer);
    log("  📤 发送: " + sendCount + " 次");
    log("  ⏱️ 间隔: " + CONFIG.updateInterval + "秒");
    log("=".repeat(60));
};

globalThis.stop = function() {
    log("🛑 停止");
    isRunning = false;
    if (ws) {
        try { ws.close(1000, "停止"); } catch (e) {}
        ws = null;
    }
    wsConnected = false;
};

// ==================== 主程序 ====================
log("=".repeat(60));
log("📱 设备收集 v11.1 (电池增强+应用名称)");
log("=".repeat(60));
log("📋 设备ID: " + CONFIG.deviceId);
if (MARKET_NAME) {
    log("📛 市场名: " + MARKET_NAME);
}
log("🏭 制造商: " + MANUFACTURER);
log("📦 Android: " + ANDROID_VERSION + " (SDK " + SDK_VERSION + ")");
log("📐 屏幕: " + SCREEN_INFO.width + "x" + SCREEN_INFO.height);
log("🌐 服务器: " + CONFIG.wsServer);
log("⏱️ 上传间隔: " + CONFIG.updateInterval + "秒");
log("=".repeat(60));

var level = detectPermissionLevel();
var names = ["无", "AutoJS", "Shizuku", "Root"];
log("🔑 权限: " + (names[level] || "未知"));

// 初始化网络统计
log("\n🌐 初始化网络监控...");
try {
    getNetworkStats(CONFIG.updateInterval);
    var netTest = getNetworkStats(CONFIG.updateInterval);
    log("✅ 网络监控已启动");
    log("  🌐 网络类型: " + netTest.type);
    log("  📊 总下载: " + netTest.totalRxStr);
    log("  📊 总上传: " + netTest.totalTxStr);
} catch (e) {
    log("❌ 网络监控初始化失败: " + e.message);
}

// ===== 启动传感器采集 =====
log("\n📡 启动传感器采集...");
try {
    startSensorCollection();
    log("✅ 传感器采集已启动");
} catch (e) {
    log("❌ 传感器启动失败: " + e.message);
}

log("\n📊 测试收集...");
try {
    var test = collectAllData();
    log("✅ 成功，字段数: " + Object.keys(test).length);
    if (test.device) {
        log("  📱 " + test.device.model);
    }
    if (test.battery) {
        log("  🔋 电量: " + test.battery.level + "%" + (test.battery.charging ? " (充电中)" : ""));
        if (test.battery.voltage > 0) log("  🔋 电压: " + test.battery.voltage + "mV");
        if (test.battery.current > 0) log("  🔋 电流: " + test.battery.current + "μA");
        if (test.battery.capacity > 0) log("  🔋 容量: " + test.battery.capacity + "mAh");
    }
    if (test.foreground) {
        log("  📱 前台包名: " + test.foreground.packageName + " (来源: " + test.foreground.source + ")");
        log("  📱 应用名称: " + test.foreground.appName);
        if (test.foreground.activity && test.foreground.activity !== "Unknown") {
            log("  📱 Activity: " + test.foreground.activity);
        }
    }
    if (test.memory && test.memory.total > 0) {
        var totalMB = (test.memory.total / 1024 / 1024).toFixed(0);
        var usedMB = (test.memory.used / 1024 / 1024).toFixed(0);
        log("  💾 内存: " + usedMB + "MB / " + totalMB + "MB");
    }
    if (test.sensors) {
        var sensorKeys = Object.keys(test.sensors);
        log("  📡 传感器: " + sensorKeys.length + "个");
    }
    if (test.network) {
        log("  🌐 网络: " + test.network.type + " (" + test.network.detail + ")");
        log("  ⬇️ " + test.network.downSpeedStr + "  ⬆️ " + test.network.upSpeedStr);
        log("  📊 间隔流量: ⬇️" + test.network.intervalRxStr + " ⬆️" + test.network.intervalTxStr);
    }
} catch (e) {
    log("❌ 失败: " + e.message);
}

log("\n🔗 启动 WebSocket...");
connectWebSocket();

setTimeout(function() {
    if (isRunning) {
        log("⏱️ 开始定期发送...");
        sendPeriodicData();
    }
}, 3000);

events.on("exit", function() {
    log("\n🧹 清理...");
    isRunning = false;
    if (ws) {
        try { ws.close(1000, "退出"); } catch (e) {}
        ws = null;
    }
    log("✅ 完成");
});

log("\n" + "=".repeat(60));
log("✅ 已启动!");
log("命令:");
log("  collectData() - 手动收集");
log("  sendData()    - 发送数据");
log("  status()      - 查看状态");
log("  stop()        - 停止");
log("=".repeat(60));