// 已冻结作品的绘制与观察；相机和窗口变化不进入作品数据。
import type {Map as MapLibreMap, CameraOptions} from 'maplibre-gl';
import {i18n} from './i18n';
import './artwork-view.css';

type Point = [number, number];
type Box = [number, number, number, number];
type Primitive = {id: string; bbox: Box} & (
  {kind: 'circle'; center: Point; diameter: number; fill: string; stroke: string; strokeWidth: number} |
  {kind: 'polyline'; points: Point[]; width: number; stroke: string; lineCap?: 'round' | 'butt'} |
  {kind: 'image'; resource: string; textRun?: {text: string}});
export type ArtworkPayload = {
  schemaVersion: number; documentEpoch: number; revision: number; styleId: string;
  canvas: {width: number; height: number; worldToArtwork: [number, number, number, number, number, number]};
  job: {kind: string; canvas: {width: number; height: number}; derive_params: {rotation: number; world_bounds: Box}};
  background: string; objects: Primitive[];
  previewBase?: {theme: unknown; layers: string[]; requiredSources: string[];
    layoutRevision: number; sceneId?: string; revision?: number};
  resources: Record<string, {id: string; width: number; height: number; url: string}>;
};
type Observation = {scale: number; x: number; y: number};
export type ArtworkDelivery = ArtworkPayload | (Omit<ArtworkPayload, 'objects'> & {
  baseRevision: number; objects?: Primitive[];
});
export type ArtworkTool = 'select' | 'pick-style' | 'pan';
export type ArtworkObserveAction = 'fit' | 'actual' | 'zoom-in' | 'zoom-out';
export type ArtworkPick = {artworkTool: ArtworkTool; documentEpoch: number; artworkRevision: number};
const NS = 'http://www.w3.org/2000/svg';
const VIEWPORT_SETTLE_MS = 500;
function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>) {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  return element;
}
function numbers(value: unknown, count: number): asserts value is number[] {
  if (!Array.isArray(value) || value.length !== count || value.some(v => typeof v !== 'number' || !Number.isFinite(v)))
    throw new Error('Invalid artwork coordinates');
}
function color(value: unknown): void {
  if (typeof value !== 'string' || !CSS.supports('color', value)) throw new Error('Invalid artwork color');
}
function validate(value: ArtworkPayload, previous?: ArtworkPayload | null): void {
  if (value.schemaVersion !== 1 || !Number.isSafeInteger(value.documentEpoch) || value.documentEpoch < 0 ||
      !Number.isSafeInteger(value.revision) || value.revision < 0) throw new Error('Invalid artwork identity');
  const {width, height, worldToArtwork} = value.canvas;
  if (![width, height].every(v => Number.isInteger(v) && v > 0 && v <= 32768) ||
      width !== value.job.canvas.width || height !== value.job.canvas.height ||
      !['normal', 'transit'].includes(value.job.kind) || !value.styleId) throw new Error('Invalid artwork canvas');
  numbers(worldToArtwork, 6); numbers(value.job.derive_params.world_bounds, 4);
  color(value.background);
  if (value.previewBase && (value.previewBase.layoutRevision !== value.revision ||
      !Array.isArray(value.previewBase.layers) || !value.previewBase.layers.every(layer => typeof layer === 'string') ||
      !Array.isArray(value.previewBase.requiredSources) || !value.previewBase.requiredSources.every(source => typeof source === 'string')))
    throw new Error('Invalid artwork base recipe');
  if (Math.abs(worldToArtwork[0]*worldToArtwork[3]-worldToArtwork[1]*worldToArtwork[2]) < 1e-15 ||
      ![0, 90, 180, 270].includes(value.job.derive_params.rotation)) throw new Error('Invalid artwork transform');
  if (previous && value.objects === previous.objects && value.resources === previous.resources) return;
  const ids = new Set<string>();
  for (const item of value.objects) {
    if (!item.id || ids.has(item.id)) throw new Error('Duplicate artwork object');
    ids.add(item.id); numbers(item.bbox, 4);
    if (item.bbox[2] <= item.bbox[0] || item.bbox[3] <= item.bbox[1]) throw new Error('Empty artwork box');
    if (item.kind === 'circle') {
      numbers(item.center, 2); numbers([item.diameter, item.strokeWidth], 2);
      color(item.fill); color(item.stroke);
      if (item.diameter <= 0 || item.strokeWidth < 0 || item.strokeWidth > item.diameter/2) throw new Error('Invalid circle');
    } else if (item.kind === 'polyline') {
      numbers([item.width], 1);
      color(item.stroke);
      if (item.width <= 0 || item.points.length < 2) throw new Error('Invalid artwork line');
      if (item.lineCap !== undefined && !['round', 'butt'].includes(item.lineCap)) throw new Error('Invalid artwork line cap');
      item.points.forEach(point => numbers(point, 2));
    } else if (item.kind !== 'image' || !value.resources[item.resource]) throw new Error('Missing artwork resource');
  }
}

