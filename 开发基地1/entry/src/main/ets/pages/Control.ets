import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { BusinessError, pasteboard } from '@kit.BasicServicesKit';
import { WifiLink, DroneLink } from '../transport/WifiLink';
import { UdpDatalink } from '../transport/UdpDatalink';
import { Prober, ProbeResult } from '../transport/Prober';
import { Ticker } from '../control/Ticker';
import { Stick } from '../protocol/Stick';
import { FlyCommand } from '../protocol/Commands';
import { Duml } from '../protocol/Duml';
import { LinkStats } from '../protocol/Inspector';

/**
 * 控制台 = 链路体检 + 发送
 *
 * 体检部分是这个页面的重点：手机是飞机唯一认可的客户端，收发都在本机，
 * 所以这里能直接看到飞机回了什么、session 是多少、各通道什么节奏。
 * 那些信息原本只能靠抓包拿，现在 App 自己就有。
 */
@Entry
@Component
struct Control {
  @State linked: boolean = false;
  @State linkText: string = '检测中…';
  @State sessionText: string = '1234';
  @State autoSession: boolean = true;
  @State running: boolean = false;
  @State probing: boolean = false;
  @State sent: number = 0;
  @State recv: number = 0;
  @State lateMs: number = 0;
  @State maxLateMs: number = 0;
  @State report: string = '（尚无数据）';
  @State probeLog: string = '';
  @State allowFly: boolean = false;
  @State note: string = '';

  private udp: UdpDatalink = new UdpDatalink();
  private ticker: Ticker = new Ticker();
  private stats: LinkStats = new LinkStats();
  private counter: number = 0;
  private seq: number = 0;
  private refreshTimer: number = -1;

  aboutToAppear(): void {
    this.keepScreenOn();
    this.prepare();
    this.refreshTimer = setInterval(() => {
      this.flushReport();
    }, 600);
  }

