// device_collector.js
// 修复版 - 传感器数据更新频率可配置 + 前台应用获取修复

"auto";

console.show();

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

// ==================== 配置 ====================
var DEVICE_ID = getDeviceId();
var MARKET_NAME = getMarketName();
var MANUFACTURER = getManufacturer();
var ANDROID_VERSION = getAndroidVersion();
var SDK_VERSION = getSdkVersion();
var SCREEN_INFO = getScreenInfo();

var CONFIG = {
    "deviceId": DEVICE_ID,
    "wsServer": "localhost:114514",
    "updateInterval": 5,  // 数据上传间隔（秒）
    "collectBasicInfo": true,
    "collectBattery": true,
    "collectForegroundApp": true,
    "collectMemory": true,
    "collectScreenState": true,
    "collectStorageInfo": true,
    "collectCpuInfo": true,
    "collectLocation": true,
    "collectSensor": true,
    "collectProcesses": true,
    "collectPackages": true
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

// ==================== 获取前台应用（增强版） ====================
function getForegroundApp() {
    var pkg = "Unknown";
    var act = "Unknown";
    var source = "none";

    // 方法1: 使用 Auto.js 原生 API (需要无障碍服务)
    if (auto.service) {
        try {
            // 尝试使用 currentPackage()
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

        // 如果 currentPackage 失败，尝试使用 app.currentPackage()
        if (pkg === "Unknown") {
            try {
                if (typeof app.currentPackage === 'function') {
                    var p = app.currentPackage();
                    if (p && p !== "Unknown" && p !== "" && p !== null && typeof p === 'string' && p.length > 0) {
                        pkg = p;
                        source = "autojs_app_currentPackage";
                    }
                }
            } catch (e) {
                // 静默处理
            }
        }

        // 获取 Activity
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

    // 方法2: 使用 dumpsys (不需要无障碍, 但需要权限)
    if (pkg === "Unknown") {
        // 多个 dumpsys 命令尝试
        var cmds = [
            // Android 8+ 推荐
            "dumpsys activity activities 2>/dev/null | grep -E 'mResumedActivity|mFocusedActivity|mCurrentFocus' | head -1",
            // 旧版本兼容
            "dumpsys window windows 2>/dev/null | grep -E 'mCurrentFocus' | head -1",
            // 备用方法
            "dumpsys activity top 2>/dev/null | grep -E 'ACTIVITY|TASK' | head -5"
        ];

        for (var i = 0; i < cmds.length; i++) {
            try {
                var output = execShell(cmds[i]);
                if (output && output.length > 0) {
                    // 尝试多种正则匹配
                    var match = null;
                    
                    // 匹配格式: com.example.app/com.example.app.MainActivity
                    match = output.match(/([a-zA-Z0-9._-]+)\/([a-zA-Z0-9._-]+)/);
                    if (match) {
                        pkg = match[1];
                        act = match[2] || "Unknown";
                        source = "dumpsys_" + (i + 1);
                        break;
                    }
                    
                    // 匹配格式: {u0 com.example.app/com.example.app.MainActivity}
                    match = output.match(/\{[^}]*\s+([a-zA-Z0-9._-]+)\/([a-zA-Z0-9._-]+)/);
                    if (match) {
                        pkg = match[1];
                        act = match[2] || "Unknown";
                        source = "dumpsys_alt_" + (i + 1);
                        break;
                    }
                    
                    // 匹配格式: com.example.app
                    match = output.match(/([a-zA-Z0-9._-]+)/);
                    if (match && match[1] && match[1].length > 3 && match[1].indexOf('.') > 0) {
                        pkg = match[1];
                        source = "dumpsys_pkg_only_" + (i + 1);
                        break;
                    }
                }
            } catch (e) {
                // 忽略
            }
        }
    }

    // 方法3: 使用 ps 命令 (备用)
    if (pkg === "Unknown") {
        try {
            // 尝试通过进程列表获取前台进程
            var output = execShell("ps -A 2>/dev/null | grep -E 'system_server|zygote' | head -1");
            // 这个方法不太准确，作为最后的备用
        } catch (e) {}
    }

    // 如果还是 Unknown，尝试通过 /proc 获取
    if (pkg === "Unknown") {
        try {
            var output = execShell("cat /proc/`pidof system_server`/cmdline 2>/dev/null");
            // 不太可能获取到，忽略
        } catch (e) {}
    }

    return {
        packageName: pkg,
        activity: act,
        source: source,
        isAutoJsService: !!auto.service
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

    // 定义要监听的传感器
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

    // 清理旧监听器
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
        } catch (e) {
            // 传感器不支持，忽略
        }
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

    // ===== 电池信息 =====
    if (CONFIG.collectBattery) {
        var battery = { level: -1, charging: false, temperature: -1, voltage: -1, health: "Unknown" };
        try {
            if (typeof device.getBattery === 'function') {
                battery.level = device.getBattery();
            }
        } catch (e) {}
        try {
            if (typeof device.isCharging === 'function') {
                battery.charging = device.isCharging();
            }
        } catch (e) {}
        try {
            if (typeof device.getBatteryTemperature === 'function') {
                var temp = device.getBatteryTemperature();
                if (temp > 0) battery.temperature = temp;
            }
        } catch (e) {}
        try {
            if (typeof device.getBatteryVoltage === 'function') {
                var volt = device.getBatteryVoltage();
                if (volt > 0) battery.voltage = volt;
            }
        } catch (e) {}
        try {
            if (typeof device.getBatteryHealth === 'function') {
                battery.health = device.getBatteryHealth();
            }
        } catch (e) {}
        data.battery = battery;
    }

    // ===== 前台应用 =====
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

        ws.on("text", function onWsText(text, socket) {
            // 静默
        });

        ws.on("close", function onWsClose(code, reason, socket) {
            if (!isRunning) return;
            log("🔴 断开: " + code);
            wsConnected = false;
            if (code !== 1000 && isRunning) {
                setTimeout(connectWebSocket, 3000);
            }
        });

        ws.on("error", function onWsError(err, socket) {
            // 忽略
        });

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
log("📱 设备收集 v10.0 (传感器高频版)");
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
    if (test.battery) log("  🔋 " + test.battery.level + "%" + (test.battery.charging ? " (充电中)" : ""));
    if (test.foreground) {
        log("  📱 前台: " + test.foreground.packageName + " (来源: " + test.foreground.source + ")");
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