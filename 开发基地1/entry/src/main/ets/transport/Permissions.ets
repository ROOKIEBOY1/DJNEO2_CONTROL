/**
 * 运行期权限核对。
 *
 * 本项目只需要四个「声明即授予」的权限（system_grant），不需要弹窗：
 *   ohos.permission.INTERNET            与飞机通信（UDP 9003）
 *   ohos.permission.GET_NETWORK_INFO    查本机在 192.168.2.x 上的地址
 *   ohos.permission.GET_WIFI_INFO       读当前连的 SSID、WLAN 是否打开
 *   ohos.permission.SET_WIFI_INFO       可选：由 App 直接加入飞机热点（当前用人手连接，先不用）
 *
 * 这四个都属于 system_grant：只要在 module.json5 里声明就生效，
 * 调用 requestPermissionsFromUser 不会弹框，只会白等一次异步——所以这里只做「核对」。
 *
 * 没有声明 → 用户点不出来，只能在配置里补，所以提示文案要区分这两种情况。
 */

import { abilityAccessCtrl, bundleManager, Permissions as Perm } from '@kit.AbilityKit';

export class PermissionIssue {
  name: string;
  hint: string;

  constructor(name: string, hint: string) {
    this.name = name;
    this.hint = hint;
  }
}

export class Permissions {
  static readonly INTERNET: Perm = 'ohos.permission.INTERNET';
  static readonly NET_INFO: Perm = 'ohos.permission.GET_NETWORK_INFO';
  static readonly WIFI_INFO: Perm = 'ohos.permission.GET_WIFI_INFO';

  private static readonly REQUIRED: Perm[] = [
    Permissions.INTERNET,
    Permissions.NET_INFO,
    Permissions.WIFI_INFO,
  ];

  /** 返回缺失的权限（空数组 = 全部可用）。 */
  static missing(): PermissionIssue[] {
    const out: PermissionIssue[] = [];
    const at: abilityAccessCtrl.AtManager = abilityAccessCtrl.createAtManager();
    const tokenId: number = Permissions.selfTokenId();
    for (let i = 0; i < Permissions.REQUIRED.length; i++) {
      const p: Perm = Permissions.REQUIRED[i];
      let granted: boolean = false;
      if (tokenId >= 0) {
        try {
          granted = at.checkAccessTokenSync(tokenId, p) === abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED;
        } catch (e) {
          granted = false;
        }
      }
      if (!granted) {
        out.push(new PermissionIssue(p, Permissions.hintOf(p)));
      }
    }
    return out;
  }

  static allGranted(): boolean {
    return Permissions.missing().length === 0;
  }

  private static hintOf(p: string): string {
    if (p === Permissions.INTERNET) {
      return '网络权限：在 entry/src/main/module.json5 的 requestPermissions 中声明后重新安装';
    }
    if (p === Permissions.WIFI_INFO) {
      return 'Wi-Fi 信息权限：同上，需在 module.json5 声明';
    }
    if (p === Permissions.NET_INFO) {
      return '网络信息权限：同上，需在 module.json5 声明';
    }
    return p;
  }

  private static selfTokenId(): number {
    try {
      const info: bundleManager.BundleInfo = bundleManager.getBundleInfoForSelfSync(
        bundleManager.BundleFlag.GET_BUNDLE_INFO_WITH_APPLICATION);
      return info.appInfo.accessTokenId;
    } catch (e) {
      return -1;
    }
  }
}
