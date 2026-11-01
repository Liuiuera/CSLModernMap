import RoutingWorker from './worker?worker&inline';
import {RoutingError} from './types';
import type {Identity, NavigationPackage, RouteResult, RoutingProfile, Snap, TransitCandidate, TransitPreference, XYZ} from './types';

type Pending = {resolve(value: unknown): void; reject(reason: Error): void; timer: number};

export class RoutingClient {
  private worker: Worker | null = null;
  private readonly pending = new Map<number, Pending>();
  private requestId = 0;
  private ready: Promise<unknown> | null = null;

  constructor(readonly data: NavigationPackage, readonly epoch: number) {}

  private send(type: string, payload: Record<string, unknown>, timeout = 5000): Promise<unknown> {
    const requestId = ++this.requestId;
    const identity: Identity = {requestId, documentEpoch: this.epoch, snapshotId: this.data.snapshotId,
      graphRevision: this.data.graphRevision, costRevision: this.data.costRevision};
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => this.stop(new RoutingError('QUERY_TIMEOUT')), timeout);
      this.pending.set(requestId, {resolve, reject, timer});
      try { this.worker!.postMessage({...identity, type, ...payload}); }
      catch (error) { this.stop(new RoutingError('WORKER_FAILED', String(error))); }
    });
  }

  async initialize(): Promise<void> {
    if (!this.ready) {
      try { this.worker = new RoutingWorker(); }
      catch (error) { throw new RoutingError('WORKER_FAILED', String(error)); }
      this.worker.onerror = event => { event.preventDefault(); this.stop(new RoutingError('WORKER_FAILED', event.message)); };
      this.worker.onmessageerror = () => this.stop(new RoutingError('WORKER_FAILED'));
      this.worker.onmessage = event => {
        const message = event.data as Identity & {status: string; result?: unknown; code?: string; detail?: string};
        const pending = this.pending.get(message.requestId);
        if (!pending) return;
        if (message.documentEpoch !== this.epoch || message.snapshotId !== this.data.snapshotId
            || message.graphRevision !== this.data.graphRevision || message.costRevision !== this.data.costRevision) {
          this.stop(new RoutingError('STALE_QUERY')); return;
        }
        this.pending.delete(message.requestId); window.clearTimeout(pending.timer);
        if (message.status === 'ok') pending.resolve(message.result);
        else pending.reject(new RoutingError(message.code ?? 'WORKER_FAILED', message.detail));
      };
      this.ready = this.send('routingInit', {data: this.data}, 15000);
    }
    await this.ready;
  }

  async snap(position: XYZ, profile: RoutingProfile = 'car-shortest'): Promise<Snap[]> {
    await this.initialize();
    return await this.send('snapQuery', {position, profile}) as Snap[];
  }

  async route(origin: Snap, destination: Snap, profile: RoutingProfile = 'car-shortest', costConfigId?: TransitPreference): Promise<RouteResult> {
    await this.initialize();
    return await this.send('routeQuery', {origin, destination, profile, costConfigId}) as RouteResult;
  }

  cancel(): void {
    if (this.pending.size) this.stop();
  }

  async candidates(origin: Snap, destination: Snap): Promise<TransitCandidate[]> {
    await this.initialize();
    // 一次请求覆盖整组三次查询，沿用 send 的 5 秒总预算。
    return await this.send('transitCandidatesQuery', {origin, destination}) as TransitCandidate[];
  }

  stop(error = new RoutingError('CANCELLED')): void {
    this.worker?.terminate(); this.worker = null; this.ready = null;
    for (const pending of this.pending.values()) {
      window.clearTimeout(pending.timer); pending.reject(error);
    }
    this.pending.clear();
  }
}
