# DJI Neo 2 通信与控制逆向工程

源码整理日期：2026-11-02。包含电脑 Python 研究实现、HarmonyOS 工程及核心源码快照。Python 端的实测结果不代表鸿蒙端已经完成移植。

## 目录

| 路径 | 内容 |
| --- | --- |
| `开发基地1/tools/` | UDP 握手、注册、可靠传输、飞行控制、环绕、图传、语音与媒体下载，以及测试 |
| `开发基地1/profiles/` | 从本机抓包提取的注册模板和环绕证据 |
| `开发基地1/docs/` 及项目内 Markdown | 协议分析、操作说明、故障与验证记录 |
| `开发基地1/entry/`、`AppScope/`、`hvigor/` | DevEco Studio / HarmonyOS 工程 |
| `核心代码/` | 从原始鸿蒙工程提取的 control / protocol / transport 快照 |
| `SOURCE_MANIFEST.json` | 此次源文件快照的 SHA-256 与大小清单 |

未收录虚拟环境、原始抓包、相机照片与录像、实时会话日志、`.state` 运行状态、签名材料和旧构建缓存。原始 `DJ/DJINEO2` 工程未重复收录。

## 已有能力与验证边界

- 电脑直连飞机 Wi-Fi，完成握手、应用注册、可靠传输与状态接收。
- 起飞、降落、前进，以及部分语音方向操作已有操作者实机反馈；八方向按时长执行并自动回中，距离需要实测标定。
- 原生 2 米环绕已有电脑实飞完成反馈；提供 2–10 米半径写入、回显及独立读回，不表示每个半径均已实飞。
- Windows 离线中文语音入口、HEVC 图传和本机浏览器预览。视频冻结已有修复及长流离线证据，丢失完整参考链仍可能等待关键帧。
- 原生录像及 PC 原片下载已有反馈；补齐前置参数后的 PC 拍照仍以对应记录为准。
- 环绕/普通飞行切换、落地重飞已有代码与测试，后续实机验证边界见《环绕衔接与视频修复记录》。

命令回复 `00`、传输确认和飞机实际执行是不同证据。历史文档保留当时调查结果；同一问题优先阅读日期较新的记录。

## 安装与离线验证

建议 Python 3.12。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-capture.txt
.\.venv\Scripts\python.exe run_tests.py
```

`run_tests.py` 只运行本机模拟与离线测试，不连接真机。未随仓库分发的原始抓包用例会明确跳过，缺少可选依赖的用例也会明确跳过，不算作通过。如果已取得全部原始样本和依赖，可运行完整发现：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s 开发基地1/tools -p "test_*.py"
```

## 运行入口

不带连接选项先查看帮助：

```powershell
.\.venv\Scripts\python.exe .\开发基地1\tools\flight_console.py
.\.venv\Scripts\python.exe .\开发基地1\tools\voice_console.py --help
```

真机使用前先阅读项目内《语音控制使用说明》《电脑原生环绕使用说明》。电脑连接飞机 Wi-Fi 后，手动入口使用 `flight_console.py --connect`，语音与图传入口使用 `voice_console.py --connect --video`。Windows 语音使用本机安装的中文识别引擎。

同时只运行一个控制会话。**接续原设备和现有注册身份时，应先通过仓库之外的方式转移最新 `.state/routing-sequence.json`；不要直接启动一个从低序号重新计数的副本。** 干净克隆不会包含现用设备的运行状态。

## 开发交接

先读 [HANDOFF.md](HANDOFF.md)。注册模板保留设备/应用相关字段，当前只针对研究所用飞机验证，不能保证适配其他设备。HarmonyOS 的网络分流、后台运行、视频解码与语音能力需独立验证；Windows 语音脚本不能直接用于鸿蒙。

本仓库没有新增开源授权声明；后续对外授权需由代码权利人确认。
