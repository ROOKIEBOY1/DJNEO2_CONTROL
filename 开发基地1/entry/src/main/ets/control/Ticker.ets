/**
 * 自校正定时器（默认 20Hz，即摇杆包发送频率）。
 *
 * 为什么不用 setInterval：它按「上一次回调之后再过 N 毫秒」计时，
 * 每次的执行耗时都会叠进下一次，几分钟就累积成明显偏慢；
 * 而且系统调度抖动会一路累积。这里改成按**绝对时间**排下一次：
 * 目标时刻 = 起点 + n * 周期，每次只补差值，偏差不会被累积。
 *
 * 实测指标通过 jitterMs / lateMs 暴露，界面可以直接显示，
 * 用来判断「是链路慢还是我们发慢了」。
 */

export class Ticker {
  hz: number = 20;
  private running: boolean = false;
  private startAt: number = 0;
  private tickIndex: number = 0;
  private timerId: number = -1;

  /** 已发次数 */
  ticks: number = 0;
  /** 最近一次回调相对理想时刻的偏差（毫秒，正数=晚了） */
  lastLateMs: number = 0;
  /** 运行期间的最大偏差 */
  maxLateMs: number = 0;

  private callback: (() => void) | null = null;

  isRunning(): boolean {
    return this.running;
  }

  start(hz: number, callback: () => void): void {
    if (this.running) {
      return;
    }
    this.hz = hz > 0 ? hz : 20;
    this.callback = callback;
    this.running = true;
    this.ticks = 0;
    this.tickIndex = 0;
    this.maxLateMs = 0;
    this.startAt = Date.now();
    this.scheduleNext(0);
  }

  stop(): void {
    this.running = false;
    if (this.timerId >= 0) {
      clearTimeout(this.timerId);
      this.timerId = -1;
    }
  }

  private periodMs(): number {
    return 1000 / this.hz;
  }

  private scheduleNext(delayMs: number): void {
    this.timerId = setTimeout(() => {
      this.onTimer();
    }, delayMs);
  }

  private onTimer(): void {
    if (!this.running) {
      return;
    }
    const now: number = Date.now();
    const ideal: number = this.startAt + Math.round(this.tickIndex * this.periodMs());
    const late: number = now - ideal;
    this.lastLateMs = late;
    if (late > this.maxLateMs) {
      this.maxLateMs = late;
    }

    // 先回调再排下一次：回调里如果抛异常，也不至于把定时器带停
    try {
      const cb: (() => void) | null = this.callback;
      if (cb !== null) {
        cb();
      }
    } finally {
      this.ticks++;
      this.tickIndex++;
      const nextIdeal: number = this.startAt + Math.round(this.tickIndex * this.periodMs());
      let delay: number = nextIdeal - Date.now();
      if (delay < 0) {
        // 已经落后超过一个周期：重置基准，避免「追赶风暴」把队列打满
        if (delay < -this.periodMs()) {
          this.startAt = Date.now();
          this.tickIndex = 0;
        }
        delay = 0;
      }
      this.scheduleNext(delay);
    }
  }
}
