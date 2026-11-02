# Neo2 原片传输（2026-10-30 抓包证据）

仅支持捕获中实际出现的只读请求：`GET http://192.168.2.1/v2?storage=1&path=...`。不发送删除、设置、拍摄或飞行命令。没有观察到 HTTP 相册列表接口，因此 `list` **仅显示本次捕获清单，不是设备最新相册**；新拍摄文件不会自动发现。

在工作区根目录运行：

```powershell
# 离线查看本次捕获清单
& '.抓包环境/Scripts/python.exe' '开发基地1/tools/media_console.py' list

# 默认只显示下载计划，不联网
& '.抓包环境/Scripts/python.exe' '开发基地1/tools/media_console.py' download DJI_20261030142058_0033_D.mp4 --output '开发基地1/captures/pc-downloads/DJI_20261030142058_0033_D.mp4'

# 电脑连接飞机 Wi-Fi 后，增加 --connect 才真正下载
& '.抓包环境/Scripts/python.exe' '开发基地1/tools/media_console.py' download DJI_20261030142058_0033_D.mp4 --output '开发基地1/captures/pc-downloads/DJI_20261030142058_0033_D.mp4' --connect

# 照片同理；当前完整原片已经从抓包恢复，无需重新下载
& '.抓包环境/Scripts/python.exe' '开发基地1/tools/media_console.py' download DJI_20261030142114_0034_D.jpg --output '开发基地1/captures/pc-downloads/DJI_20261030142114_0034_D.jpg' --connect

# 对新的抓包离线重组，输出目录必须不存在
& '.抓包环境/Scripts/python.exe' '开发基地1/tools/media_console.py' extract '开发基地1/captures/camera-media-20261030-141959.pcapng' --output '开发基地1/captures/media-new-evidence'
```

## 已实际恢复

- 文件：`captures/media-evidence-20261030-141959-final/DJI_20261030142114_0034_D.jpg`
- 5,636,096 字节，与 HTTP Content-Length 完全一致；TCP 无缺口。
- SHA256：`4804f6e266c5fccfc8e5420f8002e717ac41bbf592a1d8055f2b9f533071b44b`
- 完整解码：JPEG/MPO，主图 4000×3000，副图 960×720，两帧均解码成功；原始尾部 DJI 元数据保留。
- EXIF：DJI / FC9470；2026:10:30 14:21:14；曝光 1/50 秒，f/2.2，ISO 290，焦距 3.05 mm。

## 视频的实际限制

`DJI_20261030142058_0033_D.mp4` 总长 61,317,634 字节；抓包仅保留其中 33,964,469 个可核验的原片字节，缺少 27,353,165 字节。缺口来自 TCP 序列号，不是靠流量大小推断。**未输出不完整 MP4**。`manifest.json` 列出每条连接缺口、全部 GET/响应头、逐片段哈希和原文件缺失区间。

手机原片下载的五条 Range 已观察到，可原样重放：

```text
bytes=0-12263525
bytes=12263526-24527051
bytes=24527052-36790577
bytes=36790578-49054103
bytes=49054104-61317633
```

视频预览也使用 MP4 路径加 Range 返回内嵌 JPEG，因此“收到 JPEG”不能代表下载了视频原片。旧视频只有预览 Range，不能用当前证据下载完整文件。

下载器固定连接 192.168.2.1:80，不使用系统代理，不跟随重定向；只发清单中原样 GET/Range。它验证 HTTP 状态、Content-Length、Content-Range、ETag 和全部已捕获片段的 SHA256，再验证 JPEG 全帧解码或 MP4 顶层盒子完整性（ftyp/moov/mdat）。未捕获的视频字节须在实机重下后才能验证，MP4 盒子检查不等于视频逐帧解码。

原片通过同目录临时文件写入并同步后，以不覆盖的原子硬链接发布；失败不产生最终文件，已有文件始终拒绝覆盖。实时连接、飞机媒体服务会话要求和视频全长仍需在电脑连接飞机 Wi-Fi 后实测。

## 离线测试

```powershell
& '.抓包环境/Scripts/python.exe' -m unittest discover -s '开发基地1/tools' -p 'test_media_transfer.py'
```

覆盖 TCP 乱序/重复/冲突/缺口/序列回绕、HTTP 二进制正文和截断/重复长度、Range 边界及 ETag 隔离、路径穿越、不覆盖、默认禁网、重定向拒绝、长度/哈希不符不发布，以及 JPEG 解码和 MP4 截断检查。实时 HTTP 边界测试使用替代连接，不触达飞机。
