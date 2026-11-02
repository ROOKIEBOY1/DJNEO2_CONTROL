/**
 * 离线自检：移植是否成功，全靠这里。
 *
 * 三层验证，从粗到细：
 *   1. 黄金向量：与 Python 生成的报文逐字节比对（最能说明问题）
 *   2. 结构验证：外层长度字段、XOR7、内层 DUML 能否解回且字段正确
 *   3. 位域往返：轴量 → 通道 → 轴量，误差不超过量化步长
 *
 * 全部通过 = 协议层可用；有任何一条失败都不要去连真机。
 */

import { Duml, DumlFrame } from '../protocol/Duml';
import { Framing } from '../protocol/Framing';
import { Stick, StickChannels } from '../protocol/Stick';
import { FlyCommand } from '../protocol/Commands';
import { Inspector, OuterPacket } from '../protocol/Inspector';
import { Vectors, StickVector, FlyVector, PayloadVector } from '../protocol/Vectors';

export class TestItem {
  name: string;
  pass: boolean;
  detail: string;

  constructor(name: string, pass: boolean, detail: string) {
    this.name = name;
    this.pass = pass;
    this.detail = detail;
  }
}

export class SelfTestResult {
  items: TestItem[] = [];

  add(item: TestItem): void {
    this.items.push(item);
  }

  passCount(): number {
    let n: number = 0;
    for (let i = 0; i < this.items.length; i++) {
      if (this.items[i].pass) {
        n++;
      }
    }
    return n;
  }

  allPass(): boolean {
    return this.items.length > 0 && this.passCount() === this.items.length;
  }
}

export class SelfTest {

  static run(): SelfTestResult {
    const r: SelfTestResult = new SelfTestResult();
    SelfTest.checkStickVectors(r);
    SelfTest.checkFlyVectors(r);
    SelfTest.checkPayloadVectors(r);
    SelfTest.checkFraming(r);
    SelfTest.checkBitRoundTrip(r);
    SelfTest.checkOuterParse(r);
    return r;
  }

  // ---------- 0. 收包解析（链路体检能不能用，全看这条） ----------

  private static checkOuterParse(r: SelfTestResult): void {
    // 用自己构造的包反解：如果连自己发的包都解不出来，飞机回的包更解不出来
    const pkt: Uint8Array = Stick.packet(0, 0, 0, 0, 0x11, 0x0101, 0x8DF0);
    const p: OuterPacket | null = Inspector.parseOuter(pkt);
    if (p === null) {
      r.add(new TestItem('收包解析 外层头', false, '自己构造的包都解不出来'));
      return;
    }
    const ok: boolean = p.channel === 0x04 && p.session === 0x8DF0
      && p.length === pkt.length && p.checksumOk;
    r.add(new TestItem('收包解析 外层头', ok,
      '通道=0x' + p.channel.toString(16) + ' session=0x' + p.session.toString(16)
        + ' 长=' + p.length.toString() + ' 校验=' + (p.checksumOk ? 'OK' : '错')));

    const off: number = Inspector.frameOffset(p);
    r.add(new TestItem('收包解析 子头识别', off === 18, '帧起点偏移 ' + off.toString() + '（应为 18）'));

    // 挖掉一个字节，校验应该失败 —— 证明解析器不是照单全收
    const broken: Uint8Array = new Uint8Array(pkt);
    broken[7] = (broken[7] ^ 0xFF) & 0xFF;
    const pb: OuterPacket | null = Inspector.parseOuter(broken);
    const rejected: boolean = pb === null || !pb.checksumOk;
    r.add(new TestItem('收包解析 拒绝坏校验', rejected, rejected ? '已拒绝' : '坏包被当成好包'));

    // 长度字段与实际不符时要拒绝
    const truncated: Uint8Array = pkt.subarray(0, pkt.length - 4);
    r.add(new TestItem('收包解析 拒绝长度不符', Inspector.parseOuter(truncated) === null,
      '截短 4 字节后应返回 null'));
  }

  // ---------- 1. 黄金向量 ----------

  private static checkStickVectors(r: SelfTestResult): void {
    for (let i = 0; i < Vectors.STICK.length; i++) {
      const v: StickVector = Vectors.STICK[i];
      const got: Uint8Array = Stick.packet(v.roll, v.pitch, v.throttle, v.yaw,
        v.counter, v.seq, v.sessionId);
      const want: Uint8Array = Duml.fromHex(v.hex);
      const ok: boolean = Duml.equals(got, want);
      r.add(new TestItem('摇杆包 ' + v.name, ok,
        ok ? (got.length.toString() + ' 字节一致') : SelfTest.diff(want, got)));
    }
  }

