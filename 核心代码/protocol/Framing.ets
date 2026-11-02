/**
 * Neo 2 外层 UDP 封装（端口 9003）
 *
 * 每个 UDP 报文 = 16 字节头部 + 负载：
 *   [0:2]   u16le  总长 = 16 + body.length，最高位置 1（| 0x8000）
 *   [2:4]   u16le  session_id（握手包下发，见 SessionId 说明）
 *   [4:6]   u16le  视频序号（控制包固定 0）
 *   [6]     u8     通道：0x00 握手 / 0x01 上行 DUML / 0x02 视频 / 0x04 下行 DUML
 *   [7]     u8     XOR(byte0..byte6)
 *   [8:10]  u16le  视频 ack（仅通道 0x04 填）
 *   [10:12] u16le  视频 ack（同上）
 *   [12:16] 保留 0
 *
 * 下行 DUML 的 body 还要再套一层固定子头：
 *   16 字节常量 78 5d 78 5d 00 00 00 00 78 5d 78 5d 00 00 00 00
 *   + u16le 内层帧总长 + DUML 帧
 *
 * 与 tools/neo2_control/neo2_stick.py 的 build_outer / build_duml_app_packet 逐字节一致。
 */

import { Duml } from './Duml';

export class Channel {
  static readonly HANDSHAKE: number = 0x00;
  static readonly DUML_DRONE: number = 0x01;
  static readonly VIDEO: number = 0x02;
  static readonly DUML_APP: number = 0x04;
}

export class Framing {
  static readonly UDP_PORT: number = 9003;
  static readonly DRONE_IP: string = '192.168.2.1';
  static readonly HEADER_LEN: number = 16;

  /** 16 字节常量子头。含义未明，Neo 2 抓包里每个下行 DUML 包都带，原样复现。 */
  private static readonly SUBHEADER: Uint8Array = Duml.fromHex('785d785d00000000785d785d00000000');

  static xor7(buf: Uint8Array): number {
    let chk: number = 0;
    for (let i = 0; i < 7; i++) {
      chk ^= buf[i];
    }
    return chk & 0xFF;
  }

  /** 16 字节外层头。 */
  static buildHeader(channel: number, bodyLen: number, sessionId: number,
                     videoSeq: number = 0, videoAck: number = 0): Uint8Array {
    const total: number = Framing.HEADER_LEN + bodyLen;
    const h: Uint8Array = new Uint8Array(Framing.HEADER_LEN);
    const lenField: number = (total & 0x7FFF) | 0x8000;
    h[0] = lenField & 0xFF;
    h[1] = (lenField >> 8) & 0xFF;
    h[2] = sessionId & 0xFF;
    h[3] = (sessionId >> 8) & 0xFF;
    h[4] = videoSeq & 0xFF;
    h[5] = (videoSeq >> 8) & 0xFF;
    h[6] = channel & 0xFF;
    if (channel === Channel.DUML_APP) {
      h[8] = videoAck & 0xFF;
      h[9] = (videoAck >> 8) & 0xFF;
      h[10] = videoAck & 0xFF;
      h[11] = (videoAck >> 8) & 0xFF;
    }
    h[7] = Framing.xor7(h);
    return h;
  }

  /** 把一帧或多帧 DUML 打包成完整的下行 UDP 报文。 */
  static buildDumlAppPacket(frames: Uint8Array, sessionId: number, videoAck: number = 0): Uint8Array {
    const lenBytes: Uint8Array = new Uint8Array(2);
    lenBytes[0] = frames.length & 0xFF;
    lenBytes[1] = (frames.length >> 8) & 0xFF;
    const body: Uint8Array = Duml.concat(Framing.SUBHEADER, lenBytes, frames);
    const header: Uint8Array = Framing.buildHeader(Channel.DUML_APP, body.length, sessionId, 0, videoAck);
    return Duml.concat(header, body);
  }

  /** 拆外层头，返回 body；长度或通道不合法返回 null。 */
  static parseHeader(pkt: Uint8Array): FramingHeader | null {
    if (pkt.length < Framing.HEADER_LEN) {
      return null;
    }
    const lenField: number = pkt[0] | (pkt[1] << 8);
    const total: number = lenField & 0x7FFF;
    if (total !== pkt.length) {
      return null;
    }
    if (Framing.xor7(pkt) !== pkt[7]) {
      return null;
    }
    return new FramingHeader(
      pkt[6],
      pkt[2] | (pkt[3] << 8),
      pkt[4] | (pkt[5] << 8),
    );
  }
}

export class FramingHeader {
  channel: number;
  sessionId: number;
  videoSeq: number;

  constructor(channel: number, sessionId: number, videoSeq: number) {
    this.channel = channel;
    this.sessionId = sessionId;
    this.videoSeq = videoSeq;
  }
}
