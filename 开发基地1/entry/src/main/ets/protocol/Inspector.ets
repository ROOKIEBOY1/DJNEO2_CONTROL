/**
 * 收包解析与链路统计
 *
 * 手机是飞机唯一认可的客户端，收发都在本机 —— 这意味着 App 自己就能当抓包器，
 * 不需要 PCAPdroid、不需要 PC 中转、不需要抢 AP 槽位。
 *
 * 这个文件干两件事：
 *  1. parseOuter  —— 把收到的 UDP 报文按 Neo 2 的 16 字节外层头拆开
 *  2. LinkStats   —— 持续聚合成「能一眼看出链路长什么样」的报告
 *
 * 外层头布局（三份独立抓包一致，见 notes/capture-log.md）：
 *   [0:2]   u16le 总长 | 0x8000
 *   [2:4]   u16le session_id，每次重连 +1
 *   [4:6]   u16le 视频包序号，每包 +8；其余通道恒 0
 *   [6]     u8    通道 0x00 握手 / 0x01 飞机DUML / 0x02 视频 / 0x04 App DUML
 *   [7]     u8    XOR(byte0..6)
 *   [8:12] 上行为视频 ACK（两个 u16 相等），下行为视频 seq 对
 *   [12:16] 通常 0
 *
 * 通道 0x01 / 0x04 的 body 前面还有 16 字节常量 + 2 字节帧长，之后才是 DUML 帧。
 */

import { Duml, DumlFrame } from './Duml';

export class OuterPacket {
  length: number = 0;
  session: number = 0;
  seq: number = 0;
  channel: number = 0;
  checksumOk: boolean = false;
  body: Uint8Array = new Uint8Array(0);
  raw: Uint8Array = new Uint8Array(0);

  get channelName(): string {
    return Inspector.channelName(this.channel);
  }
}

export class Inspector {
  static readonly HDR: number = 16;

  static channelName(ch: number): string {
    if (ch === 0x00) {
      return '握手';
    }
    if (ch === 0x01) {
      return '飞机DUML';
    }
    if (ch === 0x02) {
      return '视频';
    }
    if (ch === 0x04) {
      return 'AppDUML';
    }
    return '0x' + ch.toString(16).padStart(2, '0');
  }

  static parseOuter(data: Uint8Array): OuterPacket | null {
    if (data.length < Inspector.HDR) {
      return null;
    }
    const b0: number = data[0] | (data[1] << 8);
    if ((b0 & 0x8000) === 0 || (b0 & 0x7FFF) !== data.length) {
      return null;
    }
    let chk: number = 0;
    for (let i = 0; i < 7; i++) {
      chk = chk ^ data[i];
    }
    const p: OuterPacket = new OuterPacket();
    p.length = b0 & 0x7FFF;
    p.session = data[2] | (data[3] << 8);
    p.seq = data[4] | (data[5] << 8);
    p.channel = data[6];
    p.checksumOk = chk === data[7];
    p.body = data.subarray(Inspector.HDR);
    p.raw = data;
    return p;
  }

  /** DUML 通道的 body 是否带那 16 字节常量前缀。返回帧起点，没有则 0。 */
  static frameOffset(p: OuterPacket): number {
    if (p.body.length < 18) {
      return 0;
    }
    const cst: string = '785d785d00000000785d785d00000000';
    let ok: boolean = true;
    for (let i = 0; i < 16; i++) {
      if (p.body[i] !== parseInt(cst.substr(i * 2, 2), 16)) {
        ok = false;
        break;
      }
    }
    return ok ? 18 : 0;
  }
}

/** 某通道下的一个 DUML 帧类型计数。 */
class FrameKey {
  src: number;
  dst: number;
  cmdSet: number;
  cmdId: number;

  constructor(src: number, dst: number, cmdSet: number, cmdId: number) {
    this.src = src;
    this.dst = dst;
    this.cmdSet = cmdSet;
    this.cmdId = cmdId;
  }