export class ArtworkView {
  private readonly root = document.createElement('section');
  private readonly surface = svg('svg', {width: '100%', height: '100%'});
  private readonly content = svg('g', {});
  private readonly renderMask = document.createElement('div');
  private readonly renderStatus = document.createElement('span');
  private readonly observer: ResizeObserver;
  private payload: ArtworkPayload | null = null;
  private expected: Point | null = null;
  private generation = 0;
  private reception = new AbortController();
  private observation: Observation = {scale: 1, x: 0, y: 0};
  private appliedObservation = this.observation;
  private settleTimer = 0;
  private viewport: Point = [0, 0];
  private previousCamera: (CameraOptions & {padding: ReturnType<MapLibreMap['getPadding']>}) | null = null;
  private previousMaxZoom = 24;
  private previousMinZoom = 8;
  private previousBackground: unknown;
  private hiddenLayers = new Map<string, 'visible' | 'none' | undefined>();
  private tool: ArtworkTool = 'select';
  private drag: {id: number; start: Point; last: Point; moved: boolean; canPick: boolean} | null = null;
  private requestedAt = 0;
  private basePaint = new Map<string, {id: string; property: string; value: any}>();
  private renderMaskTimer = 0;
  private renderNoticeAt: number | null = null;
  private pendingLocation: Point[] | null = null;

  private readonly updateRenderStatus = () => {
    if (!this.renderMask.hidden) this.renderStatus.textContent = i18n.t('artwork_rendering_area');
  };

  private readonly clearViewportRender = () => {
    this.map.off('render', this.checkViewportRender);
    this.map.off('error', this.clearViewportRender);
    this.map.off('webglcontextlost', this.clearViewportRender);
    window.clearTimeout(this.renderMaskTimer);
    this.renderMaskTimer = 0;
    this.renderNoticeAt = null;
    this.renderMask.hidden = true;
    this.renderStatus.textContent = '';
    this.cancelPendingLocation();
  };

  private readonly showRenderStatus = () => {
    this.renderMaskTimer = 0;
    if (this.drag || this.renderNoticeAt === null || this.map.areTilesLoaded()) return;
    this.renderMask.hidden = false;
    this.updateRenderStatus();
  };

  private readonly checkViewportRender = () => {
    if (this.map.areTilesLoaded()) {
      const location = this.pendingLocation;
      this.clearViewportRender();
      if (location) this.locate(location);
    } else if (this.renderMask.hidden && !this.drag && !this.renderMaskTimer && this.renderNoticeAt !== null) {
      const remaining = this.renderNoticeAt-performance.now();
      if (remaining <= 0) this.showRenderStatus();
      else this.renderMaskTimer = window.setTimeout(this.showRenderStatus, remaining);
    }
  };

  restorePaint(): void {
    for (const {id, property, value} of this.basePaint.values())
      if (this.map.getLayer(id)) this.map.setPaintProperty(id, property, value);
    this.basePaint.clear();
  }

  refreshBase(redraw = true): void {
    if (!this.payload?.previewBase) return;
    this.restorePaint();
    for (const layer of this.map.getStyle().layers ?? []) {
      const properties = layer.type === 'line' ? ['line-width', 'line-gap-width', 'line-offset'] :
        layer.type === 'circle' ? ['circle-radius', 'circle-stroke-width'] : [];
      for (const property of properties) {
        const value = this.map.getPaintProperty(layer.id, property);
        if (value !== undefined) this.basePaint.set(`${layer.id}:${property}`, {id: layer.id, property, value});
      }
    }
    this.keepOverlayLayersHidden();
    if (redraw) this.redraw();
  }

  keepOverlayLayersHidden(): void {
    for (const id of this.hiddenLayers.keys())
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', 'none');
  }

