/**
 * 飞机 Wi-Fi 连接状态。
 *
 * 当前策略：**由用户在系统设置里手动连一次**飞机热点，App 只负责「确认连对了」
 * —— 不调 addCandidateConfig，也就不需要 SET_WIFI_INFO 这种敏感权限。
 *
 * 判据不是「WLAN 已打开」，而是「本机拿到了 192.168.2.x 的地址」：
 * 前者可能连着家里的路由，后者才说明真的在飞机那张网上。
 *
 * 另外提供那张网卡的 NetHandle，供 UDP socket 做 socket 级绑定
 * （只让控制流量走飞机网络，不影响 App 其它请求）。
 */

import { wifiManager } from '@kit.ConnectivityKit';
import { connection } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';

export class DroneLink {
  ssid: string = '';
  ip: string = '';
  net: connection.NetHandle | null = null;

  ok(): boolean {
    return this.ip.length > 0;
  }

  summary(): string {
    if (!this.ok()) {
      return this.ssid.length > 0 ? ('已连「' + this.ssid + '」，但不是飞机网络') : '未连接飞机';
    }
    return '已连接「' + this.ssid + '」  ' + this.ip;
  }
}

export class WifiLink {
  /** 飞机热点固定网段。Neo 2 的 AP 网关是 192.168.2.1。 */
  static readonly SUBNET: string = '192.168.2.';

  static isWifiOn(): boolean {
    try {
      return wifiManager.isWifiActive();
    } catch (e) {
      const err = e as BusinessError;
      console.error('isWifiActive 失败: ' + err.message);
      return false;
    }
  }

  /** 当前连接的 SSID；未连接返回空串。 */
  static async currentSsid(): Promise<string> {
    try {
      const info: wifiManager.WifiLinkedInfo = await wifiManager.getLinkedInfo();
      return info.ssid === undefined ? '' : info.ssid;
    } catch (e) {
      const err = e as BusinessError;
      console.error('getLinkedInfo 失败: ' + err.message);
      return '';
    }
  }

  /**
   * 在系统网络表里找「本机地址落在 SUBNET 上」的那张网卡。
   * 找到即返回；找不到返回空 DroneLink。
   */
  static async probe(): Promise<DroneLink> {
    const out: DroneLink = new DroneLink();
    out.ssid = await WifiLink.currentSsid();
    try {
      const nets: connection.NetHandle[] = await connection.getAllNets();
      for (let i = 0; i < nets.length; i++) {
        const net: connection.NetHandle = nets[i];
        try {
          const props: connection.ConnectionProperties = await connection.getConnectionProperties(net);
          for (let j = 0; j < props.linkAddresses.length; j++) {
            const addr: string = props.linkAddresses[j].address.address;
            if (addr.indexOf(WifiLink.SUBNET) === 0) {
              out.ip = addr;
              out.net = net;
              return out;
            }
          }
        } catch (e) {
          // 单张网卡取属性失败就跳过，不影响其它网卡
        }
      }
    } catch (e) {
      const err = e as BusinessError;
      console.error('getAllNets 失败: ' + err.message);
    }
    return out;
  }

  /**
   * 轮询直到连上飞机网络，或超时。
   * @param timeoutMs 默认 8 秒——手动连接场景下用户已经连好，不需要等 DFS
   * @param onTick 每秒回调一次，给界面刷进度用
   */
  static async waitUntilLinked(timeoutMs: number = 8000,
                               onTick: (sec: number, ssid: string) => void): Promise<DroneLink> {
    const started: number = Date.now();
    let last: DroneLink = new DroneLink();
    while (Date.now() - started < timeoutMs) {
      last = await WifiLink.probe();
      if (last.ok()) {
        return last;
      }
      const sec: number = Math.round((Date.now() - started) / 1000);
      onTick(sec, last.ssid);
      await WifiLink.sleep(500);
    }
    return last;
  }

  private static sleep(ms: number): Promise<void> {
    return new Promise<void>((resolve: () => void) => {
      setTimeout(() => {
        resolve();
      }, ms);
    });
  }

  /**
   * 扫描附近像飞机的热点。
   *
   * 这一条不为了自动连接——HarmonyOS 不给普通应用连 WiFi 的权限
   * （SET_WIFI_INFO 是 system_basic 级），连还是要在系统设置里点一次。
   * 但「飞机到底在不在、SSID 叫什么」能在 App 里看到，省得瞎找。
   */
  static async scanDrones(): Promise<string[]> {
    try {
      await wifiManager.scan();
      await WifiLink.sleep(1200);
      const list: wifiManager.WifiScanInfo[] = wifiManager.getScanInfoList();
      const out: string[] = [];
      for (let i = 0; i < list.length; i++) {
        const info: wifiManager.WifiScanInfo = list[i];
        const ssid: string = info.ssid === undefined ? '' : info.ssid;
        if (ssid.length === 0) {
          continue;
        }
        const up: string = ssid.toUpperCase();
        if (up.indexOf('DJI') >= 0 || up.indexOf('NEO') >= 0 ||
            up.indexOf('MAVIC') >= 0 || up.indexOf('FLIP') >= 0) {
          out.push(ssid + '  ' + info.rssi.toString() + 'dBm');
        }
      }
      return out;
    } catch (e) {
      const err = e as BusinessError;
      console.error('scanDrones 失败: ' + err.message);
      return [];
    }
  }

  /** 所有可见 SSID，扫描无果时给用户看现场用。 */
  static async scanAll(): Promise<string[]> {
    try {
      await wifiManager.scan();
      await WifiLink.sleep(1200);
      const list: wifiManager.WifiScanInfo[] = wifiManager.getScanInfoList();
      const out: string[] = [];
      for (let i = 0; i < list.length; i++) {
        const ssid: string = list[i].ssid;
        if (ssid !== undefined && ssid.length > 0) {
          out.push(ssid);
        }
      }
      return out;
    } catch (e) {
      return [];
    }
  }
}
