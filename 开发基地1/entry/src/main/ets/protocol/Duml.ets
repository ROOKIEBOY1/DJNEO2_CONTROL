/**
 * DUML v1 帧编解码（DJI Neo 2）
 *
 * 帧布局（与 tools/neo2_control/neo2_stick.py 逐字节一致）：
 *   55 | len_lo | (ver<<2)|len_hi | crc8(hdr[0..2]) | src | dst |
 *   seq:u16le | flags | cmdSet | cmdId | payload | crc16:u16le
 *
 * 参数来自 Neo 2 实机抓包：CRC8 多项式 0x8C / 初值 0x77，
 * CRC16 反射多项式 0x8408 / 初值 0x3692，协议版本 1。
 *
 * 纯逻辑层：不依赖 UI 与系统 API，可被 Node 侧脚本逐字节校验。
 */

export class DumlFrame {
  sender: number;
  receiver: number;
  seq: number;
  flags: number;
  cmdSet: number;
  cmdId: number;
  payload: Uint8Array;

  constructor(sender: number, receiver: number, seq: number, flags: number,
              cmdSet: number, cmdId: number, payload: Uint8Array = new Uint8Array(0)) {
    this.sender = sender;
    this.receiver = receiver;
    this.seq = seq;
    this.flags = flags;
    this.cmdSet = cmdSet;
    this.cmdId = cmdId;
    this.payload = payload;
  }

  opcodeKey(): number {
    return (this.cmdSet << 8) | this.cmdId;
  }
}

export class DumlDecoded {
  frame: DumlFrame;
  consumed: number;

  constructor(frame: DumlFrame, consumed: number) {
    this.frame = frame;
    this.consumed = consumed;
  }
}

export class Duml {
  /** 应用侧固定作为发送方：id 0, type 2 → (0<<5)|2 */
  static readonly SENDER_APP: number = 0x02;
  /** 摇杆接收方（Neo 一代实机与 Neo 2 抓包一致） */
  static readonly RX_RC: number = 0xA9;
  /** 飞控子系统 */
  static readonly RX_FLYC: number = 0x03;
  /** 隧道外层发送 / 接收 */
  static readonly TUNNEL_SRC: number = 0x3B;
  static readonly TUNNEL_DST: number = 0xE9;

  /** cfg 字节的位含义：低 3 位 encrypt，bit5-6 ack，bit7 packType */
  static readonly FLAG_NONE: number = 0x00;
  static readonly FLAG_PACK_TYPE_1: number = 0x80;
  static readonly FLAG_RESPONSE: number = 0xC0;
  static readonly FLAG_ACK_80: number = 0x80;

  static encode(f: DumlFrame): Uint8Array {
    const total: number = 13 + f.payload.length;
    const b: Uint8Array = new Uint8Array(total);
    b[0] = 0x55;
    b[1] = total & 0xFF;
    b[2] = (1 << 2) | ((total >> 8) & 0x03); // 版本 1 + 长度高位
    b[3] = Duml.crc8(b, 0, 3);
    b[4] = f.sender;
    b[5] = f.receiver;
    b[6] = f.seq & 0xFF;
    b[7] = (f.seq >> 8) & 0xFF;
    b[8] = f.flags;
    b[9] = f.cmdSet;
    b[10] = f.cmdId;
    for (let i = 0; i < f.payload.length; i++) {
      b[11 + i] = f.payload[i];
    }
    const c: number = Duml.crc16(b, 0, total - 2);
    b[total - 2] = c & 0xFF;
    b[total - 1] = (c >> 8) & 0xFF;
    return b;
  }

  static decode(data: Uint8Array, offset: number = 0): DumlDecoded | null {
    if (data.length - offset < 13) {
      return null;
    }
    if (data[offset] !== 0x55) {
      return null;
    }
    const total: number = data[offset + 1] | ((data[offset + 2] & 0x03) << 8);
    if ((data[offset + 2] >> 2) !== 1) {
      return null;
    }
    if (total < 13 || data.length - offset < total) {
      return null;
    }
    if (Duml.crc8(data, offset, 3) !== data[offset + 3]) {
      return null;
    }
    const got: number = data[offset + total - 2] | (data[offset + total - 1] << 8);
    if (Duml.crc16(data, offset, total - 2) !== got) {
      return null;
    }
    const payload: Uint8Array = new Uint8Array(total - 13);
    for (let i = 0; i < payload.length; i++) {
      payload[i] = data[offset + 11 + i];
    }
    const frame = new DumlFrame(
      data[offset + 4],
      data[offset + 5],
      data[offset + 6] | (data[offset + 7] << 8),
      data[offset + 8],
      data[offset + 9],
      data[offset + 10],
      payload,
    );
    return new DumlDecoded(frame, total);
  }

