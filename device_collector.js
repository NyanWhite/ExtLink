// device_collector.js
// 增强版 - 电池详细信息（电压/电流/容量）+ 应用名称（非包名）
// 支持 AutoJS 自身与 Shizuku 双通道获取
// 静默运行（无日志输出）

"auto";

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
        // 静默失败
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
        // 静默失败
    }
    return info;
}

/**
 * 合并两种方式，优先使用 Shizuku 数据（更准确），若缺失则补充 AutoJS 数据
 */
function getBatteryDetails() {
    var autoData = getBatteryDetailsAutoJS();
    var shizukuData = getBatteryDetailsShizuku();
    
    var result = {};
    var keys = ["voltage", "current", "capacity", "level", "temperature", "health", "status"];
    for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        var shizVal = shizukuData[key];
        var autoVal = autoData[key];
        if (shizVal !== null && shizVal !== undefined && shizVal !== -1 && shizVal !== 0) {
            result[key] = shizVal;
        } else if (autoVal !== null && autoVal !== undefined && autoVal !== -1) {
            result[key] = autoVal;
        } else {
            result[key] = null;
        }
    }
    return result;
}

// ==================== 获取前台应用（增强版：包含应用名称和窗口标题） ====================
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
            // 静默
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
        appName: appName,               // 保留字段
        windowTitle: appName            // 新增：应用名称作为窗口标题（服务器优先使用）
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
            current: batteryDetail.current !== null ? batteryDetail.current : -1,
            capacity: batteryDetail.capacity !== null ? batteryDetail.capacity : -1,
            status: batteryDetail.status !== null ? batteryDetail.status : -1
        };
        if (battery.status !== -1) {
            battery.charging = (battery.status === 2 || battery.status === 5);
        }
        data.battery = battery;
    }

    // ===== 前台应用（含应用名称和窗口标题） =====
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

    try {
        ws = web.newWebSocket(wsUrl);

        ws.on("open", function onWsOpen(res, socket) {
            if (!isRunning) return;
            wsConnected = true;

            try {
                var data = collectAllData();
                data.dataType = "full";
                ws.send(JSON.stringify(data));
                sendCount++;
            } catch (e) {
                // 静默失败
            }
        });

        ws.on("text", function onWsText(text, socket) {});

        ws.on("close", function onWsClose(code, reason, socket) {
            if (!isRunning) return;
            wsConnected = false;
            if (code !== 1000 && isRunning) {
                setTimeout(connectWebSocket, 3000);
            }
        });

        ws.on("error", function onWsError(err, socket) {});

        ws.on("failure", function onWsFailure(err, res, socket) {
            if (!isRunning) return;
            wsConnected = false;
            setTimeout(connectWebSocket, 3000);
        });

    } catch (e) {
        // 静默
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
        } catch (e) {
            wsConnected = false;
            setTimeout(connectWebSocket, 3000);
        }
    }

    if (isRunning) {
        setTimeout(sendPeriodicData, CONFIG.updateInterval * 1000);
    }
}

// ==================== 全局命令（保留供调试，无日志） ====================
globalThis.collectData = function() {
    var data = collectAllData();
    return data;
};

globalThis.sendData = function() {
    if (wsConnected && ws) {
        try {
            var data = collectAllData();
            data.dataType = "full";
            ws.send(JSON.stringify(data));
        } catch (e) {}
    }
};

globalThis.status = function() {
    // 无输出，仅返回状态对象
    return {
        deviceId: CONFIG.deviceId,
        connected: wsConnected,
        sendCount: sendCount,
        interval: CONFIG.updateInterval
    };
};

globalThis.stop = function() {
    isRunning = false;
    if (ws) {
        try { ws.close(1000, "停止"); } catch (e) {}
        ws = null;
    }
    wsConnected = false;
};

// ==================== 主程序（静默启动） ====================
// 初始化网络统计（静默）
try {
    getNetworkStats(CONFIG.updateInterval);
} catch (e) {}

// 启动传感器采集
try {
    startSensorCollection();
} catch (e) {}

// 启动 WebSocket
connectWebSocket();

setTimeout(function() {
    if (isRunning) {
        sendPeriodicData();
    }
}, 3000);

events.on("exit", function() {
    isRunning = false;
    if (ws) {
        try { ws.close(1000, "退出"); } catch (e) {}
        ws = null;
    }
});