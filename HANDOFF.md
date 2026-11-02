# 开发交接与迁移顺序

## 从哪里开始读

1. `开发基地1/tools/flight_console.py`：控制会话与各模块的集成入口。
2. `handshake_probe.py`、`capture_protocol.py`：握手及外层包解析。
3. `registration_exchange.py`、`registration_schema.py`：会话注册和实时请求回复。
4. `reliable_transport.py`、`routing_sequences.py`：确认窗口、重传和跨重启序号。
5. `control_packets.py`、`offline_takeoff.py`、`fly_command.py`：内外层封装及校验。
6. `neutral_stick.py`、`movement_commands.py`、`landing_status.py`：四轴控制与降落状态。
7. `native_circle.py`、`circle_protocol.py`、`subject_observation.py`：原生环绕、参数及主体/任务观察。
8. `video_stream.py`：视频分片、HEVC 解码及本机预览。
9. `voice_control.py`、`voice_commands.py`、`voice_motion.py`：自然语言入口、状态检查与动作计划。
10. `native_camera.py`、`media_directory*.py`、`media_transfer.py`：原生相机及媒体下载。

## 需要另行转交的设备数据

- 同一飞机、同一注册身份的最新 `.state/routing-sequence.json`，现用程序退出后再转交；不交 `.lock` 或临时语音文件。
- 需要复现真实抓包回归时，另行转交测试所引用的 captures 样本；先检查是否包含视频、人员、网络及设备信息。
- 注册模板是本设备抓包资料，不是通用初始化协议。更换身份或设备需要重新验证。

运行状态没有进入 Git 历史。缺少状态又沿用已有身份时，不应直接进行真机控制；先处理序号接续。标定数据同样不通用于其他飞机、环境或控制强度。

## 鸿蒙迁移建议

先在不发送飞行动作的条件下验证 UDP 会话、Wi-Fi/移动网络分流、持续接收和解码视频。再移植报文编码与状态机，用已有测试向量逐字节对照。最后才接入动作执行、原生环绕及 Agent。

Agent 输出有限、明确的动作参数；动作时长、取消、回中、连接状态检查仍由设备本地处理。不要把收到 ACK 写成实际完成飞行任务。

## 测试范围

`run_tests.py` 运行无需私有原始抓包的用例。需要原始样本的用例仍保留，缺少样本会输出跳过原因。验证结果记录在根目录 `VALIDATION.txt`；本机测试不构成新的实飞验证。
