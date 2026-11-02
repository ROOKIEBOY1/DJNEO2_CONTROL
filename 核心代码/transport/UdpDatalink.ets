/**
 * UDP 通道（飞机 192.168.2.1:9003）
 *
 * 只做三件事：开、发、收。不含任何协议解析——那是 protocol/ 层的事。
 *
 * 两个必须注意的点（来自同机实践）：
 *  1. **socket 级绑定**：连飞机热点时手机通常还挂着蜂窝，默认路由会把包发错网卡。
 *     要用 NetHandle.bindSocket(socket) 把这一条 socket 绑到飞机网卡上，
 *     而不是 connection.setAppNet()（那个会把整个 App 的流量都塞进没有外网的飞机热点）。
 *  2. **绑定与 bind 的先后顺序**在不同版本上表现不同，这里提供 reverseOrder 开关；
 *     若发送后完全收不到上行，先翻转它再试。
 */

import { socket, connection } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { Duml } from '../protocol/Duml';
import { Framing } from '../protocol/Framing';

export class UdpDatalink {
  private udp: socket.UDPSocket | null = null;
  private opened: boolean = false;
  sentCount: number = 0;
  recvCount: number = 0;

  onDatagram: ((data: Uint8Array) => void) | null = null;
  onError: ((msg: string) => void) | null = null;

  isOpen(): boolean {
    return this.opened;
  }

  /**
   * @param net 飞机网卡；null 表示不绑定（只在手机没有蜂窝时可用）
   * @param localPort 本地端口，0 = 系统分配
   */
  async open(net: connection.NetHandle | null, localPort: number = 0): Promise<void> {
    if (this.opened) {
      return;
    }
    const u: socket.UDPSocket = socket.constructUDPSocketInstance();
    let stage: string = '绑定本机 UDP';
    try {
      // HarmonyOS 要求 UDP socket 先完成本地 bind，才能绑定到指定网络。
      await u.bind({ address: '0.0.0.0', port: localPort });
      if (net !== null) {
        stage = '绑定飞机网络';
        await net.bindSocket(u);
      }
      stage = '注册 UDP 回调';
      u.on('message', (info: socket.SocketMessageInfo) => {
        this.recvCount++;
        if (this.onDatagram !== null) {
          this.onDatagram(new Uint8Array(info.message));
        }
      });
      u.on('error', (err: BusinessError) => {
        if (this.onError !== null) {
          this.onError('UDP 错误 ' + err.code.toString() + ' ' + err.message);
        }
      });
      this.udp = u;
      this.opened = true;
    } catch (e) {
      const err = e as BusinessError;
      try {
        await u.close();
      } catch (ignored) {
        // 关不掉不影响报错
      }
      const code: string = err.code === undefined ? '' : ' code=' + err.code.toString();
      const message: string = err.message.length > 0 ? ' ' + err.message : '';
      throw new Error('打开 UDP 通道失败[' + stage + ']：' + code + message);
    }
  }

  /** 发一个已经构造好的完整报文。 */
  async send(packet: Uint8Array): Promise<void> {
    const u: socket.UDPSocket | null = this.udp;
    if (u === null) {
      throw new Error('UDP 通道未打开');
    }
    const opt: socket.UDPSendOptions = {
      data: Duml.toBuffer(packet),
      address: {
        address: Framing.DRONE_IP,
        port: Framing.UDP_PORT,
        family: 1,
      },
    };
    try {
      await u.send(opt);
      this.sentCount++;
    } catch (e) {
      const err = e as BusinessError;
      throw new Error('发送失败：' + err.code.toString() + ' ' + err.message);
    }
  }

  /** 发到任意地址（M2 阶段用 PC 监听脚本验证时走这条）。 */
  async sendTo(packet: Uint8Array, host: string, port: number): Promise<void> {
    const u: socket.UDPSocket | null = this.udp;
    if (u === null) {
      throw new Error('UDP 通道未打开');
    }
    const opt: socket.UDPSendOptions = {
      data: Duml.toBuffer(packet),
      address: {
        address: host,
        port: port,
        family: 1,
      },
    };
    try {
      await u.send(opt);
      this.sentCount++;
    } catch (e) {
      const err = e as BusinessError;
      throw new Error('发送失败：' + err.code.toString() + ' ' + err.message);
    }
  }

  async close(): Promise<void> {
    const u: socket.UDPSocket | null = this.udp;
    this.udp = null;
    this.opened = false;
    if (u === null) {
      return;
    }
    try {
      u.off('message');
      u.off('error');
      await u.close();
    } catch (e) {
      // 关不掉不影响流程
    }
  }
}
