// 初始化 MapLibre 预览器并处理地图交互命令。
import { i18n, setLanguage } from './i18n';
import maplibregl, { type ExpressionSpecification, type Map as MapLibreMap, type Marker } from 'maplibre-gl';
import './style.css';
import {candidate_points, group_candidates, obstacle_score, placement_reason, rank_candidates,
  type Point, type Box, type Candidate, type Placed} from './transit-badge-layout.js';
import { PreviewPerformance } from './performance';
import {stationAnchors, stationFacilityOffset, stationIconOffset} from './poi-anchors';
import { setUiAppearance } from './ui-appearance';
import { SurfaceDistanceLayer, type SurfaceDistanceSpec } from './surface-distance-layer';
import { AreaSurfaceLayer } from './area-surface-layer';
import { NavigationControl } from './routing/ui';
import { Preview3D, BUILDINGS_3D, CAMERA_TRANSITION_MS } from './preview-3d';
import { PreviewControls } from './preview-controls';
import {ArtworkView, type ArtworkDelivery, type ArtworkTool, type ArtworkObserveAction, type ArtworkPick} from './artwork-view';
import { trafficColor as trafficColorExpression } from './traffic-paint';
import {applyPreviewQuality, DEFAULT_PREVIEW_QUALITY, htmlPreviewQuality, previewTolerance, readPreviewQuality,
  type PreviewQuality} from './preview-quality';
import type { Edge, Endpoint, NavigationPackage } from './routing/types';

// 单文件导出模式：整份场景（manifest + sources + 图片）已内联在
// window.__CSL_SCENE__ 中，worker 源码由 __CSL_SCENE_WORKER_SRC__ 提供。
const QUALITY_SAMPLE = new URLSearchParams(window.location.search).has('qualitySample');
if (QUALITY_SAMPLE) {
  const read = async (path: string) => {
    const response = await fetch(new URL(path, window.location.href));
    if (!response.ok) throw new Error(`Quality sample: HTTP ${response.status}`);
    return response;
  };
  const [scene, worker] = await Promise.all([
    read('./assets/preview-quality-sample.json').then(response => response.json()),
    read('./assets/maplibre-gl-csp-worker.js').then(response => response.text()),
  ]);
  window.__CSL_SCENE__ = scene;
  window.__CSL_SCENE_WORKER_SRC__ = worker;
  document.body.classList.add('quality-sample');
}
const EMBEDDED = !!(globalThis as any).__CSL_SCENE__;
void setLanguage(document.documentElement.lang || 'zh-CN');
if (EMBEDDED) {
  try {
    const workerSource = String((globalThis as any).__CSL_SCENE_WORKER_SRC__ ?? '');
    maplibregl.setWorkerUrl(URL.createObjectURL(
      new Blob([workerSource], { type: 'text/javascript' })));
  } catch (error) {
    throw new Error(i18n.t("main_ui_2bce5cc92a"), { cause: error });
  }
} else {
  maplibregl.setWorkerUrl(new URL('./assets/maplibre-gl-csp-worker.js', window.location.href).href);
}

type RoadPaint = { fill: string; border: string; lineWidth: number; borderPx: number; dash?: number[] | null; enabled: boolean };
type RoadTheme = { background: string; sizeContract: 'pixel-v2'; opacity?: number; levels: Record<string, RoadPaint> };
type FullTheme = Record<string, any> & { road: RoadTheme };
type SceneManifest = { performanceDiagnostics?: boolean; schemaVersion: number; sceneId: string; revision: number;
  htmlQuality?: string; completion?: 'base' | 'complete';
  roadOrder?: {flatGroups?: unknown[]};
  world: { bounds?: [number, number, number, number]; geoBounds: [[number, number], [number, number]] }; sources: Record<string, string>;
  sourceRevisions: Record<string, number>; poiAtlas: { image: string; index: string };
  fonts?: Record<string, {family: string; url: string}>;
  navigation?: {schemaVersion: number; url: string};
  navigationRoute?: Record<string, unknown>;
  navigationRouteOmitted?: string;
  initialRegion?: [number, number, number, number];
  exportRegion?: [number, number, number, number];
  rotation?: number;
  view?: { mode: string; options: Record<string, any> };
  terrainCorners?: [number, number][] | null;
  satelliteCorners?: [number, number][] | null;
  satelliteAvailable?: boolean;
  satelliteSize?: number[];
  areaSurfaces?: boolean;
  surfaceFields?: {
    builtArea?: SurfaceDistanceSpec & { url: string; color?: string; bufferMeters?: number; edgeMeters?: number };
  };
  layers: Record<string, boolean>; renderStack?: string[]; theme: FullTheme };
type PreviewCommand = { type: string; sceneId?: string; revision?: number; manifestUrl?: string; preserveArtwork?: boolean;
  artwork?: ArtworkDelivery; documentEpoch?: number; artworkRevision?: number;
  observation?: {scale?: number; x?: number; y?: number};
  tool?: ArtworkTool; action?: ArtworkObserveAction;
  quality?: PreviewQuality; requestId?: string;
  theme?: FullTheme | RoadTheme; layer?: string; visible?: boolean; mode?: string;
  preset?: string; bounds?: [[number, number], [number, number]];
  options?: Record<string, any>; performanceRequestId?: string; appearance?: 'light' | 'dark';
  text?: string; language?: string; rotation?: number; worldBounds?: [number, number, number, number];
  featureId?: string; selection?: any; locate?: boolean; data?: GeoJSON.FeatureCollection;
  layers?: Record<string, boolean>; symbolLayers?: Record<string, boolean>;
  enabled?: boolean; rotationEnabled?: boolean; guideAvailable?: boolean; pendingLayers?: string[];
  immersive?: boolean };
// 单文件导出：整份场景内联数据的结构（与 web_export_v2.py 的 payload 对应）
type EmbeddedPayload = {
  manifest: SceneManifest;
  embedded: {
    sources: Record<string, any>;          // 文件名(无查询串) -> FeatureCollection
    images: Record<string, string>;        // 文件名 -> data URI
    fonts?: Record<string, string>;        // 文件名 -> data URI
    poiAtlasIndex: Record<string, any>;    // 文件名 -> 解析后的 atlas 索引
  };
};
type ExportSizing = {viewport: number[]; radius: number; stroke: number; badge_size: number; fill: string; outline: string};
declare global { interface Window { cslPreview: { dispatch: (command: PreviewCommand) => Promise<void>;
  artworkState: () => ReturnType<ArtworkView['snapshot']>;
  exportSizing: () => ExportSizing | null };
  chrome?: { webview?: { postMessage: (message: string) => void;
    addEventListener: (name: string, listener: (event: MessageEvent) => void) => void } };
  webkit?: { messageHandlers?: { cslPreview?: { postMessage: (message: unknown) => void } } };
  __CSL_SCENE__?: EmbeddedPayload; __CSL_SCENE_WORKER_SRC__?: string;
  __CSL_PREVIEW_QUALITY__?: PreviewQuality;
  __CSL_NATIVE_CAMERA__?: () => {enabled: boolean; pitch: number; bearing: number; moving: boolean; extrusion: boolean} } }

const MPP0 = 156543.03392804097;
const LOCAL_METERS_PER_DEGREE = 111195;
const WEB_MERCATOR_METERS_PER_DEGREE = 2 * Math.PI * 6378137 / 360;
const status = document.querySelector<HTMLDivElement>('#status')!;

/**
 * 预览器只在**出错**时说话。
 *
 * 加载中 / 正在更新 / 视图切换这类过程状态一律不显示：它们会盖住左下角的
 * 比例尺，而且宿主 GUI 的标题栏已经在表达同一件事。空文本 = 隐藏整条。
 */
function setError(text: string): void {
  status.textContent = text;
  status.hidden = text === '';
}
// 操作提示。用 querySelector 而非 `!` 断言：导出页的历史模板未必带这个 div，
// 缺了就是「没有提示可用」，不该让整份脚本在加载期炸掉。
const hint = document.querySelector<HTMLDivElement>('#hint');
/** 淡出时长，与 style.css 里 #hint 的 transition 保持一致。 */
const HINT_FADE_MS = 320;

/**
 * 地图**画出来之后**才有意义的一句操作提示，由宿主翻好文案传进来
 * （前端没有 i18n 设施，也不该为一句提示长出第二套）。
 *
 * 为什么不在加载前就显示：那时屏上还没有地图，讲「拖动、缩放」没有对象可指，
 * 而且用户一选存档这句就会被「正在生成地图…」整段顶掉，实际根本读不到。
 *
 * 停留时间交给用户而不是计时器：他不自己消失，一直挂到用户真的动手操作地图
 * 为止（见下面的 dismissHint 绑定）。提示本身 `pointer-events: none`，不挡交互。
 */
function showHint(text: string): void {
  if (!hint || !text) return;
  hint.textContent = text;
  hint.hidden = false;
  // 同一帧内从 hidden 切到可见不会跑过渡，等下一帧再加类。
  requestAnimationFrame(() => hint?.classList.add('hint-visible'));
}

/** 收起提示：用户已经动手操作地图，这句就不必再压着画面。 */
function dismissHint(): void {
  if (!hint || !hint.classList.contains('hint-visible')) return;
  hint.classList.remove('hint-visible');
  window.setTimeout(() => {
    // 这段时间里若又被 showHint 叫起来，就别把它藏回去。
    if (hint && !hint.classList.contains('hint-visible')) hint.hidden = true;
  }, HINT_FADE_MS);
}
const selection = document.querySelector<HTMLDivElement>('#selection')!;
const sourceIds: Record<string, string> = { roads: 'roads',
  buildings: 'buildings', builtArea: 'built-area', forest: 'forest', landUse: 'land-use',
  water: 'water', railway: 'railway', transit: 'transit', stations: 'stations', traffic: 'traffic',
  districts: 'districts', zones: 'zones', pois: 'pois',
  transitViewPois: 'transit-view-pois', labels: 'labels',
  proceduralObjects: 'procedural-objects', inspectorSelection: 'inspector-selection', searchSelection: 'search-selection',
  buildingHighlight: 'building-highlight' };
let previewQuality = EMBEDDED ? htmlPreviewQuality(window.__CSL_SCENE__!.manifest.htmlQuality ?? 'standard')
  : readPreviewQuality(window.__CSL_PREVIEW_QUALITY__ ?? DEFAULT_PREVIEW_QUALITY);
const groups: Record<string, string[]> = {
  satellite: ['satellite'], terrain: ['terrain', 'forest'], built_area: ['built-area', 'built-area-surface'], poi_area: ['poi-area'], land_use: ['land-use'],
  water: ['water-fill', 'water-shore'], building: ['buildings'],
  road: [], railway: [
      'railway-base', 'railway-dash-metro', 'railway-dash-tram',
      'railway-dash-cablecar', 'railway-dash-monorail', 'railway-dash-train',
      'railway-station'], traffic: ['traffic-base', 'traffic-forward-casing', 'traffic-backward-casing',
      'traffic-forward-fill', 'traffic-backward-fill'],
  transit: ['tv-line', 'tv-station', 'tv-station-interchange', 'tv-poi'],
  metro: ['transit', 'stations'],
  district: ['district-fill', 'district-border'], zone: ['zone-fill', 'zone-border'],
  poi: ['pois', 'transit-pois'], label: ['district-labels', 'zone-labels', 'road-labels'], procedural_objects: ['procedural-objects'], watermark: [] };
// 后端分组只用于绘制顺序；可见性和上下文分组有独立语义，不能成为第二套图层堆叠。
const renderGroups: Record<string, string[]> = {
  satellite: ['satellite'], terrain: ['terrain', 'forest'], built_area: ['built-area', 'built-area-surface'],
  poi_area: ['poi-area'], land_use: ['land-use'],
  water: ['water-fill', 'water-shore'], building: ['buildings'],
  road: [],
  railway: ['railway-base', 'railway-dash-metro', 'railway-dash-tram',
    'railway-dash-cablecar', 'railway-dash-monorail', 'railway-dash-train',
    'railway-station'], traffic: ['traffic-base', 'traffic-forward-casing', 'traffic-backward-casing',
      'traffic-forward-fill', 'traffic-backward-fill'],
  procedural_objects: ['procedural-objects'], district: ['district-fill', 'district-border', 'district-surface'],
  zone: ['zone-fill', 'zone-border', 'zone-surface'],
  transit: ['transit', 'stations', 'tv-line', 'tv-station', 'tv-station-interchange'],
  poi: ['pois', 'transit-pois', 'tv-poi'], label: ['district-labels', 'zone-labels', 'road-labels'], watermark: [],
};
const customLayers: string[] = [];
type LabelMarker = {marker: Marker; group: string; labelType: string; featureColor?: string; transitStationName?: boolean};
const markers = new Map<string, LabelMarker>();
let currentSceneId = '', currentRevision = -1, loadGeneration = 0;
let artworkSceneReady = true;
let focusAnimationGeneration = 0;
let sourceData: Record<string, any> = {};
let mapPoiDisplayFeatures: any[] = [];
let transitPoiDisplayFeatures: any[] = [];
const navigationRoadProperties = new Map<string, Record<string, any>>();
let sceneProvider: ResourceProvider | null = null;
let trafficRequest: AbortController | null = null;
let trafficSourceRevision: number | null = null;
let transitStationDisplayFeatures: any[] = [];
const registeredIcons = new Set<string>();
let labelLayoutKey = '';
let transitBadgeLayoutKey = '';
let labelLayoutTime = -Infinity, labelTimer = 0, labelDirty = false;
let retryingSymbolQuery = false;
const symbolFadeDuration = 300;
let poiAnchorInputs: unknown[] = [];
const labelMeasure = document.createElement('canvas').getContext('2d')!;
const labelWidths = new Map<string, {font: string; width: number}>();
const loadedFonts = new Set<string>();
let currentManifest: SceneManifest | null = null, currentTheme: FullTheme | null = null;
let artworkBrowseTheme: FullTheme | null = null;
let artworkBrowseLayers: Record<string, boolean> | null = null;
const roadWarning = document.createElement('div');
roadWarning.className = 'road-warning'; roadWarning.hidden = true;
roadWarning.setAttribute('role', 'status'); document.body.appendChild(roadWarning);
function updateRoadWarning(): void {
  roadWarning.hidden = !EMBEDDED || !currentManifest?.roadOrder?.flatGroups?.length;
  roadWarning.textContent = roadWarning.hidden ? '' : i18n.t('roads_local_flat_fallback');
}
i18n.on('languageChanged', updateRoadWarning);
let selectedWorldBounds: [number, number, number, number] | null = null;
let selectionStart: { x: number; y: number } | null = null, selectionMode = false;
let suppressNextClick = false;
let hoverFrame = 0;
let hoverPoint: {x: number; y: number} | null = null;
let contextMode = 'map';
let contextOptions: Record<string, any> = {};
let builtSurfaceLayer: SurfaceDistanceLayer | null = null;
const areaSources = {district: 'districts', zone: 'zones'} as const;
const areaKinds = ['district', 'zone'] as const;
const areaSurfaces: Partial<Record<keyof typeof areaSources, AreaSurfaceLayer>> = {};
function usesAreaSurfaces(manifest: SceneManifest | null): boolean {
  return !EMBEDDED && !QUALITY_SAMPLE && manifest?.areaSurfaces === true;
}
let inspectorSelectionId = '';
let inspectorSelectedFeature: any | null = null;
const watermark = document.createElement('div');
watermark.className = 'preview-watermark'; document.body.appendChild(watermark);

function postEvent(type: string, payload: Record<string, unknown> = {}): void {
  const event = { type, sceneId: currentSceneId, revision: currentRevision, ...payload };
  if (QUALITY_SAMPLE && window.parent !== window) {
    window.parent.postMessage(event, window.location.origin); return;
  }
  if (window.chrome?.webview) window.chrome.webview.postMessage(JSON.stringify(event));
  else window.webkit?.messageHandlers?.cslPreview?.postMessage(event);
}
function reportError(error: unknown, identity?: {sceneId?: string; revision?: number; stage?: string}, fatal = true): void {
  const message = error instanceof Error ? error.message : String(error);
  if (fatal) setError(i18n.t("main_reportError_8a023d6da8", { p0: message }));
  postEvent('renderError', { message, fatal, details: error instanceof Error ? error.stack : message, ...identity });
}
const map: MapLibreMap = new maplibregl.Map({ container: 'map', style: { version: 8, sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#eef1f4' } }] },
  center: [0, 0], zoom: 12, minZoom: 8, maxZoom: 24, attributionControl: false,
  renderWorldCopies: false, pitchWithRotate: false, dragRotate: false, maxPitch: 60, fadeDuration: symbolFadeDuration,
  localIdeographFontFamily: false, canvasContextAttributes: {antialias: true},
  pixelRatio: window.devicePixelRatio * previewQuality.pixelScale });
window.addEventListener('resize', () => {
  const ratio = window.devicePixelRatio * previewQuality.pixelScale;
  if (map.getPixelRatio() !== ratio) map.setPixelRatio(ratio);
});
let mapStarted = false;
map.on('error', event => reportError(event.error, undefined, !mapStarted));
map.on('webglcontextlost', () => postEvent('previewContextLost'));
map.on('webglcontextrestored', () => postEvent('previewContextRestored'));
const artworkView = new ArtworkView(map,
  (type, data) => {
    if (type === 'artworkViewChanged') drawWorldSelection();
    postEvent(type, data);
  }, () => {
    if (selectionMode || selectionStart) clearWorldSelection();
    if (artworkBrowseTheme) { const theme = artworkBrowseTheme; artworkBrowseTheme = null; applyTheme(theme); }
    if (artworkBrowseLayers) {
      const layers = artworkBrowseLayers; artworkBrowseLayers = null;
      for (const [layer, visible] of Object.entries(layers)) void setLayerVisibility(layer, visible);
    }
    labelLayoutKey = ''; scheduleLabels(); }, pickObjectAt);
map.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: 'metric' }), 'bottom-left');
const preview3d = new Preview3D(() => outputBearing(outputRotation), dismissHint);
map.addControl(preview3d, 'bottom-right');
const navigation: NavigationControl = new NavigationControl(map,
  () => selectionMode || selectionStart !== null, dismissHint,
  navigationPlaceAt, {properties: navigationRoadStyle, width: navigationWidth, widthMeters: navigationWidthMeters}, preview3d);
navigation.onNavigationOpened = () => postEvent('navigationOpened');
preview3d.onToggle = enabled => navigation.simulation.set3DEnabled(enabled);
let quickLayersEnabled = EMBEDDED;
let quickSymbolLayers: Record<string, boolean> | undefined;
let immersive = false;
const previewControls = new PreviewControls(navigation, feature => {
  navigation.simulation.stop(); dismissHint(); locateGeometry(feature.geometry);
  setFeatureHighlight('search-selection', feature);
}, (layer, visible) => {
  if (EMBEDDED) {
    void setLayerVisibility(layer, visible).catch(reportError).finally(() => updateQuickLayers(true));
  } else postEvent('requestLayerVisibility', {layer, visible});
}, () => fitBounds(CAMERA_TRANSITION_MS), delta => {
  navigation.simulation.stop(); map.zoomTo(map.getZoom() + delta);
}, type => {
  if (type === 'mapInteractionGuideOpened') { navigation.simulation.stop(); dismissHint(); }
  postEvent(type);
}, () => map.getCanvas().focus(), EMBEDDED,
  !EMBEDDED && new URLSearchParams(location.search).get('desktopUi') === '1'
    ? delta => postEvent('requestMapRotation', {delta}) : undefined);
map.addControl(previewControls.viewControl, 'bottom-right');
function updateZoomControls(): void {
  previewControls.setZoomAvailability(map.getZoom() < map.getMaxZoom(), map.getZoom() > map.getMinZoom());
}
map.on('zoom', updateZoomControls); updateZoomControls();

function updateQuickLayers(settled = false, pending?: string[]): void {
  if (!currentManifest) return;
  const sources = currentManifest.sources;
  const available = new Set<string>(['label', 'watermark']);
  const sourceGroups: Record<string, string[]> = {
    satellite: ['satellite'], terrain: ['terrain', 'forest'], built_area: ['builtArea'], land_use: ['landUse', 'buildings'], water: ['water'],
    road: ['roads'], building: ['buildings'], railway: ['railway'], traffic: ['traffic'],
    metro: ['transit', 'stations'], transit: ['transit', 'stations'], poi_area: ['buildings'],
    poi: ['pois', 'transitViewPois'], district: ['districts'], zone: ['zones'], procedural_objects: ['proceduralObjects'],
  };
  for (const [key, names] of Object.entries(sourceGroups)) if (names.some(name => name in sources)) available.add(key);
  available.delete(contextMode === 'transit' ? 'metro' : 'transit');
  if (currentManifest.terrainCorners?.length) available.add('terrain');
  if (currentManifest.satelliteAvailable || sources.satellite) available.add('satellite');
  if (currentManifest.surfaceFields?.builtArea) available.add('built_area');
  if (quickSymbolLayers) {
    available.delete('label');
    for (const key of ['poi_text', 'road_text', 'district_text']) available.add(key);
  }
  const layers = {...currentManifest.layers, ...quickSymbolLayers};
  previewControls.setLayers(layers, available, quickLayersEnabled, settled, pending);
}

