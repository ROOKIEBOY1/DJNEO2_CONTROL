import { router } from '@kit.ArkUI';
import { Permissions, PermissionIssue } from '../transport/Permissions';
import { WifiLink, DroneLink } from '../transport/WifiLink';

@Entry
@Component
struct Index {
  @State wifiOn: boolean = false;
  @State permOk: boolean = false;
  @State permText: string = '未检查';
  @State linkText: string = '未检测';
  @State linked: boolean = false;
  @State busy: boolean = false;
  @State scanning: boolean = false;
  @State scanText: string = '';

  aboutToAppear(): void {
    this.refresh();
  }

  async refresh(): Promise<void> {
    this.busy = true;
    const missing: PermissionIssue[] = Permissions.missing();
    this.permOk = missing.length === 0;
    if (this.permOk) {
      this.permText = '已就绪';
    } else {
      let s: string = '缺少：';
      for (let i = 0; i < missing.length; i++) {
        if (i > 0) {
          s += '、';
        }
        s += missing[i].name.replace('ohos.permission.', '');
      }
      this.permText = s;
    }

    this.wifiOn = WifiLink.isWifiOn();
    if (!this.wifiOn) {
      this.linkText = 'WLAN 未打开';
      this.linked = false;
      this.busy = false;
      return;
    }

    const link: DroneLink = await WifiLink.probe();
    this.linked = link.ok();
    this.linkText = link.summary();
    this.busy = false;
  }

  async scan(): Promise<void> {
    this.scanning = true;
    this.scanText = '扫描中…';
    const drones: string[] = await WifiLink.scanDrones();
    if (drones.length > 0) {
      this.scanText = '附近飞机热点：\n' + drones.join('\n') +
        '\n\n去「设置 → WLAN」连它，回本页点重新检测。';
    } else {
      const all: string[] = await WifiLink.scanAll();
      this.scanText = '没扫到 DJI/NEO 热点。\n当前可见 ' + all.length.toString() + ' 个：' +
        (all.length > 0 ? all.join('、') : '（无）') +
        '\n\n飞机热点不是开机就广播的，要在 DJI Fly 里点：' +
        '连接引导 → DJI Neo 2 → 手机 Wi-Fi 飞行。首次还要长按飞机电源键确认。';
    }
    this.scanning = false;
  }

  @Builder
  statusRow(label: string, value: string, ok: boolean) {
    Row() {
      Text(label)
        .fontSize(14)
        .fontColor('#6B7A8D')
        .width(88)
      Text(value)
        .fontSize(14)
        .fontColor(ok ? '#2E8B7A' : '#B3261E')
        .layoutWeight(1)
    }
    .width('100%')
    .padding({ top: 6, bottom: 6 })
  }

  build() {
    Scroll() {
      Column() {
      Text('DJI Neo 2 控制台')
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .fontColor('#0F2A45')
        .width('100%')
        .margin({ top: 8, bottom: 4 })
      Text('协议层自检 + 连接状态')
        .fontSize(13)
        .fontColor('#6B7A8D')
        .width('100%')
        .margin({ bottom: 20 })

      Column() {
        this.statusRow('权限', this.permText, this.permOk)
        this.statusRow('WLAN', this.wifiOn ? '已打开' : '未打开', this.wifiOn)
        this.statusRow('飞机网络', this.linkText, this.linked)
      }
      .width('100%')
      .padding(16)
      .backgroundColor('#F5F7FA')
      .borderRadius(12)

      Row() {
        Button('重新检测')
          .layoutWeight(1)
          .backgroundColor('#1F5C8B')
          .enabled(!this.busy)
          .onClick(() => {
            this.refresh();
          })
        Button(this.scanning ? '扫描中…' : '扫飞机热点')
          .layoutWeight(1)
          .margin({ left: 10 })
          .backgroundColor('#6B7A8D')
          .enabled(!this.scanning)
          .onClick(() => {
            this.scan();
          })
      }
      .width('100%')
      .margin({ top: 16 })

      if (this.scanText.length > 0) {
        Text(this.scanText)
          .fontSize(11)
          .fontColor('#23384D')
          .width('100%')
          .padding(10)
          .margin({ top: 10 })
          .backgroundColor('#F7F9FB')
          .borderRadius(8)
      }

      Column() {
        Text('离线自检')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .fontColor('#0F2A45')
          .width('100%')
        Text('与 Python 生成的报文逐字节比对，不联网、不需要飞机')
          .fontSize(12)
          .fontColor('#6B7A8D')
          .width('100%')
          .margin({ top: 4, bottom: 12 })
        Button('运行自检')
          .width('100%')
          .backgroundColor('#2E8B7A')
          .onClick(() => {
            router.pushUrl({ url: 'pages/Selftest' });
          })
      }
      .width('100%')
      .padding(16)
      .margin({ top: 16 })
      .backgroundColor('#FFFFFF')
      .borderRadius(12)
      .border({ width: 1, color: '#E3E9F0' })

      Column() {
        Text('控制台')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .fontColor('#0F2A45')
          .width('100%')
        Text('连上飞机热点后可用；默认只发中位包，飞控命令需要显式打开')
          .fontSize(12)
          .fontColor('#6B7A8D')
          .width('100%')
          .margin({ top: 4, bottom: 12 })
        Button('进入控制台')
          .width('100%')
          .backgroundColor('#D98A2B')
          .enabled(this.linked)
          .onClick(() => {
            router.pushUrl({ url: 'pages/Control' });
          })
      }
      .width('100%')
      .padding(16)
      .margin({ top: 12 })
      .backgroundColor('#FFFFFF')
      .borderRadius(12)
      .border({ width: 1, color: '#E3E9F0' })

      Text('飞机热点不是开机就广播的：先在 DJI Fly 里点「连接引导 → DJI Neo 2 → 手机 Wi-Fi 飞行」，' +
        '再去「设置 → WLAN」连它，回到本页点重新检测。首次连接需长按飞机电源键确认。' +
        '注意选「保持连接」——飞机热点没有外网，系统会提示切换。')
        .fontSize(11)
        .fontColor('#9AA9B8')
        .width('100%')
        .margin({ top: 16, bottom: 24 })
      }
      .width('100%')
      .padding({ left: 20, right: 20, top: 12 })
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#FFFFFF')
    .scrollBar(BarState.Auto)
    .align(Alignment.Top)
  }
}
