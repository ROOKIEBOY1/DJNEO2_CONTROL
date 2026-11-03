# DJNEO2_CONTROL — 飞机通信与控制核心

这是面向 **HarmonyOS 手机迁移** 的协议与能力参考工程。保留连接、注册、可靠传输、起降、八方向、原生环绕、原生滑动变焦、原生跟随、状态观察和视频分片组装；另提供视频解码/预览、离线语音、相机控制与原片下载。不包含旧鸿蒙应用壳、原始抓包和媒体文件。

**本仓库不是可以直接安装的鸿蒙 App。** 接手者应在目标 SDK 下新建工程，按文档移植协议和状态机，并替换网络、并发、计时和持久化实现。

## 建议阅读顺序

1. [迁移指南](docs/HARMONYOS_PORTING.md)：模块对应、手机双网络、迁移顺序和验收标准。
2. [协议说明](docs/PROTOCOL.md)：外层包、DUML、注册、序号、八方向和环绕。
3. [状态机与接口](docs/STATE_AND_API.md)：初始化、取消、降落、重飞及环绕接续。
4. [验证与已知边界](docs/VALIDATION.md)：哪些已实飞、哪些只经自动测试。
5. [视频、语音与相机迁移](docs/VIDEO_VOICE_CAMERA.md)：新增三项能力的运行方法、协议和手机替换点。

## 仓库结构

```text
core/             通信飞控 + 相机/媒体协议；照片校验需 Pillow
extras/           电脑视频解码/预览、离线语音和演示入口
profiles/         本研究设备的注册模板、环绕报文证据
samples/          跨语言比对用的十六进制测试向量
tests/            无需原始抓包的单元及本机 UDP 模拟测试
tests_optional/   视频、语音、相机和媒体下载回归测试
docs/             协议、状态机和鸿蒙迁移说明
run_tests.py      离线测试入口，不连接飞机
SOURCE_MAP.json   精简模块与原完整版本文件的对应关系
```

## 开始验证

建议 Python 3.12，无需安装第三方包：

```powershell
python -S run_tests.py
```

`-S` 禁用 site-packages，以验证通信飞控和基础测试不依赖第三方包。照片完整校验和电脑视频解码使用下述可选依赖。测试使用临时目录及本机回环 UDP，不发送真机指令。

要使用完整功能或测试扩展：

```powershell
python -m pip install -r requirements-extras.txt
python run_tests.py --extras
python extras/voice_console.py --help
```

Windows 离线语音需要已安装中文识别引擎和麦克风。仅预览识别可运行 `python extras/voice_console.py --listen`；连接飞机及图传使用 `--connect --video`，但必须先按迁移说明转交最新路由状态文件。连接后键盘输入“启用语音”才开放语音动作。相机口令包括“拍照、开始录像、停止录像、相册、下载最新照片、下载最新录像”。

首次读代码从 `core/flight_controller.py` 开始；编码移植从 `duml.py`、`outer.py`、`handshake.py` 开始。测试中保留少量旧文件名以追溯原用例，导入已改为精简版模块。

## 实机接手前必须了解

- 目标为本研究所用 DJI Neo 2，默认飞机地址 `192.168.2.1:9003/UDP`。注册模板和部分路由字段不是已经证明通用的多机协议。
- 同一注册身份接续现有飞机时，另行转交最新 `.state/routing-sequence.json`，原程序退出后再交接。没有状态就沿用原身份从低序号重启，可能被飞机拒绝。
- `connect()` 返回不表示完整飞控可用；需可靠传输、注册、设备回复和新鲜视频全部通过检查。握手成功、ACK 成功、实际飞行动作完成是不同事件。
- 原生环绕可能从地面自动起飞。2 米环绕有实飞证据；支持写入 2–10 米不表示全部半径已实飞。
- 鸿蒙双网络、后台定时和图传解码需在指定手机与系统版本验证。这里没有声称完成手机适配。

本次精简以完整版本 `cf8ccf7` 为基线，完整开发工程仍可在 Git 历史中找到；本机原开发目录未修改。没有重新定义原代码的开源授权。

## 2026-11-03 代码更新

- 原生跟随PC入口：`python extras/follow_console.py --connect --video`，从地面依次“准备 → 跟随 → 确认”，停止后悬停，再单独降落。首版每次连接一轮，自动识别人，未实现指定人物或跟随参数调整。见[跟随说明](docs/电脑原生跟随使用说明.md)。
- 原生滑动变焦：`python extras/dolly_zoom_console.py --connect --video`；语音入口支持滑动变焦及参数。见[PC说明](docs/智能滑动变焦PC使用说明.md)及[语音说明](docs/智能滑动变焦语音使用说明.md)。
- 降落后续确认和新鲜状态检查、应用心跳修正、停止优先处理及断连提示已同步。
- 图传增加有限帧序整理与收包诊断；真实缺帧时仍可能冻结，尚未实现主动关键帧恢复。管理员Windows诊断入口为 `python extras/diagnose_follow_layers.py --capture`，飞行由操作者交互控制。见[逐层诊断](docs/图传逐层诊断使用说明.md)。
- 保留可选键盘控制台：`python extras/flight_console.py --help`。

此次仅同步代码、协议常量、测试和使用说明，**不含原始抓包、媒体、运行日志或路由状态JSON**。`core/routing_sequences.py`为序号管理代码；实际`.state/routing-sequence.json`仍需另行交接。

发布目录验证：`python -S run_tests.py`共173项、8项依赖私人抓包跳过；`python run_tests.py --extras`共337项、21项依赖私人抓包跳过，其余均通过。被跳过的用例不能作为此发布目录的已执行验证。原开发工程的完整测试与实飞记录不随本次上传。
