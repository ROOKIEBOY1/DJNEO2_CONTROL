/**
 * Neo 2 摇杆控件（虚拟摇杆的等价物）
 *
 * 28 字节 payload，4 个 11bit 通道从 bit 24 起，顺序 roll / pitch / throttle / yaw，
 * 量程 364 – 1024 – 1684（中位 1024）。byte[20..22] 是 24 位滚动计数器。
 *
 * 这块模板取自 Neo 一代实机可飞实现，Neo 2 抓包中同一条命令的
 * dst(0xa9) / cmdSet(0x01) / cmdId(0x0a) / 长度(28) / 频率(20Hz) / counter 偏移 六项全部吻合，
 * 但「Neo 2 的字节布局与一代完全同构」仍是唯一未证假设——首次实测必须拆桨。
 *
 * ⚠️ 轴向符号也未在 Neo 2 上验证过，第一次务必逐轴小幅度确认后再组合使用。
 */

import { Duml, DumlFrame } from './Duml';
import { Framing } from './Framing';

export class StickAxis {
  static readonly ROLL: number = 0;
  static readonly PITCH: number = 1;
  static readonly THROTTLE: number = 2;
  static readonly YAW: number = 3;
}

/** 四通道原始值快照，便于自检与界面显示。 */
export class StickChannels {
  roll: number;
  pitch: number;
  throttle: number;
  yaw: number;
  counter: number;

  constructor(roll: number, pitch: number, throttle: number, yaw: number, counter: number) {
    this.roll = roll;
    this.pitch = pitch;
    this.throttle = throttle;
    this.yaw = yaw;
    this.counter = counter;
  }
}

export class Stick {
  static readonly CH_MIN: number = 364;
  static readonly CH_CENTER: number = 1024;
  static readonly CH_MAX: number = 1684;
  static readonly CH_START_BIT: number = 24;
  static readonly CH_WIDTH: number = 11;
  static readonly PAYLOAD_LEN: number = 28;
  static readonly HZ: number = 20;

  static readonly CMD_SET: number = 0x01;
  static readonly CMD_ID: number = 0x0A;

  /** Neo 一代实机中位模板；counter 位于 byte[20..22]。 */
  private static readonly NEUTRAL: Uint8Array = Duml.bytes(
    0x01, 0x0D, 0x00, 0x00, 0x04, 0x20, 0x00, 0x01, 0x08, 0x40,
    0x00, 0x02, 0x00, 0x00, 0x06, 0x55, 0x01, 0x04, 0x56, 0x08,
    0x46, 0xF8, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00,
  );

  private static setBits(buf: Uint8Array, start: number, width: number, value: number): void {
    for (let i = 0; i < width; i++) {
      const bit: number = (value >> i) & 1;
      const pos: number = start + i;
      const byte: number = Math.floor(pos / 8);
      const off: number = pos % 8;
      if (bit !== 0) {
        buf[byte] = buf[byte] | (1 << off);
      } else {
        buf[byte] = buf[byte] & ~(1 << off);
      }
    }
  }

  private static getBits(buf: Uint8Array, start: number, width: number): number {
    let v: number = 0;
    for (let i = 0; i < width; i++) {
      const pos: number = start + i;
      if (((buf[Math.floor(pos / 8)] >> (pos % 8)) & 1) !== 0) {
        v = v | (1 << i);
      }
    }
    return v;
  }

  /** [-1, +1] → 364..1684。 */
  static axisToChannel(x: number): number {
    let v: number = x;
    if (v < -1) {
      v = -1;
    }
    if (v > 1) {
      v = 1;
    }
    return Math.round(Stick.CH_CENTER + v * (Stick.CH_MAX - Stick.CH_CENTER));
  }

  static channelToAxis(ch: number): number {
    return (ch - Stick.CH_CENTER) / (Stick.CH_MAX - Stick.CH_CENTER);
  }

  /**
   * 生成 28 字节 payload。
   * @param roll 右为正
   * @param pitch 前为正
   * @param throttle 上为正
   * @param yaw 右转为正
   */
  static payload(roll: number = 0, pitch: number = 0, throttle: number = 0, yaw: number = 0,
                 counter: number = 0): Uint8Array {
    const buf: Uint8Array = new Uint8Array(Stick.PAYLOAD_LEN);
    for (let i = 0; i < Stick.PAYLOAD_LEN; i++) {
      buf[i] = Stick.NEUTRAL[i];
    }
    const chans: number[] = [
      Stick.axisToChannel(roll),
      Stick.axisToChannel(pitch),
      Stick.axisToChannel(throttle),
      Stick.axisToChannel(yaw),
    ];
    for (let k = 0; k < 4; k++) {
      Stick.setBits(buf, Stick.CH_START_BIT + k * Stick.CH_WIDTH, Stick.CH_WIDTH, chans[k]);
    }
    buf[20] = counter & 0xFF;
    buf[21] = (counter >> 8) & 0xFF;
    buf[22] = (counter >> 16) & 0xFF;
    return buf;
  }

  /** 从 payload 读回四通道，用于自检与真机抓包核对。 */
  static decode(payload: Uint8Array): StickChannels {
    const counter: number = payload[20] | (payload[21] << 8) | (payload[22] << 16);
    return new StickChannels(
      Stick.getBits(payload, Stick.CH_START_BIT, Stick.CH_WIDTH),
      Stick.getBits(payload, Stick.CH_START_BIT + Stick.CH_WIDTH, Stick.CH_WIDTH),
      Stick.getBits(payload, Stick.CH_START_BIT + 2 * Stick.CH_WIDTH, Stick.CH_WIDTH),
      Stick.getBits(payload, Stick.CH_START_BIT + 3 * Stick.CH_WIDTH, Stick.CH_WIDTH),
      counter,
    );
  }

  /** 完整 UDP 报文：外层 + DUML(0x02→0xa9, 0x01/0x0a) + 摇杆 payload。 */
  static packet(roll: number, pitch: number, throttle: number, yaw: number,
                counter: number, seq: number, sessionId: number): Uint8Array {
    const payload: Uint8Array = Stick.payload(roll, pitch, throttle, yaw, counter);
    const frame: DumlFrame = new DumlFrame(Duml.SENDER_APP, Duml.RX_RC, seq, 0,
      Stick.CMD_SET, Stick.CMD_ID, payload);
    return Framing.buildDumlAppPacket(Duml.encode(frame), sessionId);
  }
}