const token = (name: string): Record<string, any> => currentTheme?.[name] ?? {};
function add(layer: any): void { map.addLayer(layer); customLayers.push(layer.id); }
function applyRenderStack(): void {
  const stack = currentManifest?.renderStack;
  if (!stack?.length) return;
  const overlay = map.getLayer('inspector-selection-fill')
    ? 'inspector-selection-fill' : undefined;
  const ordered = [...stack, ...Object.keys(renderGroups).filter(key => !stack.includes(key))];
  // 预览分区目前从建筑占地推导；若按栅格顺序绘制，会被不透明建筑完全覆盖。
  // 因此作为半透明主题覆盖层绘制在建筑上方。
  const landUseIndex = ordered.indexOf('land_use');
  const buildingIndex = ordered.indexOf('building');
  if (landUseIndex >= 0 && buildingIndex >= 0 && landUseIndex < buildingIndex) {
    ordered.splice(landUseIndex, 1);
    ordered.splice(ordered.indexOf('building') + 1, 0, 'land_use');
  }
  for (const key of ordered) {
    for (const id of renderGroups[key] ?? []) {
      if (map.getLayer(id)) map.moveLayer(id, overlay);
    }
  }
  preview3d.refresh();
}
function categoryColor(values: Record<string, unknown>, fallback: string): string | ExpressionSpecification {
  const entries = Object.entries(values ?? {});
  return entries.length ? ['match', ['get', 'category'], ...entries.flat(), fallback] as unknown as ExpressionSpecification : fallback;
}
function districtFillColor(): ExpressionSpecification {
  const district = token('district');
  const colors = district.fillColors ?? [district.fill ?? '#D9E8CC'];
  return ['to-color', ['at', ['%', ['coalesce', ['get', 'paletteIndex'], 0], colors.length], ['literal', colors]]];
}
function painterOrderedOpacity(value: number): ExpressionSpecification {
  // 自定义图层处于 MapLibre 半透明通道；水层常量透明度为一会被提前移入不透明通道。
  // 使用依赖数据的表达式保持实际透明度，同时阻止通道折叠。
  return ['*', value, ['coalesce',
    ['number', ['get', '__csl_preview_water_opacity']], 1]] as ExpressionSpecification;
}
function roadToken(field: keyof RoadPaint, fallback: string | number): ExpressionSpecification {
  const value: unknown[] = ['match', ['get', 'styleKey']];
  for (const [key, paint] of Object.entries(currentTheme?.road.levels ?? {})) value.push(key, paint[field]);
  value.push(fallback); return value as ExpressionSpecification;
}
// 草稿按画布比例绘制，单文件地图沿用导出样式。
function usesDistanceSizing(): boolean { return (!EMBEDDED || QUALITY_SAMPLE) && artworkBrowseTheme === null; }
const distanceZooms = [11, 13, 15, 17] as const;
function distanceSize(middle: number, zoom: number, farSize = middle * .75): number {
  const [far, start, end, near] = distanceZooms;
  if (zoom < start) {
    return farSize + (middle - farSize) * Math.max(0, (zoom - far) / (start - far));
  }
  return middle * (1 + .25 * Math.min(1, Math.max(0, (zoom - end) / (near - end))));
}
type NumericStyle = number | ExpressionSpecification;
function distanceExpression(at: (zoom: number) => NumericStyle): ExpressionSpecification {
  return ['interpolate', ['linear'], ['zoom'], ...distanceZooms.flatMap(z => [z, at(z)])] as ExpressionSpecification;
}
function lineScaleAt(zoom: number): number {
  return usesDistanceSizing() ? distanceSize(previewQuality.linePercent / 100, zoom) : 1;
}
function scaledLine(width: NumericStyle): NumericStyle {
  return usesDistanceSizing() ? distanceExpression(z => ['*', lineScaleAt(z), width]) : width;
}
function previewFontSize(base: number, zoom: number): number {
  const middle = base * previewQuality.textPercent / 100;
  return usesDistanceSizing() ? distanceSize(middle, zoom, Math.min(middle * .5, 10)) : base;
}
function widthAt(_z: number, casing = false): ExpressionSpecification {
  const fill = roadToken('lineWidth', 0);
  return casing ? ['+', fill, roadToken('borderPx', 0)] : fill;
}
function fillWidth(): ExpressionSpecification { return widthAt(0); }
function casingWidth(): ExpressionSpecification { return widthAt(0, true); }
function navigationRoadStyle(edge: Edge): Record<string, unknown> {
  const properties = navigationRoadProperties.get(edge.sourceSegmentId);
  return {physicalWidth: properties?.physicalWidth ?? edge.widthM ?? 0,
    styleKey: properties?.styleKey ?? `${edge.level}:${edge.mode.toLowerCase()}`,
    surfaceWidth: properties?.geometryRole === 'road-surface' ? properties.physicalWidth : 0};
}
function navigationWidth(casing: boolean): ExpressionSpecification {
  const alternative = ['==', ['get', 'alternative'], true];
  return ['interpolate', ['exponential', 2], ['zoom'], ...Array.from({length: 25}, (_, z) => [z,
    ['case', ['==', ['get', 'mode'], 'RIDE'], casing ? 10 : 6,
      ['+', casing ? 6 : 0, ['max', ['case', alternative, 5, 8],
        ['*', ['case', alternative, 1.3, 1.6], ['case', ['>', ['get', 'surfaceWidth'], 0],
          ['*', ['get', 'surfaceWidth'], Math.pow(2, z)/MPP0*WEB_MERCATOR_METERS_PER_DEGREE/LOCAL_METERS_PER_DEGREE],
          widthAt(z)]]]]]
  ]).flat()] as ExpressionSpecification;
}
function navigationWidthMeters(edge: Edge): number {
  const properties = navigationRoadStyle(edge);
  // MapLibre 的地面投影使用 512px 瓦片；既有路线宽度表达式沿用 256px 基准。
  if (Number(properties.surfaceWidth) > 0) return Number(properties.surfaceWidth)/2;
  const paint = currentTheme?.road.levels?.[String(properties.styleKey)];
  const ppm = 2**18/MPP0*WEB_MERCATOR_METERS_PER_DEGREE/LOCAL_METERS_PER_DEGREE;
  return Number(paint?.lineWidth ?? 0)/(ppm*2);
}
// 表面多边形已包含道路宽度，边界只绘制外沿，同时保留固定像素和缩放宽度。
function borderWidth(): ExpressionSpecification { return roadToken('borderPx', 0); }
function enabledFilter(): ExpressionSpecification { const value: unknown[] = ['match', ['coalesce', ['get', 'sourceStyleKey'], ['get', 'styleKey']]];
  for (const [key, paint] of Object.entries(currentTheme?.road.levels ?? {})) value.push(key, paint.enabled);
  value.push(true); return ['==', value as ExpressionSpecification, true]; }
const role = (name: string): ExpressionSpecification => ['all', enabledFilter(), ['==', ['get', 'geometryRole'], name]];

type RoadLayer = {id: string; role: string; rank: number; type: 'line' | 'fill';
  paint: 'border' | 'fill'; width?: 'border' | 'casing' | 'fill'; flatOrder?: number};
function roadLineWidth(width: RoadLayer['width']): NumericStyle {
  return scaledLine(width === 'border' ? borderWidth() : width === 'casing' ? casingWidth() : fillWidth());
}
let roadLayers: RoadLayer[] = [];
function contextFade(): number {
  return contextMode === 'transit'
    ? Math.max(.08, 1 - Math.max(0, Math.min(1, Number(contextOptions.transit_mask ?? .6)))) : 1;
}
function roadOpacity(): number {
  // MapLibre 5 按图层混合透明度；道路交叠处会叠加，不能等同于整组离屏合成。
  return contextMode === 'transit' && contextOptions.base_road === false ? 0
    : (currentTheme?.road.opacity ?? 1) * contextFade();
}
function roadFilter(layer: RoadLayer): ExpressionSpecification {
  return ['all', role(layer.role), ['==', ['coalesce', ['get', 'rank'], 0], layer.rank],
    layer.flatOrder === undefined ? ['!', ['has', 'flatGroup']]
      : ['all', ['has', 'flatGroup'], ['==', ['get', 'sortKey'], layer.flatOrder]]];
}
function addRoads(): void {
  const rolesByRank = new Map<number, Set<string>>();
  const flatRolesByRank = new Map<number, Map<number, Set<string>>>();
  for (const feature of sourceData.roads?.features ?? []) {
    const rank = Number(feature.properties?.rank ?? 0);
    if (!rolesByRank.has(rank)) rolesByRank.set(rank, new Set());
    rolesByRank.get(rank)!.add(String(feature.properties?.geometryRole));
    const p = feature.properties;
    if (p?.flatGroup !== undefined) {
      if (!flatRolesByRank.has(rank)) flatRolesByRank.set(rank, new Map());
      const orders = flatRolesByRank.get(rank)!;
      if (!orders.has(p.sortKey)) orders.set(p.sortKey, new Set());
      orders.get(p.sortKey)!.add(p.geometryRole);
    }
  }
  const ranks = [...rolesByRank.keys()].sort((a, b) => a-b);
  roadLayers = [];
  for (const rank of ranks) {
    const suffix = rank === 0 ? '' : `-rank-${rank}`;
    // 同一等级先绘制全部外沿，再绘制全部路面；高等级完整覆盖低等级表面。
    const definitions = [
      ['road-surface-casing', 'road-boundary', 'line', 'border', 'border'],
      ['road-casing', 'road-edge', 'line', 'border', 'casing'],
      ['road-piece-casing', 'road-piece', 'line', 'border', 'casing'],
      ['road-transition-casing', 'road-transition', 'line', 'border', 'casing'],
      ['road-surface-fill', 'road-surface', 'fill', 'fill', undefined],
      ['road-fill', 'road-edge', 'line', 'fill', 'fill'],
      ['road-piece-fill', 'road-piece', 'line', 'fill', 'fill'],
      ['road-transition-fill', 'road-transition', 'line', 'fill', 'fill'],
    ] as const;
    const flatRoles = flatRolesByRank.get(rank) ?? new Map<number, Set<string>>();
    const variants: Array<{definition: typeof definitions[number]; flatOrder?: number}> =
      definitions.map(definition => ({definition}));
    for (const [flatOrder, roles] of [...flatRoles].sort((a, b) => a[0]-b[0]))
      for (const definition of definitions)
        if (roles.has(definition[1])) variants.push({definition, flatOrder});
    // 回退组内的原生路面与中心线必须共用样式顺序，不能由图元类型决定覆盖。
    variants.sort((a, b) => Number(a.definition[3] === 'fill')-Number(b.definition[3] === 'fill'));
    for (const {definition: [base, geometryRole, type, paint, width], flatOrder} of variants) {
      if (!rolesByRank.get(rank)?.has(geometryRole)) continue;
      const layer: RoadLayer = {id: base+suffix+(flatOrder === undefined ? '' : `-flat-${flatOrder}`),
        role: geometryRole, rank, type, paint, width, flatOrder};
      roadLayers.push(layer);
      add({id: layer.id, type, source: 'roads', filter: roadFilter(layer),
        layout: type === 'fill' ? {'fill-sort-key': ['get', 'sortKey']} : {
          'line-cap': geometryRole === 'road-edge' ? 'round' : 'butt',
          'line-join': 'round', 'line-sort-key': ['get', 'sortKey']},
        paint: type === 'fill' ? {'fill-color': roadToken('fill', '#fff'),
          'fill-opacity': roadOpacity(),
          // 人为多边形切口不能获得 MapLibre 隐式轮廓。
          'fill-antialias': false} : {
          'line-color': roadToken(paint, paint === 'border' ? '#c3c8d2' : '#fff'),
          'line-opacity': roadOpacity(),
          'line-width': roadLineWidth(width)}});
      if (paint === 'fill') selectableObjects[layer.id] = {
        objectType: 'road', styleLayer: 'road', priority: 75};
    }
  }
  groups.road = roadLayers.map(layer => layer.id);
  renderGroups.road = [...groups.road];
}
function rebuildRoadLayers(): void {
  for (const layer of roadLayers) {
    if (map.getLayer(layer.id)) map.removeLayer(layer.id);
    const index = customLayers.indexOf(layer.id);
    if (index >= 0) customLayers.splice(index, 1);
    delete selectableObjects[layer.id];
  }
  addRoads();
}

function buildingColor(): ExpressionSpecification {
  const building = token('building');
  const fallback = building.fill ?? '#D8D2C6';
  if (building.colorMode === 'uniform') return fallback;
  const poiColor = ['get', 'poiColor'] as ExpressionSpecification;
  const automatic = ['case', ['==', poiColor, null], fallback,
    ['interpolate', ['linear'], ['literal', Number(
      building.poiTintMixWhite ?? .7)],
    0, ['to-color', poiColor], 1, '#ffffff']] as ExpressionSpecification;
  const colors = Object.entries(building.categoryColors ?? {});
  if (building.colorMode !== 'category' || colors.length === 0) return automatic;
  return ['match', ['get', 'colorCategory'],
    ...colors.flatMap(([category, color]) => [category, color]), automatic] as unknown as ExpressionSpecification;
}
function showsTransportOverlay(): boolean {
  return contextMode === 'transit' || (currentManifest?.layers.metro === true && !!contextOptions.layers);
}
function effectivePoiIcons(): Record<string, boolean> {
  const icons = {...(token('poiVisibility').icons ?? {})} as Record<string, boolean>;
  if (showsTransportOverlay()) icons.transit = contextOptions.facilities_enabled !== false;
  return icons;
}
function poiVisibilityFilter(): ExpressionSpecification {
  const icons = effectivePoiIcons();
  const visibleGroups = ['transit', 'facility', 'ordinary', 'area'].filter(group => icons[group] !== false);
  const ownGroup: ExpressionSpecification = ['in', ['coalesce', ['get', 'visibilityGroup'], ['get', 'poiGroup'],
    ['case', ['==', ['get', 'owner'], 'transit'], 'transit', 'ordinary']],
    ['literal', visibleGroups]];
  return ['all', ownGroup, poiLineSelectionFilter(), ...['transit', 'facility', 'ordinary', 'area']
    .filter(group => icons[group] === false)
    .map<ExpressionSpecification>(group => ['!', ['in', group, ['coalesce', ['get', 'visibilityGroups'], ['literal', []]]]])];
}
function poiFilter(): ExpressionSpecification {
  const zoom: ExpressionSpecification = ['zoom'];
  return ['all', poiVisibilityFilter(), ['!=', ['get', 'owner'], 'transit'],
    ['<=', ['get', 'minZoom'], zoom], ['in', zoom, ['get', 'visibleZooms']]];
}
function transitPoiFilter(): ExpressionSpecification {
  if (showsTransportOverlay()) return alwaysFalseFilter();
  const zoom: ExpressionSpecification = ['zoom'];
  return ['all', poiVisibilityFilter(), ['==', ['get', 'owner'], 'transit'],
    ['<=', ['get', 'minZoom'], zoom], ['in', zoom, ['get', 'visibleZooms']]];
}
function selectedTransitRouteIds(): number[] {
  const typeValues = contextOptions.types as unknown[] | undefined;
  const requestedTypes = new Set((typeValues ?? []).map((value: unknown) => String(value).toLowerCase()));
  const requestedIds = contextOptions.line_ids == null
    ? null : new Set((contextOptions.line_ids as unknown[]).map(value => Number(value)));
  return (sourceData.transit?.features ?? [])
    .filter((feature: any) => (typeValues == null || requestedTypes.has(String(feature.properties?.kind ?? '').toLowerCase()))
      && (requestedIds === null || requestedIds.has(Number(feature.properties?.lineId))))
    .map((feature: any) => Number(feature.properties?.lineId));
}
function poiLineSelectionFilter(): ExpressionSpecification {
  const ids = selectedTransitRouteIds();
  return ['any', ['!=', ['get', 'lineRelated'], true],
    ...ids.map<ExpressionSpecification>(id => ['in', id, ['get', 'servingLineIds']])];
}
function transitViewLineFilter(): ExpressionSpecification {
  return ['in', ['to-number', ['get', 'lineId']], ['literal', selectedTransitRouteIds()]] as ExpressionSpecification;
}
/** 初始隐藏 / 无匹配项时的恒假 filter。
 *  不能再用 `['==', 1, 0]`：新版 MapLibre 会把它当成两个 number literal
 *  的常数比较，对 symbol 等图层直接抛 "string expected, number found"，
 *  导致 addDomains 中断，tv-line/tv-station/tv-poi 根本没加到地图上。
 *  用 `['==', ['get', 'featureId'], '']` 依赖 featureId 属性（preview_scene
 *  给每个 feature 都写了非空 featureId），永远为 false，且符合 filter 语法。
 */
function alwaysFalseFilter(): ExpressionSpecification {
  return ['==', ['get', 'featureId'], ''] as unknown as ExpressionSpecification;
}
function transitViewPoiFilter(): ExpressionSpecification {
  const zoom: ExpressionSpecification = ['zoom'];
  return ['all', poiVisibilityFilter(), ['<=', ['coalesce', ['get', 'minZoom'], 0], zoom],
    ['any', ['==', ['get', 'iconStatus'], 'empty'], ['in', zoom, ['get', 'visibleZooms']]]];
}
function updatePoiFilters(): void {
  for (const [id, filter] of [['pois', poiFilter()], ['transit-pois', transitPoiFilter()],
    ['tv-poi', transitViewPoiFilter()]] as const) if (map.getLayer(id)) map.setFilter(id, filter);
}
function updateTransitViewLayout(): void {
  const stationSource = map.getSource('transit-view-stations') as maplibregl.GeoJSONSource | undefined;
  const poiSource = map.getSource('transit-view-pois-displaced') as maplibregl.GeoJSONSource | undefined;
  const mapSource = map.getSource('map-pois-displaced') as maplibregl.GeoJSONSource | undefined;
  const inputs = [sourceData.stations, sourceData.pois, sourceData.transitViewPois, sourceData.transit,
    JSON.stringify([contextOptions.line_ids, contextOptions.types, contextOptions.stations,
      currentManifest?.layers.metro, contextMode])];
  if (inputs.every((value, index) => value === poiAnchorInputs[index])) return;
  poiAnchorInputs = inputs;
  const visible = stationVisibility();
  const selected = new Set(selectedTransitRouteIds());
  const groupOfLine = new Map<number, number>((sourceData.transit?.features ?? []).map((feature: any) =>
    [Number(feature.properties.lineId), Number(feature.properties.groupId ?? feature.properties.lineId)]));
  const stations = (sourceData.stations?.features ?? []).filter(visible)
    .map((feature: any) => {
      const groups = new Set<number>((feature.properties.servingLineIds ?? [])
        .filter((id: number) => selected.has(Number(id))).map((id: number) => groupOfLine.get(Number(id))));
      groups.delete(undefined as unknown as number);
      return {...feature, properties: {...feature.properties,
        isInterchange: map.hasImage('cmm-station-interchange') && groups.size >= 2}};
    })
    .sort((a: any, b: any) => Number(b.properties?.servingLineCount ?? 0) - Number(a.properties?.servingLineCount ?? 0));
  transitStationDisplayFeatures = stations;
  mapPoiDisplayFeatures = stationAnchors(sourceData.pois?.features ?? [], stations,
    contextOptions.stations !== false && currentManifest?.layers.metro !== false);
  mapSource?.setData({type: 'FeatureCollection', features: mapPoiDisplayFeatures});
  if (showsTransportOverlay()) {
    stationSource?.setData({type: 'FeatureCollection', features: stations});
    transitPoiDisplayFeatures = stationAnchors(sourceData.transitViewPois?.features ?? [], stations,
      contextOptions.stations !== false);
    poiSource?.setData({type: 'FeatureCollection', features: transitPoiDisplayFeatures});
  }
  labelLayoutKey = '';
}
// 直径包含描边；小圆圈的描边随外径缩小，不再额外撑大符号。
function baseStationMetrics(): {outer: number; radius: number; stroke: number} {
  const outer = Number(contextOptions.station_diameter ?? token('transit').stationSize) / 2;
  const stroke = Math.min(Number(token('transit').stationBorder), outer);
  return {outer, radius: outer - stroke, stroke};
}
function stationScale(outer: number, zoom: number): number {
  if (!usesDistanceSizing() || outer === 0) return 1;
  const middle = outer * previewQuality.linePercent / 100;
  return distanceSize(middle, zoom, Math.min(middle * .75, 3)) / outer;
}
function stationRadius(): number {
  const {outer} = baseStationMetrics();
  return outer * stationScale(outer, map.getZoom());
}
function symbolStyle(at: (zoom: number) => number): NumericStyle {
  return zoomingSymbols && usesDistanceSizing() ? distanceExpression(at) : at(map.getZoom());
}
function stationStyle(metric: 'radius' | 'stroke' | 'iconSize'): NumericStyle {
  const base = baseStationMetrics();
  const size = metric === 'iconSize' ? base.outer / 8 : base[metric];
  return symbolStyle(zoom => size * stationScale(base.outer, zoom));
}
function updateStationSizes(): void {
  for (const id of ['stations', 'tv-station']) if (map.getLayer(id)) {
    map.setPaintProperty(id, 'circle-radius', stationStyle('radius'));
    map.setPaintProperty(id, 'circle-stroke-width', stationStyle('stroke'));
  }
  if (map.getLayer('tv-station-interchange'))
    map.setLayoutProperty('tv-station-interchange', 'icon-size', stationStyle('iconSize'));
}
function poiSize(transit: boolean, zoom: number): number {
  const base = transit ? Number(contextOptions.facility_icon_size ?? 24) : Number(token('poi').size ?? 18);
  const middle = base * previewQuality.iconPercent / 100;
  return usesDistanceSizing() ? distanceSize(middle, zoom, Math.min(middle * .5, 12)) : base;
}
function poiStyle(transit: boolean): NumericStyle {
  return symbolStyle(zoom => poiSize(transit, zoom) / 24);
}
function poiDimensions(transit: boolean): [number, number] {
  return [poiSize(transit, map.getZoom()), stationRadius() * 2];
}
function updatePoiSizes(): void {
  for (const id of ['pois', 'transit-pois', 'tv-poi']) if (map.getLayer(id)) {
    const transit = id === 'tv-poi';
    map.setLayoutProperty(id, 'icon-size', poiStyle(transit));
    if (!zoomingSymbols) map.setLayoutProperty(id, 'icon-offset', stationIconOffset(...poiDimensions(transit)));
  }
}
function poiScreenOffset(feature: any): [number, number] {
  return feature.properties?.stationAnchor
    ? stationFacilityOffset(...poiDimensions(showsTransportOverlay() && feature.properties.owner === 'transit')) : [0, 0];
}
function poiScreenPoint(feature: any) {
  const point = map.project(feature.geometry.coordinates as [number, number]), offset = poiScreenOffset(feature);
  return {x: point.x + offset[0], y: point.y + offset[1]};
}
function transportLineWidth(overlay = true): NumericStyle {
  const width = Number(token('transit').width ?? 3);
  const selected = overlay ? Number(contextOptions.line_width ?? width) : width;
  const entries = Object.entries(token('transit').typeWidths ?? {});
  if (!entries.length) return scaledLine(selected);
  return scaledLine(['match', ['downcase', ['to-string', ['get', 'kind']]],
    ...entries.flatMap(([kind, value]) => [kind, !overlay ? Number(value) : width ? Number(value) * selected / width : selected]),
    selected] as ExpressionSpecification);
}
/** 公共交通视图线路标签的视觉规范。
 *
 * 与导出侧 ``transport_layer._make_view_badge`` 共用主题 ``transit_view`` 段，
 * 由 ``preview_scene._transit_label_style`` 下发。尺寸已经应用元素倍率，单位为像素。
 * 规范全文：docs/tasks/公共交通视图-整改-20260910/01-线路标签视觉统一-20260914.md
 */