  constructor(private readonly map: MapLibreMap,
              private readonly emit: (type: string, data: Record<string, unknown>) => void,
              private readonly onLeave: () => void,
              private readonly onPick: (point: {x: number; y: number}, context: ArtworkPick) => void) {
    this.root.className = 'artwork-view'; this.root.hidden = true;
    this.surface.append(this.content); this.root.append(this.surface);
    this.renderMask.className = 'artwork-render-mask'; this.renderMask.hidden = true;
    this.renderStatus.setAttribute('role', 'status');
    this.renderMask.append(this.renderStatus);
    this.root.append(this.renderMask); map.getContainer().append(this.root);
    i18n.on('languageChanged', this.updateRenderStatus);
    this.surface.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = this.root.getBoundingClientRect(), point: Point = [event.clientX-rect.x, event.clientY-rect.y];
      this.setView({scale: Math.max(.01, Math.min(8, this.observation.scale * Math.exp(-event.deltaY*.001)))}, point, true);
    }, {passive: false});
    this.surface.addEventListener('pointerdown', event => {
      if (event.button !== 0 || this.drag) return;
      this.root.focus({preventScroll: true});
      const point: Point = [event.clientX, event.clientY];
      this.drag = {id: event.pointerId, start: point, last: point, moved: false, canPick: this.containsPoint(...point)};
      this.surface.setPointerCapture(event.pointerId);
    });
    this.surface.addEventListener('pointermove', event => {
      const drag = this.drag;
      if (!drag || event.pointerId !== drag.id) return;
      if (Math.hypot(event.clientX-drag.start[0], event.clientY-drag.start[1]) >= 4) drag.moved = true;
      if (!drag.moved) return;
      this.setView({x: this.observation.x+event.clientX-drag.last[0], y: this.observation.y+event.clientY-drag.last[1]}, undefined, true);
      drag.last = [event.clientX, event.clientY];
    });
    this.surface.addEventListener('pointerup', event => {
      const drag = this.drag;
      if (!drag || event.pointerId !== drag.id || event.button !== 0) return;
      this.finishDrag();
      if (!drag.moved && Math.hypot(event.clientX-drag.start[0], event.clientY-drag.start[1]) < 4 &&
          drag.canPick && this.containsPoint(event.clientX, event.clientY) &&
          this.payload?.job.kind === 'normal' && this.tool !== 'pan') {
        const rect = this.map.getCanvas().getBoundingClientRect();
        const scale = this.observation.scale/this.appliedObservation.scale;
        this.onPick({x: (event.clientX-rect.left)/scale, y: (event.clientY-rect.top)/scale},
          {artworkTool: this.tool, documentEpoch: this.payload.documentEpoch, artworkRevision: this.payload.revision});
      }
    });
    for (const name of ['pointercancel', 'lostpointercapture']) this.surface.addEventListener(name, () => this.finishDrag());
    this.root.tabIndex = -1;
    this.observer = new ResizeObserver(() => {
      if (!this.active) return;
      const size: Point = [this.root.clientWidth, this.root.clientHeight];
      if (size[0] === this.viewport[0] && size[1] === this.viewport[1]) return;
      this.observation = {...this.observation, x: this.observation.x+(size[0]-this.viewport[0])/2,
        y: this.observation.y+(size[1]-this.viewport[1])/2};
      this.viewport = size; this.redraw();
    });
    this.observer.observe(map.getContainer());
  }

  get active(): boolean { return this.payload !== null && !this.root.hidden; }
  get observationScale(): number { return this.active ? this.appliedObservation.scale : 1; }
  private cancelDrag(): void {
    const drag = this.drag; this.drag = null;
    if (drag && this.surface.hasPointerCapture(drag.id)) this.surface.releasePointerCapture(drag.id);
  }
  private finishDrag(): void {
    if (!this.drag) return;
    this.cancelDrag();
    if (this.observation !== this.appliedObservation) this.scheduleViewportCommit();
    else if (this.renderNoticeAt !== null) this.map.triggerRepaint();
  }
  private containsPoint(clientX: number, clientY: number): boolean {
    if (!this.payload) return false;
    const rect = this.root.getBoundingClientRect(), {scale, x, y} = this.observation;
    const px = clientX-rect.left, py = clientY-rect.top;
    return px >= 0 && py >= 0 && px < rect.width && py < rect.height &&
      px >= x && py >= y && px < x+this.payload.canvas.width*scale && py < y+this.payload.canvas.height*scale;
  }
  setTool(tool: ArtworkTool): void {
    if (!['select', 'pick-style', 'pan'].includes(tool)) throw new Error('Invalid artwork tool');
    this.finishDrag(); this.tool = tool; this.root.dataset.tool = tool;
  }
  observe(action: ArtworkObserveAction): void {
    if (action === 'fit') this.fit();
    else if (action === 'actual') this.setView({scale: 1});
    else if (action === 'zoom-in' || action === 'zoom-out')
      this.setView({scale: Math.max(.01, Math.min(8, this.observation.scale*(action === 'zoom-in' ? 1.25 : .8)))}, undefined, true);
    else throw new Error('Invalid artwork observation action');
  }
  requestLeave(): void {
    if (this.expected) this.emit('requestLeaveArtwork', {documentEpoch: this.expected[0], artworkRevision: this.expected[1]});
  }
  locate(points: Point[]): void {
    if (!this.active || !points.length) return;
    if (!this.renderMask.hidden) { this.pendingLocation = points; return; }
    const [a, b, c, d, e, f] = this.payload!.canvas.worldToArtwork;
    let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
    for (const [lng, lat] of points) {
      const wx = lat*111195, wz = -lng*111195;
      const x = a*wx+c*wz+e, y = b*wx+d*wz+f;
      left = Math.min(left, x); right = Math.max(right, x);
      top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
    const scale = Math.max(.01, Math.min(8,
      (this.root.clientWidth-80)/Math.max(1, right-left),
      (this.root.clientHeight-80)/Math.max(1, bottom-top)));
    this.setView({scale, x: this.root.clientWidth/2-(left+right)*scale/2,
      y: this.root.clientHeight/2-(top+bottom)*scale/2});
  }
  cancelPendingLocation(): void { this.pendingLocation = null; }
  matches(epoch: number | undefined, revision: number | undefined): boolean {
    return this.expected?.[0] === epoch && this.expected?.[1] === revision;
  }
  matchesPresented(epoch: number | undefined, revision: number | undefined): boolean {
    return this.payload !== null && this.payload.documentEpoch === epoch && this.payload.revision === revision;
  }
  prepareSceneChange(): void {
    this.reception.abort();
    this.reception = new AbortController();
    ++this.generation;
  }
  request(epoch: number, revision: number): boolean {
    if (!Number.isSafeInteger(epoch) || epoch < 0 || !Number.isSafeInteger(revision) || revision < 0)
      throw new Error('Invalid artwork request identity');
    if (this.expected && (epoch < this.expected[0] || epoch === this.expected[0] && revision <= this.expected[1])) return false;
    this.cancelDrag();
    this.clearViewportRender();
    this.flushViewport();
    this.reception.abort(); this.reception = new AbortController();
    this.expected = [epoch, revision]; ++this.generation;
    this.requestedAt = performance.now();
    // 已有画面在新修订就绪前保留，呈现身份仍指向旧版。
    this.emit('artworkRequested', {documentEpoch: epoch, artworkRevision: revision});
    return true;
  }

  async load(input: ArtworkDelivery, prepareBase?: () => Promise<boolean>): Promise<boolean> {
    const started = performance.now();
    const identity: Point = [input.documentEpoch, input.revision];
    if (!this.matches(...identity)) return false;
    const generation = this.generation;
    const signal = this.reception.signal;
    const previous = this.payload;
    let payload: ArtworkPayload;
    let reusedObjects = false;
    if ('baseRevision' in input) {
      if (!previous || previous.documentEpoch !== input.documentEpoch || previous.revision !== input.baseRevision) {
        this.emit('artworkSnapshotNeeded', {documentEpoch: input.documentEpoch, artworkRevision: input.revision});
        return false;
      }
      const {baseRevision, objects, ...fields} = structuredClone(input);
      reusedObjects = objects === undefined;
      payload = {...fields, objects: objects ?? previous.objects};
      if (reusedObjects && Object.keys(payload.resources).length === 0) payload.resources = previous.resources;
      else {
        for (const item of payload.objects) {
          if (item.kind === 'image' && !payload.resources[item.resource] && previous.resources[item.resource])
            payload.resources[item.resource] = previous.resources[item.resource];
        }
      }
    } else payload = structuredClone(input);
    validate(payload, previous);
    const validated = performance.now();
    const sameResources = payload.resources === previous?.resources;
    let reusedResources = sameResources ? Object.keys(payload.resources).length : 0;
    try {
      if (!sameResources) await Promise.all(Object.entries(payload.resources).map(async ([key, resource]) => {
        if (!resource.url.startsWith('data:image/png;base64,') || resource.url.length > 24*1024*1024 || resource.id !== key)
          throw new Error('Invalid local artwork resource');
        const previous = this.payload?.resources[key];
        if (previous && previous.url === resource.url && previous.width === resource.width && previous.height === resource.height) {
          ++reusedResources; return;
        }
        const bytes = Uint8Array.from(atob(resource.url.split(',')[1]), char => char.charCodeAt(0));
        const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(v => v.toString(16).padStart(2, '0')).join('');
        if (signal.aborted) return;
        if (key !== digest) throw new Error('Artwork resource hash mismatch');
        const image = new Image();
        const cancel = () => { image.removeAttribute('src'); };
        signal.addEventListener('abort', cancel, {once: true});
        try {
          image.src = resource.url; await image.decode();
          if (image.width !== resource.width || image.height !== resource.height || image.width*image.height > 16777216)
            throw new Error('Artwork image dimensions mismatch');
        } finally { signal.removeEventListener('abort', cancel); image.removeAttribute('src'); }
      }));
    } catch (error) {
      if (generation !== this.generation) return false;
      throw error;
    }
    if (generation !== this.generation || !this.matches(...identity)) return false;
    const decoded = performance.now();
    const first = !this.active;
    const sameObjects = reusedObjects || !first && JSON.stringify(previous!.objects) === JSON.stringify(payload.objects);
    const sameCanvas = !first && JSON.stringify(this.payload!.canvas) === JSON.stringify(payload.canvas) &&
      this.payload!.job.derive_params.rotation === payload.job.derive_params.rotation;
    const baseChanged = prepareBase ? await prepareBase() : false;
    if (generation !== this.generation || !this.matches(...identity)) return false;
    const basePrepared = performance.now();
    if (first) {
      this.previousCamera = {center: this.map.getCenter(), zoom: this.map.getZoom(), bearing: this.map.getBearing(), pitch: this.map.getPitch(), padding: this.map.getPadding()};
      this.previousMaxZoom = this.map.getMaxZoom(); this.map.setMaxZoom(30);
      this.previousMinZoom = this.map.getMinZoom(); this.map.setMinZoom(0);
      this.previousBackground = this.map.getPaintProperty('background', 'background-color');
      this.map.stop();
      for (const layer of this.map.getStyle().layers ?? []) {
        if (input.previewBase ? ['district-labels', 'zone-labels', 'road-labels', 'pois', 'transit-pois',
            'tv-line', 'tv-station', 'tv-station-interchange', 'tv-poi', 'transit', 'stations'].includes(layer.id)
            : layer.type === 'symbol' || layer.type === 'circle') {
          this.hiddenLayers.set(layer.id, this.map.getLayoutProperty(layer.id, 'visibility'));
          this.map.setLayoutProperty(layer.id, 'visibility', 'none');
        }
      }
    }
    this.payload = payload; this.root.hidden = false; document.body.classList.add('artwork-active');
    if (first) this.setTool(payload.job.kind === 'normal' ? 'select' : 'pan');
    this.map.setPaintProperty('background', 'background-color', payload.background);
    if (first || baseChanged) this.refreshBase(false);
    this.keepOverlayLayersHidden();
    if (first) {
      const clip = svg('clipPath', {id: 'artwork-page-clip'}); clip.append(svg('rect', {}));
      this.content.replaceChildren(clip, svg('g', {'clip-path': 'url(#artwork-page-clip)'}));
    }
    if (!sameCanvas) {
      const rect = this.content.querySelector('clipPath rect')!;
      rect.setAttribute('width', String(payload.canvas.width)); rect.setAttribute('height', String(payload.canvas.height));
    }
    if (!sameObjects) {
      const group = this.content.lastElementChild!;
      const existing = new Map(Array.from(group.children, (node, index) =>
        [previous!.objects[index].id, {node, item: previous!.objects[index]}] as const));
      let cursor = group.firstElementChild;
      for (const item of payload.objects) {
        const old = existing.get(item.id);
        if (old && JSON.stringify(old.item) === JSON.stringify(item)) {
          if (old.node !== cursor) group.insertBefore(old.node, cursor);
          cursor = old.node.nextElementSibling;
          continue;
        }
        let element: SVGElement;
        if (item.kind === 'circle') {
          element = svg('circle', {cx: item.center[0], cy: item.center[1], r: (item.diameter-item.strokeWidth)/2,
            fill: item.fill, stroke: item.stroke, 'stroke-width': item.strokeWidth});
        } else if (item.kind === 'polyline') {
          element = svg('polyline', {points: item.points.map(p => p.join(',')).join(' '), fill: 'none',
            stroke: item.stroke, 'stroke-width': item.width, 'stroke-linecap': item.lineCap ?? 'round', 'stroke-linejoin': 'round'});
        } else {
          const [x0, y0, x1, y1] = item.bbox;
          element = svg('image', {x: x0, y: y0, width: x1-x0, height: y1-y0,
            href: payload.resources[item.resource].url, preserveAspectRatio: 'none'});
          if (item.textRun) { const title = svg('title', {}); title.textContent = item.textRun.text; element.append(title); }
        }
        element.dataset.objectId = item.id; group.insertBefore(element, cursor);
      }
      while (cursor) { const next = cursor.nextElementSibling; cursor.remove(); cursor = next; }
    }
    if (first) this.fit(false); else if (!sameCanvas || baseChanged) this.redraw(); else this.emitViewChanged();
    this.root.focus({preventScroll: true});
    const applied = performance.now();
    let renderEvents = 0;
    const sourceEvents: Record<string, number> = {};
    const pendingSources = () => Object.keys(this.map.getStyle().sources).filter(id => !this.map.isSourceLoaded(id));
    const pendingAtApply = pendingSources();
    const countedRender = () => { ++renderEvents; };
    const countedSource = (event: {sourceId?: string}) => {
      if (event.sourceId) sourceEvents[event.sourceId] = (sourceEvents[event.sourceId] ?? 0)+1;
    };
    this.map.on('render', countedRender); this.map.on('sourcedata', countedSource);
    let idle: number;
    try {
      if (payload.previewBase && !this.map.loaded()) await new Promise<void>(resolve => {
        const done = () => { this.map.off('idle', done); signal.removeEventListener('abort', done); resolve(); };
        signal.addEventListener('abort', done, {once: true});
        this.map.on('idle', done); this.map.triggerRepaint();
      });
      idle = performance.now();
      for (let frame = 0; frame < 2 && !signal.aborted; ++frame) await new Promise<void>(resolve => {
        const done = () => { cancelAnimationFrame(id); signal.removeEventListener('abort', done); resolve(); };
        const id = requestAnimationFrame(done);
        signal.addEventListener('abort', done, {once: true});
      });
    } finally { this.map.off('render', countedRender); this.map.off('sourcedata', countedSource); }
    if (generation !== this.generation) return false;
    this.emit('artworkPresented', {documentEpoch: identity[0], artworkRevision: identity[1],
      performance: {receiveToPresentedMs: performance.now()-started, requestToPresentedMs: performance.now()-this.requestedAt,
        validateMs: validated-started, resourcesMs: decoded-validated,
        applyMs: applied-decoded, prepareBaseMs: basePrepared-decoded, objectsMs: applied-basePrepared,
        presentationMs: performance.now()-applied, idleMs: idle-applied, framesMs: performance.now()-idle,
        renderEvents, sourceEvents, pendingAtApply, pendingAtPresent: pendingSources(),
        reusedResources, reusedObjects: sameObjects}});
    return true;
  }

  setView(next: Partial<Observation> & {viewportWidth?: number; viewportHeight?: number}, anchor?: Point, defer = false): void {
    if (!this.active) throw new Error('No artwork is presented');
    if (!this.renderMask.hidden) return;
    for (const value of Object.values(next)) if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('Invalid observation');
    const previous = this.observation, scale = next.scale ?? previous.scale;
    if (scale < .01 || scale > 8) throw new Error('Artwork observation must be between 1% and 800%');
    this.cameraZoom(scale);
    const point = anchor ?? [this.root.clientWidth/2, this.root.clientHeight/2];
    this.observation = {scale, x: next.x ?? point[0]-(point[0]-previous.x)*scale/previous.scale,
      y: next.y ?? point[1]-(point[1]-previous.y)*scale/previous.scale};
    // 恢复时加载提示可能改变视口大小，保留观察中心而不是旧视口的左上角。
    if (next.viewportWidth !== undefined) this.observation.x += (this.viewport[0]-next.viewportWidth)/2;
    if (next.viewportHeight !== undefined) this.observation.y += (this.viewport[1]-next.viewportHeight)/2;
    if (defer && this.payload?.previewBase) this.previewViewport();
    else this.redraw(true);
  }
  fit(observe = true): void {
    if (!this.payload || !this.renderMask.hidden) return;
    const {width, height} = this.payload.canvas;
    this.viewport = [this.root.clientWidth, this.root.clientHeight];
    const scale = Math.max(.01, Math.min((this.viewport[0]-48)/width, (this.viewport[1]-48)/height, 8));
    this.observation = {scale, x: (this.viewport[0]-width*scale)/2, y: (this.viewport[1]-height*scale)/2};
    this.redraw(observe);
  }
  private world(px: number, py: number): Point {
    const [a, b, c, d, e, f] = this.payload!.canvas.worldToArtwork, det = a*d-b*c;
    return [(d*(px-e)-c*(py-f))/det, (-b*(px-e)+a*(py-f))/det];
  }
  private previewTransform(): [number, number, number] {
    const scale = this.observation.scale/this.appliedObservation.scale;
    return [scale, this.observation.x-this.appliedObservation.x*scale, this.observation.y-this.appliedObservation.y*scale];
  }

  project(coordinate: Point): {x: number; y: number} {
    const point = this.map.project(coordinate);
    if (!this.active || this.observation === this.appliedObservation) return point;
    const [scale, x, y] = this.previewTransform();
    return {x: point.x*scale+x, y: point.y*scale+y};
  }

  private previewViewport(): void {
    const [scale, x, y] = this.previewTransform();
    const canvas = this.map.getCanvas();
    if (scale === 1 && x === 0 && y === 0) {
      this.clearViewportPreview();
      this.appliedObservation = this.observation;
    } else {
      this.clearViewportRender();
      // 手势阶段只变换已绘制的底图；停稳后才把最终视角交给 MapLibre。
      canvas.style.transformOrigin = '0 0';
      canvas.style.transform = `matrix(${scale}, 0, 0, ${scale}, ${x}, ${y})`;
      this.map.getCanvasContainer().style.backgroundColor = this.payload!.background;
      this.scheduleViewportCommit();
    }
    this.drawArtworkOverlay();
  }

  private scheduleViewportCommit(): void {
    window.clearTimeout(this.settleTimer);
    if (this.drag) return;
    this.settleTimer = window.setTimeout(() => {
      if (!this.drag) this.redraw(true, 0);
    }, VIEWPORT_SETTLE_MS);
  }

  private clearViewportPreview(): void {
    window.clearTimeout(this.settleTimer);
    this.settleTimer = 0;
    const canvas = this.map.getCanvas();
    canvas.style.transform = ''; canvas.style.transformOrigin = '';
    this.map.getCanvasContainer().style.backgroundColor = '';
  }

  flushViewport(): void {
    if (this.active && this.observation !== this.appliedObservation) this.redraw();
  }

  private cameraZoom(scale: number): number {
    const [a, b] = this.payload!.canvas.worldToArtwork;
    const zoom = Math.log2(Math.hypot(a, b)*scale*111195*360/512);
    if (zoom < this.map.getMinZoom() || zoom > this.map.getMaxZoom()) throw new Error('Artwork view exceeds map camera zoom limits');
    return zoom;
  }

  private redraw(observe = false, noticeDelay = VIEWPORT_SETTLE_MS): void {
    if (!this.payload) return;
    this.clearViewportPreview();
    const {scale, x, y} = this.observation;
    const [wx, wz] = this.world((this.root.clientWidth/2-x)/scale, (this.root.clientHeight/2-y)/scale);
    const zoom = this.cameraZoom(scale);
    this.appliedObservation = this.observation;
    this.map.resize();
    const center = this.map.getCenter(), padding = this.map.getPadding();
    const bearing = -this.payload.job.derive_params.rotation;
    if (Math.abs(center.lng+wz/111195) > 1e-10 || Math.abs(center.lat-wx/111195) > 1e-10 ||
        Math.abs(this.map.getZoom()-zoom) > 1e-10 || this.map.getBearing() !== bearing ||
        this.map.getPitch() !== 0 || Object.values(padding).some(value => value !== 0)) {
      this.map.jumpTo({center: [-wz/111195, wx/111195], zoom,
        bearing, pitch: 0, padding: {top: 0, right: 0, bottom: 0, left: 0}});
      if (observe && this.payload.previewBase) {
        // 相机事件先于瓦片更新，绘制后才能检查当前视口所需的瓦片。
        this.clearViewportRender();
        this.renderNoticeAt = performance.now()+noticeDelay;
        this.map.on('render', this.checkViewportRender);
        this.map.on('error', this.clearViewportRender);
        this.map.on('webglcontextlost', this.clearViewportRender);
      }
    }
    const canonicalZoom = zoom-Math.log2(scale);
    const fixedZoom = (value: any): any => Array.isArray(value)
      ? value.length === 1 && value[0] === 'zoom' ? canonicalZoom : value.map(fixedZoom) : value;
    for (const {id, property, value} of this.basePaint.values())
      this.map.setPaintProperty(id, property, ['*', fixedZoom(value), scale]);
    this.drawArtworkOverlay();
  }

  private drawArtworkOverlay(): void {
    const {scale, x, y} = this.observation, {width, height} = this.payload!.canvas;
    this.content.setAttribute('transform', `matrix(${scale} 0 0 ${scale} ${x} ${y})`);
    this.root.style.setProperty('--artwork-left', `${x}px`); this.root.style.setProperty('--artwork-top', `${y}px`);
    this.root.style.setProperty('--artwork-width', `${width*scale}px`); this.root.style.setProperty('--artwork-height', `${height*scale}px`);
    this.emitViewChanged();
  }
  private emitViewChanged(): void {
    this.emit('artworkViewChanged', {documentEpoch: this.payload!.documentEpoch,
      artworkRevision: this.payload!.revision, ...this.observation,
      viewportWidth: this.viewport[0], viewportHeight: this.viewport[1]});
  }
  snapshot() {
    if (!this.payload) return {expected: this.expected, active: false};
    const {scale, x, y} = this.observation;
    return {expected: this.expected, active: this.active, artwork: structuredClone(this.payload),
      observation: {...this.observation}, tool: this.tool,
      baseLayers: [...this.hiddenLayers.keys(), 'forest'].filter(id => this.map.getLayer(id)).map(id =>
        ({id, visibility: this.map.getLayoutProperty(id, 'visibility') ?? 'visible'})),
      boxes: this.payload.objects.map(item => ({id: item.id, box: item.bbox.map((v, i) => v*scale+(i%2 ? y : x))})),
      projection: this.payload.objects.filter(item => item.kind === 'circle').map(item => {
        const [px, py] = item.center, [wx, wz] = this.world(px, py), projected = this.project([-wz/111195, wx/111195]);
        return {id: item.id, deltaArtwork: [(projected.x-x)/scale-px, (projected.y-y)/scale-py]};
      })};
  }
  leave(): void {
    const hadRequest = this.expected !== null || this.active;
    const identity = this.expected;
    const hadCamera = this.previousCamera !== null;
    this.clearViewportRender();
    this.clearViewportPreview();
    this.reception.abort();
    ++this.generation; this.expected = null; this.cancelDrag();
    this.root.hidden = true; document.body.classList.remove('artwork-active'); this.payload = null;
    this.content.replaceChildren();
    for (const [id, visibility] of this.hiddenLayers) if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', visibility ?? 'visible');
    this.hiddenLayers.clear();
    this.restorePaint();
    if (this.previousCamera) this.map.jumpTo(this.previousCamera);
    if (hadCamera) {
      this.map.setMaxZoom(this.previousMaxZoom); this.map.setMinZoom(this.previousMinZoom);
      this.map.setPaintProperty('background', 'background-color', this.previousBackground);
    }
    this.previousCamera = null;
    if (hadRequest) {
      this.emit('artworkLeft', {documentEpoch: identity?.[0], artworkRevision: identity?.[1]});
      this.onLeave();
    }
  }
  dispose(): void {
    this.leave(); this.observer.disconnect(); i18n.off('languageChanged', this.updateRenderStatus); this.root.remove();
  }
}
