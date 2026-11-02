/**
 * 会话探测
 *
 * 问题：飞机不会主动跟未授权的客户端说话。谁的 socket 先发一个合法的包，
 * 回包才会落到那个 socket 上。抓到 session_id 也靠这个。
 *
 * 这个文件按「从最保守到最激进」的顺序发几个候选包，每一步之后看收包计数有没有涨。
 * 涨了就是这一步打通了，后面的不用再发。
 *
 * 安全性：前两步是纯握手探测（通道 0x00，空体），第三、四步是**中位摇杆**
 * ——四个通道全部 1024（中位），含义是"不动"。飞机在地面上不会因此起飞。
 * 全程不发任何起飞/降落/模式切换命令。
 *
 * 没有真机抓包时，这四步的结果本身就是最有价值的信息：
 * 哪一步开始有回包，就说明飞机认哪种封装。
 */

import { Framing } from '../protocol/Framing';
import { Stick } from '../protocol/Stick';
import { Duml } from '../protocol/Duml';

export class ProbeResult {
  step: string = '';
  sent: number = 0;
  recvBefore: number = 0;
  recvAfter: number = 0;
  note: string = '';

  get gained(): number {
    return this.recvAfter - this.recvBefore;
  }

  get accepted(): boolean {
    return this.gained > 0;
  }
}

export class Prober {
  /**
   * @param send 发一个完整 UDP 报文
   * @param recvCount 读当前累计收包数
   * @param log 每步进展回调，给界面刷
   * @param session 已采到的 session；0 表示未知
   */
  static async run(send: (pkt: Uint8Array) => Promise<void>,
                   recvCount: () => number,
                   log: (line: string) => void,
                   session: number = 0): Promise<ProbeResult[]> {
    const results: ProbeResult[] = [];

    const tick = async (ms: number): Promise<void> => {
      await new Promise<void>((resolve: () => void) => {
        setTimeout(() => {
          resolve();
        }, ms);
      });
    };

    const step = async (name: string, build: () => Uint8Array, times: number,
                        gapMs: number, waitMs: number): Promise<boolean> => {
      const r: ProbeResult = new ProbeResult();
      r.step = name;
      r.recvBefore = recvCount();
      log('▸ ' + name + ' …');
      try {
        for (let i = 0; i < times; i++) {
          const pkt: Uint8Array = build();
          await send(pkt);
          r.sent += pkt.length;
          await tick(gapMs);
        }
      } catch (e) {
        r.note = '发送失败：' + (e as Error).message;
        r.recvAfter = recvCount();
        results.push(r);
        log('  ✗ ' + r.note);
        return false;
      }
      await tick(waitMs);
      r.recvAfter = recvCount();
      results.push(r);
      if (r.accepted) {
        log('  ✓ 有回包 +' + r.gained.toString() + ' 个');
        return true;
      }
      log('  · 无回包');
      return false;
    };

    // 1) 最小握手：只有 16 字节外层头，通道 0x00，空体
    const hello16 = (sess: number): Uint8Array => {
      return Framing.buildHeader(0x00, 0, sess);
    };
    if (await step('空握手 ch=0x00 len=16 session=0', () => hello16(0), 3, 120, 1200)) {
      return results;
    }

    // 2) 握手带 2 字节体（很多 DJI 固件在这里放一个版本号/类型）
    //    注意：buildHeader 只出 16 字节头，body 要自己拼，否则长度字段与实际不符
    const hello18 = (): Uint8Array => {
      const h: Uint8Array = Framing.buildHeader(0x00, 2, 0);
      const body: Uint8Array = new Uint8Array(2);
      return Duml.concat(h, body);
    };
    if (await step('握手 ch=0x00 len=18 body=0000', hello18, 3, 120, 1200)) {
      return results;
    }

    // 3) 已知 session 时，直接发一个中位摇杆 —— 最接近"真实客户端"的包
    if (session !== 0) {
      if (await step('中位摇杆（session=0x' + session.toString(16) + '）',
        () => Stick.packet(0, 0, 0, 0, 0, 1, session), 5, 60, 1200)) {
        return results;
      }
      // 4) 中位摇杆 + session=0，摸一下飞机是否校验 session
      if (await step('中位摇杆（session=0）',
        () => Stick.packet(0, 0, 0, 0, 0, 1, 0), 5, 60, 1200)) {
        return results;
      }
    }

    // 5) 最后兜底：把 session 猜成 1（有些固件首次会话就是 1）
    await step('中位摇杆（session=1）',
      () => Stick.packet(0, 0, 0, 0, 0, 1, 1), 5, 60, 1200);

    return results;
  }

  /** 把探测结果整理成能粘出去的短报告。 */
  static summary(results: ProbeResult[]): string {
    if (results.length === 0) {
      return '探测未执行';
    }
    const lines: string[] = [];
    for (let i = 0; i < results.length; i++) {
      const r: ProbeResult = results[i];
      lines.push((r.accepted ? '✓ ' : '· ') + r.step + '  → 回包 +' + r.gained.toString() +
        (r.note.length > 0 ? '  ' + r.note : ''));
    }
    let hit: number = -1;
    for (let i = 0; i < results.length; i++) {
      if (results[i].accepted) {
        hit = i;
        break;
      }
    }
    lines.push('');
    if (hit >= 0) {
      lines.push('结论：飞机从「' + results[hit].step + '」开始回包。');
      lines.push('下一步看体检报告里的 session / [4:6] / 通道分布，就能定命令封装。');
    } else {
      lines.push('结论：四步全无回包。说明飞机要求更完整的握手（设备 ID / 配对口令一类），');
      lines.push('或者必须与 DJI Fly 同时在线。此时要用「手机抓包」方案拿那一包握手，');
      lines.push('而不是继续猜。抓包脚本见 tools/neo2_control/。');
    }
    return lines.join('\n');
  }
}
