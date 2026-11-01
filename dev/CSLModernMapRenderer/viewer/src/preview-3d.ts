import type {IControl, Map as MapLibreMap, ExpressionSpecification} from 'maplibre-gl';
import {i18n} from './i18n';
import {setButtonContent} from './control-icons';
import './preview-3d.css';

export const BUILDINGS_3D = 'buildings-3d';
export const CAMERA_TRANSITION_MS = 400;
export type Preview3DState = {enabled: boolean; pitch: number; bearing: number};
const metersScale = (2 * Math.PI * 6378137 / 360) / 111195;
const messages = {
  en: {title: '3D preview', help: 'Hold the middle mouse button: drag sideways to rotate, up/down to tilt.',
    missing: 'Some buildings have no model height and remain flat. Export again with the updated mod.'},
  'zh-CN': {title: '3D 预览', help: '按住鼠标中键：左右旋转，上下调整倾斜。',
    missing: '部分建筑没有模型高度，仍以平面显示。请使用新版模组重新导出。'},
  'zh-TW': {title: '3D 預覽', help: '按住滑鼠中鍵：左右旋轉，上下調整傾斜。',
    missing: '部分建築沒有模型高度，仍以平面顯示。請使用新版模組重新匯出。'},
};
for (const [language, values] of Object.entries(messages)) i18n.addResourceBundle(language, 'preview3d', values);
const text = (key: keyof typeof messages.en) => i18n.t(key, {ns: 'preview3d', defaultValue: messages.en[key]});

/** 共享预览状态和建筑拉伸；导航只选择视图，不再拥有另一份建筑图层。 */
export class Preview3D implements IControl {
  private map!: MapLibreMap;
  private readonly root = document.createElement('div');
  private readonly button = document.createElement('button');
  private readonly notice = document.createElement('div');
  private readonly abort = new AbortController();
  private enabledValue = false;
  private available = false;
  private missing = false;
  private suspended: Preview3DState | null = null;
  private drag: {id: number; x: number; y: number; pitch: number; bearing: number} | null = null;
  private pending: PointerEvent | null = null;
  private frame = 0;
  onToggle: (enabled: boolean) => void = () => {};