  aboutToDisappear(): void {
    if (this.refreshTimer >= 0) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = -1;
    }
    this.ticker.stop();
    this.udp.close();
  }

  keepScreenOn(): void {
    try {
      const ctx: common.UIAbilityContext = this.getUIContext().getHostContext() as common.UIAbilityContext;
      window.getLastWindow(ctx).then((w: window.Window) => {
        w.setWindowKeepScreenOn(true);
      }).catch((e: BusinessError) => {
        console.error('keepScreenOn 失败: ' + e.message);
      });
    } catch (e) {
      // 拿不到窗口不影响控制
    }
  }

  flushReport(): void {
    this.sent = this.udp.sentCount;
    this.recv = this.udp.recvCount;
    this.report = this.stats.takeReport();
    if (this.autoSession && this.stats.session !== 0) {
      const s: string = this.stats.session.toString(16).toUpperCase();
      if (this.sessionText.toUpperCase() !== s) {
        this.sessionText = s;
      }
    }
  }

  async prepare(): Promise<void> {
    const link: DroneLink = await WifiLink.probe();
    this.linked = link.ok();
    this.linkText = link.summary();
    if (!this.linked) {
      this.note = '未连上飞机网络，无法发送';
      return;
    }
    try {
      await this.udp.open(link.net);
      this.udp.onDatagram = (data: Uint8Array) => {
        this.recv = this.udp.recvCount;
        this.stats.add(data, true);
      };
      this.udp.onError = (msg: string) => {
        this.note = msg;
      };
      this.note = 'UDP 已打开，本机 ' + link.ip;
    } catch (e) {
      this.note = (e as Error).message;
    }
  }

  sessionId(): number {
    const v: number = parseInt(this.sessionText, 16);
    return isNaN(v) ? 0 : (v & 0xFFFF);
  }

  tick(): void {
    const pkt: Uint8Array = Stick.packet(0, 0, 0, 0, this.counter, this.seq, this.sessionId());
    this.counter = (this.counter + 1) & 0xFFFFFF;
    this.seq = (this.seq + 1) & 0xFFFF;
    this.udp.send(pkt).then(() => {
      this.sent = this.udp.sentCount;
      this.lateMs = this.ticker.lastLateMs;
      this.maxLateMs = this.ticker.maxLateMs;
    }).catch((e: Error) => {
      this.note = '发送失败：' + e.message;
    });
  }

  toggleStream(): void {
    if (this.running) {
      this.ticker.stop();
      this.running = false;
      return;
    }
    if (!this.udp.isOpen()) {
      this.note = 'UDP 未打开，先确认已连飞机网络';
      return;
    }
    this.counter = 0;
    this.seq = 0;
    this.udp.sentCount = 0;
    this.sent = 0;
    this.ticker.start(Stick.HZ, () => {
      this.tick();
    });
    this.running = true;
    this.note = '正在以 ' + Stick.HZ.toString() + 'Hz 发送中位包';
  }

  async runProbe(): Promise<void> {
    if (!this.udp.isOpen()) {
      this.note = 'UDP 未打开';
      return;
    }
    if (this.probing) {
      return;
    }
    this.probing = true;
    this.probeLog = '';
    this.stats.reset();
    const lines: string[] = [];
    const push = (s: string): void => {
      lines.push(s);
      this.probeLog = lines.join('\n');
    };
    push('开始探测（只发握手与中位包，不发任何起飞/降落）');
    try {
      const res: ProbeResult[] = await Prober.run(
        (pkt: Uint8Array) => this.udp.send(pkt),
        () => this.udp.recvCount,
        push,
        this.stats.session);
      push('');
      push(Prober.summary(res));
    } catch (e) {
      push('探测中断：' + (e as Error).message);
    }
    this.flushReport();
    this.probing = false;
  }

  sendFly(param: number): void {
    if (!this.allowFly) {
      this.note = '请先打开「允许发送飞控命令」';
      return;
    }
    if (!this.udp.isOpen()) {
      this.note = 'UDP 未打开';
      return;
    }
    const pkt: Uint8Array = FlyCommand.packet(param, this.seq, this.sessionId());
    this.seq = (this.seq + 1) & 0xFFFF;
    this.udp.send(pkt).then(() => {
      this.note = FlyCommand.label(param) + ' 已发送（' + pkt.length.toString() + ' 字节）';
    }).catch((e: Error) => {
      this.note = '发送失败：' + e.message;
    });
  }

  copyAll(): void {
    const text: string = '=== 探测 ===\n' + this.probeLog +
      '\n\n=== 链路体检 ===\n' + this.report +
      '\n\n=== 环境 ===\n' + this.linkText +
      '\nsession 输入框=' + this.sessionText +
      '\n已发=' + this.sent.toString() + ' 已收=' + this.recv.toString();
    try {
      const data: pasteboard.PasteData = pasteboard.createData(pasteboard.MIMETYPE_TEXT_PLAIN, text);
      pasteboard.getSystemPasteboard().setData(data).then(() => {
        this.note = '已复制（' + text.length.toString() + ' 字符），粘出来就能分析';
      }).catch((e: BusinessError) => {
        this.note = '复制失败：' + e.message;
      });
    } catch (e) {
      this.note = '剪贴板不可用：' + (e as Error).message;
    }
  }

  @Builder
  metric(label: string, value: string) {
    Column() {
      Text(value)
        .fontSize(17)
        .fontWeight(FontWeight.Bold)
        .fontColor('#0F2A45')
      Text(label)
        .fontSize(10)
        .fontColor('#7A8BA0')
        .margin({ top: 2 })
    }
    .layoutWeight(1)
    .alignItems(HorizontalAlign.Center)
  }

  @Builder
  sectionTitle(t: string) {
    Text(t)
      .fontSize(12)
      .fontWeight(FontWeight.Medium)
      .fontColor('#1F5C8B')
      .width('100%')
      .margin({ top: 16, bottom: 6 })
  }

  build() {
    Column() {
      Row() {
        Text('控制台')
          .fontSize(19)
          .fontWeight(FontWeight.Bold)
          .fontColor('#0F2A45')
          .layoutWeight(1)
        Button('返回')
          .fontSize(13)
          .backgroundColor('#EEF3F9')
          .fontColor('#1F5C8B')
          .onClick(() => {
            this.ticker.stop();
            this.udp.close();
            router.back();
          })
      }
      .width('100%')
      .margin({ top: 8, bottom: 8 })

      Scroll() {
        Column() {
          Row() {
            Text(this.linkText)
              .fontSize(12)
              .fontColor(this.linked ? '#2E8B7A' : '#B3261E')
              .layoutWeight(1)
          }
          .width('100%')
          .padding(10)
          .backgroundColor('#F5F7FA')
          .borderRadius(8)

          Row() {
            this.metric('已发', this.sent.toString())
            this.metric('已收', this.recv.toString())
            this.metric('延迟ms', this.lateMs.toString())
            this.metric('最大ms', this.maxLateMs.toString())
          }
          .width('100%')
          .padding({ top: 10, bottom: 10 })
          .margin({ top: 10 })
          .backgroundColor('#F5F7FA')
          .borderRadius(10)

          this.sectionTitle('会话')

          Row() {
            Text('session_id')
              .fontSize(13)
              .fontColor('#6B7A8D')
              .width(86)
            TextInput({ text: this.sessionText, placeholder: '十六进制' })
              .layoutWeight(1)
              .fontSize(13)
              .height(36)
              .enabled(!this.autoSession)
              .onChange((v: string) => {
                this.sessionText = v;
              })
            Toggle({ type: ToggleType.Switch, isOn: this.autoSession })
              .margin({ left: 8 })
              .onChange((on: boolean) => {
                this.autoSession = on;
              })
          }
          .width('100%')

          Text('自动采用 = 从收到的包里读 session（推荐）。关掉才能手填。')
            .fontSize(10)
            .fontColor('#9AA9B8')
            .width('100%')
            .margin({ top: 4 })

          this.sectionTitle('链路体检')

          Button(this.probing ? '探测中…' : '探测会话（只发握手/中位包）')
            .width('100%')
            .height(42)
            .fontSize(14)
            .backgroundColor('#1F5C8B')
            .enabled(this.linked && !this.probing)
            .onClick(() => {
              this.runProbe();
            })

          if (this.probeLog.length > 0) {
            Text(this.probeLog)
              .fontSize(10)
              .fontColor('#23384D')
              .width('100%')
              .padding(8)
              .margin({ top: 8 })
              .backgroundColor('#F7F9FB')
              .borderRadius(6)
          }

          Text(this.report)
            .fontSize(10)
            .fontColor('#23384D')
            .width('100%')
            .padding(8)
            .margin({ top: 8 })
            .backgroundColor('#F7F9FB')
            .borderRadius(6)

          Row() {
            Button('复制全部结果')
              .layoutWeight(1)
              .height(38)
              .fontSize(13)
              .backgroundColor('#6B7A8D')
              .onClick(() => {
                this.copyAll();
              })
            Button('清空体检')
              .height(38)
              .fontSize(13)
              .margin({ left: 10 })
              .backgroundColor('#EEF3F9')
              .fontColor('#1F5C8B')
              .onClick(() => {
                this.stats.reset();
                this.flushReport();
                this.probeLog = '';
              })
          }
          .width('100%')
          .margin({ top: 8 })

          this.sectionTitle('发送')

          Button(this.running ? '停止发送中位包' : '开始 20Hz 中位包')
            .width('100%')
            .height(44)
            .backgroundColor(this.running ? '#B3261E' : '#2E8B7A')
            .enabled(this.linked)
            .onClick(() => {
              this.toggleStream();
            })

          Row() {
            Toggle({ type: ToggleType.Switch, isOn: this.allowFly })
              .onChange((on: boolean) => {
                this.allowFly = on;
              })
            Text('允许发送飞控命令')
              .fontSize(12)
              .fontColor('#6B7A8D')
              .margin({ left: 8 })
          }
          .width('100%')
          .margin({ top: 12 })

          Row() {
            Button('起飞')
              .layoutWeight(1)
              .height(42)
              .backgroundColor('#D98A2B')
              .enabled(this.linked && this.allowFly)
              .onClick(() => {
                this.sendFly(FlyCommand.TAKEOFF);
              })
            Button('降落')
              .layoutWeight(1)
              .height(42)
              .margin({ left: 10 })
              .backgroundColor('#1F5C8B')
              .enabled(this.linked && this.allowFly)
              .onClick(() => {
                this.sendFly(FlyCommand.LAND);
              })
          }
          .width('100%')
          .margin({ top: 8 })

          if (this.note.length > 0) {
            Text(this.note)
              .fontSize(11)
              .fontColor('#B9812E')
              .width('100%')
              .margin({ top: 10, bottom: 20 })
          } else {
            Blank().height(20)
          }
        }
        .width('100%')
      }
      .layoutWeight(1)
      .scrollBar(BarState.Auto)
      .align(Alignment.Top)
    }
    .width('100%')
    .height('100%')
    .padding({ left: 18, right: 18, top: 8 })
    .backgroundColor('#FFFFFF')
  }
}