  key(): string {
    return FrameKey.fmt(this.src, this.dst, this.cmdSet, this.cmdId);
  }

  static fmt(src: number, dst: number, cs: number, ci: number): string {
    return hex2(src) + '>' + hex2(dst) + ' ' + hex2(cs) + '/' + hex2(ci);
  }
}

function hex2(v: number): string {
  return v.toString(16).padStart(2, '0');
}

function hexN(b: Uint8Array, max: number): string {
  const n: number = Math.min(b.length, max);
  let s: string = '';
  for (let i = 0; i < n; i++) {
    if (i > 0) {
      s += ' ';
    }
    s += hex2(b[i]);
  }
  return s;
}

/** 每个通道的累计状态。 */
class ChannelStat {
  count: number = 0;
  lens: Map<number, number> = new Map<number, number>();
  sessions: Map<number, number> = new Map<number, number>();
  seqs: number[] = [];
  seqSeen: number = 0;
  seqSteps: Map<number, number> = new Map<number, number>();
  samples: string[] = [];
  frames: Map<string, number> = new Map<string, number>();
  badChecksum: number = 0;
}

/**
 * 链路统计。收多少包都不会拖垮界面 —— 只在 takeReport() 时生成文本，
 * 由调用方按固定间隔（比如 500ms）拉一次。
 */
export class LinkStats {
  private up: Map<number, ChannelStat> = new Map<number, ChannelStat>();
  private dn: Map<number, ChannelStat> = new Map<number, ChannelStat>();
  private total: number = 0;
  private invalid: number = 0;
  private lastSession: number = 0;

  reset(): void {
    this.up.clear();
    this.dn.clear();
    this.total = 0;
    this.invalid = 0;
    this.lastSession = 0;
  }

  get totalCount(): number {
    return this.total;
  }

  /** 最近一次在报文里看到的 session_id，0 表示还没收到过。 */
  get session(): number {
    return this.lastSession;
  }

  private slot(map: Map<number, ChannelStat>, ch: number): ChannelStat {
    let s: ChannelStat | undefined = map.get(ch);
    if (s === undefined) {
      s = new ChannelStat();
      map.set(ch, s);
    }
    return s;
  }

  add(data: Uint8Array, fromDrone: boolean): void {
    this.total++;
    const p: OuterPacket | null = Inspector.parseOuter(data);
    if (p === null) {
      this.invalid++;
      // 头不合法的也留个样本，可能是不同封装
      if (this.invalid <= 3) {
        const s: ChannelStat = this.slot(fromDrone ? this.dn : this.up, -1);
        s.samples.push('len=' + data.length.toString() + ' ' + hexN(data, 32));
      }
      return;
    }
    const map: Map<number, ChannelStat> = fromDrone ? this.dn : this.up;
    const s: ChannelStat = this.slot(map, p.channel);
    s.count++;
    if (!p.checksumOk) {
      s.badChecksum++;
    }
    const lc: number | undefined = s.lens.get(p.length);
    s.lens.set(p.length, (lc === undefined ? 0 : lc) + 1);

    const sc: number | undefined = s.sessions.get(p.session);
    s.sessions.set(p.session, (sc === undefined ? 0 : sc) + 1);
    if (p.session !== 0) {
      this.lastSession = p.session;
    }

    // [4:6] 步进：只对前 200 个采样，避免视频通道的 30fps 把内存吃掉
    if (s.seqs.length < 200) {
      if (s.seqs.length > 0) {
        const step: number = (p.seq - s.seqs[s.seqs.length - 1]) & 0xFFFF;
        if (step !== 0) {
          const c: number | undefined = s.seqSteps.get(step);
          s.seqSteps.set(step, (c === undefined ? 0 : c) + 1);
        }
      }
      s.seqs.push(p.seq);
      s.seqSeen++;
    }

    // 每个通道留 3 个样本，各截前 64 字节
    if (s.samples.length < 3) {
      s.samples.push('len=' + p.length.toString() + ' sess=0x' + p.session.toString(16) +
        ' seq=0x' + p.seq.toString(16) + ' | ' + hexN(p.body, 48));
    }

    // DUML 帧类型统计（只对 DUML 通道，视频通道跳过省算力）
    // 只扫前 600 个包：帧类型分布收敛很快，再扫是纯烧电
    if ((p.channel === 0x01 || p.channel === 0x04) && s.count <= 600) {
      const off: number = Inspector.frameOffset(p);
      if (off > 0) {
        const frames: DumlFrame[] = Duml.scanFrames(p.body.subarray(off));
        for (let i = 0; i < frames.length && i < 64; i++) {
          const f: DumlFrame = frames[i];
          const k: string = FrameKey.fmt(f.sender, f.receiver, f.cmdSet, f.cmdId);
          const c: number | undefined = s.frames.get(k);
          s.frames.set(k, (c === undefined ? 0 : c) + 1);
        }
      }
    }
  }