type TransitLabelStyle = {
  preset: string;
  presets: Record<string, {padding: number; border: number; radius: number; height: number; accent: string; icon: boolean;
    icon_size?: number; icon_gap?: number; icon_width?: number}>;
  palette: {surface: string; text: string; border: string};
  icons: Record<string, string>;
  fontSizeBase: number;
  fontWeight: number;
  font: string;
  mergeDistance: number;
};

const TRANSIT_LABEL_FALLBACK: TransitLabelStyle = {
  preset: 'split-sign', presets: {}, palette: {surface: '#FFFFFF', text: '#26352F', border: '#D4DDD7'}, icons: {},
  fontSizeBase: 15,
  fontWeight: 700, font: 'Microsoft YaHei', mergeDistance: 40,
};

function transitLabelStyle(): TransitLabelStyle {
  const raw = (token('transit').labelStyle ?? {}) as Record<string, any>;
  const merged: Record<string, any> = {...TRANSIT_LABEL_FALLBACK};
  for (const key of Object.keys(merged)) {
    const value = raw[key];
    if (value === undefined || value === null || value === '') continue;
    if (typeof value === 'number' && !Number.isFinite(value)) continue;
    merged[key] = value;
  }

  if (showsTransportOverlay()) merged.preset = String(contextOptions.line_label_style ?? merged.preset);
  if (merged.preset === 'classic') merged.preset = 'split-sign';
  return merged as TransitLabelStyle;
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.startsWith('#') ? hex.slice(1) : hex;
  if (normalized.length !== 6) return [0, 0, 0];
  return [parseInt(normalized.slice(0, 2), 16),
    parseInt(normalized.slice(2, 4), 16), parseInt(normalized.slice(4, 6), 16)];
}

function baseBadgeSize(style: TransitLabelStyle): number {
  return Math.max(1, Number(contextOptions.line_label_size ?? style.fontSizeBase));
}
/** 线路标签几何（已解析字号、内边距和轮廓宽度）。 */
function transitBadgeMetrics() {
  const style = transitLabelStyle();
  // 小字号也按设置缩小，避免预览另设下限使 1～6 的输入失效。
  const size = previewFontSize(baseBadgeSize(style), map.getZoom());
  const preset = style.presets[style.preset];
  const outline = preset.border * size;
  const padX = preset.padding * size;
  const iconSize = preset.icon ? size * preset.icon_size! : 0;
  const iconGap = preset.icon ? size * preset.icon_gap! : 0;
  const iconWidth = preset.accent === 'split' ? size * preset.icon_width! : 0;
  const extraWidth = preset.accent === 'split'
    ? iconWidth + iconGap + padX + outline * 2
    : (padX + outline) * 2 + iconSize + iconGap;
  return {
    style, preset, extraWidth, iconSize, iconGap, iconWidth,
    size,
    padX,
    outline,
    height: size * preset.height,
    radius: size * preset.radius,
  };
}