  /**
   * 在一整段 UDP 报文里扫描所有 CRC 合法的 DUML 帧。
   * 上行报文里可能一包多帧，先用它把帧拆出来，比解析外层头更不容易错。
   */
  static scanFrames(datagram: Uint8Array): DumlFrame[] {
    const out: DumlFrame[] = [];
    let i: number = 0;
    while (i < datagram.length) {
      if (datagram[i] === 0x55) {
        const d: DumlDecoded | null = Duml.decode(datagram, i);
        if (d !== null) {
          out.push(d.frame);
          i += d.consumed;
          continue;
        }
      }
      i++;
    }
    return out;
  }

  static crc8(data: Uint8Array, offset: number = 0, length: number = -1): number {
    const len: number = length < 0 ? data.length - offset : length;
    let c: number = 0x77;
    for (let i = offset; i < offset + len; i++) {
      c = c ^ data[i];
      for (let k = 0; k < 8; k++) {
        c = (c & 1) !== 0 ? (c >> 1) ^ 0x8C : c >> 1;
      }
      c = c & 0xFF;
    }
    return c & 0xFF;
  }

  static crc16(data: Uint8Array, offset: number = 0, length: number = -1): number {
    const len: number = length < 0 ? data.length - offset : length;
    let c: number = 0x3692;
    for (let i = offset; i < offset + len; i++) {
      c = c ^ data[i];
      for (let k = 0; k < 8; k++) {
        c = (c & 1) !== 0 ? (c >> 1) ^ 0x8408 : c >> 1;
      }
      c = c & 0xFFFF;
    }
    return c & 0xFFFF;
  }

  // ---------- 工具 ----------

  static hex(b: Uint8Array): string {
    let s: string = '';
    for (let i = 0; i < b.length; i++) {
      if (i > 0) {
        s += ' ';
      }
      const v: number = b[i];
      s += (v < 16 ? '0' : '') + v.toString(16).toUpperCase();
    }
    return s;
  }

  static bytes(...v: number[]): Uint8Array {
    const out: Uint8Array = new Uint8Array(v.length);
    for (let i = 0; i < v.length; i++) {
      out[i] = v[i] & 0xFF;
    }
    return out;
  }

  static fromHex(hex: string): Uint8Array {
    const clean: string = hex.replace(/\s+/g, '');
    const n: number = Math.floor(clean.length / 2);
    const out: Uint8Array = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      out[i] = parseInt(clean.substr(i * 2, 2), 16);
    }
    return out;
  }

  /** Uint8Array → ArrayBuffer。socket API 只吃 ArrayBuffer。 */
  static toBuffer(b: Uint8Array): ArrayBuffer {
    const out: ArrayBuffer = new ArrayBuffer(b.length);
    const v: Uint8Array = new Uint8Array(out);
    for (let i = 0; i < b.length; i++) {
      v[i] = b[i];
    }
    return out;
  }

  static fromBuffer(buf: ArrayBuffer): Uint8Array {
    return new Uint8Array(buf);
  }

  static concat(...parts: Uint8Array[]): Uint8Array {
    let total: number = 0;
    for (let i = 0; i < parts.length; i++) {
      total += parts[i].length;
    }
    const out: Uint8Array = new Uint8Array(total);
    let at: number = 0;
    for (let i = 0; i < parts.length; i++) {
      const p: Uint8Array = parts[i];
      for (let j = 0; j < p.length; j++) {
        out[at + j] = p[j];
      }
      at += p.length;
    }
    return out;
  }

  static equals(a: Uint8Array, b: Uint8Array): boolean {
    if (a.length !== b.length) {
      return false;
    }
    for (let i = 0; i < a.length; i++) {
      if (a[i] !== b[i]) {
        return false;
      }
    }
    return true;
  }
}