  /** 生成给界面看、也给粘出去分析的文本报告。 */
  takeReport(): string {
    const lines: string[] = [];
    lines.push('收包 ' + this.total.toString() + '  头非法 ' + this.invalid.toString() +
      (this.lastSession !== 0 ? '  session=0x' + this.lastSession.toString(16) : '  session=未采到'));

    const dump = (map: Map<number, ChannelStat>, dir: string): void => {
      if (map.size === 0) {
        return;
      }
      const keys: number[] = [];
      map.forEach((_v: ChannelStat, k: number) => {
        keys.push(k);
      });
      keys.sort((a: number, b: number) => b - a);
      for (let i = 0; i < keys.length; i++) {
        const ch: number = keys[i];
        const s: ChannelStat | undefined = map.get(ch);
        if (s === undefined) {
          continue;
        }
        const name: string = ch < 0 ? '非法' : Inspector.channelName(ch);
        let topLen: string = '';
        const lens: number[] = [];
        s.lens.forEach((_c: number, L: number) => {
          lens.push(L);
        });
        lens.sort((a: number, b: number) => a - b);
        topLen = lens.length > 0 ? lens[0].toString() : '-';

        let sess: string = '';
        s.sessions.forEach((c: number, v: number) => {
          sess += (sess.length > 0 ? ',' : '') + '0x' + v.toString(16) + 'x' + c.toString();
        });

        let step: string = '恒定';
        if (s.seqSteps.size > 0) {
          let bestStep: number = 0;
          let bestN: number = -1;
          s.seqSteps.forEach((c: number, v: number) => {
            if (c > bestN) {
              bestN = c;
              bestStep = v;
            }
          });
          step = '+' + bestStep.toString();
        }

        lines.push(dir + ' ' + hex2(ch) + ' ' + name + '  n=' + s.count.toString() +
          '  常见长=' + topLen + '  sess=' + (sess.length > 0 ? sess : '-') +
          '  [4:6]=' + step + (s.badChecksum > 0 ? '  校验错=' + s.badChecksum.toString() : ''));

        if (s.frames.size > 0) {
          const fs: string[] = [];
          let shown: number = 0;
          s.frames.forEach((c: number, k: string) => {
            if (shown < 8) {
              fs.push(k + 'x' + c.toString());
              shown++;
            }
          });
          lines.push('     帧: ' + fs.join('  '));
        }
        for (let j = 0; j < s.samples.length; j++) {
          lines.push('     # ' + s.samples[j]);
        }
      }
    };

    dump(this.up, 'UP');
    dump(this.dn, 'DN');

    if (this.total === 0) {
      lines.push('');
      lines.push('一个包都没收到。飞机不会主动跟未授权的客户端说话 ——');
      lines.push('先发一个探测包（下面按钮），回包会落到同一个 socket 上。');
    }
    return lines.join('\n');
  }
}