function transitContrastText(hex: string): string {
  const luminance = (rgb: number[]) => rgb.map(v => v / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const value = luminance(hexToRgb(hex)), dark = luminance(hexToRgb('#26352F'));
  return (Math.max(value, dark) + .05) / (Math.min(value, dark) + .05) >= 1.05 / (value + .05)
    ? '#26352F' : '#FFFFFF';
}

/** 创建和主题更新共用线路徽章样式。 */
function paintTransitBadge(el: HTMLElement, color: string,
                           metrics = transitBadgeMetrics()): void {
  const {style, preset} = metrics;
  const name = el.dataset.routeName ?? el.textContent ?? '';
  el.dataset.preset = style.preset;
  // MapLibre 在同一元素上维护定位 transform，只清理标签自身的样式。
  for (const key of ['height', 'max-width', 'box-sizing', 'display', 'padding', 'box-shadow', 'overflow'])
    el.style.removeProperty(key);
  if (el.dataset.paintedPreset !== style.preset) {
    const title = document.createElement('span'); title.className = 'transit-badge-name';
    el.replaceChildren(title);
    if (preset.icon) {
      const tile = document.createElement('span'); tile.className = 'transit-badge-mode';
      tile.setAttribute('aria-hidden', 'true');
      const icon = document.createElement('span'); icon.className = 'transit-badge-icon';
      tile.append(icon); el.prepend(tile);
    }
    el.dataset.paintedPreset = style.preset;
  }
  el.querySelector('.transit-badge-name')!.textContent = name;
  el.style.setProperty('--badge-color', color);
  el.style.setProperty('--badge-surface', style.palette.surface);
  el.style.setProperty('--badge-text', preset.accent === 'fill' ? transitContrastText(color) : style.palette.text);
  el.style.setProperty('--badge-border', preset.accent === 'frame' ? color : style.palette.border);
  sizeTransitBadge(el, metrics);
  if (preset.icon) {
    const icon = el.querySelector('.transit-badge-icon') as HTMLElement;
    icon.style.maskImage = `url("${style.icons[el.dataset.routeKind ?? '']}")`;
    const tile = el.querySelector('.transit-badge-mode') as HTMLElement;
    tile.style.color = preset.accent === 'split' ? transitContrastText(color) : style.palette.text;
  }
  el.style.fontWeight = String(style.fontWeight);
  el.style.fontFamily = `"${style.font}", "Microsoft YaHei", sans-serif`;
  el.style.background = preset.accent === 'fill' ? color : style.palette.surface;
  el.style.color = preset.accent === 'fill' ? transitContrastText(color) : style.palette.text;
  el.style.borderColor = preset.accent === 'frame' ? color : style.palette.border;
}
function sizeTransitBadge(el: HTMLElement, metrics: ReturnType<typeof transitBadgeMetrics>): void {
  el.style.setProperty('--badge-pad', `${metrics.padX}px`);
  el.style.setProperty('--badge-outline', `${metrics.outline}px`);
  el.style.setProperty('--badge-radius', `${metrics.radius}px`);
  el.style.setProperty('--badge-size', `${metrics.size}px`);
  el.style.setProperty('--badge-icon-size', `${metrics.iconSize}px`);
  el.style.setProperty('--badge-icon-gap', `${metrics.iconGap}px`);
  el.style.setProperty('--badge-icon-width', `${metrics.iconWidth}px`);
  el.style.boxSizing = 'border-box'; el.style.display = 'flex';
  el.style.height = `${metrics.height}px`;
  el.style.maxWidth = `${180 + metrics.extraWidth}px`;
  el.style.overflow = 'hidden';
  el.style.fontSize = `${metrics.size}px`;
  el.style.borderWidth = `${metrics.outline}px`;
  el.style.borderRadius = `${metrics.radius}px`;
}
type TrafficDirection = 'forward' | 'backward';
type TrafficLayer = {id: string; direction?: TrafficDirection; casing: boolean};
const trafficLayers: TrafficLayer[] = [
  {id: 'traffic-base', casing: false},
  ...[true, false].flatMap(casing => (['forward', 'backward'] as const).map(direction => ({
    id: `traffic-${direction}-${casing ? 'casing' : 'fill'}`, direction, casing,
  }))),
];
const TRAFFIC_OUTLINE = .65;
function trafficFilter(direction?: TrafficDirection): ExpressionSpecification {
  return ['all', enabledFilter(), ['==', ['get', 'geometryRole'], 'traffic'],
    ...(direction ? [['>=', ['get', `${direction}Congestion`], 0] as ExpressionSpecification] : [])
  ] as ExpressionSpecification;
}
function trafficRoadWidthAt(z: number): ExpressionSpecification {
  // 原生路面按米投影，普通线道路沿用现有制图宽度。MapLibre 的世界瓦片为 512 px。
  const width: ExpressionSpecification = ['case', ['==', ['get', 'surface'], true],
    ['*', ['get', 'physicalWidth'], 2 ** z / MPP0 * 2
      * WEB_MERCATOR_METERS_PER_DEGREE / LOCAL_METERS_PER_DEGREE], ['*', lineScaleAt(z), widthAt(z)]];
  return currentManifest?.layers.road === false ? ['min', 14 * lineScaleAt(z), width] : width;
}
function trafficBandWidthAt(z: number): ExpressionSpecification {
  const road = trafficRoadWidthAt(z);
  const scale = lineScaleAt(z);
  return ['case', ['==', ['get', 'splitDirections'], true],
    ['min', 6 * scale, ['max', .5 * scale, ['*', .34, road]]],
    ['min', 10 * scale, ['max', .8 * scale, ['*', .8, road]]]];
}
function trafficMetric(direction?: TrafficDirection, casing = false, offset = false): ExpressionSpecification {
  return ['interpolate', ['exponential', 2], ['zoom'],
    ...Array.from({length: 25}, (_, z) => {
      const road = trafficRoadWidthAt(z), band = trafficBandWidthAt(z);
      const outline: ExpressionSpecification = ['min', TRAFFIC_OUTLINE * lineScaleAt(z), ['*', .2, band]];
      const outer: ExpressionSpecification = ['+', band, ['*', 2, outline]];
      const side: ExpressionSpecification = ['*', direction === 'forward' ? -1 : 1,
        ['coalesce', ['get', 'forwardSide'], 1]];
      const shift: ExpressionSpecification = ['case', ['==', ['get', 'splitDirections'], true],
        ['*', side, ['max', 0, ['/', ['-', road, outer], 2]]], 0];
      return [z, offset ? shift : direction ? (casing ? outer : band) : road];
    }).flat()] as ExpressionSpecification;
}
function trafficColor(direction: TrafficDirection): ExpressionSpecification {
  return trafficColorExpression(['get', `${direction}Congestion`],
    token('traffic').noDataColor, token('traffic').ramp);
}
function addTrafficLayers(): void {
  // 描边统一先于色带，不为路况构建独立高低层级。
  for (const layer of trafficLayers) {
    add({id: layer.id, type: 'line', source: 'traffic', filter: trafficFilter(layer.direction),
      layout: {'line-cap': 'round', 'line-join': 'round'},
      paint: {'line-color': layer.direction ? (layer.casing ? '#52635B' : trafficColor(layer.direction)) : token('traffic').noDataColor,
        'line-width': trafficMetric(layer.direction, layer.casing),
        'line-offset': layer.direction ? trafficMetric(layer.direction, false, true) : 0}});
  }
  updateTrafficPaint();
}
function updateTrafficColors(): void {
  if (!map.getSource('traffic')) return;
  for (const layer of trafficLayers) {
    map.setPaintProperty(layer.id, 'line-color', layer.direction
      ? (layer.casing ? '#52635B' : trafficColor(layer.direction)) : token('traffic').noDataColor);
  }
}
function updateTrafficPaint(): void {
  if (!map.getSource('traffic')) return;
  updateTrafficColors();
  for (const layer of trafficLayers) {
    map.setLayoutProperty(layer.id, 'visibility', layer.direction || currentManifest?.layers.road === false
      ? 'visible' : 'none');
    map.setFilter(layer.id, trafficFilter(layer.direction));
    map.setPaintProperty(layer.id, 'line-width', trafficMetric(layer.direction, layer.casing));
    if (layer.direction) map.setPaintProperty(layer.id, 'line-offset', trafficMetric(layer.direction, false, true));
  }
}
function releaseTraffic(): void {
  trafficRequest?.abort(); trafficRequest = null; trafficSourceRevision = null;
  for (const id of groups.traffic) {
    if (map.getLayer(id)) map.removeLayer(id);
    const index = customLayers.indexOf(id);
    if (index >= 0) customLayers.splice(index, 1);
  }
  if (map.getSource('traffic')) map.removeSource('traffic');
}
async function syncTraffic(): Promise<void> {
  const manifest = currentManifest, provider = sceneProvider;
  if (!manifest || !provider || manifest.layers.traffic === false || !manifest.sources.traffic) {
    releaseTraffic(); return;
  }
  if (trafficSourceRevision === manifest.sourceRevisions.traffic) return;
  releaseTraffic();
  const request = new AbortController(); trafficRequest = request;
  const generation = loadGeneration;
  try {
    const data = await provider.json(new URL(manifest.sources.traffic, provider.base), undefined, request.signal);
    if (request.signal.aborted || generation !== loadGeneration || currentManifest !== manifest) return;
    map.addSource('traffic', {type: 'geojson', data, maxzoom: 18, buffer: 32,
      tolerance: .375, promoteId: 'featureId'});
    trafficSourceRevision = manifest.sourceRevisions.traffic;
    addTrafficLayers(); applyRenderStack(); applyContextStyling();
  } catch (error) {
    if (!request.signal.aborted) throw error;
  } finally {
    if (trafficRequest === request) trafficRequest = null;
  }
}
const RAIL_TYPES = ['metro', 'tram', 'cablecar', 'monorail', 'train'] as const;
function railTypePaint(field: string, fallback: string | number): ExpressionSpecification {
  const value: unknown[] = ['match', ['get', 'railType']];
  for (const kind of RAIL_TYPES) value.push(kind, token('railway').types?.[kind]?.[field] ?? fallback);
  value.push(fallback);
  return value as ExpressionSpecification;
}
function railWidth(field: 'width' | 'stationWidth' | 'dashWidth'): NumericStyle { return scaledLine(railTypePaint(field, 0)); }
function railFilter(station: boolean): ExpressionSpecification {
  return ['all', ['==', ['get', 'geometryRole'], 'track'],
    ['==', ['get', 'station'], station]];
}
function addRailways(): void {
  add({id: 'railway-base', type: 'line', source: 'railway', filter: railFilter(false),
    layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {
      'line-color': railTypePaint('baseColor', '#C36E69'),
      'line-opacity': railTypePaint('baseOpacity', 1), 'line-width': railWidth('width')}});
  for (const kind of RAIL_TYPES) {
    const paint = token('railway').types?.[kind] ?? {};
    add({id: `railway-dash-${kind}`, type: 'line', source: 'railway',
      filter: ['all', ...railFilter(false).slice(1), ['==', ['get', 'railType'], kind]],
      layout: {'visibility': paint.dashEnabled ? 'visible' : 'none',
        'line-cap': 'butt', 'line-join': 'round'}, paint: {
        'line-color': paint.dashColor ?? '#FFFFFF',
        'line-opacity': Number(paint.dashOpacity ?? 1),
        'line-width': railWidth('dashWidth'),
        'line-dasharray': paint.dashPattern ?? [2, 4]}});
  }
  add({id: 'railway-station', type: 'line', source: 'railway', filter: railFilter(true),
    layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {
      'line-color': railTypePaint('baseColor', '#C36E69'),
      'line-opacity': railTypePaint('baseOpacity', 1),
      'line-width': railWidth('stationWidth')}});
}
function addLandUseLayer(): void {
  const use = token('land_use');
  add({id: 'land-use', type: 'fill', source: currentManifest?.sources.landUse ? 'land-use' : 'buildings', paint: {
    'fill-color': categoryColor(use.colors ?? {}, '#D8D0C8'),
    'fill-opacity': use.opacity ?? .35}});
}
function addDomains(): void {
  const land = token('terrain'), water = token('water'), building = token('building');
  const district = token('district'), zone = token('zone');
  const transit = token('transit');
  updateTransitViewLayout();
  if (!map.getSource('transit-view-stations')) map.addSource('transit-view-stations', {
    type: 'geojson', data: {type: 'FeatureCollection', features: showsTransportOverlay() ? transitStationDisplayFeatures : []},
    maxzoom: 24, buffer: 256, tolerance: 0, promoteId: 'featureId',
  });
  if (!map.getSource('transit-view-pois-displaced')) map.addSource('transit-view-pois-displaced', {
    type: 'geojson', data: {type: 'FeatureCollection', features: transitPoiDisplayFeatures},
    maxzoom: 24, buffer: 256, tolerance: 0, promoteId: 'featureId',
  });
  if (!map.getSource('map-pois-displaced')) map.addSource('map-pois-displaced', {
    type: 'geojson', data: {type: 'FeatureCollection', features: mapPoiDisplayFeatures},
    maxzoom: 24, buffer: 256, tolerance: 0, promoteId: 'featureId',
  });
  if (map.getSource('satellite-image')) add({id: 'satellite', type: 'raster', source: 'satellite-image', paint: {'raster-fade-duration': 0}});
  if (map.getSource('terrain-image')) add({ id: 'terrain', type: 'raster', source: 'terrain-image',
    paint: { 'raster-opacity': land.opacity ?? 1, 'raster-fade-duration': 0 } });
  add({ id: 'forest', type: 'circle', source: 'forest', paint: {
    'circle-color': land.forest ?? '#B8DCA0', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 1, 15, 7],
    'circle-opacity': .58 } });
  if (map.getSource('built-area')) add({ id: 'built-area', type: 'fill', source: 'built-area', paint: {
    'fill-color': token('built_area').fill ?? '#F1F0EB', 'fill-opacity': token('built_area').opacity ?? 1 } });
  addLandUseLayer();
  add({ id: 'poi-area', type: 'fill', source: 'buildings', paint: {
    'fill-color': buildingColor(), 'fill-outline-color': 'rgba(0,0,0,0)' } });
  add({ id: 'water-fill', type: 'fill', source: 'water', filter: ['==', ['get', 'geometryRole'], 'water'],
    paint: { 'fill-color': water.fill ?? '#80D4FE',
      'fill-opacity': painterOrderedOpacity(1) } });
  add({ id: 'water-shore', type: 'line', source: 'water', filter: ['==', ['get', 'geometryRole'], 'water'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': water.border ?? '#60C0E8', 'line-width': water.width ?? 1 } });
  if (usesAreaSurfaces(currentManifest)) {
    for (const kind of areaKinds) {
      const surface = new AreaSurfaceLayer(`${kind}-surface`, sourceData[areaSources[kind]].features,
        () => artworkView.observationScale);
      areaSurfaces[kind] = surface;
      add(surface);
    }
  } else {
    add({ id: 'district-fill', type: 'fill', source: 'districts', paint: {
      'fill-color': districtFillColor(), 'fill-opacity': district.opacity ?? .3 } });
    add({ id: 'district-border', type: 'line', source: 'districts', paint: {
      'line-color': district.border ?? '#A6A6AD', 'line-width': 1.2 } });
    add({ id: 'zone-fill', type: 'fill', source: 'zones', paint: {
      'fill-color': zone.fill ?? '#C8C8C8', 'fill-opacity': zone.opacity ?? .35 } });
    add({ id: 'zone-border', type: 'line', source: 'zones', paint: {
      'line-color': zone.border ?? '#A89880', 'line-width': 1.2 } });
  }
  add({ id: 'buildings', type: 'fill', source: 'buildings', paint: {
    'fill-color': buildingColor(),
    'fill-outline-color': building.border ?? '#B9B2A5' } });
  add({ id: 'procedural-objects', type: 'fill', source: 'procedural-objects', paint: {
    'fill-color': token('procedural_objects').fill ?? '#D8D2C6',
    'fill-outline-color': token('procedural_objects').border ?? '#8E877C' } });
  addRailways();
  addRoads();
  add({ id: 'transit', type: 'line', source: 'transit', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': transit.width ?? 3 } });
  add({ id: 'stations', type: 'circle', source: 'stations', paint: {
    'circle-radius': stationStyle('radius'), 'circle-color': transit.stationFill ?? '#fff',
    'circle-stroke-color': transit.stationOutline ?? '#39424C', 'circle-stroke-width': stationStyle('stroke') } });
  add({ id: 'tv-line', type: 'line', source: 'transit', filter: alwaysFalseFilter(),
    layout: { 'visibility': 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': transit.width ?? 3 } });
  add({ id: 'tv-station', type: 'circle', source: 'transit-view-stations',
    filter: ['!=', ['get', 'isInterchange'], true],
    layout: { 'visibility': 'none' }, paint: {
      'circle-radius': stationStyle('radius'), 'circle-color': transit.stationFill ?? '#fff',
      'circle-stroke-color': transit.stationOutline ?? '#39424C', 'circle-stroke-width': stationStyle('stroke') } });
  add({ id: 'tv-station-interchange', type: 'symbol', source: 'transit-view-stations',
    filter: ['==', ['get', 'isInterchange'], true], layout: {
      'visibility': 'none', 'icon-image': 'cmm-station-interchange',
      'icon-size': stationStyle('iconSize'),
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-padding': 0,
      'icon-pitch-alignment': 'viewport', 'icon-rotation-alignment': 'viewport' } });
  add({ id: 'pois', type: 'symbol', source: 'map-pois-displaced',
    filter: poiFilter(),
    layout: { 'icon-image': ['get', 'iconKey'], 'icon-size': poiStyle(false),
      'symbol-sort-key': ['*', -1, ['get', 'priority']], 'symbol-z-order': 'source',
      'icon-allow-overlap': false, 'icon-ignore-placement': false, 'icon-padding': 4, 'icon-offset': stationIconOffset(...poiDimensions(false)), 'icon-rotation-alignment': 'viewport', 'icon-pitch-alignment': 'viewport' } });
  // 普通地图使用此图层；交通视图通过 tv-poi 管理图标。
  add({ id: 'transit-pois', type: 'symbol', source: 'map-pois-displaced',
    filter: transitPoiFilter(),
    layout: { 'icon-image': ['get', 'iconKey'], 'icon-size': poiStyle(false),
      'symbol-sort-key': ['*', -1, ['get', 'priority']], 'symbol-z-order': 'source',
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-padding': 0, 'icon-offset': stationIconOffset(...poiDimensions(false)), 'icon-rotation-alignment': 'viewport', 'icon-pitch-alignment': 'viewport' } });
  add({ id: 'tv-poi', type: 'symbol', source: 'transit-view-pois-displaced', filter: transitViewPoiFilter(),
    layout: { 'visibility': 'none', 'icon-image': ['get', 'iconKey'],
      'icon-size': poiStyle(true),
      'symbol-sort-key': ['*', -1, ['get', 'transitPriority']], 'symbol-z-order': 'source',
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-padding': 0, 'icon-offset': stationIconOffset(...poiDimensions(true)), 'icon-rotation-alignment': 'viewport', 'icon-pitch-alignment': 'viewport' } });
  addBaseTextLayers();
  for (const source of ['building-highlight', 'inspector-selection', 'search-selection']) {
    add({ id: `${source}-fill`, type: 'fill', source,
      filter: ['==', ['geometry-type'], 'Polygon'], paint: {
        'fill-color': '#2772E2', 'fill-opacity': .16,
        'fill-outline-color': '#1E65CB' } });
    add({ id: `${source}-line`, type: 'line', source,
      filter: ['==', ['geometry-type'], 'LineString'], layout: {
        'line-cap': 'round', 'line-join': 'round' }, paint: {
        'line-color': '#2772E2', 'line-width': 6, 'line-opacity': .86 } });
    add({ id: `${source}-point`, type: 'circle', source,
      filter: ['==', ['geometry-type'], 'Point'], paint: {
        'circle-radius': 11, 'circle-color': '#2772E2', 'circle-opacity': .18,
        'circle-stroke-color': '#1E65CB', 'circle-stroke-width': 3 } });
  }
}

// Compare geometry and layer options separately from paints that can be applied in place.
function basePaintOptions(theme: FullTheme) {
  const {background, opacity, levels, ...road} = theme.road;
  return {
    road: {...road, levels: Object.fromEntries(Object.entries(levels).map(([id, {fill, border, ...rest}]) => [id, rest]))},
    waterWidth: theme.water?.width,
    railway: RAIL_TYPES.map(kind => {
      const {baseColor, baseOpacity, dashColor, dashOpacity, ...options} = theme.railway?.types?.[kind] ?? {};
      return options;
    }),
    districtWidth: theme.district?.width, zoneWidth: theme.zone?.width,
    trafficWidth: theme.traffic?.widthScale, trafficMinLane: theme.traffic?.minLanePx,
  };
}

function updateAreaSurfacePaint(kind: 'district' | 'zone') {
  const paint = token(kind);
  areaSurfaces[kind]?.updatePaint({colors: paint.fillColors ?? [paint.fill ?? (kind === 'district' ? '#D9E8CC' : '#C8C8C8')],
    border: paint.border ?? (kind === 'district' ? '#A6A6AD' : '#A89880'), width: Number(paint.width ?? 1),
    opacity: Number(paint.opacity ?? (kind === 'district' ? .3 : .35)),
    fade: contextMode === 'transit' && contextOptions.base_district === false ? 0 : contextFade()});
}

function applyBaseColors(previous?: FullTheme): void {
  const changed = (key: string) => !previous || JSON.stringify(previous[key]) !== JSON.stringify(currentTheme![key]);
  const set = (id: string, prop: string, value: any) => {
    if (map.getLayer(id) && value !== undefined) map.setPaintProperty(id, prop, value);
  };
  if (!previous || previous.road.background !== currentTheme!.road.background)
    set('background', 'background-color', currentTheme!.road.background);
  if (!previous || previous.road.opacity !== currentTheme!.road.opacity ||
      JSON.stringify(previous.road.levels) !== JSON.stringify(currentTheme!.road.levels)) {
    for (const layer of roadLayers) {
      set(layer.id, `${layer.type}-opacity`, roadOpacity());
      set(layer.id, `${layer.type}-color`, roadToken(layer.paint, layer.paint === 'border' ? '#c3c8d2' : '#fff'));
    }
  }
  if (changed('building')) {
    const fill = buildingColor();
    set('buildings', 'fill-color', fill); set('poi-area', 'fill-color', fill);
    set('buildings', 'fill-outline-color', token('building').border);
  }
  if (changed('water')) {
    set('water-fill', 'fill-color', token('water').fill); set('water-shore', 'line-color', token('water').border);
  }
  if (changed('railway')) {
    for (const id of ['railway-base', 'railway-station']) {
      set(id, 'line-color', railTypePaint('baseColor', '#C36E69'));
      set(id, 'line-opacity', railTypePaint('baseOpacity', 1));
    }
    for (const kind of RAIL_TYPES) {
      const paint = token('railway').types?.[kind] ?? {};
      set(`railway-dash-${kind}`, 'line-color', paint.dashColor ?? '#FFFFFF');
      set(`railway-dash-${kind}`, 'line-opacity', Number(paint.dashOpacity ?? 1));
    }
  }
  if (previous && changed('traffic')) updateTrafficColors();
  if (changed('terrain')) {
    set('forest', 'circle-color', token('terrain').forest);
    set('terrain', 'raster-opacity', Number(token('terrain').opacity ?? 1) * contextFade());
  }
  if (changed('land_use')) {
    set('land-use', 'fill-color', categoryColor(token('land_use').colors ?? {}, '#D8D0C8'));
    set('land-use', 'fill-opacity', Number(token('land_use').opacity ?? .35) * contextFade());
  }
  if (changed('built_area')) {
    const paint = token('built_area');
    set('built-area', 'fill-color', paint.fill);
    set('built-area', 'fill-opacity', Number(paint.opacity ?? 1) * contextFade());
    builtSurfaceLayer?.updatePaint({color: paint.fill ?? '#F1F0EB', opacity: Number(paint.opacity ?? 1),
      bufferMeters: paint.includeParks ? 0 : Number(paint.bufferMeters ?? 60), edgeMeters: Number(paint.edgeMeters ?? 0)});
  }
  for (const kind of areaKinds) if (changed(kind)) {
    const paint = token(kind);
    set(kind+'-fill', 'fill-color', kind === 'district' ? districtFillColor() : paint.fill);
    set(kind+'-border', 'line-color', paint.border);
    const fade = contextMode === 'transit' && contextOptions.base_district === false ? 0 : contextFade();
    set(kind+'-fill', 'fill-opacity', Number(paint.opacity ?? (kind === 'district' ? .3 : .35)) * fade);
    updateAreaSurfacePaint(kind);
  }
  if (changed('procedural_objects')) {
    set('procedural-objects', 'fill-color', token('procedural_objects').fill);
    set('procedural-objects', 'fill-outline-color', token('procedural_objects').border);
  }
}

function applyArtworkTheme(theme: FullTheme, entering = false): boolean {
  const previous = currentTheme;
  if (!entering && previous && JSON.stringify(basePaintOptions(previous)) === JSON.stringify(basePaintOptions(theme))) {
    currentTheme = theme;
    applyBaseColors(previous);
    return false;
  }
  artworkView.restorePaint();
  applyTheme(theme);
  return true;
}

function applyTheme(theme: FullTheme): void {
  currentTheme = theme; applyBaseColors();
  const set = (id: string, prop: string, value: any) => { if (map.getLayer(id) && value !== undefined) map.setPaintProperty(id, prop, value); };
  const filter = (id: string, value: any) => { if (map.getLayer(id)) map.setFilter(id, value); };
  for (const layer of roadLayers) {
    filter(layer.id, roadFilter(layer));
    if (layer.type === 'line') set(layer.id, 'line-width',
      roadLineWidth(layer.width));
  }
  set('water-shore', 'line-width', token('water').width);
  set('railway-base', 'line-width', railWidth('width'));
  set('railway-station', 'line-width', railWidth('stationWidth'));
  for (const kind of RAIL_TYPES) {
    const paint = token('railway').types?.[kind] ?? {};
    const id = `railway-dash-${kind}`;
    set(id, 'line-width', railWidth('dashWidth'));
    set(id, 'line-dasharray', paint.dashPattern ?? [2, 4]);
    if (map.getLayer(id)) map.setLayoutProperty(
      id, 'visibility', paint.dashEnabled ? 'visible' : 'none');
  }
  set('district-border', 'line-width', Number(token('district').width ?? 1));
  set('zone-border', 'line-width', Number(token('zone').width ?? 1));
  updateTrafficPaint();
  set('transit', 'line-width', transportLineWidth(false));
  for (const id of ['stations', 'tv-station']) {
    set(id, 'circle-color', token('transit').stationFill ?? '#fff');
    set(id, 'circle-stroke-color', token('transit').stationOutline ?? '#39424C');
  }
  set('tv-line', 'line-width', transportLineWidth());
  if (map.getLayer('pois')) map.setFilter('pois', poiFilter());
  set('built-area', 'fill-opacity', token('built_area').opacity ?? 1);
  labelLayoutKey = '';
  const wm = token('watermark'); watermark.textContent = String(wm.text ?? 'CSLModernMap');
  watermark.style.opacity = String(wm.opacity ?? .4);
  watermark.classList.toggle('preview-watermark-left', wm.position === 'bl');
  for (const item of markers.values()) { const el = item.marker.getElement();
    if (item.group === 'transit-line') {
      if (el.classList.contains('map-transit-badge')) paintTransitBadge(el, el.dataset.color!);
      else for (const part of el.querySelectorAll<HTMLElement>('.map-transit-badge')) {
        paintTransitBadge(part, part.dataset.color!);
      }
    } else {
      el.style.color = labelColor(item.labelType, item.featureColor);
      const base = baseFontSize(item.labelType, item.transitStationName);
      const size = previewFontSize(base, map.getZoom());
      el.style.fontSize = `${size}px`;
      el.style.fontFamily = labelFontFamily(item.labelType);
      applyLabelHalo(el, item.labelType, base > 0 ? size / base : 0);
    } }
  applyContextStyling();
  navigation.refreshStyle();
  scheduleLabels();
}
let satellitePreviousLayers: Record<string, boolean> | null = null;
async function setLayerVisibility(layer: string, visible: boolean): Promise<void> {
  if (layer === 'satellite' && visible && currentManifest?.satelliteSize) {
    const gl = map.getCanvas().getContext('webgl2');
    if (gl) {
      const limit = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;
      const size = Math.max(...currentManifest.satelliteSize);
      if (size > limit) {
        currentManifest.layers.satellite = false;
        reportError(new Error(i18n.t('satellite_texture_limit', {size, limit})), undefined, false);
        return;
      }
    }
  }
  if (EMBEDDED && layer === 'satellite' && currentManifest && visible !== currentManifest.layers.satellite) {
    if (visible) {
      satellitePreviousLayers = {...currentManifest.layers};
      for (const name of ['terrain', 'water', 'building', 'built_area', 'land_use', 'poi_area', 'procedural_objects', 'road', 'railway', 'traffic'])
        await setLayerVisibility(name, false);
    } else if (satellitePreviousLayers) {
      const previous = satellitePreviousLayers; satellitePreviousLayers = null;
      for (const [name, enabled] of Object.entries(previous)) if (name !== 'satellite') await setLayerVisibility(name, enabled);
    }
  }
  for (const id of groups[layer] ?? []) if (map.getLayer(id)) {
    let layerVisible = visible;
    if (layerVisible && layer === 'railway' && id.startsWith('railway-dash-')) {
      const kind = id.slice('railway-dash-'.length);
      layerVisible = Boolean(token('railway').types?.[kind]?.dashEnabled);
    }
    map.setLayoutProperty(id, 'visibility', layerVisible ? 'visible' : 'none');
  }
  if (layer === 'district' || layer === 'zone') {
    const surface = areaSurfaces[layer];
    if (surface) { surface.visible = visible; map.triggerRepaint(); }
  }
  for (const item of markers.values()) if (item.group === layer) item.marker.getElement().style.display = visible ? '' : 'none';
  if (layer === 'watermark') watermark.style.display = visible ? '' : 'none';
  if (currentManifest) currentManifest.layers[layer] = visible;
  if (visible) requestLayerSources?.(layer);
  if (layer === 'traffic') {
    for (const checkbox of document.querySelectorAll<HTMLInputElement>('input[data-layer="traffic"]')) checkbox.checked = visible;
    await syncTraffic();
  }
  if (layer === 'road' || layer === 'traffic') updateTrafficPaint();
  if (layer === 'poi' || layer === 'metro') applyContextStyling();
  if (layer === 'label') updateBaseTextLayers();
  if (layer === 'building') preview3d.refresh();
  artworkView.keepOverlayLayersHidden();
  labelLayoutKey = '';
  scheduleLabels();
  updateQuickLayers();
}
function applyContextStyling(): void {
  updateBaseTextLayers();
  const active = contextMode === 'transit';
  const overlay = showsTransportOverlay();
  const fade = contextFade();
  for (const kind of areaKinds) updateAreaSurfacePaint(kind);
  if (!map.getLayer('transit')) return;
  const transitLayers = (contextOptions.layers ?? {}) as Record<string, unknown>;
  const transitLayerVisible = (key: string, fallback = true): boolean =>
    transitLayers[key] == null ? fallback : transitLayers[key] !== false;
  builtSurfaceLayer?.setFade(fade);
  const set = (id: string, prop: string, value: any) => {
    if (map.getLayer(id)) map.setPaintProperty(id, prop, value);
  };
  set('terrain', 'raster-opacity', Number(token('terrain').opacity ?? 1) * fade);
  set('forest', 'circle-opacity', .58 * fade);
  set('built-area', 'fill-opacity', Number(token('built_area').opacity ?? 1) * fade);
  set('land-use', 'fill-opacity', Number(token('land_use').opacity ?? .35) * fade);
  set('poi-area', 'fill-opacity', fade);
  for (const id of ['water-fill', 'district-fill', 'zone-fill', 'buildings', 'procedural-objects']) {
    const opacity = (id === 'district-fill' ? Number(token('district').opacity ?? .3)
      : id === 'zone-fill' ? Number(token('zone').opacity ?? .35) : 1) * fade;
    const allowed = !active || (id === 'water-fill'
      ? contextOptions.base_water !== false
      : id === 'district-fill' || id === 'zone-fill'
        ? contextOptions.base_district !== false : true);
    set(id, 'fill-opacity', id === 'water-fill' ? painterOrderedOpacity(allowed ? opacity : 0) : allowed ? opacity : 0);
  }
  for (const id of ['water-shore', 'district-border', 'zone-border',
    'railway-base', 'railway-dash-metro', 'railway-dash-tram',
    'railway-dash-cablecar', 'railway-dash-monorail', 'railway-dash-train',
    'railway-station', ...groups.traffic]) {
    const allowed = !active || (id === 'water-shore'
      ? contextOptions.base_water !== false
      : id === 'district-border' || id === 'zone-border'
        ? contextOptions.base_district !== false
      : true);
    set(id, 'line-opacity', allowed ? fade : 0);
  }
  for (const layer of roadLayers) set(layer.id, `${layer.type}-opacity`, roadOpacity());
  const ordinaryPoiMode = String(contextOptions.ordinary_pois ?? 'hidden');
  set('pois', 'icon-opacity', active ? ordinaryPoiMode === 'visible' ? 1 : ordinaryPoiMode === 'dim' ? fade : 0 : 1);
  set('transit-pois', 'icon-opacity', overlay ? 0 : 1);
  set('transit', 'line-opacity', overlay ? 0 : 1);
  set('stations', 'circle-opacity', overlay ? 0 : 1);
  set('tv-line', 'line-opacity', overlay ? 1 : 0);
  set('tv-line', 'line-width', transportLineWidth());
  set('tv-line', 'line-color', ['get', 'color']);
  set('tv-station', 'circle-opacity', overlay ? 1 : 0);
  updateStationSizes();
  for (const id of ['pois', 'transit-pois']) if (map.getLayer(id)) {
    const allowOverlap = token('poi').overlapAvoidance === false;
    map.setLayoutProperty(id, 'icon-allow-overlap', allowOverlap);
    map.setLayoutProperty(id, 'icon-ignore-placement', allowOverlap);
  }
  if (map.getLayer('tv-poi')) {
    // POI 与站点开关互相独立，同时启用时两个类别都必须可见，图标围绕精确站点锚点偏移。
    const allowOverlap = token('poi').transitOverlapAvoidance === false;
    map.setLayoutProperty('tv-poi', 'icon-allow-overlap', allowOverlap);
    map.setLayoutProperty('tv-poi', 'icon-ignore-placement', allowOverlap);
    map.setLayoutProperty('tv-poi', 'icon-padding', 0);
  }
  if (map.getLayer('tv-line')) map.setFilter('tv-line', transitViewLineFilter());
  updateTransitViewLayout();
  updatePoiSizes(); updatePoiFilters();
  if (overlay) {
    map.setLayoutProperty('transit', 'visibility', 'none');
    map.setLayoutProperty('stations', 'visibility', 'none');
    map.setLayoutProperty('transit-pois', 'visibility', 'none');
    map.setLayoutProperty('tv-line', 'visibility',
      transitLayerVisible('lines') ? 'visible' : 'none');
    map.setLayoutProperty('tv-station', 'visibility',
      contextOptions.stations === false ? 'none' : 'visible');
    map.setLayoutProperty('tv-station-interchange', 'visibility',
      contextOptions.stations === false ? 'none' : 'visible');
    map.setLayoutProperty('tv-poi', 'visibility',
      contextOptions.facilities_enabled === false ? 'none' : 'visible');
  } else if (currentManifest) {
    const visible = currentManifest.layers.metro !== false;
    map.setLayoutProperty('transit', 'visibility', visible ? 'visible' : 'none');
    map.setLayoutProperty('stations', 'visibility', visible && contextOptions.stations !== false ? 'visible' : 'none');
    map.setLayoutProperty('transit-pois', 'visibility', currentManifest.layers.poi === false ? 'none' : 'visible');
    map.setLayoutProperty('tv-line', 'visibility', 'none');
    map.setLayoutProperty('tv-station', 'visibility', 'none');
    map.setLayoutProperty('tv-station-interchange', 'visibility', 'none');
    map.setLayoutProperty('tv-poi', 'visibility', 'none');
    // 类型白名单：没带（undefined）就保持不过滤，带了就按类型收敛。
    if (map.getLayer('transit')) map.setFilter('transit', transitViewLineFilter());
    if (map.getLayer('stations')) {
      const clauses = selectedTransitRouteIds().map<ExpressionSpecification>(id => ['in', id, ['get', 'servingLineIds']]);
      map.setFilter('stations', contextOptions.line_ids == null && contextOptions.types == null
        ? null : clauses.length ? ['any', ...clauses] : alwaysFalseFilter());
    }
  }
  labelLayoutKey = '';
  scheduleLabels();
}
function setContextMode(mode: string, options: Record<string, any> = {}): void {
  const nextMode = mode === 'transit' ? 'transit' : 'map';
  if (nextMode !== contextMode) navigation.simulation.stop();
  contextMode = nextMode;
  contextOptions = {...options};
  applyContextStyling();
  updateQuickLayers();
}
const selectableObjects: Record<string, { objectType: string; styleLayer: string; priority: number }> = {
  [BUILDINGS_3D]: {objectType: 'building', styleLayer: 'building', priority: 98},
  'tv-poi': {objectType: 'poi', styleLayer: 'poi', priority: 108},
  'tv-station': {objectType: 'transit_station', styleLayer: 'metro', priority: 97},
  'tv-station-interchange': {objectType: 'transit_station', styleLayer: 'metro', priority: 97},
  'tv-line': {objectType: 'transit_line', styleLayer: 'metro', priority: 92},
  'transit-pois': {objectType: 'poi', styleLayer: 'poi', priority: 105},
  pois: {objectType: 'poi', styleLayer: 'poi', priority: 100},
  stations: {objectType: 'transit_station', styleLayer: 'metro', priority: 95},
  transit: {objectType: 'transit_line', styleLayer: 'metro', priority: 90},
  'railway-station': {objectType: 'railway', styleLayer: 'railway', priority: 82},
  'railway-base': {objectType: 'railway', styleLayer: 'railway', priority: 80},
  'road-surface-fill': {objectType: 'road', styleLayer: 'road', priority: 75},
  'road-transition-fill': {objectType: 'road', styleLayer: 'road', priority: 75},
  'road-fill': {objectType: 'road', styleLayer: 'road', priority: 74},
  buildings: {objectType: 'building', styleLayer: 'building', priority: 60},
  'poi-area': {objectType: 'building', styleLayer: 'building', priority: 60},
  'zone-fill': {objectType: 'zone', styleLayer: 'zone', priority: 50},
  'district-fill': {objectType: 'district', styleLayer: 'district', priority: 40},
};
function navigationPlaceAt(point: {x: number; y: number}): Endpoint['place'] {
  const layers = ['pois', 'transit-pois', BUILDINGS_3D, 'buildings', 'poi-area'].filter(id => map.getLayer(id));
  if (!layers.length) return undefined;
  const features = map.queryRenderedFeatures([point.x, point.y], {layers});
  const feature = features.find(f => f.properties?.buildingId != null && Number(f.properties.buildingId) > 0)
    ?? features.find(f => f.layer.id === 'buildings');
  if (!feature) return undefined;
  const building = sourceData.buildings?.features?.find((f: any) =>
    String(f.properties?.buildingId) === String(feature.properties?.buildingId));
  const selected = building ?? feature;
  return {name: String(selected.properties?.name || feature.properties?.name || i18n.t('navigation_building')), geometry: selected.geometry};
}

function selectableLayerIds(): string[] {
  return Object.keys(selectableObjects).filter(id => Boolean(map.getLayer(id)));
}
function featureAt(point: {x: number; y: number}): any | null {
  const layers = selectableLayerIds();
  const radius = 5;
  const left = point.x-radius, right = point.x+radius, top = point.y-radius, bottom = point.y+radius;
  const features = layers.length ? map.queryRenderedFeatures([[left, top], [right, bottom]], {layers}) : [];
  const unique = new Map<string, any>();
  for (const feature of features) {
    const featureId = String(feature.properties?.featureId ?? feature.id ?? '');
    const key = `${feature.layer.id}:${featureId}`;
    if (!unique.has(key)) unique.set(key, feature);
  }
  const picked = [...unique.values()].sort((a, b) =>
    (selectableObjects[b.layer.id]?.priority ?? 0)
    - (selectableObjects[a.layer.id]?.priority ?? 0)
    || Number(b.properties?.rank ?? 0)-Number(a.properties?.rank ?? 0))[0] ?? null;
  if (picked || !areaKinds.some(kind => areaSurfaces[kind]?.visible)) return picked;
  const ring = [[left, top], [right, top], [right, bottom], [left, bottom], [left, top]]
    .map(([x, y]) => map.unproject([x, y]).toArray());
  for (const kind of ['zone', 'district'] as const) {
    const feature = areaSurfaces[kind]?.pick([ring]);
    if (feature) return {...feature, source: areaSources[kind], layer: {id: `${kind}-fill`}};
  }
  return null;
}
function setInspectorHighlight(feature: any | null): void {
  setFeatureHighlight('inspector-selection', feature);
}
function setFeatureHighlight(sourceId: string, feature: any | null): void {
  const source = map.getSource(sourceId) as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  const data = feature ? {type: 'FeatureCollection', features: [{
    type: 'Feature', geometry: feature.geometry,
    properties: {featureId: String(feature.properties?.featureId ?? feature.id ?? '')},
  }]} : {type: 'FeatureCollection', features: []};
  source.setData(data as any);
}
function clearInspectorSelection(report = true): void {
  if (!inspectorSelectionId && !report) return;
  inspectorSelectionId = '';
  inspectorSelectedFeature = null;
  setInspectorHighlight(null);
  if (report) postEvent('objectSelectionCleared');
}
function restoreInspectorSelection(selection: any): void {
  if (!selection) return;
  if (selection.source === 'building-catalog' && selection.feature) {
    inspectorSelectionId = selection.featureId;
    inspectorSelectedFeature = selection.feature;
    setInspectorHighlight(selection.feature);
    return;
  }
  const sourceKey = ({'transit-view-pois-displaced': 'transitViewPois', 'map-pois-displaced': 'pois',
    'transit-view-lines': 'transit', 'transit-view-stations': 'stations'} as Record<string, string>)[selection.source]
    ?? Object.keys(sourceIds).find(key => sourceIds[key] === selection.source);
  const candidates = sourceKey ? (sourceData[sourceKey]?.features ?? []) : [];
  const identityKey = ['roadId', 'buildingId', 'poiId', 'stationId', 'lineId', 'areaId']
    .find(key => selection.properties?.[key] != null);
  const feature = identityKey
    ? candidates.find((f: any) => String(f.properties?.[identityKey]) === String(selection.properties[identityKey]))
    : candidates.find((f: any) => String(f.properties?.featureId ?? f.id) === String(selection.featureId));
  if (!feature) { setInspectorHighlight(null); inspectorSelectedFeature = null; return; }
  inspectorSelectionId = String(feature.properties?.featureId ?? feature.id);
  inspectorSelectedFeature = feature;
  setInspectorHighlight(feature);
}
function locateInspectorObject(): void {
  if (inspectorSelectedFeature) locateGeometry(inspectorSelectedFeature.geometry);
}
function locateGeometry(geometry: GeoJSON.Geometry): void {
  preview3d.reset();
  const coordinates = 'coordinates' in geometry ? geometry.coordinates : undefined;
  if (!coordinates) return;
  const points: number[][] = [];
  function collect(value: any): void {
    if (Array.isArray(value) && typeof value[0] === 'number' && typeof value[1] === 'number') points.push(value);
    else if (Array.isArray(value)) value.forEach(collect);
  }
  collect(coordinates);
  if (!points.length) return;
  if (artworkView.active) { artworkView.locate(points as [number, number][]); return; }
  if (points.length === 1) { map.easeTo({center: points[0] as [number, number], zoom: Math.max(map.getZoom(), 15), duration: 260, bearing: outputBearing(outputRotation)}); return; }
  const bounds = new maplibregl.LngLatBounds();
  for (const point of points) bounds.extend(point as [number, number]);
  const width = map.getCanvas().clientWidth;
  map.fitBounds(bounds, {padding: {top: 65, bottom: 55, left: 35, right: Math.min(120, width / 4)},
    maxZoom: 17, duration: 260, bearing: outputBearing(outputRotation)});
}
function pickObjectAt(point: {x: number; y: number}, artwork?: ArtworkPick): void {
  const feature = featureAt(point);
  if (feature) inspectFeature(feature, artwork); else clearInspectorSelection();
}
function inspectFeature(feature: any, artwork?: ArtworkPick): void {
  const descriptor = selectableObjects[feature.layer.id];
  if (!descriptor) return;
  const featureId = String(feature.properties?.featureId ?? feature.id ?? '');
  inspectorSelectionId = featureId;
  const originalSource = feature.source === 'map-pois-displaced' ? sourceData.pois
    : feature.source === 'transit-view-pois-displaced' ? sourceData.transitViewPois : undefined;
  const original = originalSource?.features.find((item: any) => String(item.properties?.featureId ?? item.id) === featureId) ?? feature;
  inspectorSelectedFeature = original;
  setInspectorHighlight(original);
  postEvent('objectSelected', {
    objectType: descriptor.objectType,
    styleLayer: descriptor.styleLayer,
    featureId,
    source: String(feature.source ?? ''),
    layerId: feature.layer.id,
    geometryType: String(feature.geometry?.type ?? ''),
    properties: {...(feature.properties ?? {})},
    ...artwork,
  });
}
/**
 * 「输出旋转角度」与 MapLibre bearing 的换算。
 *
 * 界面上的「旋转」是把地图**内容**顺时针转 θ（与位图导出同一套语义）；
 * 而 MapLibre 的 bearing 表示「屏幕上方指向哪个罗盘方向」，两者方向相反，
 * 所以 bearing = 360 − θ。θ=90（内容顺时针转 90°、北跑到屏幕右侧）
 * 等价于 bearing=270（西朝上）—— 两句话描述的是同一个画面。
 *
 * 值记在模块级：场景重建（`loadScene`）后要按回去，否则重编译一次朝向就跑了。
 * 用户随后自己转由 MapLibre 接管，只有宿主再次下发角度才会覆盖。
 */
let outputRotation = 0;
function outputBearing(rotation: number): number {
  const value = ((Number(rotation) || 0) % 360 + 360) % 360;
  return (360 - value) % 360;
}
/*
 * ⚠️ 所有取景调用都必须显式带上 bearing。
 * MapLibre 的 `cameraForBounds` 里写的是 `options?.bearing || 0`，
 * `fitBounds` 又是「cameraForBounds → easeTo/flyTo」，所以**不带 bearing 的
 * 适配一律按正北计算**：刚设好的朝向会被当场拧回去（导出页打开时那次
 * fitBounds 就是这么把 90° 抹平的）。改这里之前先想清楚这一点。
 */
function fitWorldBounds(bounds: [number, number, number, number], padding = 28, duration = 0): void {
  const [x0, z0, x1, z1] = bounds;
  map.fitBounds([
    [-z1 / LOCAL_METERS_PER_DEGREE, x0 / LOCAL_METERS_PER_DEGREE],
    [-z0 / LOCAL_METERS_PER_DEGREE, x1 / LOCAL_METERS_PER_DEGREE],
  ], {padding, duration, pitch: 0, bearing: outputBearing(outputRotation), linear: true});
}
function fitBounds(duration = 0): void {
  navigation.simulation.stop(duration === 0);
  preview3d.reset(false);
  if (!currentManifest) return;
  map.resize();
  if (currentManifest.initialRegion?.length === 4) {
    fitWorldBounds(currentManifest.initialRegion, currentManifest.exportRegion ? 0 : 28, duration);
    return;
  }
  map.fitBounds(currentManifest.world.geoBounds, {
    padding: 28, duration, pitch: 0, bearing: outputBearing(outputRotation), linear: true});
}
function applyOutputRotation(rotation: number): void {
  const value = ((Number(rotation) || 0) % 360 + 360) % 360;
  // 宿主在刷新后会重放相同输出角度；它不应覆盖用户的自由预览朝向。
  if (value === outputRotation) return;
  navigation.simulation.stop();
  outputRotation = value;
  // 角度改回 0 也要显式设一次，不能靠「0 就不用设」—— 那是转不回去的。
  map.setBearing(outputBearing(value));
}
const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
// 过渡前半段快速推进，后半段缓慢停在选中行政区。
const easeOutCubic = (t: number): number => 1 - Math.pow(1 - t, 3);
function easeCameraTo(
  camera: ReturnType<MapLibreMap['cameraForBounds']>, duration: number,
  easing: (t: number) => number, generation: number,
): Promise<boolean> {
  if (!camera || generation !== focusAnimationGeneration) return Promise.resolve(false);
  return new Promise(resolve => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(fallback);
      map.off('moveend', finish);
      resolve(generation === focusAnimationGeneration);
    };
    const fallback = window.setTimeout(finish, duration + 300);
    map.once('moveend', finish);
    map.easeTo({...camera, duration, easing});
  });
}
async function focusGeoBounds(bounds: [[number, number], [number, number]]): Promise<void> {
  if (!currentManifest) return;
  preview3d.reset();
  const generation = ++focusAnimationGeneration;
  map.stop();
  const overview = map.cameraForBounds(currentManifest.world.geoBounds,
    {padding: 28, bearing: outputBearing(outputRotation)});
  if (!await easeCameraTo(overview, 460, easeInOutCubic, generation)) return;
  await new Promise(resolve => window.setTimeout(resolve, 70));
  if (generation !== focusAnimationGeneration) return;
  const closeup = map.cameraForBounds(bounds,
    {padding: 44, bearing: outputBearing(outputRotation)});
  await easeCameraTo(closeup, 1120, easeOutCubic, generation);
}
const ZOOM_PRESETS: Record<string, number> = { city: 11, region: 13, district: 15, detail: 17 };
function setZoomPreset(preset: string): void {
  navigation.simulation.stop();
  if (preset === 'overview') { fitBounds(); return; }
  map.easeTo({ zoom: ZOOM_PRESETS[preset] ?? ZOOM_PRESETS.city, duration: 220 });
}
function clearScene(): void {
  releaseTraffic(); navigation.clear(); preview3d.reset(); cancelLabelLayout(); satellitePreviousLayers = null;
  for (const item of markers.values()) item.marker.remove();
  markers.clear(); transitBadgeLayoutKey = ''; poiAnchorInputs = []; labelWidths.clear();
  for (const id of [...customLayers].reverse()) if (map.getLayer(id)) map.removeLayer(id); customLayers.length = 0;
  if (map.getSource('terrain-image')) map.removeSource('terrain-image');
  if (map.getSource('satellite-image')) map.removeSource('satellite-image');
  for (const id of Object.values(sourceIds)) if (map.getSource(id)) map.removeSource(id);
  if (map.getSource('transit-view-stations')) map.removeSource('transit-view-stations');
  if (map.getSource('transit-view-pois-displaced')) map.removeSource('transit-view-pois-displaced');
  if (map.getSource('map-pois-displaced')) map.removeSource('map-pois-displaced');
  transitStationDisplayFeatures = []; mapPoiDisplayFeatures = []; transitPoiDisplayFeatures = [];
  for (const layer of roadLayers) delete selectableObjects[layer.id];
  roadLayers = [];
  builtSurfaceLayer = null;
  for (const kind of areaKinds) delete areaSurfaces[kind];
}
function stationVisibility(): (feature: any) => boolean {
  const ids = new Set(selectedTransitRouteIds());
  const requestedTypes = new Set((contextOptions.types ?? []).map((value: unknown) => String(value).toLowerCase()));
  return feature => {
    if ((feature.properties?.servingLineIds ?? []).some((id: unknown) => ids.has(Number(id)))) return true;
    return contextOptions.line_ids == null && (contextOptions.types == null
      || requestedTypes.has(String(feature.properties?.kind ?? '').toLowerCase()));
  };
}
function resolvedLabelType(feature: any, group: string): string {
  if (group === 'transit') return 'stop';
  if (group === 'poi') return 'poi';
  return String(feature.properties?.labelType ?? 'default').toLowerCase();
}
function baseFontSize(labelType: string, transitStationName = false): number {
  const label = token('label');
  if (transitStationName && contextOptions.station_name_size != null) return Number(contextOptions.station_name_size);
  if (label.metrics && labelType === 'stop') return Number(label.metrics.station?.size ?? 11);
  return Number(label.metrics?.sizes?.[labelType] ?? label.sizes?.[labelType] ?? label.size ?? 12);
}
function labelColor(labelType: string, featureColor?: string): string {
  const label = token('label');
  if (labelType === 'poi' && label.colors?.poi) return String(label.colors.poi);
  if (featureColor) return featureColor;
  if (labelType === 'stop' && label.metrics) return String(label.metrics.station?.color ?? '#344054');
  return String(label.colors?.[labelType] ?? label.color ?? '#39424C');
}
/** 按类别取主题字体，并附加中文与系统字体。 */
function labelFontFamily(labelType: string): string {
  const label = token('label');
  const fonts = (label.fonts ?? {}) as Record<string, string>;
  const family = String(labelType === 'stop' && label.metrics
    ? token('transit').labelStyle?.font || 'Microsoft YaHei'
    : fonts[labelType] || label.font || '').trim() || 'Microsoft YaHei';
  return `"${family}", "Microsoft YaHei", sans-serif`;
}
/** 该类别要不要描边（主题里按类别配 stroke / stroke_width，见 preview_scene._label_halo）。 */
function labelHalo(labelType: string): boolean {
  const label = token('label');
  if (labelType === 'stop' && label.metrics) return labelStroke(labelType).width > 0;
  const halos = (label.halos ?? {}) as Record<string, boolean>;
  return Boolean(halos[labelType] ?? label.halo ?? true);
}
/** 该类别的描边颜色与宽度（px）；宽度由主题 stroke_width 应用元素倍率后下发。 */
function labelStroke(labelType: string): {color: string; width: number} {
  const label = token('label');
  if (label.metrics) {
    if (labelType === 'poi') return {color: String(label.strokes?.poi?.color ?? '#FFFFFF'),
      width: Number(label.metrics.poiStrokeWidth ?? 0)};
    if (labelType === 'stop') return {color: String(label.metrics.station?.stroke_fill ?? '#FFFFFF'),
      width: Number(label.metrics.station?.stroke_width ?? (label.metrics.station?.halo === false ? 0 : 1))};
    return {color: String(label.strokes?.[labelType]?.color ?? '#FFFFFF'),
      width: Number(label.metrics.strokeWidths?.[labelType] ?? 0)};
  }
  const strokes = (label.strokes ?? {}) as Record<string, any>;
  const entry = (strokes[labelType] ?? {}) as Record<string, any>;
  const width = Number(entry.width ?? 1);
  return {
    color: String(entry.color ?? '#FFFFFF'),
    width: Number.isFinite(width) ? width : 1,
  };
}
/** 把主题描边应用到元素；先描边后填字面，双倍线宽对应相同的外扩宽度。 */
function applyLabelHalo(el: HTMLElement, labelType: string, scale: number): void {
  const stroke = labelStroke(labelType);
  const width = labelHalo(labelType) ? stroke.width : 0;
  const label = token('label');
  const blur = Math.max(0, label.metrics && labelType !== 'poi'
    ? Number(label.metrics.strokeBlurs?.[labelType] ?? 0)
    : Number(label.strokes?.[labelType]?.blur ?? 0));
  el.classList.toggle('map-label-no-halo', width <= 0);
  el.classList.toggle('map-label-blurred-halo', width > 0 && blur > 0);
  el.dataset.haloText = el.textContent ?? '';
  if (width <= 0) return;
  el.style.setProperty('--label-stroke-width', `${width * scale * 2}px`);
  el.style.setProperty('--label-stroke-c', stroke.color);
  el.style.setProperty('--label-stroke-blur', `${blur * scale}px`);
}
const baseTextLayers = [
  {id: 'district-labels', scope: 'district', labelType: 'district'},
  {id: 'zone-labels', scope: 'zone', labelType: 'park'},
  {id: 'road-labels', scope: 'road', labelType: 'road'},
];
function nativeTextFont(labelType: string): string[] {
  const asset = currentManifest?.fonts?.[labelType];
  const label = token('label');
  const family = String(label.fonts?.[labelType] || label.font || 'Microsoft YaHei');
  const bold = Boolean(token('baseText')[labelType]?.bold);
  return asset ? [asset.family, 'Microsoft YaHei']
    : [...(bold ? [`${family} Bold`] : []), family, 'Microsoft YaHei'];
}
let zoomingSymbols = false;
function nativeTextSize(labelType: string, stroke: 'width' | 'blur' | null = null): NumericStyle {
  const label = token('label');
  const base = Number(label.metrics?.sizes?.[labelType] ?? label.sizes?.[labelType] ?? label.size ?? 12);
  const halo = Number(label.metrics?.[stroke === 'width' ? 'strokeWidths' : 'strokeBlurs']?.[labelType]
    ?? label.strokes?.[labelType]?.[stroke ?? 'width'] ?? 0);
  const levels = labelType === 'road' ? token('baseText').road?.by_level : undefined;
  function at(zoom: number): NumericStyle {
    const metric = (size: number, outline: number) => stroke
      ? (size > 0 ? outline * previewFontSize(size, zoom) / size : 0) : previewFontSize(size, zoom);
    const fallback = metric(base, halo);
    if (!levels || !Object.keys(levels).length) return fallback;
    const expr: unknown[] = ['match', ['get', 'roadLevel']];
    for (const [level, config] of Object.entries(levels as Record<string, {size: number; stroke_width: number}>))
      expr.push(level, metric(config.size, stroke === 'width' ? config.stroke_width : halo));
    return [...expr, fallback] as ExpressionSpecification;
  }
  return usesDistanceSizing() && (zoomingSymbols || stroke) ? distanceExpression(at) : at(map.getZoom());
}
// 跨级缩放可能复用另一层级的符号瓦片。手势时插值，结束后按实际视距重新放置。
function updateZoomSymbols(moving: boolean): void {
  zoomingSymbols = moving;
  if (!usesDistanceSizing()) return;
  updateStationSizes();
  updatePoiSizes();
  for (const {id, labelType} of baseTextLayers)
    if (map.getLayer(id)) map.setLayoutProperty(id, 'text-size', nativeTextSize(labelType));
}
map.on('zoomstart', () => updateZoomSymbols(true));
map.on('zoomend', () => updateZoomSymbols(false));
function baseTextFilter(scope: string): ExpressionSpecification {
  const visibility = token('label').visibility ?? {};
  const filter: ExpressionSpecification = ['all', ['==', ['get', 'labelScope'], scope]];
  if (scope === 'road') {
    filter.push(['==', ['geometry-type'], 'LineString'],
      ['any', ['all', ['==', ['get', 'nameSource'], 'custom'], visibility.roadPlayer !== false],
        ['all', ['==', ['get', 'nameSource'], 'game'], visibility.gameNames === true]],
      ['step', ['zoom'], ['in', ['get', 'roadLevel'], ['literal', ['highway', 'main']]],
        15, ['in', ['get', 'roadLevel'], ['literal', ['highway', 'main', 'branch']]], 17, true]);
  } else filter.push(['==', ['geometry-type'], 'Point']);
  return filter;
}
function addBaseTextLayers(): void {
  // Vellum 原生线路符号共享 MapLibre 的放置、碰撞和字形缓存。
  // 参考 Vellum 7bd3b3f 的 layer-roads.ts，许可证见 VELLUM_LICENSE.txt。
  for (const {id, scope, labelType} of baseTextLayers) {
    add({id, type: 'symbol', source: 'labels', minzoom: scope === 'road' ? 14 : 8,
      filter: baseTextFilter(scope), layout: {
        'symbol-placement': scope === 'road' ? 'line' : 'point',
        'symbol-spacing': Number(token('baseText').road_repeat ?? 320),
        'symbol-sort-key': ['+', ['case', ['==', ['get', 'nameSource'], 'custom'], -100, 0],
          ['*', -1, ['coalesce', ['get', 'priority'], 0]]],
        'text-field': ['get', 'text'], 'text-font': nativeTextFont(labelType),
        'text-size': nativeTextSize(labelType), 'text-max-width': 12,
        'text-max-angle': 30, 'text-padding': 4, 'text-keep-upright': true,
        'text-allow-overlap': false, 'text-ignore-placement': false,
      }});
  }
  updateBaseTextLayers();
}
function updateBaseTextLayers(): void {
  const visibility = token('label').visibility ?? {};
  for (const {id, scope, labelType} of baseTextLayers) {
    if (!map.getLayer(id)) continue;
    const enabled = contextMode === 'map' && currentManifest?.layers.label !== false
      && visibility[scope] !== false;
    map.setLayoutProperty(id, 'visibility', enabled ? 'visible' : 'none');
    map.setFilter(id, baseTextFilter(scope));
    map.setLayoutProperty(id, 'text-font', nativeTextFont(labelType));
    map.setLayoutProperty(id, 'text-size', nativeTextSize(labelType));
    map.setLayoutProperty(id, 'symbol-spacing', Number(token('baseText').road_repeat ?? 320));
    map.setPaintProperty(id, 'text-color', labelColor(labelType));
    map.setPaintProperty(id, 'text-halo-color', labelStroke(labelType).color);
    map.setPaintProperty(id, 'text-halo-width', labelHalo(labelType) ? nativeTextSize(labelType, 'width') : 0);
    map.setPaintProperty(id, 'text-halo-blur', labelHalo(labelType) ? nativeTextSize(labelType, 'blur') : 0);
  }
}
async function loadSceneFonts(manifest: SceneManifest, base: string, current: () => boolean, warn: (error: unknown) => void): Promise<void> {
  const assets = new Map(Object.values(manifest.fonts ?? {}).map(asset => [asset.family, asset]));
  await Promise.all([...assets.values()].map(async asset => {
    if (loadedFonts.has(asset.family)) return;
    const filename = asset.url.split('?')[0];
    const url = EMBEDDED ? window.__CSL_SCENE__?.embedded.fonts?.[filename]
      : new URL(asset.url, base).href;
    if (!url) throw new Error(i18n.t("main_loadSceneFonts_1bbe594fcf", { p0: filename }));
    const font = await new FontFace(asset.family, `url("${url}")`, {
      weight: asset.family.endsWith('-Bold') ? '700' : '400',
    }).load();
    if (!current()) return;
    document.fonts.add(font);
    loadedFonts.add(asset.family);
    labelLayoutKey = ''; scheduleLabels();
  }).map(task => task.catch(warn)));
}
function cancelLabelLayout(): void {
  window.clearTimeout(labelTimer); labelTimer = 0; labelDirty = false; labelLayoutTime = -Infinity;
  retryingSymbolQuery = false;
}
function scheduleLabels(): void {
  if (artworkView.active || !currentManifest || labelDirty) return;
  labelDirty = true;
  const interval = retryingSymbolQuery ? symbolFadeDuration : 250;
  const delay = Math.max(0, interval - (performance.now() - labelLayoutTime));
  labelTimer = window.setTimeout(() => {
    labelTimer = 0;
    map.triggerRepaint();
  }, delay);
}
function measuredLabelWidth(text: string, font: string): number {
  const cached = labelWidths.get(text);
  if (cached?.font === font) return cached.width;
  labelMeasure.font = font;
  const width = labelMeasure.measureText(text).width;
  labelWidths.set(text, {font, width}); return width;
}
function placeLabel(id: string, coordinates: [number, number], offset: [number, number],
  metadata: Omit<LabelMarker, 'marker'>): HTMLElement {
  let item = markers.get(id);
  if (!item) {
    const marker = new maplibregl.Marker({element: document.createElement('div'), anchor: 'center', offset})
      .setLngLat(coordinates).addTo(map);
    item = {...metadata, marker}; markers.set(id, item);
  } else {
    const point = item.marker.getLngLat(), previous = item.marker.getOffset();
    if (point.lng !== coordinates[0] || point.lat !== coordinates[1]) item.marker.setLngLat(coordinates);
    if (previous.x !== offset[0] || previous.y !== offset[1]) item.marker.setOffset(offset);
    Object.assign(item, metadata);
  }
  const element = item.marker.getElement(); element.style.display = '';
  return element;
}
function placeTransitBadges(metrics: ReturnType<typeof transitBadgeMetrics>, badge: Record<string, any>,
  selectedRoutes: Set<number>, stationPoints: Point[], width: number, height: number,
  reserve: (box: Box) => void, overlaps: (box: Box) => boolean, overlapArea: (box: Box) => number,
  kept: Set<string>): void {
  const {style, size} = metrics;
  const options = token('transit').labelStyle?.layout ?? {};
  const interval = Number(contextOptions.line_label_interval ?? options.repeat_interval ?? badge.repeatInterval ?? 600);
  const maximum = Math.max(1, Number(options.max_per_line ?? badge.maxPerLine ?? 4));
  const step = options.candidate_interval ?? 110;
  const limit = Math.max(maximum, options.candidate_limit ?? 72);
  const padding = Number(options.clearance_padding ?? 14);
  const font = `${style.fontWeight} ${size}px "${style.font}", "Microsoft YaHei", sans-serif`;
  type Member = {id: string; name: string; color: string; kind: string; width: number};
  const samples: Candidate<Member>[] = [];
  for (const feature of sourceData.transit?.features ?? []) {
    const props = feature.properties;
    if (!selectedRoutes.has(Number(props.lineId))) continue;
    const name = String(props.name ?? '').trim();
    if (!name) continue;
    const id = String(Number(props.lineId));
    const member = {id, name, color: String(props.color), kind: String(props.kind ?? '').toLowerCase(),
      width: Math.min(180, measuredLabelWidth(name, font)) + metrics.extraWidth};
    const paths: Point[][] = feature.geometry.type === 'MultiLineString'
      ? feature.geometry.coordinates : [feature.geometry.coordinates];
    // 视野外也保留候选点，平移后已有经纬度锚点可随地图进入屏幕。
    for (const path of paths) {
      let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity, length = 0;
      const projected = path.map(coordinate => {
        const {x, y} = map.project(coordinate);
        left = Math.min(left, x); top = Math.min(top, y);
        right = Math.max(right, x); bottom = Math.max(bottom, y);
        return [x, y] as Point;
      });
      for (let i = 1; i < projected.length; i++)
        length += Math.hypot(projected[i][0] - projected[i-1][0], projected[i][1] - projected[i-1][1]);
      const points = projected.map(([x, y]): Point => [x - left, y - top]);
      for (const [x, y] of candidate_points(points, Math.max(step, length / limit), limit, right - left, bottom - top))
        samples.push([x + left, y + top, id, member]);
    }
  }
  const seam = Math.max(1, Math.round(size * .4)) / 2;
  const groups = group_candidates(samples, options.merge_dist ?? style.mergeDistance);
  const badgeBox = (x: number, y: number, members: Member[]): Box => {
    const w = members.reduce((sum, m) => sum + m.width, seam * (members.length - 1));
    return [x - w/2, y - metrics.height/2, x + w/2, y + metrics.height/2];
  };
  const ranked: [number, number, number, number, {index: number; box: Box}][] = [];
  const pad = Math.max(2, Math.round(padding));
  groups.forEach(([x, y, , members], index) => {
    const box = badgeBox(x, y, members);
    const [outside, cost, clearance] = obstacle_score(box, stationPoints, [width, height], padding);
    const area = overlapArea([box[0]-pad, box[1]-pad, box[2]+pad, box[3]+pad]);
    const occupancy = area / ((box[2]-box[0] + 2*pad) * (metrics.height + 2*pad));
    ranked.push([outside, cost, occupancy, -clearance, {index, box}]);
  });
  rank_candidates(ranked);
  const placed: Placed[] = [], counts: Record<string, number> = {};
  const used = new Set<number>();
  const add = (index: number, x: number, y: number, box: Box, members: Member[]) => {
    reserve(box);
    const id = `badge:${index}:${JSON.stringify(members.map(member => member.id))}`;
    kept.add(id);
    const el = placeLabel(id, map.unproject([x, y]).toArray(), [0, 0], {group: 'transit-line', labelType: 'transit-line'});
    if (members.length > 1) {
      el.classList.add('map-transit-badge-group');
      el.style.gap = `${seam}px`;
      while (el.childElementCount < members.length) el.append(document.createElement('span'));
    }
    members.forEach((member, i) => {
      const part = members.length === 1 ? el : el.children[i] as HTMLElement;
      part.classList.add('map-transit-badge');
      part.dataset.routeId = member.id;
      part.dataset.routeName = member.name;
      part.dataset.routeKind = member.kind;
      part.dataset.color = member.color;
      paintTransitBadge(part, member.color, metrics);
    });
  };

  // 先保证线路覆盖；配置的间隔和配额用于后续重复标签。
  for (const [, , , , {index}] of ranked) {
    const [x, y, , candidates] = groups[index];
    const members = candidates.filter(member => !counts[member.id]);
    if (!members.length) continue;
    const box = badgeBox(x, y, members);
    if (placement_reason(x, y, box, members.map(member => member.id), 0, 0,
      maximum, placed, counts, overlaps) !== 'placed') continue;
    used.add(index);
    add(index, x, y, box, members);
  }
  // 候选全部被遮挡时，首个标签可压住普通标注，但不能叠在已放置的线路标签上。
  for (const [, , , , {index}] of ranked) {
    const [x, y, , candidates] = groups[index];
    const members = candidates.filter(member => !counts[member.id]);
    if (!members.length) continue;
    let labelY = y;
    let box = badgeBox(x, labelY, members);
    while (placement_reason(x, labelY, box, members.map(member => member.id), 0, 0,
      maximum, placed, counts, () => false) !== 'placed') {
      const step = metrics.height + 5;
      labelY = labelY <= y ? y + (y - labelY) + step : y - (labelY - y);
      box = badgeBox(x, labelY, members);
    }
    used.add(index);
    add(index, x, labelY, box, members);
  }
  for (const [, , , , {index, box}] of ranked) {
    if (used.has(index)) continue;
    const [x, y, keys, members] = groups[index];
    if (placement_reason(x, y, box, keys, options.min_gap ?? 350, interval,
      maximum, placed, counts, overlaps) === 'placed') add(index, x, y, box, members);
  }
}

function layoutLabels(): void {
  if (artworkView.active) return;
  if (!currentManifest || map.isMoving()) return;
  labelLayoutTime = performance.now();
  const overlay = showsTransportOverlay();
  const ordinary = contextMode === 'transit' ? contextOptions.ordinary_pois !== 'hidden' && contextOptions.ordinary_pois != null : currentManifest.layers.poi !== false;
  const renderedLayers = overlay
    ? [contextOptions.facilities_enabled === false ? '' : 'tv-poi',
       ordinary ? 'pois' : '']
    : currentManifest.layers.poi === false ? [] : ['pois', 'transit-pois'];
  const layers = renderedLayers.filter(id => id && map.getLayer(id));
  let rendered: maplibregl.MapGeoJSONFeature[];
  try {
    rendered = layers.length ? map.queryRenderedFeatures({layers}) : [];
  } catch (error) {
    // 放置结果保留独立的要素索引；即使瓦片已加载，也可能在切换时读到失配的字典。
    if (retryingSymbolQuery || !(error instanceof Error)
      || !/^Out of bounds\. Index requested n=\d+ can't be >= this\._numberToString\.length \d+$/.test(error.message)
      || !error.stack?.includes('lookupSymbolFeatures')) throw error;
    retryingSymbolQuery = true;
    scheduleLabels();
    return;
  }
  retryingSymbolQuery = false;
  const seen = new Set<string>();
  const icons = rendered.filter(f => { const id = String(f.properties?.featureId ?? f.id); if (seen.has(id)) return false; seen.add(id); return true; });
  const labelVisibility = (token('label').visibility ?? {}) as Record<string, boolean>;
  const poiNameVisibility = (token('poiVisibility').names ?? {}) as Record<string, boolean>;
  const key = JSON.stringify([currentRevision, map.getZoom(), map.getCenter(), map.getCanvas().clientWidth,
    map.getCanvas().clientHeight, icons.map(f => [f.id, f.geometry, f.properties?.stationAnchor, f.layer.id]).sort((a, b) => String(a[0]).localeCompare(String(b[0]))), currentManifest.layers,
    map.getBearing(), map.getPitch(), contextMode, contextOptions, token('transit').label,
    token('transit').labelStyle, labelVisibility, token('baseText'),
    poiNameVisibility, effectivePoiIcons()]);
  if (key === labelLayoutKey) return;
  labelLayoutKey = key;
  const kept = new Set<string>();
  const cells = new Map<string, Box[]>();
  const keys = (b: Box): string[] => { const result: string[] = [];
    for (let x = Math.floor(b[0]/64); x <= Math.floor(b[2]/64); x++)
      for (let y = Math.floor(b[1]/64); y <= Math.floor(b[3]/64); y++) result.push(`${x}:${y}`); return result; };
  const reserve = (b: Box) => { for (const k of keys(b)) cells.set(k, [...(cells.get(k) ?? []), b]); };
  const overlaps = (b: Box) => keys(b).some(k => (cells.get(k) ?? []).some(a => b[0]<a[2] && a[0]<b[2] && b[1]<a[3] && a[1]<b[3]));
  const overlapArea = (b: Box): number => [...new Set(keys(b).flatMap(k => cells.get(k) ?? []))]
    .reduce((sum, a) => sum + Math.max(0, Math.min(b[2], a[2])-Math.max(b[0], a[0]))
      * Math.max(0, Math.min(b[3], a[3])-Math.max(b[1], a[1])), 0);
  for (const f of icons) { if (f.geometry.type !== 'Point') continue;
    const p = poiScreenPoint(f), r = poiDimensions(f.layer.id === 'tv-poi')[0] / 2;
    reserve([p.x-r, p.y-r, p.x+r, p.y+r]); }
  const stationRadiusPx = stationRadius();
  const stationPoints: Point[] = [];
  if (overlay && contextOptions.stations !== false) {
    for (const feature of transitStationDisplayFeatures) {
      const p = map.project(feature.geometry.coordinates as [number, number]);
      stationPoints.push([p.x, p.y]);
      reserve([p.x-stationRadiusPx, p.y-stationRadiusPx, p.x+stationRadiusPx, p.y+stationRadiusPx]);
    }
  }
  // 附属名称只跟随已显示图标；显式空图标保留纯文字。
  const iconById = new Map(icons.map(f => [String(f.properties?.poiId ?? f.properties?.featureId ?? f.id), f]));
  const width = map.getCanvas().clientWidth, height = map.getCanvas().clientHeight;
  const sources = overlay
    ? [...transitPoiDisplayFeatures, ...(ordinary ? mapPoiDisplayFeatures.filter((f: any) => f.properties?.owner !== 'transit') : [])]
    : currentManifest.layers.poi === false ? [] : mapPoiDisplayFeatures;
  const labelSeen = new Set<string>();
  const selectedRoutes = new Set(selectedTransitRouteIds());
  const iconGroups = effectivePoiIcons();
  const bounds = map.getBounds();
  const stationIcons = contextOptions.station_name_placement === 'station'
    && contextOptions.stations !== false && overlay
    ? transitStationDisplayFeatures : [];
  const labelIcons = sources.filter((f: any) => {
    if (f.geometry.type !== 'Point' || !bounds.contains(f.geometry.coordinates as [number, number])) return false;
    const properties = f.properties ?? {};
    if (properties.lineRelated === true
        && !(properties.servingLineIds ?? []).some((id: number) => selectedRoutes.has(Number(id)))) return false;
    if ((properties.visibilityGroups ?? []).some((required: string) => iconGroups[required] === false)) return false;
    const id = String(properties.poiId ?? properties.featureId ?? f.id);
    if (labelSeen.has(id)) return false;
    labelSeen.add(id);
    const group = String(properties.visibilityGroup ?? properties.poiGroup ?? 'ordinary');
    if (iconGroups[group] === false) return false;
    if (properties.iconStatus !== 'empty' && !iconById.has(id)) return false;
    if (map.getZoom() < Number(properties.minZoom ?? 0)) return false;
    if (overlay && properties.owner === 'transit' && contextOptions.facilities_enabled === false) return false;
    const point = poiScreenPoint(f);
    if (point.x < 0 || point.y < 0 || point.x > width || point.y > height) return false;
    return Boolean(properties.drawName) && (overlay && group === 'transit'
      ? contextOptions.station_name_placement === 'icon' : poiNameVisibility[group] !== false);
  });
  const candidates = [...labelIcons, ...stationIcons]
    .map((f: any) => ({f, group: 'poi', field: 'name'}))
    .sort((a, b) => {
    // 图标密度筛选时，符合条件的玩家名称优先于生成名称，缩放与密度阈值保持不变。
    // 同组内仍以数值优先级打破并列。
    const aCustom = String(a.f.properties?.nameSource ?? '').toLowerCase() === 'custom';
    const bCustom = String(b.f.properties?.nameSource ?? '').toLowerCase() === 'custom';
    if (aCustom !== bCustom) return aCustom ? -1 : 1;
    return Number(b.f.properties?.priority ?? 0)-Number(a.f.properties?.priority ?? 0);
  });
  for (const {f, group, field} of candidates) {
    const stationSymbol = f.properties?.symbolKind === 'station';
    const contextVisible = stationSymbol || overlay || currentManifest.layers.poi !== false;
    if (!contextVisible || f.geometry.type !== 'Point' || !f.properties?.[field]) continue;
    const p = poiScreenPoint(f);
    const labelType = stationSymbol ? 'stop' : resolvedLabelType(f, group);
    const transitStationName = overlay
      && (stationSymbol || f.properties?.owner === 'transit');
    const baseSize = baseFontSize(labelType, transitStationName);
    const fontSize = previewFontSize(baseSize, map.getZoom());
    if (baseSize < (transitStationName ? 1 : 6)) continue;
    const font = `${fontSize}px ${labelFontFamily(labelType)}`;
    const text = String(f.properties[field]);
    const width = measuredLabelWidth(text, font)+10, height = fontSize+8;
    const gap = (stationSymbol ? stationRadiusPx : poiSize(overlay && f.properties?.owner === 'transit', map.getZoom()) / 2) + 5;
    const offsets: [number, number][] = [
      [gap + width/2, 0], [-gap - width/2, 0],
      [0, -gap - height/2], [0, gap + height/2],
    ];
    const score = ([x, y]: [number, number]): number => overlapArea(
      [p.x+x-width/2, p.y+y-height/2, p.x+x+width/2, p.y+y+height/2]);
    const avoid = token('poi')[overlay ? 'transitOverlapAvoidance' : 'overlapAvoidance'] !== false;
    const position = !avoid ? offsets[0]
      : offsets.reduce((best, candidate) => score(candidate) < score(best) ? candidate : best);
    const [offsetX, offsetY] = position;
    const box: Box = [p.x+offsetX-width/2, p.y+offsetY-height/2,
      p.x+offsetX+width/2, p.y+offsetY+height/2];
    if (transitStationName && avoid && usesDistanceSizing() && map.getZoom() < distanceZooms[1] && overlaps(box)) continue;
    const featureColor = group === 'poi' ? String(f.properties?.labelColor ?? '') : undefined;
    reserve(box);
    const id = `poi:${f.properties.featureId ?? f.properties.poiId ?? f.id}`, iconOffset = poiScreenOffset(f);
    kept.add(id);
    const el = placeLabel(id, f.geometry.coordinates as [number, number],
      [offsetX + iconOffset[0], offsetY + iconOffset[1]], {group, labelType, featureColor, transitStationName});
    el.classList.add('map-label', `map-label-${group}`);
    if (el.textContent !== text) el.textContent = text;
    el.style.color = labelColor(labelType, featureColor);
    el.style.fontSize = `${fontSize}px`; el.style.fontFamily = labelFontFamily(labelType);
    applyLabelHalo(el, labelType, fontSize / baseSize);
  }
  const badge = token('transit').label ?? {};
  if (overlay && (contextOptions.line_label_enabled ?? badge.enabled) !== false
      && (contextOptions.layers ?? {}).lines !== false) {
    const metrics = transitBadgeMetrics();
    const {style: badgeStyle, size: badgeSize} = metrics;
    const key = JSON.stringify([currentSceneId, currentRevision, map.getZoom(), map.getBearing(),
      map.getPitch(), width, height, contextMode, contextOptions, badge, badgeStyle, badgeSize,
      stationRadiusPx, token('label'), token('baseText'), token('poi'), token('poiVisibility')]);
    if (key !== transitBadgeLayoutKey) {
      placeTransitBadges(metrics, badge, selectedRoutes, stationPoints, width, height,
        reserve, overlaps, overlapArea, kept);
      transitBadgeLayoutKey = key;
    } else {
      for (const [id, item] of markers) if (item.group === 'transit-line') kept.add(id);
    }
  } else {
    transitBadgeLayoutKey = '';
  }
  for (const [id, item] of markers) if (!kept.has(id)) { item.marker.remove(); markers.delete(id); }
}
// 手势期间只更新已有标注的尺寸，停止后再做测量和避让。
map.on('zoom', () => {
  if (!usesDistanceSizing() || !markers.size || artworkView.active) return;
  let badge: ReturnType<typeof transitBadgeMetrics> | undefined;
  for (const item of markers.values()) {
    const el = item.marker.getElement();
    if (item.group === 'transit-line') {
      badge ??= transitBadgeMetrics();
      if (el.classList.contains('map-transit-badge')) sizeTransitBadge(el, badge);
      else {
        el.style.gap = `${Math.max(1, Math.round(badge.size * .4)) / 2}px`;
        for (const child of el.children) sizeTransitBadge(child as HTMLElement, badge);
      }
    } else {
      const base = baseFontSize(item.labelType, item.transitStationName);
      const size = previewFontSize(base, map.getZoom());
      el.style.fontSize = `${size}px`;
      applyLabelHalo(el, item.labelType, base > 0 ? size / base : 0);
    }
  }
});
map.on('move', scheduleLabels);
map.on('moveend', scheduleLabels);
if (QUALITY_SAMPLE) map.on('moveend', event => {
  if (event.originalEvent) postEvent('sampleCamera', {
    center: map.getCenter().toArray(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch(),
  });
});
map.on('idle', () => {
  // idle 在瓦片加载和符号放置后触发。
  if (!labelDirty || labelTimer) return;
  labelDirty = false;
  layoutLabels();
});
map.on('sourcedata', event => {
  if (event.isSourceLoaded && ['map-pois-displaced', 'transit-view-pois-displaced'].includes(event.sourceId)) scheduleLabels();
});
map.on('resize', scheduleLabels);

// 内嵌场景资源解析：把相对 URL（含 ?query）规整为纯文件名，再从内联数据取用
function embeddedBaseName(relativeUrl: string): string {
  return String(relativeUrl).split('?')[0].split('/').pop() ?? '';
}
function embeddedJson(relativeUrl: string): any {
  const payload = window.__CSL_SCENE__;
  if (!payload) return undefined;
  const base = embeddedBaseName(relativeUrl);
  if (base === 'manifest.json') return payload.manifest;
  const value = payload.embedded.sources[base] ?? payload.embedded.poiAtlasIndex[base];
  return typeof value === 'string' ? JSON.parse(value) : value;
}
function embeddedImage(relativeUrl: string): string | undefined {
  return window.__CSL_SCENE__?.embedded.images[embeddedBaseName(relativeUrl)];
}
// 资源提供器：http（桌面实时预览）与 embedded（单文件导出）两种实现，
// 均按同款相对 URL 规约取数，applyScene 的加载逻辑因此完全复用。
type ResourceProvider = {
  base: string;
  json(url: URL, trace?: PreviewPerformance, signal?: AbortSignal): Promise<any>;
  image(url: string, trace?: PreviewPerformance): Promise<ImageBitmap>;
  imageUrlFor(url: string): string;
};
function httpProvider(base: string): ResourceProvider {
  return {
    base,
    async json(url: URL, trace?: PreviewPerformance, signal?: AbortSignal) {
      const response = await fetch(url, {signal});
      if (!response.ok) throw new Error(`${url.pathname}: HTTP ${response.status}`);
      if (!trace?.enabled) return response.json();
      const key = url.pathname.split('/').pop()!;
      const text = await trace.async(`resource.${key}.body`, () => response.text());
      return trace.sync(`resource.${key}.parse`, () => JSON.parse(text));
    },
    async image(url: string, trace?: PreviewPerformance) {
      const response = await fetch(new URL(url, base));
      if (!response.ok) throw new Error(`image HTTP ${response.status}`);
      if (!trace?.enabled) return createImageBitmap(await response.blob());
      const key = url.split('?')[0];
      const blob = await trace.async(`resource.${key}.body`, () => response.blob());
      return trace.async(`resource.${key}.decode`, () => createImageBitmap(blob));
    },
    imageUrlFor(url: string) { return new URL(url, base).href; },
  };
}
function embeddedProvider(): ResourceProvider {
  return {
    // 先解析清单本身，再相对其文件地址解析各资源；目录式基址会让单文件 HTML 查找错误。
    base: 'embedded://scene/manifest.json',
    async json(url: URL) {
      const value = embeddedJson(url.href);
      if (value !== undefined) return value;
      throw new Error(i18n.t("main_embeddedProvider_efba963919", { p0: url.pathname }));
    },
    async image(url: string) {
      const dataUri = embeddedImage(url);
      if (dataUri === undefined) throw new Error(i18n.t("main_embeddedProvider_9985ab4875", { p0: url }));
      const image = new Image();
      image.src = dataUri;
      await image.decode();
      return createImageBitmap(image);
    },
    imageUrlFor(url: string) {
      const dataUri = embeddedImage(url);
      if (dataUri === undefined) throw new Error(i18n.t("main_embeddedProvider_9985ab4875", { p0: url }));
      return dataUri;
    },
  };
}
const appliedSources = new Map<string, number>();
let requestedScene = '', requestedSceneRevision = -1;
let requestLayerSources: ((layer: string) => void) | undefined;
let presentationListener: (() => void) | undefined;

function applySceneSource(manifest: SceneManifest, key: string, data: any): void {
  const id = sourceIds[key];
  sourceData[key] = data;
  if (!id || key === 'pois' || key === 'transitViewPois') return;
  if (usesAreaSurfaces(manifest) && (key === 'districts' || key === 'zones')) {
    areaSurfaces[key === 'districts' ? 'district' : 'zone']?.setFeatures(data.features);
    return;
  }
  if (key === 'roads') {
    navigationRoadProperties.clear();
    for (const feature of data.features) {
      const properties = feature.properties, id = String(properties.roadId);
      if (properties.geometryRole === 'road-surface' || !navigationRoadProperties.has(id))
        navigationRoadProperties.set(id, properties);
    }
  }
  const source = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  if (source) source.setData(data);
  else map.addSource(id, {type: 'geojson', data, maxzoom: key === 'labels' ? 14 : 18,
    buffer: 256, tolerance: previewTolerance(key, previewQuality), promoteId: 'featureId'});
}

async function applySceneIcons(provider: ResourceProvider, manifest: SceneManifest, current: () => boolean): Promise<void> {
  const atlasIndex = await provider.json(new URL(manifest.poiAtlas.index, provider.base));
  if (!current()) return;
  const atlas = await provider.image(manifest.poiAtlas.image);
  try {
    if (!current()) return;
    const canvas = document.createElement('canvas'); const ctx = canvas.getContext('2d', {willReadFrequently: true})!;
    const searchIcons = new Map<string, ImageData>();
    for (const [id, entry] of Object.entries(atlasIndex as Record<string, any>)) {
      canvas.width = entry.width; canvas.height = entry.height;
      ctx.drawImage(atlas, entry.x, entry.y, entry.width, entry.height, 0, 0, entry.width, entry.height);
      const pixels = ctx.getImageData(0, 0, entry.width, entry.height);
      searchIcons.set(id, pixels);
      if (map.hasImage(id)) map.updateImage(id, pixels); else map.addImage(id, pixels, {pixelRatio: entry.pixelRatio});
      registeredIcons.add(id);
    }
    previewControls.setSearchIcons(searchIcons);
  } finally { atlas.close(); }
}
async function applyInterchangeIcon(manifest: SceneManifest, current: () => boolean): Promise<void> {
  const interchangeIcon = manifest.theme.transit?.interchangeIcon;
  if (interchangeIcon && !map.hasImage('cmm-station-interchange')) {
    const image = new Image();
    image.src = interchangeIcon;
    await image.decode();
    if (!current() || map.hasImage('cmm-station-interchange')) return;
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 64;
    const ctx = canvas.getContext('2d')!;
    ctx.imageSmoothingQuality = 'high'; ctx.drawImage(image, 0, 0, 64, 64);
    map.addImage('cmm-station-interchange', ctx.getImageData(0, 0, 64, 64), {pixelRatio: 4});
  }
}

function builtAreaFill(): void {
  if (map.getLayer('built-area-surface')) map.removeLayer('built-area-surface');
  builtSurfaceLayer = null;
  if (!map.getLayer('built-area') && map.getSource('built-area'))
    map.addLayer({id: 'built-area', type: 'fill', source: 'built-area', paint: {
      'fill-color': token('built_area').fill ?? '#F1F0EB', 'fill-opacity': token('built_area').opacity ?? 1}},
      map.getLayer('land-use') ? 'land-use' : undefined);
  if (!customLayers.includes('built-area')) customLayers.push('built-area');
}

async function loadScene(provider: ResourceProvider, requestedRevision: number,
  trace = new PreviewPerformance(), preserveArtwork = false, scene = ''): Promise<void> {
  if (requestedRevision < requestedSceneRevision) return;
  if (requestedScene !== scene || requestedSceneRevision !== requestedRevision) ++loadGeneration;
  requestedScene = scene; requestedSceneRevision = requestedRevision;
  const generation = loadGeneration;
  const manifestUrl = provider.base;
  let manifest: SceneManifest;
  try { manifest = await provider.json(new URL(manifestUrl)) as SceneManifest; }
  catch (error) { if (generation !== loadGeneration) return; throw error; }
  const manifestReady = performance.now();
  const completion = manifest.completion ?? 'complete';
  const bundleId = new URL(manifestUrl).pathname.split('/').at(-2);
  const identity = {sceneId: manifest.sceneId, revision: requestedRevision, bundleId, completion};
  const wanted = () => generation === loadGeneration && manifest.revision === requestedRevision &&
    manifest.revision >= currentRevision && !(completion === 'base' && currentManifest?.sceneId === manifest.sceneId &&
      currentRevision === requestedRevision && currentManifest.completion === 'complete');
  if (!wanted()) return;
  if (manifest.schemaVersion !== 1) throw new Error(i18n.t("main_loadScene_2197b880cc", { p0: manifest.schemaVersion }));
  if (EMBEDDED && !('metro' in manifest.layers) && 'transit' in manifest.layers) manifest.layers.metro = manifest.layers.transit;
  trace.enabled = !!manifest.performanceDiagnostics;
  trace.record('manifest', trace.origin);
  const sourceVersion = (key: string) => manifest.sourceRevisions[key === 'builtSurface' ? 'builtArea' : key] ?? requestedRevision;
  const needs = (key: string) => currentSceneId !== manifest.sceneId ||
    appliedSources.get(key) !== sourceVersion(key);
  const resources = new Map<string, Promise<any>>();
  const read = (key: string) => {
    let resource = resources.get(key);
    if (!resource) {
      resource = provider.json(new URL(manifest.sources[key], manifestUrl), trace);
      resources.set(key, resource);
    }
    return resource;
  };
  const core = ['roads', 'buildings'].filter(key => manifest.sources[key] && needs(key) &&
    (key === 'roads' ? manifest.layers.road !== false :
       manifest.layers.building !== false || manifest.layers.land_use || manifest.layers.poi_area));
  const fetched = new Map<string, any>();
  if (core.length) {
    try {
      await Promise.any(core.map(async key => fetched.set(key, await read(key))));
    } catch (error) {
      if (!wanted()) return;
      if (currentManifest?.sceneId === manifest.sceneId && currentRevision === requestedRevision &&
          currentManifest.completion === 'base') {
        reportError(error, {...identity, stage: 'resource'}, false);
        return;
      }
      throw error;
    }
  }
  if (!wanted()) return;
  const changed = currentSceneId !== manifest.sceneId;
  const previous = currentManifest;
  const current = () => currentManifest === manifest && generation === loadGeneration;
  const warn = (error: unknown) => {
    if (current()) reportError(error, {...identity, stage: 'resource'}, false);
  };
  if (presentationListener) map.off('idle', presentationListener);
  presentationListener = undefined;
  requestLayerSources = undefined;
  if (preserveArtwork) artworkView.prepareSceneChange();
  else artworkView.leave();
  trafficRequest?.abort();
  if (changed || currentRevision !== requestedRevision) {
    previewControls.resetSearch(); setFeatureHighlight('search-selection', null);
  }
  if (changed) {
    ++focusAnimationGeneration; map.stop(); navigation.simulation.stop();
    clearWorldSelection(false); clearInspectorSelection(false); clearScene(); sourceData = {};
    for (const id of registeredIcons) if (map.hasImage(id)) map.removeImage(id);
    registeredIcons.clear(); appliedSources.clear(); previewControls.setSearchIcons(new Map());
  }
  currentSceneId = manifest.sceneId; currentRevision = manifest.revision;
  currentManifest = manifest; currentTheme = manifest.theme; sceneProvider = provider;
  preview3d.setAvailable(EMBEDDED || !manifest.exportRegion);
  labelLayoutKey = '';
  map.setMaxBounds(manifest.exportRegion ? manifest.world.geoBounds : null);
  const landUseChanged = !changed && Boolean(manifest.sources.landUse) !== Boolean(previous?.sources.landUse);
  if (landUseChanged && map.getLayer('land-use')) {
    map.removeLayer('land-use'); customLayers.splice(customLayers.indexOf('land-use'), 1);
  }
  if (!manifest.sources.landUse && map.getSource('land-use')) {
    map.removeSource('land-use'); delete sourceData.landUse; appliedSources.delete('landUse');
  }
  const mark = (key: string) => appliedSources.set(key, sourceVersion(key));
  for (const key of Object.keys(sourceIds)) {
    if (key === 'traffic' || key === 'landUse' && !manifest.sources.landUse) continue;
    if (fetched.has(key)) {
      applySceneSource(manifest, key, fetched.get(key)); mark(key);
    } else if (changed || previous?.sources[key] && !manifest.sources[key]) {
      applySceneSource(manifest, key, {type:'FeatureCollection', features:[]});
      appliedSources.delete(key);
    }
  }
  if (changed) addDomains();
  else if (fetched.has('roads')) rebuildRoadLayers();
  if (landUseChanged) addLandUseLayer();
  if (changed || fetched.has('buildings')) preview3d.setBuildings(sourceData.buildings?.features ?? [], manifest.sceneId);
  if (preserveArtwork) artworkView.restorePaint();
  applyRenderStack(); applyTheme(manifest.theme);
  for (const [layer, visible] of Object.entries(manifest.layers))
    if (layer !== 'traffic') void setLayerVisibility(layer, visible).catch(warn);
  applyContextStyling();
  navigation.setScene(manifest.navigation?.schemaVersion === 1
    ? () => provider.json(new URL(manifest.navigation!.url, manifestUrl)) as Promise<NavigationPackage> : null,
    Boolean(manifest.navigationRouteOmitted));
  if (changed && manifest.navigationRoute && !EMBEDDED) void navigation.restoreRoute(manifest.navigationRoute).catch(warn);
  if (changed && !artworkView.active) fitBounds();
  artworkSceneReady = true;
  const applied = performance.now();
  postEvent('sceneLoaded', {...identity, performance: {steps: trace.steps, completion: 'applied',
    workerCount: maplibregl.getWorkerCount(), manifestMs: manifestReady - trace.origin,
    resourcesMs: applied - manifestReady, totalMs: applied - trace.origin}});

  let refresh = 0;
  const refreshDetails = () => {
    if (refresh) return;
    refresh = window.setTimeout(() => {
      refresh = 0;
      if (!current()) return;
      try {
        applyContextStyling(); applyRenderStack();
        previewControls.rebuild(sourceData, true); updateQuickLayers(true);
        updateRoadWarning(); scheduleLabels();
        if (preserveArtwork && artworkView.active) artworkView.refreshBase();
      } catch (error) { warn(error); }
    }, 0);
  };
  const owners: Record<string, string> = {forest:'terrain', builtArea:'built_area', water:'water',
    railway:'railway', labels:'label', proceduralObjects:'procedural_objects'};
  const pending = new Map<string, Promise<void>>();
  const schedule = (key: string, action: () => Promise<void>) => {
    if (!pending.has(key)) pending.set(key, action().catch(warn));
  };
  const loadSource = (key: string) => schedule(key, async () => {
    const data = await read(key);
    if (!current()) return;
    applySceneSource(manifest, key, data); mark(key);
    if (key === 'roads') {
      rebuildRoadLayers();
      await setLayerVisibility('road', manifest.layers.road !== false);
    }
    if (key === 'buildings') preview3d.setBuildings(data.features, manifest.sceneId);
    refreshDetails();
  });
  const loadSurface = () => schedule('built-area-surface', async () => {
    const spec = manifest.surfaceFields?.builtArea;
    if (!spec || !needs('builtSurface')) return;
    const bitmap = await provider.image(spec.url, trace);
    try {
      if (!current()) return;
      const layer = new SurfaceDistanceLayer('built-area-surface', bitmap, spec, {
        color: spec.color ?? token('built_area').fill ?? '#F1F0EB', opacity: Number(token('built_area').opacity ?? 1),
        bufferMeters: Number(spec.bufferMeters ?? (token('built_area').includeParks ? 0 : token('built_area').bufferMeters ?? 60)),
        edgeMeters: Number(spec.edgeMeters ?? token('built_area').edgeMeters ?? 0)});
      if (map.getLayer('built-area-surface')) map.removeLayer('built-area-surface');
      map.addLayer(layer, map.getLayer('land-use') ? 'land-use' : undefined);
      builtSurfaceLayer = layer;
      if (map.getLayer('built-area')) map.removeLayer('built-area');
      if (!customLayers.includes('built-area-surface')) customLayers.push('built-area-surface');
      mark('builtSurface'); await setLayerVisibility('built_area', manifest.layers.built_area !== false);
      refreshDetails();
    } finally { bitmap.close(); }
  });
  if (needs('builtSurface') || !manifest.surfaceFields?.builtArea) {
    builtAreaFill();
    void setLayerVisibility('built_area', manifest.layers.built_area !== false).catch(warn);
  }
  function loadRaster(key: 'satellite' | 'terrain') {
    const url = manifest.sources[key];
    const corners = key === 'satellite' ? manifest.satelliteCorners : manifest.terrainCorners;
    if (!url || corners?.length !== 4) {
      if (map.getLayer(key)) map.removeLayer(key);
      if (map.getSource(key + '-image')) map.removeSource(key + '-image');
      appliedSources.delete(key);
      return;
    }
    if (!needs(key)) return;
    schedule(key, async () => {
      const id = key + '-image';
      const image = {url: provider.imageUrlFor(url), coordinates: corners as [[number,number],[number,number],[number,number],[number,number]]};
      const source = map.getSource(id) as maplibregl.ImageSource | undefined;
      if (source) source.updateImage(image); else map.addSource(id, {type:'image', ...image});
      if (!map.getLayer(key)) {
        map.addLayer({id:key, type:'raster', source:id, paint:{'raster-fade-duration':0}}, customLayers.find(id => !!map.getLayer(id)));
        customLayers.unshift(key);
      }
      mark(key); refreshDetails();
    });
  }
  function loadLayer(layer?: string) {
    for (const key of Object.keys(manifest.sources)) {
      if (!sourceIds[key] || key === 'traffic' || !needs(key)) continue;
      if (layer ? owners[key] !== layer : owners[key] && manifest.layers[owners[key]] === false) continue;
      loadSource(key);
    }
    if (layer === 'built_area' || !layer && manifest.layers.built_area !== false) loadSurface();
    for (const key of ['satellite', 'terrain'] as const)
      if (key === layer || !layer && manifest.layers[key] !== false) loadRaster(key);
  }
  requestLayerSources = layer => { if (current()) loadLayer(layer); };
  loadLayer();
  if (manifest.poiAtlas && (manifest.sources.pois || manifest.sources.transitViewPois)) schedule('poi-atlas', () => applySceneIcons(provider, manifest, current).then(refreshDetails));
  schedule('interchange-icon', () => applyInterchangeIcon(manifest, current));
  schedule('fonts', () => loadSceneFonts(manifest, manifestUrl, current, warn));
  schedule('traffic', syncTraffic);
  refreshDetails();
  void Promise.all(pending.values()).then(() => {
    if (!current()) return;
    presentationListener = () => {
      if (current()) postEvent('scenePresentationReady', {...identity, performance: {
        completion:'idle', presentationMs: performance.now() - applied, totalMs: performance.now() - trace.origin}});
    };
    map.once('idle', presentationListener); map.triggerRepaint();
  });
}
async function dispatch(command: PreviewCommand): Promise<void> {
  const commandStarted = performance.now(); let outcome = 'applied';
  const loadTrace = command.type === 'loadScene' ? new PreviewPerformance() : undefined;
  try {
  if (command.type === 'leaveArtwork') {
    if (artworkView.matches(command.documentEpoch, command.artworkRevision)
        || artworkView.matchesPresented(command.documentEpoch, command.artworkRevision)) artworkView.leave();
    return;
  }
  if (['beginArtwork', 'loadArtwork', 'setArtworkView', 'setArtworkTool', 'artworkObserve', 'locateArtwork', 'cancelArtworkLocate'].includes(command.type)) {
    if (!artworkSceneReady || currentSceneId &&
        (command.sceneId !== currentSceneId || command.revision !== currentRevision)) return;
    if (command.type === 'beginArtwork') {
      if (!artworkView.request(command.documentEpoch!, command.artworkRevision!)) outcome = 'stale';
      cancelLabelLayout();
    } else if (command.type === 'loadArtwork') {
      if (!command.artwork) throw new Error('Missing artwork payload');
      const artwork = command.artwork;
      const base = artwork.previewBase;
      const prepareBase = base ? async () => {
        if (base.sceneId !== currentSceneId || base.revision !== currentRevision ||
            base.layoutRevision !== artwork.revision)
          throw new Error('Artwork base belongs to another scene or revision');
        const missing = base.requiredSources.filter(source => !currentManifest?.sources[source] &&
          !(source === 'builtArea' && currentManifest?.surfaceFields?.builtArea));
        if (missing.length) throw new Error(`Artwork base sources are not ready: ${missing.join(', ')}`);
        const entering = artworkBrowseTheme === null;
        if (entering && currentTheme) artworkBrowseTheme = structuredClone(currentTheme);
        if (!artworkBrowseLayers && currentManifest) artworkBrowseLayers = {...currentManifest.layers};
        const changed = applyArtworkTheme(base.theme as FullTheme, entering);
        for (const layer of Object.keys(groups)) {
          if (!artworkView.matches(artwork.documentEpoch, artwork.revision)) return false;
          if (!['transit', 'metro', 'poi', 'label', 'watermark'].includes(layer) &&
              currentManifest?.layers[layer] !== base.layers.includes(layer))
            await setLayerVisibility(layer, base.layers.includes(layer));
        }
        return changed;
      } : undefined;
      if (!await artworkView.load(command.artwork, prepareBase)) {
        outcome = 'stale';
        postEvent('artworkDiscarded', {documentEpoch: command.artwork.documentEpoch,
          artworkRevision: command.artwork.revision, outcome});
      }
    } else if (command.type === 'setArtworkView' && artworkView.matches(command.documentEpoch, command.artworkRevision)) {
      artworkView.setView(command.observation ?? {});
    } else if (command.type === 'setArtworkTool' && artworkView.matches(command.documentEpoch, command.artworkRevision)) {
      if (selectionMode || selectionStart) clearWorldSelection();
      artworkView.setTool(command.tool!);
    } else if (command.type === 'artworkObserve' && artworkView.matches(command.documentEpoch, command.artworkRevision)) {
      artworkView.observe(command.action!);
    } else if (command.type === 'locateArtwork' && artworkView.matches(command.documentEpoch, command.artworkRevision)) {
      const bounds = command.worldBounds;
      if (!bounds || bounds.length !== 4 || !bounds.every(Number.isFinite) || bounds[0] >= bounds[2] || bounds[1] >= bounds[3])
        throw new Error('Invalid artwork closeup bounds');
      const [x0, z0, x1, z1] = bounds;
      artworkView.locate([[x0,z0],[x0,z1],[x1,z1],[x1,z0]].map(([x,z]) =>
        [-z/LOCAL_METERS_PER_DEGREE, x/LOCAL_METERS_PER_DEGREE]));
    } else if (command.type === 'cancelArtworkLocate' && artworkView.matches(command.documentEpoch, command.artworkRevision)) {
      artworkView.cancelPendingLocation();
    }
    return;
  }
  if (typeof command.revision === 'number' && command.revision < currentRevision) {
    outcome = 'stale';
    if (command.type === 'loadScene') postEvent(
      'sceneDiscarded', { sceneId: command.sceneId, revision: command.revision, outcome });
    return;
  }
  if (command.type !== 'loadScene' && currentSceneId && command.sceneId &&
      (command.sceneId !== currentSceneId || command.revision !== currentRevision)) { outcome = 'stale'; return; }
  if (command.type !== 'loadScene' && artworkView.active &&
      !['setUiAppearance', 'setLanguage', 'setPreviewQuality', 'showHint', 'setPreviewUiState',
        'applyTheme', 'applyRoadTheme', 'setLayerVisibility', 'setRoadVisibility', 'setOutputRotation', 'setContextMode',
        'beginSelection', 'clearSelection', 'setSelection', 'clearObjectSelection', 'restoreObjectSelection',
        'locateObject', 'selectCatalogBuilding', 'setBuildingHighlight', 'showMapInteractionGuide'].includes(command.type)) artworkView.leave();
  if (['setZoomPreset', 'fitBounds', 'fitGeoBounds',
    'focusGeoBounds', 'beginSelection', 'locateObject', 'selectCatalogBuilding'].includes(command.type)) navigation.simulation.stop();
  switch (command.type) {
    case 'setSampleCamera':
      if (QUALITY_SAMPLE && command.options) map.jumpTo(command.options);
      break;
    case 'setPreviewQuality': {
      try {
        const next = readPreviewQuality(command.quality);
        const sizesChanged = next.textPercent !== previewQuality.textPercent || next.linePercent !== previewQuality.linePercent ||
          next.iconPercent !== previewQuality.iconPercent;
        applyPreviewQuality(map, previewQuality, next);
        previewQuality = next;
        if (sizesChanged && usesDistanceSizing() && currentTheme) applyTheme(currentTheme);
        postEvent('previewQualityApplied', {requestId: command.requestId, outcome: 'applied'});
      } catch (error) {
        postEvent('previewQualityApplied', {requestId: command.requestId, outcome: 'error',
          message: error instanceof Error ? error.message : String(error)});
      }
      break;
    }
    case 'loadScene': if (command.manifestUrl && typeof command.revision === 'number')
      await loadScene(httpProvider(command.manifestUrl), command.revision, loadTrace, command.preserveArtwork, command.sceneId); break;
    case 'applyTheme': if (command.theme) {
      if (artworkView.active) {
        if (applyArtworkTheme(command.theme as FullTheme)) artworkView.refreshBase();
      } else applyTheme(command.theme as FullTheme);
    } break;
    case 'setUiAppearance': setUiAppearance(command.appearance); break;
    case 'setLanguage': if (command.language) await setLanguage(command.language); break;
    case 'applyRoadTheme': if (command.theme) {
      artworkView.restorePaint();
      applyTheme({ ...(currentTheme ?? {}), road: command.theme as RoadTheme } as FullTheme);
      artworkView.refreshBase();
    } break;
    case 'setLayerVisibility': if (command.layer) await setLayerVisibility(command.layer, Boolean(command.visible)); break;
    case 'setContextMode':
      setContextMode(command.mode ?? 'map', command.options ?? {});
      artworkView.keepOverlayLayersHidden();
      break;
    case 'setZoomPreset': setZoomPreset(command.preset ?? 'city'); break;
    case 'setOutputRotation': applyOutputRotation(command.rotation ?? 0); break;
    case 'setRoadVisibility': await setLayerVisibility('road', Boolean(command.visible)); break;
    case 'fitBounds': fitBounds(); break;
    case 'fitGeoBounds': preview3d.reset(); if (command.bounds) map.fitBounds(command.bounds, {padding: 36, duration: 260, bearing: outputBearing(outputRotation)}); break;
    case 'focusGeoBounds': if (command.bounds) await focusGeoBounds(command.bounds); break;
    case 'beginSelection': artworkView.flushViewport(); preview3d.suspendSelection(); selectionMode = true; map.getCanvas().style.cursor = 'crosshair'; document.body.classList.add('selecting'); break;
    case 'clearSelection': clearWorldSelection(); break;
    case 'setSelection': selectedWorldBounds = command.worldBounds ?? null; drawWorldSelection(); break;
    case 'clearObjectSelection': clearInspectorSelection(); break;
    case 'restoreObjectSelection': restoreInspectorSelection(command.selection); break;
    case 'selectCatalogBuilding':
      restoreInspectorSelection(command.selection);
      if (command.locate) locateInspectorObject();
      break;
    case 'setBuildingHighlight':
      (map.getSource('building-highlight') as maplibregl.GeoJSONSource | undefined)?.setData(
        command.data ?? {type: 'FeatureCollection', features: []});
      break;
    case 'locateObject': locateInspectorObject(); break;
    case 'showHint': showHint(String(command.text ?? '')); break;
    case 'setPreviewUiState':
      if (!EMBEDDED && typeof command.immersive === 'boolean') {
        immersive = command.immersive;
        previewControls.setImmersive(immersive); navigation.setImmersive(immersive);
      }
      if (currentManifest && command.layers) Object.assign(currentManifest.layers, command.layers);
      quickSymbolLayers = command.symbolLayers;
      if (typeof command.enabled === 'boolean') quickLayersEnabled = command.enabled;
      const rotationEnabled = command.rotationEnabled ?? command.enabled;
      if (typeof rotationEnabled === 'boolean') previewControls.setRotationAvailability(rotationEnabled);
      updateQuickLayers(true, command.pendingLayers);
      previewControls.setGuideAvailable(command.guideAvailable !== false); break;
    case 'showMapInteractionGuide': previewControls.showGuide(true); break;
    case 'dispose': ++loadGeneration; artworkView.dispose(); releaseTraffic(); previewControls.dispose(); navigation.dispose(); cancelLabelLayout(); map.remove(); break;
  }
} catch (error) { outcome = 'error'; reportError(error, {sceneId: command.sceneId,
    revision: command.revision, stage: command.type});
  if (command.type === 'loadArtwork') postEvent('artworkFailed', {
    documentEpoch: command.artwork?.documentEpoch, artworkRevision: command.artwork?.revision,
    message: error instanceof Error ? error.message : String(error)});
  if (command.type === 'loadScene' && typeof command.revision === 'number') {
    postEvent('sceneDiscarded', { sceneId: command.sceneId, revision: command.revision, outcome: 'error',
      performance: {steps: loadTrace?.steps ?? [], completion: 'error'},
      message: error instanceof Error ? error.message : String(error) });
  }
}
finally {
  if (command.performanceRequestId) {
    await new Promise<void>(resolve => requestAnimationFrame(
      () => requestAnimationFrame(() => resolve())));
    postEvent('commandApplied', {
      performanceRequestId: command.performanceRequestId,
      commandType: command.type,
      outcome,
      performance: { applyMs: performance.now() - commandStarted },
    });
  }
} }
window.cslPreview = { dispatch, artworkState: () => artworkView.snapshot(), exportSizing: () => {
  if (artworkView.active) return null;
  if (!currentManifest || contextMode !== 'transit') return null;
  map.resize();
  const canvas = map.getCanvas();
  return {viewport: [canvas.clientWidth, canvas.clientHeight],
    radius: baseStationMetrics().outer,
    stroke: baseStationMetrics().stroke,
    fill: String(map.getPaintProperty('tv-station', 'circle-color')),
    outline: String(map.getPaintProperty('tv-station', 'circle-stroke-color')),
    badge_size: baseBadgeSize(transitLabelStyle())};
} };
if (import.meta.env.VITE_CSLMM_NATIVE_CHECKS === '1') {
  window.__CSL_NATIVE_CAMERA__ = () => ({...preview3d.snapshot(), moving: map.isMoving(), extrusion: !!map.getLayer(BUILDINGS_3D)});
}
window.chrome?.webview?.addEventListener('message', event => { const command = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
  void dispatch(command as PreviewCommand); });
function clearWorldSelection(report = true): void {
  selectedWorldBounds = null; selectionStart = null; selectionMode = false;
  selection.style.display = 'none'; map.dragPan.enable();
  map.getCanvas().style.cursor = ''; document.body.classList.remove('selecting');
  preview3d.restoreSelection();
  if (report) postEvent('selectionCleared');
}
function projectedRegion(bounds: [number, number, number, number]): {x: number; y: number}[] {
  const [x0,z0,x1,z1] = bounds;
  return [[x0,z0],[x0,z1],[x1,z1],[x1,z0]].map(([x,z]) => artworkView.project([-z/111195,x/111195]));
}
function drawWorldSelection(): void {
  if (!selectedWorldBounds || selectionStart) return;
  const points = projectedRegion(selectedWorldBounds);
  const left = Math.min(...points.map(p => p.x)), top = Math.min(...points.map(p => p.y));
  Object.assign(selection.style, {display: 'block', left: `${left}px`, top: `${top}px`,
    width: `${Math.max(...points.map(p => p.x))-left}px`, height: `${Math.max(...points.map(p => p.y))-top}px`});
}
map.on('move', drawWorldSelection);
map.on('resize', drawWorldSelection);
map.getCanvas().addEventListener('mousedown', event => {
  if (!selectionMode || event.button !== 0) return;
  event.preventDefault(); map.dragPan.disable(); selectedWorldBounds = null;
  selectionStart = {x:event.clientX, y:event.clientY};
  Object.assign(selection.style, {display:'block',left:`${event.clientX}px`,top:`${event.clientY}px`,width:'0',height:'0'});
});
window.addEventListener('mousemove', event => {
  if (!selectionStart) return;
  Object.assign(selection.style, {left:`${Math.min(selectionStart.x,event.clientX)}px`,top:`${Math.min(selectionStart.y,event.clientY)}px`,
    width:`${Math.abs(event.clientX-selectionStart.x)}px`,height:`${Math.abs(event.clientY-selectionStart.y)}px`});
});
window.addEventListener('mouseup', event => {
  if (!selectionStart || event.button !== 0) return;
  const rect = map.getCanvas().getBoundingClientRect();
  if (Math.abs(event.clientX-selectionStart.x)<3 || Math.abs(event.clientY-selectionStart.y)<3) { clearWorldSelection(); return; }
  const points = [[selectionStart.x,selectionStart.y],[selectionStart.x,event.clientY],
    [event.clientX,event.clientY],[event.clientX,selectionStart.y]].map(([x,y]) => map.unproject([
      Math.max(0,Math.min(rect.width,x-rect.left)),Math.max(0,Math.min(rect.height,y-rect.top))]));
  const world = currentManifest?.world.bounds ?? [-8640,-8640,8640,8640];
  const bounds: [number,number,number,number] = [
    Math.max(world[0],Math.min(...points.map(p=>p.lat*111195))),
    Math.max(world[1],Math.min(...points.map(p=>-p.lng*111195))),
    Math.min(world[2],Math.max(...points.map(p=>p.lat*111195))),
    Math.min(world[3],Math.max(...points.map(p=>-p.lng*111195)))];
  selectionStart = null; selectionMode = false; suppressNextClick = true;
  map.dragPan.enable(); map.getCanvas().style.cursor = ''; document.body.classList.remove('selecting');
  if (!bounds.every(Number.isFinite) || bounds[2]-bounds[0]<=1e-6 || bounds[3]-bounds[1]<=1e-6) { clearWorldSelection(); return; }
  selectedWorldBounds = bounds; preview3d.restoreSelection(); drawWorldSelection(); postEvent('selectionChanged',{bounds});
});
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !event.defaultPrevented && (selectionMode || selectionStart)) {
    clearWorldSelection(); event.preventDefault();
  }
});
window.addEventListener('blur', () => { if (selectionStart) clearWorldSelection(); });
// 用户真的动手了就把操作提示收掉 —— 他已经会了，这句不该再压在画面上。
// 这也是提示**唯一**的退场路径：它不设自动消失计时，不懂怎么操作的人可以一直
// 看着它。这里刻意**不**复用上面的 movestart / zoomstart：那两个在宿主下发
// fitBounds / setZoomPreset 这类程序化移动时也会触发，地图刚出来时宿主往往
// 正好要定位一次，提示会在用户看清之前就被自己收掉。
map.on('dragstart', dismissHint);
map.on('rotatestart', dismissHint);
map.getCanvas().addEventListener('wheel', dismissHint, { passive: true });
map.getCanvas().addEventListener('touchstart', dismissHint, { passive: true });
map.on('click', event => {
  if (QUALITY_SAMPLE) return;
  if (suppressNextClick) { suppressNextClick = false; return; }
  if (selectionMode || selectionStart) return;
  if (navigation.handleClick(event)) return;
  pickObjectAt(event.point);
});
map.on('mousemove', event => {
  if (selectionMode || preview3d.dragging || navigation.selectingPoint) return;
  hoverPoint = {x: event.point.x, y: event.point.y};
  if (hoverFrame) return;
  hoverFrame = requestAnimationFrame(() => {
    hoverFrame = 0;
    if (!selectionMode && !preview3d.dragging && !navigation.selectingPoint && hoverPoint)
      map.getCanvas().style.cursor = featureAt(hoverPoint) ? 'pointer' : '';
  });
});
map.getCanvas().addEventListener('pointerup', () => {
  if (navigation.selectingPoint && !preview3d.dragging) map.getCanvas().style.cursor = 'crosshair';
});
map.getCanvas().addEventListener('mouseleave', () => {
  if (hoverFrame) cancelAnimationFrame(hoverFrame);
  hoverFrame = 0;
  hoverPoint = null;
  if (!selectionMode && !preview3d.dragging && !navigation.selectingPoint) map.getCanvas().style.cursor = '';
});
window.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || event.defaultPrevented || selectionStart) return;
  if (artworkView.active) {
    event.preventDefault();
    if (!event.repeat) artworkView.requestLeave();
    return;
  }
  if (inspectorSelectionId) { clearInspectorSelection(); event.preventDefault(); }
  else if (immersive) {
    if (!event.repeat) { event.preventDefault(); postEvent('exitImmersive'); }
  } else clearInspectorSelection();
});
async function loadEmbeddedScene(payload: EmbeddedPayload): Promise<void> {
  const revision = payload.manifest.revision;
  currentRevision = Math.max(currentRevision, revision);
  await loadScene(embeddedProvider(), revision);
  // 单文件导出：角度随 manifest 一起来（导出页没有宿主在旁边发命令）。
  // 下面这次适配也带上朝向，否则刚设好的角度会被 fitBounds 按正北抹掉。
  if (payload.manifest.view) setContextMode(payload.manifest.view.mode, payload.manifest.view.options);
  applyOutputRotation(Number(payload.manifest.rotation ?? 0));
  fitBounds();
  if (payload.manifest.navigationRoute) await navigation.restoreRoute(payload.manifest.navigationRoute);
}
map.once('load', () => {
  mapStarted = true;
  if (EMBEDDED && window.__CSL_SCENE__) {
    void loadEmbeddedScene(window.__CSL_SCENE__).catch(reportError);
  } else {
    setError('');
    postEvent('ready');
  }
});
