# BLE 心率广播（macOS）

在 macOS 上用 Python 扫描 BLE 心率广播，并以「呼吸灯 + 曲线」形式可视化。

## 目录结构

- `ble_hr_broadcast.py` 扫描广播包并尝试从 payload 解析心率
- `ble_hr_gatt.py` 通过 GATT 订阅心率（设备支持时更稳定）
- `ble_hr_corebluetooth.py` CoreBluetooth 监听器（macOS）
- `hr_simulator.py` 生成本地模拟心率数据流
- `hr_display.py` UI：呼吸灯 + 实时曲线 + 对战评分
- `hr_scan_sources.py` 扫描并保存心率数据源，便于下次优先连接
- `start_live.sh` 一键启动监听 + UI

## 依赖

- Python 3.9+
- `bleak`
- `tkinter`（`hr_display.py` 需要；系统 Python 自带，Homebrew Python 需额外安装）

安装依赖：

```bash
python3 -m pip install bleak
```

如果使用 Homebrew Python（如 `/opt/homebrew/bin/python3.13`），需安装 Tk：

```bash
brew install python-tk@3.13
```

提示：macOS 自带的 `python3` 往往是 Tk 8.5，可能出现 UI 空白。建议使用 Homebrew Python + Tk 8.6 以上。

## 一键启动（推荐）

```bash
./start_live.sh
```

说明：
- 自动启动心率监听与 UI
- 优先使用 `./.venv/bin/python`
- 日志输出到 `data/hr_corebluetooth.log`

## 使用方法

扫描广播包：

```bash
python3 ble_hr_broadcast.py
```

按设备名过滤：

```bash
python3 ble_hr_broadcast.py --name Mi
```

按地址过滤：

```bash
python3 ble_hr_broadcast.py --address XX:XX:XX:XX:XX:XX
```

扫描 N 秒：

```bash
python3 ble_hr_broadcast.py --timeout 30
```

使用 GATT（推荐）：

```bash
python3 ble_hr_gatt.py --name "Xiaomi Smart Band"
```

或按地址：

```bash
python3 ble_hr_gatt.py --address XX:XX:XX:XX:XX:XX
```

使用 CoreBluetooth（macOS）：

```bash
python3 ble_hr_corebluetooth.py
```

保存 / 优先已扫描过的数据源：

```bash
./.venv/bin/python ble_hr_corebluetooth.py \
  --sources-file data/hr_sources.json \
  --preferred-grace 6 \
  --scan-all
```

多设备同时监听：

```bash
./.venv/bin/python ble_hr_corebluetooth.py \
  --name "Xiaomi Smart Band" \
  --name "Apple Watch" \
  --max-devices 2 \
  --scan-all \
  --file data/hr_stream.jsonl --truncate
```

## 实时心率 + UI

停止模拟器，写入真实心率，再启动 UI：

```bash
./.venv/bin/python ble_hr_corebluetooth.py --file data/hr_stream.jsonl --truncate
```

```bash
python3 hr_display.py --file data/hr_stream.jsonl
```

多玩家模式（单窗口自动适配；第二个面板 N 秒无数据后隐藏）：

```bash
python3 hr_display.py \
  --file data/hr_stream.jsonl \
  --hide-seconds 12
```

UI 控件：
- **Scan all sources**：扫描并保存数据源到 `data/hr_sources.json`
- **Always on top**：窗口置顶
- **Show logs**：显示/隐藏日志浮层
- **Inline timeline**：分数曲线显示在主界面下方，否则 Start 后另起窗口
- **Timer**：输入分钟数，点击 Start 开始对战评分
提示：可加 `--listener-log data/hr_corebluetooth.log` 在 UI 里显示连接日志

评分规则：
- 分数 = 平均值 + 最小值 + 最大值（仅统计本次计时）
- 分数越低胜出
- 分数曲线会在末端标注数据源名称与当前分数

## 模拟数据 + UI

生成模拟心率：

```bash
python3 hr_simulator.py --file data/hr_stream.jsonl
```

打开 UI：

```bash
python3 hr_display.py --file data/hr_stream.jsonl
```

## 备注

- 广播解析会从 `service_data` 与 `manufacturer_data` 中尝试提取标准心率 payload
- 若广播中无心率，说明设备不在广播里附带 HR，建议使用 GATT（`ble_hr_gatt.py`）
- Apple Watch 需要安装第三方应用并开启心率广播后才会出现在广播中；该应用会通过 iPhone 转发心率广播，本程序监听到的是 iPhone 转发的数据。示例应用：

```
https://apps.apple.com/tw/app/%E5%BF%83%E7%8E%87%E5%B9%BF%E6%92%AD-heart-rate-monitor/id6473728264
```