  private static checkFlyVectors(r: SelfTestResult): void {
    for (let i = 0; i < Vectors.FLY.length; i++) {
      const v: FlyVector = Vectors.FLY[i];
      const got: Uint8Array = FlyCommand.packet(v.param, v.seq, v.sessionId);
      const want: Uint8Array = Duml.fromHex(v.hex);
      const ok: boolean = Duml.equals(got, want);
      r.add(new TestItem('飞控包 ' + v.name, ok,
        ok ? (got.length.toString() + ' 字节一致') : SelfTest.diff(want, got)));
    }
  }

  private static checkPayloadVectors(r: SelfTestResult): void {
    for (let i = 0; i < Vectors.PAYLOAD.length; i++) {
      const v: PayloadVector = Vectors.PAYLOAD[i];
      const got: Uint8Array = Stick.payload(v.roll, v.pitch, v.throttle, v.yaw, v.counter);
      const want: Uint8Array = Duml.fromHex(v.hex);
      const ok: boolean = Duml.equals(got, want);
      r.add(new TestItem('payload ' + v.name, ok,
        ok ? '28 字节一致' : SelfTest.diff(want, got)));
    }
  }

  // ---------- 2. 结构 ----------

  private static checkFraming(r: SelfTestResult): void {
    const pkt: Uint8Array = Stick.packet(0, 0, 0, 0, 0, 0, 0);

    const lenField: number = pkt[0] | (pkt[1] << 8);
    r.add(new TestItem('外层长度字段', (lenField & 0x7FFF) === pkt.length,
      '字段 ' + (lenField & 0x7FFF).toString() + ' / 实际 ' + pkt.length.toString()));

    r.add(new TestItem('外层 XOR7', Framing.xor7(pkt) === pkt[7],
      '算出 0x' + Framing.xor7(pkt).toString(16).toUpperCase()
        + ' / 报文 0x' + pkt[7].toString(16).toUpperCase()));

    // body = 16B 子头 + 2B 长度 + 帧；跳过前 18 字节后再扫
    const body: Uint8Array = pkt.subarray(Framing.HEADER_LEN);
    const frames: Uint8Array = body.subarray(18);
    const list: DumlFrame[] = Duml.scanFrames(frames);
    if (list.length === 0) {
      r.add(new TestItem('内层 DUML 解析', false, '一个合法帧都没扫到'));
      return;
    }
    const f: DumlFrame = list[0];
    const okFields: boolean = f.sender === 0x02 && f.receiver === 0xA9
      && f.cmdSet === 0x01 && f.cmdId === 0x0A && f.payload.length === 28;
    r.add(new TestItem('内层 DUML 字段', okFields,
      'src=' + f.sender.toString(16) + ' dst=' + f.receiver.toString(16)
        + ' set/id=' + f.cmdSet.toString(16) + '/' + f.cmdId.toString(16)
        + ' payload=' + f.payload.length.toString()));
  }

  // ---------- 3. 位域往返 ----------

  private static checkBitRoundTrip(r: SelfTestResult): void {
    const cases: number[] = [-1, -0.5, -0.1, 0, 0.1, 0.5, 1];
    let worst: number = 0;
    for (let i = 0; i < cases.length; i++) {
      const x: number = cases[i];
      const payload: Uint8Array = Stick.payload(x, x, x, x, 0);
      const ch: StickChannels = Stick.decode(payload);
      const back: number[] = [
        Stick.channelToAxis(ch.roll),
        Stick.channelToAxis(ch.pitch),
        Stick.channelToAxis(ch.throttle),
        Stick.channelToAxis(ch.yaw),
      ];
      for (let k = 0; k < back.length; k++) {
        const err: number = Math.abs(back[k] - x);
        if (err > worst) {
          worst = err;
        }
      }
    }
    // 11bit / 1320 量程的量化步长约 0.00076，留一点余量
    r.add(new TestItem('位域往返 7 个姿态', worst < 0.002,
      '最大误差 ' + (worst * 100).toFixed(4) + '%'));
  }

  // ---------- 工具 ----------

  private static diff(want: Uint8Array, got: Uint8Array): string {
    if (want.length !== got.length) {
      return '长度不同：期望 ' + want.length.toString() + '，实际 ' + got.length.toString();
    }
    for (let i = 0; i < want.length; i++) {
      if (want[i] !== got[i]) {
        return '第 ' + i.toString() + ' 字节不同：期望 0x'
          + want[i].toString(16).toUpperCase() + '，实际 0x' + got[i].toString(16).toUpperCase();
      }
    }
    return '内容一致（不应走到这里）';
  }
}
