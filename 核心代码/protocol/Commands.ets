/**
 * 起飞 / 降落 / 强制降落
 *
 * 结构：内层 DUML(0x02→0x03, 0x03/0x2a, payload 1 字节参数, cfg=0x80)
 *       外面再套一层隧道 DUML(0x3b→0xe9, 0x51/0x01)，隧道 payload = 内层帧 + 22 字节 trailer。
 *
 * trailer 的 byte[5:6] 要回填 seq（与内层同一序号），其余为固定值。
 * 这套结构在 Neo 2 抓包里逐字节复现；起降是否被接受仍待真机验证。
 */

import { Duml, DumlFrame } from './Duml';
import { Framing } from './Framing';

export class FlyCommand {
  static readonly TAKEOFF: number = 0x01;
  static readonly LAND: number = 0x02;
  static readonly FORCE_LAND: number = 0x1E;

  static readonly CMD_SET: number = 0x03;
  static readonly CMD_ID: number = 0x2A;
  static readonly TUNNEL_SET: number = 0x51;
  static readonly TUNNEL_ID: number = 0x01;

  static label(param: number): string {
    if (param === FlyCommand.TAKEOFF) {
      return '起飞';
    }
    if (param === FlyCommand.LAND) {
      return '降落';
    }
    if (param === FlyCommand.FORCE_LAND) {
      return '强制降落';
    }
    return '未知(0x' + param.toString(16) + ')';
  }

  /** 22 字节隧道 trailer；byte[5:6] 回填 seq。 */
  private static trailer(seq: number): Uint8Array {
    return Duml.bytes(
      0xFE, 0x91, 0x02, 0x55, 0x02,
      seq & 0xFF, (seq >> 8) & 0xFF, 0x00, 0x00,
      0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x82,
      0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    );
  }

  /** 内层飞控命令帧（未套隧道）。 */
  static innerFrame(param: number, seq: number): Uint8Array {
    const payload: Uint8Array = Duml.bytes(param);
    const f: DumlFrame = new DumlFrame(Duml.SENDER_APP, Duml.RX_FLYC, seq,
      Duml.FLAG_PACK_TYPE_1, FlyCommand.CMD_SET, FlyCommand.CMD_ID, payload);
    return Duml.encode(f);
  }

  /** 套隧道后的外层帧。 */
  static tunneledFrame(param: number, seq: number): Uint8Array {
    const inner: Uint8Array = FlyCommand.innerFrame(param, seq);
    const body: Uint8Array = Duml.concat(inner, FlyCommand.trailer(seq));
    const f: DumlFrame = new DumlFrame(Duml.TUNNEL_SRC, Duml.TUNNEL_DST, seq,
      Duml.FLAG_NONE, FlyCommand.TUNNEL_SET, FlyCommand.TUNNEL_ID, body);
    return Duml.encode(f);
  }

  /** 完整 UDP 报文。 */
  static packet(param: number, seq: number, sessionId: number): Uint8Array {
    return Framing.buildDumlAppPacket(FlyCommand.tunneledFrame(param, seq), sessionId);
  }
}
