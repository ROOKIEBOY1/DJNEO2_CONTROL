/**
 * 黄金测试向量 —— 由 tools/neo2_control/gen_vectors.py 生成，勿手改。
 *
 * 每条都是「一组输入 → 一个完整 UDP 报文的十六进制」。
 * ArkTS 侧用同一组输入重新生成，逐字节比对；任何一位不同即判定移植失败。
 * 生成日期：2026-10-22
 */

export class StickVector {
  name: string;
  roll: number;
  pitch: number;
  throttle: number;
  yaw: number;
  counter: number;
  seq: number;
  sessionId: number;
  hex: string;

  constructor(name: string, roll: number, pitch: number, throttle: number, yaw: number,
              counter: number, seq: number, sessionId: number, hex: string) {
    this.name = name;
    this.roll = roll;
    this.pitch = pitch;
    this.throttle = throttle;
    this.yaw = yaw;
    this.counter = counter;
    this.seq = seq;
    this.sessionId = sessionId;
    this.hex = hex;
  }
}

export class FlyVector {
  name: string;
  param: number;
  seq: number;
  sessionId: number;
  hex: string;

  constructor(name: string, param: number, seq: number, sessionId: number, hex: string) {
    this.name = name;
    this.param = param;
    this.seq = seq;
    this.sessionId = sessionId;
    this.hex = hex;
  }
}

export class PayloadVector {
  name: string;
  roll: number;
  pitch: number;
  throttle: number;
  yaw: number;
  counter: number;
  hex: string;

  constructor(name: string, roll: number, pitch: number, throttle: number, yaw: number,
              counter: number, hex: string) {
    this.name = name;
    this.roll = roll;
    this.pitch = pitch;
    this.throttle = throttle;
    this.yaw = yaw;
    this.counter = counter;
    this.hex = hex;
  }
}

export class Vectors {
  static readonly STICK: StickVector[] = [
    new StickVector('neutral', 0.0, 0.0, 0.0, 0.0, 0x0, 0x0, 0x0, '4b800000000004cf0000000000000000785d785d00000000785d785d000000002900552904c902a9000000010a010d000004200001084000020000065501045608000000000000000082e4'),
    new StickVector('neutral_seeded', 0.0, 0.0, 0.0, 0.0, 0x112233, 0x101, 0x1234, '4b803412000004e90000000000000000785d785d00000000785d785d000000002900552904c902a9010100010a010d000004200001084000020000065501045608332211000000000078a6'),
    new StickVector('forward_half', 0.0, 0.5, 0.0, 0.0, 0x112233, 0x101, 0x1234, '4b803412000004e90000000000000000785d785d00000000785d785d000000002900552904c902a9010100010a010d0000542a00010840000200000655010456083322110000000000942f'),
    new StickVector('throttle_half', 0.0, 0.0, 0.5, 0.0, 0x112233, 0x101, 0x1234, '4b803412000004e90000000000000000785d785d00000000785d785d000000002900552904c902a9010100010a010d000004a052010840000200000655010456083322110000000000d4e4'),
    new StickVector('yaw_half', 0.0, 0.0, 0.0, 0.5, 0x112233, 0x101, 0x1234, '4b803412000004e90000000000000000785d785d00000000785d785d000000002900552904c902a9010100010a010d0000042000950a400002000006550104560833221100000000002dcc'),
    new StickVector('roll_half', 0.5, 0.0, 0.0, 0.0, 0x112233, 0x101, 0x1234, '4b803412000004e90000000000000000785d785d00000000785d785d000000002900552904c902a9010100010a010d004a052000010840000200000655010456083322110000000000c3c0'),
    new StickVector('roll_neg_full', -1.0, 0.0, 0.0, 0.0, 0x112233, 0x101, 0x1234, '4b803412000004e90000000000000000785d785d00000000785d785d000000002900552904c902a9010100010a010d006c012000010840000200000655010456083322110000000000b833'),
    new StickVector('mixed', 0.25, -0.5, 0.75, -0.25, 0x112233, 0x101, 0x1234, '4b803412000004e90000000000000000785d785d00000000785d785d000000002900552904c902a9010100010a010d00a5b4d57bb7064000020000065501045608332211000000000035ba'),
  ];

  static readonly FLY: FlyVector[] = [
    new FlyVector('takeoff', 0x1, 0x7, 0x1234, '53803412000004f10000000000000000785d785d00000000785d785d000000003100553104533be90700005101550e04660203070080032a0194e3fe9102550207000000ffffffff0182000000000000006383'),
    new FlyVector('land', 0x2, 0x7, 0x1234, '53803412000004f10000000000000000785d785d00000000785d785d000000003100553104533be90700005101550e04660203070080032a020fd1fe9102550207000000ffffffff0182000000000000006383'),
    new FlyVector('force_land', 0x1e, 0x7, 0x1234, '53803412000004f10000000000000000785d785d00000000785d785d000000003100553104533be90700005101550e04660203070080032a1ee20bfe9102550207000000ffffffff0182000000000000006383'),
  ];

  static readonly PAYLOAD: PayloadVector[] = [
    new PayloadVector('neutral', 0.0, 0.0, 0.0, 0.0, 0x0, '010d0000042000010840000200000655010456080000000000000000'),
    new PayloadVector('mixed', 0.25, -0.5, 0.75, -0.25, 0x112233, '010d00a5b4d57bb70640000200000655010456083322110000000000'),
  ];
}