  constructor(private readonly outputBearing: () => number, private readonly interacted: () => void) {}
  get enabled(): boolean { return this.enabledValue; }
  get dragging(): boolean { return this.drag !== null; }
  snapshot(): Preview3DState {
    return {enabled: this.enabled, pitch: this.map.getPitch(), bearing: this.map.getBearing()};
  }
  onAdd(map: MapLibreMap): HTMLElement {
    this.map = map;
    this.root.className = 'maplibregl-ctrl maplibregl-ctrl-group preview-3d';
    this.button.type = 'button'; this.button.className = 'preview-3d-toggle';
    this.button.onclick = () => {
      const enabled = !this.enabled;
      // 先退出导航跟随，避免停止入场动画时取消新的镜头过渡。
      this.onToggle(enabled);
      this.setEnabled(enabled, true, CAMERA_TRANSITION_MS); this.interacted();
    };
    this.notice.className = 'preview-3d-notice'; this.notice.setAttribute('role', 'status');
    this.root.append(this.button, this.notice);
    const canvas = map.getCanvas(), options = {signal: this.abort.signal};
    canvas.addEventListener('pointerdown', event => {
      if (event.button !== 1 || !this.enabled || !this.available || this.suspended) return;
      event.preventDefault(); map.stop();
      this.drag = {id: event.pointerId, x: event.clientX, y: event.clientY,
        pitch: map.getPitch(), bearing: map.getBearing()};
      canvas.setPointerCapture(event.pointerId); canvas.style.cursor = 'grabbing'; this.interacted();
    }, options);
    canvas.addEventListener('pointermove', event => {
      if (!this.drag || this.drag.id !== event.pointerId) return;
      if (!(event.buttons & 4)) { this.endDrag(); return; }
      this.pending = event;
      if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; this.flushDrag(); });
    }, options);
    canvas.addEventListener('pointerup', event => {
      if (event.button === 1 && event.pointerId === this.drag?.id) {
        this.pending = event; this.endDrag();
      }
    }, options);
    for (const name of ['pointercancel', 'lostpointercapture']) canvas.addEventListener(name, this.endDrag, options);
    window.addEventListener('blur', this.endDrag, options);
    for (const name of ['mousedown', 'auxclick'] as const) canvas.addEventListener(name, event => {
      if (event.button === 1 && this.enabled && this.available) event.preventDefault();
    }, options);
    i18n.on('languageChanged', this.translate); this.translate();
    return this.root;
  }
  onRemove(): void {
    this.endDrag(); this.abort.abort(); i18n.off('languageChanged', this.translate);
    if (this.map.getLayer(BUILDINGS_3D)) this.map.removeLayer(BUILDINGS_3D);
    this.root.remove();
  }
  private flushDrag(): void {
    const event = this.pending, drag = this.drag; this.pending = null;
    if (!event || !drag) return;
    this.map.jumpTo({bearing: drag.bearing + (event.clientX-drag.x)*.5,
      pitch: Math.max(0, Math.min(60, drag.pitch-(event.clientY-drag.y)*.3))}, {originalEvent: event});
  }
  private readonly endDrag = (): void => {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0; this.flushDrag();
    const drag = this.drag; this.drag = null;
    if (!drag) return;
    const canvas = this.map.getCanvas();
    if (canvas.hasPointerCapture(drag.id)) canvas.releasePointerCapture(drag.id);
    canvas.style.cursor = '';
  };
  private readonly translate = (): void => {
    setButtonContent(this.button, text('title'), '3d-cube-sphere'); this.button.title = text('help');
    this.button.setAttribute('aria-label', text('title'));
    this.button.setAttribute('aria-pressed', String(this.enabled));
    this.button.disabled = !this.available || this.suspended !== null;
    this.notice.textContent = text('missing'); this.notice.hidden = !this.enabled || !this.missing;
  };
  setAvailable(available: boolean): void {
    this.available = available;
    if (!available) this.reset();
    this.translate();
  }
  setBuildings(features: Array<{properties?: Record<string, unknown>}>): void {
    this.missing = features.some(item => typeof item.properties?.height_m !== 'number');
    this.translate();
  }
  setEnabled(enabled: boolean, camera = true, duration = 0): void {
    this.endDrag(); this.enabledValue = enabled && this.available;
    if (camera) {
      const target = this.enabled ? {pitch: 52} : {pitch: 0, bearing: this.outputBearing()};
      if (duration) this.map.easeTo({...target, duration});
      else this.map.jumpTo(target);
    }
    this.refresh(); this.translate();
  }
  reset(camera = true): void { this.suspended = null; this.setEnabled(false, camera); }
  restore(state: Preview3DState): void {
    this.setEnabled(state.enabled, false);
    this.map.jumpTo({pitch: state.pitch, bearing: state.bearing});
  }
  suspendSelection(): void {
    if (this.suspended) return;
    const state = this.snapshot(); this.setEnabled(false); this.suspended = state; this.translate();
  }
  restoreSelection(): void {
    if (!this.suspended) return;
    const state = this.suspended; this.suspended = null; this.restore(state);
  }
  refresh(): void {
    const map = this.map;
    const visible = this.enabled && map.getLayer('buildings')
      && map.getLayoutProperty('buildings', 'visibility') !== 'none';
    if (!visible) { if (map.getLayer(BUILDINGS_3D)) map.removeLayer(BUILDINGS_3D); return; }
    const color = map.getPaintProperty('buildings', 'fill-color') as string | ExpressionSpecification;
    if (!map.getLayer(BUILDINGS_3D)) map.addLayer({id: BUILDINGS_3D, type: 'fill-extrusion', source: 'buildings',
      filter: ['>', ['coalesce', ['get', 'height_m'], 0], 0],
      paint: {'fill-extrusion-height': ['*', ['get', 'height_m'], metersScale], 'fill-extrusion-base': 0,
        'fill-extrusion-color': color, 'fill-extrusion-opacity': 1}});
    else map.setPaintProperty(BUILDINGS_3D, 'fill-extrusion-color', color);
    map.moveLayer(BUILDINGS_3D);
    for (const layer of map.getStyle().layers) if (layer.type === 'symbol') map.moveLayer(layer.id);
  }
}
