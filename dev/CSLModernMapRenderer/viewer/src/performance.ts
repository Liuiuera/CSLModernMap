// 记录单次预览场景加载的限量性能信息。
/* 每次场景加载只记录一条有限长度追踪，不设置观察器、轮询或逐要素日志。 */
export class PreviewPerformance {
  enabled = false;
  readonly steps: {name: string; startMs: number; durationMs: number; outcome: string}[] = [];
  constructor(readonly origin = performance.now()) {}
  record(name: string, start: number, outcome = 'ok'): void {
    if (this.enabled && this.steps.length < 512)
      this.steps.push({name, startMs: start - this.origin,
        durationMs: performance.now() - start, outcome});
  }
  sync<T>(name: string, action: () => T): T {
    if (!this.enabled) return action();
    const start = performance.now(); let outcome = 'ok';
    try { return action(); }
    catch (error) { outcome = 'error'; throw error; }
    finally { this.record(name, start, outcome); }
  }
  async async<T>(name: string, action: () => Promise<T>): Promise<T> {
    if (!this.enabled) return action();
    const start = performance.now(); let outcome = 'ok';
    try { return await action(); }
    catch (error) { outcome = 'error'; throw error; }
    finally { this.record(name, start, outcome); }
  }
}
