# 本地只接收视频预览

`VideoReceiver(preview=True, queue_size=256)` 在独立线程组帧和解码。
`start()` 返回 `127.0.0.1` 随机端口的预览地址，`submit(raw)` 只复制完整视频 UDP 包到有界队列，不等待解码或网页；队列满或已关闭时返回 False。
模块不连接飞机、不发送控制命令、不自动打开浏览器。可使用 `preview=False` 禁用 HTTP 服务。
依赖现有 `.抓包环境` 中的 PyAV 和 Pillow。

网页使用中文，2 秒没有新画面时明确标记旧画面，并区分“仍在收包但画面冻结”和“视频收包停止”。只有解码帧计数增加才重新获取 JPEG。
`/frame.jpg` 提供最后一张 JPEG；它可能是旧画面，调用方必须结合 `/status` 判断新鲜度。
`status()` 除原有计数外报告 `state`、`last_receive_age_s`、`last_assembly_age_s`、
`decoder_warnings`、`last_decoder_warning`、`corrupt_frames` 和 `timing_parameters_suppressed`。
FFmpeg 有些无输出错误仅写日志而不抛异常，因此 `decode_errors=0` 不能证明正常解码。

## 2026-10-29 冻结定位与修复范围

长流回放确认两种不同原因：

1. 无人机重复发送 VPS/SPS，把时序从 30 fps 改为 15 fps 后又恢复。其余解码参数相同，后续仍是预测帧。FFmpeg 重新应用 SPS 会清除参考链，出现持续无输出。
2. 完整视频包在抓包中缺失。`circle-20261029.pcapng` 的 2.74835 → 3.21849 秒间，帧号 75 → 82、视频外层序号 63024 → 63232（正常每包增加 8）。后续 FFmpeg 报告缺少 POC 26 等参考图像；抓包末尾也没有新的 IDR。不是预览线程卡死，也不是仅丢弃一个不完整组帧的数量能够表示的全部损失。

修复只过滤**经过 HEVC 语法定位、且除 `num_units_in_tick` 和 `time_scale` 外每一位都相同**的 VPS/SPS 重发，保留现有参考链。实际解码参数变化、未知语法、截断参数均原样交给解码器；不替换未知 SPS，不推测分辨率。参数签名缓存有界。PPS、VCL、SEI 保持原数据。
过滤后若整个输入为空，不调用 `parse(b'')`，避免误冲刷正在接收的帧。
没有开启 `SHOW_ALL` 或允许损坏帧输出，`is_corrupt` 帧仍被拒绝。

完整文件离线回放（不是仅取前 5 帧；不向飞机发送任何数据）：

| 抓包 | 视频包 / 完整组帧 | 修复前可显示帧 | 修复后可显示帧 | 结果 |
| --- | --- | --- | --- | --- |
| circle-20261029，118.76 秒 | 63114 / 3513 | 58 | 72 | 跨过时序切换；真实缺包后仍无可恢复关键帧 |
| circle-20261029-actions，约 279 秒 | 292419 / 8214 | 137 | 2797 | 后续参数和 IDR 恢复，解码持续至抓包尾部 |
| circle-stop-20261029-172351，66.97 秒 | 25812 / 1875 | 737 | 1773 | 解码持续至抓包尾部 |

证据保存在 `开发基地1/captures/video-{long,actions,stop}-{before,after}.json`。
首两份 before 文件采用不做 JPEG 转换的原始 codec 统计，与本次原始解码路径一致；after 和 stop-before 使用实际 HEVCDecoder/JPEG 路径。
诊断脚本保留 10 秒分段统计、NAL 类型、解码器警告和最早帧号跳变。
“最后画面可解码”不等于逐帧无损；文件本身含缺包和重复包，损坏帧没有作为成功画面计数。

实际冻结日志 `control-attempt-20261029-203942.json` 显示接收 159818 包、组帧 9558、解码 1983、队列丢包 0、不完整帧丢弃 21，画面比最后收包早约 120 秒停止。日志只留尾部 2000 个视频包，不包含当时完整参数和参考链，不能据此声称该次实飞已恢复。
本次只做离线验证。完整参考帧丢失或晚接入缺少 VPS/SPS/PPS 时，仍须等待有效的流内参数和关键帧；本模块不会猜测或发送请求关键帧的飞机命令。

## 复现

从工作区根目录执行：

```powershell
& '.抓包环境/Scripts/python.exe' -m unittest discover -s '开发基地1/tools' -p test_video_stream.py
& '.抓包环境/Scripts/python.exe' '开发基地1/tools/diagnose_video_freeze.py' '开发基地1/captures/circle-20261029-actions.pcapng'
# 对照：同一解码器，禁用 timing 过滤
& '.抓包环境/Scripts/python.exe' '开发基地1/tools/diagnose_video_freeze.py' '开发基地1/captures/circle-20261029-actions.pcapng' --original
```

真实时序切换回归先在旧实现失败（仅 58 帧），修复后通过。
另验证非时序参数不被过滤、未知参数清理缓存、空输入不冲刷、缺参数时不伪造画面、后续有效参数 + IDR 自然恢复、冻结与停收状态以及有界队列。
原 `decode_video_capture.py --frames 5` 仍可用于导出 JPEG，但它只能证明开头画面，不能证明长流稳定。

视频组帧保持已有格式：16 字节外层头后，字节 16 为帧号；字节 17 低 7 位为分片数；
分片索引为 `((byte18 & 0x1f) << 1) | (byte17 >> 7)`，载荷从字节 20 开始。
只发送完整组帧给 HEVC parser，没有 FEC 或丢包重建。
