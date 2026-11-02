# 控制会话、状态机与接口

## 1. 会话顺序

```text
取得飞机 Wi-Fi 本机 IP / 网络绑定
    ↓
读取并预留该身份的路由 counter，取得单会话锁
    ↓
同一 UDP socket 发送握手，核验短回复
    ↓
接收循环 + 发送/确认/重传循环 + 注册调度
    ↓
基础查询与设备信息查询
    ↓
验证设备回复 + 新鲜视频 → 可申请动作
```

仅调用 `FlightController.connect()` 不会默认打开全部可靠注册：移植时明确启用 `reliable=True`，提供注册配置和路由状态。旧 legacy 选项仍保留用于底层对照，不能作为鸿蒙首版推荐路线。

下面只是 Python 集成示意，**不应直接作为无人值守起飞脚本运行**：

```python
from pathlib import Path
from flight_controller import FlightController
from registration_exchange import PROFILE
from routing_sequences import RoutingSequenceStore

# 此文件必须是已经核对身份并接续的最新状态，不在此处生成低序号初值。
state_path = Path('.state/routing-sequence.json')
if not state_path.exists():
    raise RuntimeError('先完成设备身份与路由序号交接')
route = RoutingSequenceStore(state_path)
controller = None
try:
    controller = FlightController.connect(
        local_ip=wifi_local_ip,  # 由平台获得飞机 Wi-Fi IP
        reliable=True,
        registration_profile=PROFILE,
        routing_sequences=route,
        tick_interval=0.05,
    )
    if not controller.initialize_control_link():
        raise RuntimeError('初始化未完成，不开放动作')
    # 把公开动作接口交给有确认、取消和状态显示的本地调度器。
finally:
    if controller is not None:
        controller.close()
    route.close()
```

Python 模块本身不带新的 CLI，也不会在 import 时连接飞机。

## 2. 应对 UI 暴露的接口

| 参考方法 | 意义 | 必须保留的条件 |
| --- | --- | --- |
| `initialize_control_link()` | 完成设备查询与新鲜视频检查 | 失败不可放行起飞 |
| `status()` | 当前会话、确认、轴值、遥测和任务快照 | 过期数据保留年龄，不能显示为当前事实 |
| `takeoff(cancel_event=...)` | 普通起飞请求 | 设备/视频新鲜，无降落锁，本轮未起飞 |
| `move(direction, seconds, cancel_event=...)` | 单方向定时动作 | 有起飞许可或已验证原生停止后的悬停接管，且未请求降落 |
| `center_sticks()` | 取消当前动作并四轴回中 | 不等于原生任务停止 |
| `land()` | 回中并请求降落 | 不能把回中失败当作跳过降落的理由 |
| `prepare_grounded_cycle(confirmed_grounded=True)` | 根据新鲜落地和任务状态重建周期 | 必须有操作者确认和本轮之后的新鲜遥测 |
| `takeoff_again(confirmed_grounded=True)` | 上一步检查后普通起飞 | 不能在空中重置状态重新起飞 |
| `close()` | 尝试回中，必要时请求降落，停止线程/socket | 不是飞机肯定落地的证明 |

方向字符串：`forward/backward/up/down/left/right/turn_left/turn_right`。最大 10 秒是当前本地策略；不要为“支持语音十米”自行换算成未标定的时长。

## 3. 动作状态至少分开保存

- `takeoff_sent`：本轮普通起飞已尝试发送。
- `takeoff_acknowledged`：收到匹配的普通起飞 `00`。
- `native_task_may_fly`：原生任务可能已经使飞机起飞，哪怕启动回复未到；用于保留退出时降落尝试。
- `land_requested`、`land_sent`、`land_acknowledged`：请求意图、实际发送、匹配回复。
- 新鲜 OSD 的 `motors_on`、`airborne_flag`、模式、采样时间。
- 原生任务开始时间、停止成功回复时间、此后的新鲜任务与悬停状态。

这些状态不能合并成一个 `connected` 或 `flying` 布尔值。尤其不能因握手成功设置“飞控已就绪”，或因环绕自动起飞伪造普通起飞 ACK。

## 4. 环绕启动、停止和手动接管

`NativeCircle.prepare(radius, cancel_event=token)` 先回中，选择模式，写半径并读回。`start(confirmed=True, cancel_event=同一token)` 才启动；程序上层负责真正取得用户确认，不能无条件硬编码 confirmed=True。

`wait_execution(timeout=12, ...)` 观察启动之后的新鲜任务状态。这里只将样本中的 `0505000100` 记为“已观察到执行”，不是完整的任务成功判据。

`NativeCircle.stop()` 取消准备/启动后续步骤并发原生停止，不排队等当前工作结束。普通回中帧不能代替它。`NativeCircle.land()` 即便停止失败也尝试降落。

停止环绕后继续八方向，当前参考同时要求：

1. 同一次或更新的启动之后收到匹配停止回复 `00`。
2. 停止回复之后的新鲜 OSD，电机开启、空中标志为真、模式 6。
3. 停止回复之后的新鲜任务状态为已观察的停止状态。

满足接管条件仍保留 `native_task_may_fly` 供退出处理；不把旧的普通起飞 ACK 当作接管证明。

落地后重新起飞或环绕，需要操作者确认、新鲜电机停止和无空中标志，原生任务也要确认结束；不能只因收到某个降落 ACK 清空旧状态。新周期的取消 token 与上一周期隔离；有异步操作时重建 `NativeCircle` 对象，避免继承其 `land_requested` 或旧时间戳。

## 5. 并发与异常路径

发送前在同一串行边界分配序号、登记待回复项、检查取消，然后发包。超时后清理 pending，不用旧回复完成新请求。可靠重传缓存中已经发出的包与尚未发送的工作项要区分；不能为了取消某条命令制造可靠序号缺口。

降落优先：设置本轮取消意图、取消方向动作、回中、尝试停止原生任务、发送降落；回中或任务停止报错不能阻断降落尝试。传输窗口已满或网络中断时应报告未确认，不能显示“已经停止”。

默认没有起飞后固定 N 秒的自动降落计时器；`max_flight_seconds` 仅在显式配置时启用。拒绝新任务不能意外取消仍有效的原降落保护。

## 6. 错误分层

| 层级 | 示例 | 正确反馈 |
| --- | --- | --- |
| 网络 | 飞机 Wi-Fi 丢失、socket 错误 | 连接失效，停止接收新动作 |
| 握手/注册 | 短回复存在但没有设备/视频 | 初始化未完成 |
| 传输 | 窗口满、确认超时 | 命令发送/确认失败 |
| 命令 | 错接收端、非零回复、无匹配回复 | 请求未被验证接受 |
| 任务 | ACK 00 但没有执行阶段 | 已回复，尚未观察到执行 |
| 实际飞行 | 降落 ACK 后恢复悬停 | 未确认落地，需操作者接管 |

此分层应同时体现在 UI 和日志中，便于其他开发者判断问题位于网络、协议还是机载任务条件。
