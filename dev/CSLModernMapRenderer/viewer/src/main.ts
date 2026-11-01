// 初始化 MapLibre 预览器并处理地图交互命令。
import { i18n, setLanguage } from './i18n';
import * as maplibregl from 'maplibre-gl';
import { type ExpressionSpecification, type Map as MapLibreMap, type Marker } from 'maplibre-gl';
import './style.css';
import {stationAnchors, stationFacilityOffset, stationIconOffset} from './poi-anchors';
import { setUiAppearance } from './ui-appearance';
import { SurfaceDistanceLayer, type SurfaceDistanceSpec } from './surface-distance-layer';
import { NavigationControl } from './routing/ui';
import { Preview3D, BUILDINGS_3D, CAMERA_TRANSITION_MS } from './preview-3d';
import { PreviewControls } from './preview-controls';
import { trafficColor as trafficColorExpression } from './traffic-paint';
import type { Edge, Endpoint, NavigationPackage } from './routing/types';

// 单文件导出模式：整份场景（manifest + sources + 图片）已内联在
// window.__CSL_SCENE__ 中，worker 源码由 __CSL_SCENE_WORKER_SRC__ 提供。
const EMBEDDED = !!(globalThis as any).__CSL_SCENE__;
void setLanguage(document.documentElement.lang || 'zh-CN');
if (EMBEDDED) {
  try {
    const workerSource = String((globalThis as any).__CSL_SCENE_WORKER_SRC__ ?? '');
    // MapLibre 6 按 .cjs 后缀选择经典 Worker，供 file:// 离线页面使用。
    maplibregl.setWorkerUrl(URL.createObjectURL(
      new Blob([workerSource], { type: 'text/javascript' })) + '#maplibre-gl-worker.cjs');
  } catch (error) {
    throw new Error(i18n.t("main_ui_2bce5cc92a"), { cause: error });
  }
} else {
  maplibregl.setWorkerUrl(new URL('./assets/maplibre-gl-csp-worker.js', window.location.href).href);
}

type RoadPaint = { fill: string; border: string; minFillPx: number; borderFixedPx: number;
  borderScale: number; dash?: number[] | null; enabled: boolean };
type RoadTheme = { background: string; widthScale: number; levels: Record<string, RoadPaint> };
type FullTheme = Record<string, any> & { road: RoadTheme };
type SceneManifest = { schemaVersion: number; sceneId: string; revision: number;
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
  surfaceFields?: {
    builtArea?: SurfaceDistanceSpec & { url: string; color?: string; bufferMeters?: number; edgeMeters?: number };
  };
  layers: Record<string, boolean>; renderStack?: string[]; theme: FullTheme };
type PreviewCommand = { type: string; sceneId?: string; revision?: number; manifestUrl?: string;
  theme?: FullTheme | RoadTheme; layer?: string; visible?: boolean; mode?: string;
  preset?: string; bounds?: [[number, number], [number, number]];
  options?: Record<string, any>; appearance?: 'light' | 'dark';
  text?: string; language?: string; rotation?: number; worldBounds?: [number, number, number, number];
  featureId?: string; selection?: any; layers?: Record<string, boolean>; enabled?: boolean; guideAvailable?: boolean; pendingLayers?: string[] };
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
declare global { interface Window { cslPreview: { dispatch: (command: PreviewCommand) => Promise<void> };
  chrome?: { webview?: { postMessage: (message: string) => void;
    addEventListener: (name: string, listener: (event: MessageEvent) => void) => void } };
  webkit?: { messageHandlers?: { cslPreview?: { postMessage: (message: unknown) => void } } };
  __CSL_SCENE__?: EmbeddedPayload; __CSL_SCENE_WORKER_SRC__?: string } }

const MPP0 = 156543.03392804097;
const WORLD_SIZE_METERS = 17280;
const REFERENCE_RENDER_PX = 4096;
const LOCAL_METERS_PER_DEGREE = 111195;
const WEB_MERCATOR_METERS_PER_DEGREE = 2 * Math.PI * 6378137 / 360;
const RENDER_SCALE_PER_PPM = WORLD_SIZE_METERS / REFERENCE_RENDER_PX
  * WEB_MERCATOR_METERS_PER_DEGREE / LOCAL_METERS_PER_DEGREE;
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
  proceduralObjects: 'procedural-objects', inspectorSelection: 'inspector-selection', searchSelection: 'search-selection' };
const groups: Record<string, string[]> = {
  terrain: ['terrain', 'forest'], built_area: ['built-area', 'built-area-surface'], poi_area: ['poi-area'], land_use: ['land-use'],
  water: ['water-fill', 'water-shore'], building: ['buildings'],
  road: [], railway: [
      'railway-base', 'railway-dash-metro', 'railway-dash-tram',
      'railway-dash-cablecar', 'railway-dash-monorail', 'railway-dash-train',
      'railway-station'], traffic: ['traffic-base', 'traffic-forward-casing', 'traffic-backward-casing',
      'traffic-forward-fill', 'traffic-backward-fill'],
  transit: ['tv-line', 'tv-station', 'tv-poi'],
  metro: ['transit', 'stations'],
  district: ['district-fill', 'district-border'], zone: ['zone-fill', 'zone-border'],
  poi: ['pois', 'transit-pois'], label: ['district-labels', 'zone-labels', 'road-labels'], procedural_objects: ['procedural-objects'], watermark: [] };
// 后端分组只用于绘制顺序；可见性和上下文分组有独立语义，不能成为第二套图层堆叠。
const renderGroups: Record<string, string[]> = {
  terrain: ['terrain', 'forest'], built_area: ['built-area', 'built-area-surface'],
  poi_area: ['poi-area'], land_use: ['land-use'],
  water: ['water-fill', 'water-shore'], building: ['buildings'],
  road: [],
  railway: ['railway-base', 'railway-dash-metro', 'railway-dash-tram',
    'railway-dash-cablecar', 'railway-dash-monorail', 'railway-dash-train',
    'railway-station'], traffic: ['traffic-base', 'traffic-forward-casing', 'traffic-backward-casing',
      'traffic-forward-fill', 'traffic-backward-fill'],
  procedural_objects: ['procedural-objects'], district: ['district-fill', 'district-border'],
  zone: ['zone-fill', 'zone-border'],
  transit: ['transit', 'stations', 'tv-line', 'tv-station'],
  poi: ['pois', 'transit-pois', 'tv-poi'], label: ['district-labels', 'zone-labels', 'road-labels'], watermark: [],
};
const customLayers: string[] = [];
type LabelMarker = {marker: Marker; group: string; labelType: string; featureColor?: string; transitStationName?: boolean};
const markers = new Map<string, LabelMarker>();
let currentSceneId = '', currentRevision = -1, loadGeneration = 0;
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
let labelLayoutTime = -Infinity, labelTimer = 0, labelDirty = false;
let poiAnchorInputs: unknown[] = [];
const labelMeasure = document.createElement('canvas').getContext('2d')!;
const labelWidths = new Map<string, {font: string; width: number}>();
const loadedFonts = new Set<string>();
let currentManifest: SceneManifest | null = null, currentTheme: FullTheme | null = null;
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
let pendingBuiltSurface: { image: ImageBitmap; spec: SurfaceDistanceSpec } | null = null;
let inspectorSelectionId = '';
let inspectorSelectedFeature: any | null = null;
const watermark = document.createElement('div');
watermark.className = 'preview-watermark'; document.body.appendChild(watermark);

function postEvent(type: string, payload: Record<string, unknown> = {}): void {
  const event = { type, sceneId: currentSceneId, revision: currentRevision, ...payload };
  if (window.chrome?.webview) window.chrome.webview.postMessage(JSON.stringify(event));
  else window.webkit?.messageHandlers?.cslPreview?.postMessage(event);
}
function reportError(error: unknown): void { const message = error instanceof Error ? error.message : String(error);
  setError(i18n.t("main_reportError_8a023d6da8", { p0: message })); postEvent('renderError', { message }); }
const map: MapLibreMap = new maplibregl.Map({ container: 'map', style: { version: 8, sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#eef1f4' } }] },
  center: [0, 0], zoom: 12, minZoom: 8, maxZoom: 24, attributionControl: false,
  renderWorldCopies: false, pitchWithRotate: false, dragRotate: false, maxPitch: 60,
  localIdeographFontFamily: false });
map.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: 'metric' }), 'bottom-left');
const preview3d = new Preview3D(() => outputBearing(outputRotation), dismissHint);
map.addControl(preview3d, 'bottom-right');
const navigation: NavigationControl = new NavigationControl(map,
  () => selectionMode || selectionStart !== null, () => {
    if (!currentManifest || currentManifest.layers.traffic === false) return;
    void setLayerVisibility('traffic', false).catch(reportError);
    postEvent('navigationStarted');
  }, navigationPlaceAt, {properties: navigationRoadStyle, width: navigationWidth, widthMeters: navigationWidthMeters}, preview3d);
preview3d.onToggle = enabled => navigation.simulation.set3DEnabled(enabled);
let quickLayersEnabled = EMBEDDED;
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
}, () => map.getCanvas().focus(), EMBEDDED);
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
    terrain: ['terrain', 'forest'], built_area: ['builtArea'], land_use: ['landUse'], water: ['water'],
    road: ['roads'], building: ['buildings'], railway: ['railway'], traffic: ['traffic'],
    metro: ['transit', 'stations'], transit: ['transit', 'stations'], poi_area: ['buildings'],
    poi: ['pois', 'transitViewPois'], district: ['districts'], zone: ['zones'], procedural_objects: ['proceduralObjects'],
  };
  for (const [key, names] of Object.entries(sourceGroups)) if (names.some(name => name in sources)) available.add(key);
  available.delete(contextMode === 'transit' ? 'metro' : 'transit');
  if (currentManifest.terrainCorners?.length) available.add('terrain');
  if (currentManifest.surfaceFields?.builtArea) available.add('built_area');
  previewControls.setLayers(currentManifest.layers, available, quickLayersEnabled, settled, pending);
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
function renderScaleAt(z: number): number {
  // 与 Python 渲染比例一致：世界米对应的像素数除以全图基准密度，并计入局部坐标投影比例。
  return Math.pow(2, z) / MPP0 * RENDER_SCALE_PER_PPM;
}
function widthAt(z: number, casing = false): ExpressionSpecification {
  const renderScale = renderScaleAt(z);
  const fill: ExpressionSpecification = ['max',
    roadToken('minFillPx', .8),
    ['+', .5, ['*', ['get', 'physicalWidth'], currentTheme?.road.widthScale ?? .15,
      renderScale]]];
  return casing ? ['+', fill, roadToken('borderFixedPx', 1),
    ['*', roadToken('borderScale', 0), renderScale]] : fill;
}
function fillWidth(): ExpressionSpecification {
  return ['interpolate', ['exponential', 2], ['zoom'], ...Array.from({length: 25}, (_, z) => [z, widthAt(z)]).flat()] as ExpressionSpecification;
}
function casingWidth(): ExpressionSpecification {
  return ['interpolate', ['exponential', 2], ['zoom'], ...Array.from({length: 25}, (_, z) => [z, widthAt(z, true)]).flat()] as ExpressionSpecification;
}
function navigationRoadStyle(edge: Edge): Record<string, unknown> {
  const properties = navigationRoadProperties.get(edge.sourceSegmentId);
  return {physicalWidth: properties?.physicalWidth ?? edge.widthM ?? 0,
    styleKey: properties?.styleKey ?? `${edge.level}:${edge.mode.toLowerCase()}`,
    surfaceWidth: properties?.geometryRole === 'road-surface' ? properties.physicalWidth : 0};
}
function navigationWidth(casing: boolean): ExpressionSpecification {
  return ['interpolate', ['exponential', 2], ['zoom'], ...Array.from({length: 25}, (_, z) => [z,
    ['case', ['==', ['get', 'mode'], 'RIDE'], casing ? 10 : 6,
      ['+', casing ? 6 : 0, ['case', ['>', ['get', 'surfaceWidth'], 0],
        ['*', ['get', 'surfaceWidth'], Math.pow(2, z)/MPP0*WEB_MERCATOR_METERS_PER_DEGREE/LOCAL_METERS_PER_DEGREE],
        widthAt(z)]]]
  ]).flat()] as ExpressionSpecification;
}
function navigationWidthMeters(edge: Edge): number {
  const properties = navigationRoadStyle(edge);
  // MapLibre 的地面投影使用 512px 瓦片；既有路线宽度表达式沿用 256px 基准。
  if (Number(properties.surfaceWidth) > 0) return Number(properties.surfaceWidth)/2;
  const paint = currentTheme?.road.levels?.[String(properties.styleKey)];
  const ppm = 2**18/MPP0*WEB_MERCATOR_METERS_PER_DEGREE/LOCAL_METERS_PER_DEGREE;
  const physical = Number(properties.physicalWidth)*(currentTheme?.road.widthScale ?? .15)*WORLD_SIZE_METERS/REFERENCE_RENDER_PX;
  return Math.max(paint?.minFillPx ?? .8, .5+physical*ppm)/(ppm*2);
}
// 表面多边形已包含道路宽度，边界只绘制外沿，同时保留固定像素和缩放宽度。
function borderWidth(): ExpressionSpecification {
  return ['interpolate', ['exponential', 2], ['zoom'], ...Array.from({length: 25}, (_, z) => [z,
    ['+', roadToken('borderFixedPx', 1), ['*', roadToken('borderScale', 0), renderScaleAt(z)]]]).flat()] as ExpressionSpecification;
}
function enabledFilter(): ExpressionSpecification { const value: unknown[] = ['match', ['coalesce', ['get', 'sourceStyleKey'], ['get', 'styleKey']]];
  for (const [key, paint] of Object.entries(currentTheme?.road.levels ?? {})) value.push(key, paint.enabled);
  value.push(true); return ['==', value as ExpressionSpecification, true]; }
const role = (name: string): ExpressionSpecification => ['all', enabledFilter(), ['==', ['get', 'geometryRole'], name]];

type RoadLayer = {id: string; role: string; rank: number; type: 'line' | 'fill';
  paint: 'border' | 'fill'; width?: 'border' | 'casing' | 'fill'; flatOrder?: number};
function roadLineWidth(width: RoadLayer['width']): ExpressionSpecification {
  return width === 'border' ? borderWidth() : width === 'casing' ? casingWidth() : fillWidth();
}
let roadLayers: RoadLayer[] = [];
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
          // 人为多边形切口不能获得 MapLibre 隐式轮廓。
          'fill-antialias': false} : {
          'line-color': roadToken(paint, paint === 'border' ? '#c3c8d2' : '#fff'),
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
function effectivePoiIcons(): Record<string, boolean> {
  const icons = {...(token('poiVisibility').icons ?? {})} as Record<string, boolean>;
  if (contextMode === 'transit') icons.transit = contextOptions.facilities_enabled !== false;
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
  if (contextMode === 'transit') return alwaysFalseFilter();
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
  const stations = (sourceData.stations?.features ?? []).filter(visible)
    .sort((a: any, b: any) => Number(b.properties?.servingLineCount ?? 0) - Number(a.properties?.servingLineCount ?? 0));
  transitStationDisplayFeatures = stations;
  mapPoiDisplayFeatures = stationAnchors(sourceData.pois?.features ?? [], stations,
    contextOptions.stations !== false && currentManifest?.layers.metro !== false);
  mapSource?.setData({type: 'FeatureCollection', features: mapPoiDisplayFeatures});
  if (contextMode === 'transit') {
    stationSource?.setData({type: 'FeatureCollection', features: stations});
    transitPoiDisplayFeatures = stationAnchors(sourceData.transitViewPois?.features ?? [], stations,
      contextOptions.stations !== false);
    poiSource?.setData({type: 'FeatureCollection', features: transitPoiDisplayFeatures});
  }
  labelLayoutKey = '';
}
function poiDimensions(transit: boolean): [number, number] {
  return [Number(transit ? contextOptions.facility_icon_size ?? 24 : token('poi').size ?? 18),
    Number(transit ? contextOptions.station_size ?? token('transit').stationSize ?? 8 : token('transit').stationSize ?? 8)];
}
function updatePoiOffsets(): void {
  for (const id of ['pois', 'transit-pois', 'tv-poi']) if (map.getLayer(id))
    map.setLayoutProperty(id, 'icon-offset', stationIconOffset(...poiDimensions(id === 'tv-poi')));
}
function poiScreenOffset(feature: any): [number, number] {
  return feature.properties?.stationAnchor
    ? stationFacilityOffset(...poiDimensions(contextMode === 'transit' && feature.properties.owner === 'transit')) : [0, 0];
}
function poiScreenPoint(feature: any) {
  const point = map.project(feature.geometry.coordinates as [number, number]), offset = poiScreenOffset(feature);
  return {x: point.x + offset[0], y: point.y + offset[1]};
}
function transitViewColor(): ExpressionSpecification {
  const colorMode = String(contextOptions.line_color_mode ?? 'game');
  if (colorMode === 'game') return ['get', 'color'];
  const expression: unknown[] = ['match', ['to-number', ['get', 'lineId']]];
  const lines = sourceData.transit?.features ?? [];
  lines.forEach((feature: any, index: number) => {
    expression.push(Number(feature.properties?.lineId), transitViewFeatureColor(feature, index));
  });
  expression.push('#4277C9');
  return expression as ExpressionSpecification;
}
function transitViewFeatureColor(feature: any, index: number): string {
  const classic = ['#D94B4B', '#3978C5', '#2A9D69', '#E59A2F', '#8A5BB5', '#2A9DA8'];
  const soft = ['#D98282', '#729BCD', '#72B795', '#E4B46F', '#A98AC3', '#6EB6BC'];
  const colorMode = String(contextOptions.line_color_mode ?? 'game');
  if (colorMode === 'game') {
    return String(feature.properties?.color ?? '#4277C9');
  }
  const palette = colorMode === 'soft' ? soft : classic;
  return palette[index % palette.length];
}
// 公共交通视图的线路标签改成「彩底 + 自动深浅文字」（与
// legend_zoom 的图例胶囊一致：亮色用 #101828，暗色用 #FFFFFF）。
// 阈值与 ``transport_layer._light_outline_threshold`` 同步，避免
// 同一台机器上"图例深/字浅、标签深/字深"的不一致。
function perceivedLuminance(hex: string): number {
  const normalized = hex.startsWith('#') ? hex.slice(1) : hex;
  if (normalized.length !== 6) return 0;
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  // sRGB gamma 已近似于线性后取加权和：0.2126R+0.7152G+0.0722B
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
/** 公共交通视图线路标签的视觉规范。
 *
 * 与导出侧 ``transport_layer._make_view_badge`` 共用主题 ``transit_view`` 段，
 * 由 ``preview_scene._transit_label_style`` 下发。尺寸基准 = 长边 1024px 的成图。
 */
type TransitLabelStyle = {
  fontSizeBase: number;
  paddingRatio: number;
  outlineWidth: number;
  outlineColor: string;
  outlineAlpha: number;
  seamWidth: number;
  textOnLight: string;
  textOnDark: string;
  luminanceThreshold: number;
  fontWeight: number;
  font: string;
  mergeDistance: number;
};

const TRANSIT_LABEL_FALLBACK: TransitLabelStyle = {
  fontSizeBase: 15, paddingRatio: 0.6, outlineWidth: 2,
  outlineColor: '#101828', outlineAlpha: 0.55, seamWidth: 3,
  textOnLight: '#101828', textOnDark: '#FFFFFF', luminanceThreshold: 165,
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
  if (contextMode === 'transit') merged.fontSizeBase = Number(contextOptions.line_label_size ?? merged.fontSizeBase);
  return merged as TransitLabelStyle;
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.startsWith('#') ? hex.slice(1) : hex;
  if (normalized.length !== 6) return [0, 0, 0];
  return [parseInt(normalized.slice(0, 2), 16),
    parseInt(normalized.slice(2, 4), 16), parseInt(normalized.slice(4, 6), 16)];
}

/** 轮廓色：整条／整组共用一圈「深色 + 低透明度」的细边（与导出一致）。 */
function transitOutlineColor(style: TransitLabelStyle): string {
  const [r, g, b] = hexToRgb(style.outlineColor);
  return `rgba(${r}, ${g}, ${b}, ${style.outlineAlpha})`;
}

function transitBadgeStyle(hex: string, style: TransitLabelStyle):
    { color: string; border: string } {
  // 阈值与权重必须与导出侧 _luminance709 同式，否则中间亮度的线路色
  // 会在预览和导出图上判出相反的字色。
  const light = perceivedLuminance(hex) >= style.luminanceThreshold;
  return {
    color: light ? style.textOnLight : style.textOnDark,
    border: transitOutlineColor(style),
  };
}

/** 当前画布下的线路标签几何（字号按画布宽折算 + 内边距 / 轮廓宽度）。 */
function transitBadgeMetrics() {
  const style = transitLabelStyle();
  const canvasWidth = Math.max(map.getCanvas().clientWidth, 1);
  // 规范基准是「长边 1024px 成图」，屏幕系数 = 画布宽 / 1024；
  // 夹在 [6, 64]，与侧栏「名称字号」的可选范围（6~36）对齐 —— 早先夹在
  // [12, 22] 会把小于 12 的值抬上来、大于 22 的值压下去，等于字号只有
  // 中间一小段能真正生效，看起来就是「调了没反应」。
  const size = Math.min(64, Math.max(6,
    style.fontSizeBase * (canvasWidth / 1024)));
  // 轮廓跟着「实际生效的字号缩放系数」走，而不是画布系数：字号被 [12, 22]
  // 夹住时轮廓也同步被夹，「轮廓 / 字号」的比例才和导出侧恒定一致。
  // 再取整到整数像素 —— CSS 的非整数边框会被反锯齿冲淡，1.5px 实际观感
  // 只剩约一半浓度，这正是之前「设了轮廓却看不见」的直接原因。
  const scale = style.fontSizeBase > 0 ? size / style.fontSizeBase : 1;
  return {
    style,
    size,
    padX: size * style.paddingRatio,
    padY: size * 0.375,          // 胶囊高 = 1.75 × 字号
    outline: Math.max(Math.round(style.outlineWidth * scale), 1),
    radius: size * 1.75 / 2,
  };
}

/** 创建和主题更新共用线路徽章样式。 */
function paintTransitBadge(el: HTMLElement, color: string,
                           metrics = transitBadgeMetrics()): void {
  const {style} = metrics;
  el.style.fontSize = `${metrics.size}px`;
  el.style.fontWeight = String(style.fontWeight);
  el.style.fontFamily = `"${style.font}", "Microsoft YaHei", sans-serif`;
  el.style.padding = `${metrics.padY}px ${metrics.padX}px`;
  el.style.borderRadius = `${metrics.radius}px`;
  el.style.borderWidth = `${metrics.outline}px`;
  // 「线路色实心底 + 自适应深浅字 + 一圈半透明细轮廓」：与导出侧
  // _make_view_badge 同一组值（主题 transit_view 段，经 labelStyle 下发）。
  el.style.background = color;
  const tone = transitBadgeStyle(color, style);
  el.style.color = tone.color;
  el.style.borderColor = tone.border;
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
      * WEB_MERCATOR_METERS_PER_DEGREE / LOCAL_METERS_PER_DEGREE], widthAt(z)];
  return currentManifest?.layers.road === false ? ['min', 14, width] : width;
}
function trafficBandWidthAt(z: number): ExpressionSpecification {
  const road = trafficRoadWidthAt(z);
  return ['case', ['==', ['get', 'splitDirections'], true],
    ['min', 6, ['max', .5, ['*', .34, road]]],
    ['min', 10, ['max', .8, ['*', .8, road]]]];
}
function trafficMetric(direction?: TrafficDirection, casing = false, offset = false): ExpressionSpecification {
  return ['interpolate', ['exponential', 2], ['zoom'],
    ...Array.from({length: 25}, (_, z) => {
      const road = trafficRoadWidthAt(z), band = trafficBandWidthAt(z);
      const outline: ExpressionSpecification = ['min', TRAFFIC_OUTLINE, ['*', .2, band]];
      const outer: ExpressionSpecification = ['+', band, ['*', 2, outline]];
      const side: ExpressionSpecification = ['*', direction === 'forward' ? -1 : 1,
        ['coalesce', ['get', 'forwardSide'], 1]];
      const shift: ExpressionSpecification = ['case', ['==', ['get', 'splitDirections'], true],
        ['*', side, ['max', 0, ['/', ['-', road, outer], 2]]], 0];
      return [z, offset ? shift : direction ? (casing ? outer : band) : road];
    }).flat()] as ExpressionSpecification;
}
function trafficColor(direction: TrafficDirection): ExpressionSpecification {
  return trafficColorExpression(['get', `${direction}Congestion`]);
}
function addTrafficLayers(): void {
  // 描边统一先于色带，不为路况构建独立高低层级。
  for (const layer of trafficLayers) {
    add({id: layer.id, type: 'line', source: 'traffic', filter: trafficFilter(layer.direction),
      layout: {'line-cap': 'round', 'line-join': 'round'},
      paint: {'line-color': layer.direction ? (layer.casing ? '#52635B' : trafficColor(layer.direction)) : '#D9DDDA',
        'line-width': trafficMetric(layer.direction, layer.casing),
        'line-offset': layer.direction ? trafficMetric(layer.direction, false, true) : 0}});
  }
  updateTrafficPaint();
}
function updateTrafficPaint(): void {
  if (!map.getSource('traffic')) return;
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
    const data = await provider.json(new URL(manifest.sources.traffic, provider.base), request.signal);
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
function railWidth(field: 'width' | 'stationWidth' | 'dashWidth'): ExpressionSpecification {
  return ['interpolate', ['exponential', 2], ['zoom'],
    ...Array.from({length: 25}, (_, z) => {
      const value: unknown[] = ['match', ['get', 'railType']];
      for (const kind of RAIL_TYPES) {
        const size = token('railway').types?.[kind]?.[field] ?? {fixed: 1, scaled: 0};
        value.push(kind, Math.max(.5, Number(size.fixed ?? 0)
          + Number(size.scaled ?? 0) * renderScaleAt(z)));
      }
      value.push(.5);
      return [z, value as ExpressionSpecification];
    }).flat()
  ] as ExpressionSpecification;
}
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
function addDomains(): void {
  const land = token('terrain'), water = token('water'), building = token('building');
  const district = token('district'), zone = token('zone');
  const transit = token('transit'), use = token('land_use');
  updateTransitViewLayout();
  if (!map.getSource('transit-view-stations')) map.addSource('transit-view-stations', {
    type: 'geojson', data: {type: 'FeatureCollection', features: contextMode === 'transit' ? transitStationDisplayFeatures : []},
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
  if (map.getSource('terrain-image')) add({ id: 'terrain', type: 'raster', source: 'terrain-image',
    paint: { 'raster-opacity': land.opacity ?? 1, 'raster-fade-duration': 0 } });
  add({ id: 'forest', type: 'circle', source: 'forest', paint: {
    'circle-color': land.forest ?? '#B8DCA0', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 1, 15, 7],
    'circle-opacity': .58 } });
  if (pendingBuiltSurface) {
    const paint = token('built_area');
    builtSurfaceLayer = new SurfaceDistanceLayer(
      'built-area-surface', pendingBuiltSurface.image, pendingBuiltSurface.spec,
      { color: paint.fill ?? '#F1F0EB', opacity: Number(paint.opacity ?? 1),
        bufferMeters: paint.includeParks ? 0 : Number(paint.bufferMeters ?? 60), edgeMeters: Number(paint.edgeMeters ?? 0) });
    add(builtSurfaceLayer);
  } else if (map.getSource('built-area')) add({ id: 'built-area', type: 'fill', source: 'built-area', paint: {
    'fill-color': token('built_area').fill ?? '#F1F0EB', 'fill-opacity': token('built_area').opacity ?? 1 } });
  add({ id: 'land-use', type: 'fill', source: 'land-use', paint: {
    'fill-color': categoryColor(use.colors ?? {}, '#D8D0C8'),
    'fill-opacity': use.opacity ?? .35 } });
  add({ id: 'poi-area', type: 'fill', source: 'buildings', paint: {
    'fill-color': buildingColor(), 'fill-outline-color': 'rgba(0,0,0,0)' } });
  add({ id: 'water-fill', type: 'fill', source: 'water', filter: ['==', ['get', 'geometryRole'], 'water'],
    paint: { 'fill-color': water.fill ?? '#80D4FE',
      'fill-opacity': painterOrderedOpacity(1) } });
  add({ id: 'water-shore', type: 'line', source: 'water', filter: ['==', ['get', 'geometryRole'], 'water'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': water.border ?? '#60C0E8', 'line-width': water.width ?? 1 } });
  add({ id: 'district-fill', type: 'fill', source: 'districts', paint: {
    'fill-color': district.fill ?? '#D9E8CC', 'fill-opacity': district.opacity ?? .3 } });
  add({ id: 'district-border', type: 'line', source: 'districts', paint: {
    'line-color': district.border ?? '#A6A6AD', 'line-width': 1.2 } });
  add({ id: 'zone-fill', type: 'fill', source: 'zones', paint: {
    'fill-color': zone.fill ?? '#C8C8C8', 'fill-opacity': zone.opacity ?? .35 } });
  add({ id: 'zone-border', type: 'line', source: 'zones', paint: {
    'line-color': zone.border ?? '#A89880', 'line-width': 1.2 } });
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
    'circle-radius': Math.max(3, Number(transit.stationSize ?? 8) / 2), 'circle-color': '#fff',
    'circle-stroke-color': '#39424C', 'circle-stroke-width': 1.5 } });
  add({ id: 'tv-line', type: 'line', source: 'transit', filter: alwaysFalseFilter(),
    layout: { 'visibility': 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': transit.width ?? 3 } });
  add({ id: 'tv-station', type: 'circle', source: 'transit-view-stations',
    layout: { 'visibility': 'none' }, paint: {
      'circle-radius': Math.max(3, Number(transit.stationSize ?? 8) / 2), 'circle-color': '#fff',
      'circle-stroke-color': '#39424C', 'circle-stroke-width': 1.5 } });
  add({ id: 'pois', type: 'symbol', source: 'map-pois-displaced',
    filter: poiFilter(),
    layout: { 'icon-image': ['get', 'iconKey'], 'icon-size': Number(token('poi').size ?? 18) / 24,
      'symbol-sort-key': ['*', -1, ['get', 'priority']], 'symbol-z-order': 'source',
      'icon-allow-overlap': false, 'icon-ignore-placement': false, 'icon-padding': 4, 'icon-offset': stationIconOffset(...poiDimensions(false)), 'icon-rotation-alignment': 'viewport', 'icon-pitch-alignment': 'viewport' } });
  // 普通地图使用此图层；交通视图通过 tv-poi 管理图标。
  add({ id: 'transit-pois', type: 'symbol', source: 'map-pois-displaced',
    filter: transitPoiFilter(),
    layout: { 'icon-image': ['get', 'iconKey'], 'icon-size': Number(token('poi').size ?? 18) / 24,
      'symbol-sort-key': ['*', -1, ['get', 'priority']], 'symbol-z-order': 'source',
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-padding': 0, 'icon-offset': stationIconOffset(...poiDimensions(false)), 'icon-rotation-alignment': 'viewport', 'icon-pitch-alignment': 'viewport' } });
  add({ id: 'tv-poi', type: 'symbol', source: 'transit-view-pois-displaced', filter: transitViewPoiFilter(),
    layout: { 'visibility': 'none', 'icon-image': ['get', 'iconKey'],
      'icon-size': Number(contextOptions.facility_icon_size ?? 24) / 24,
      'symbol-sort-key': ['*', -1, ['get', 'transitPriority']], 'symbol-z-order': 'source',
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-padding': 0, 'icon-offset': stationIconOffset(...poiDimensions(true)), 'icon-rotation-alignment': 'viewport', 'icon-pitch-alignment': 'viewport' } });
  addBaseTextLayers();
  for (const source of ['inspector-selection', 'search-selection']) {
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

function applyTheme(theme: FullTheme): void {
  currentTheme = theme; map.setPaintProperty('background', 'background-color', theme.road.background);
  const set = (id: string, prop: Parameters<MapLibreMap['setPaintProperty']>[1], value: any) => { if (map.getLayer(id) && value !== undefined) map.setPaintProperty(id, prop, value); };
  const filter = (id: string, value: any) => { if (map.getLayer(id)) map.setFilter(id, value); };
  const layout = (id: string, prop: Parameters<MapLibreMap['setLayoutProperty']>[1], value: any) => { if (map.getLayer(id)) map.setLayoutProperty(id, prop, value); };
  for (const layer of roadLayers) {
    filter(layer.id, roadFilter(layer));
    set(layer.id, `${layer.type}-color`, roadToken(layer.paint,
      layer.paint === 'border' ? '#c3c8d2' : '#fff'));
    if (layer.type === 'line') set(layer.id, 'line-width',
      roadLineWidth(layer.width));
  }
  set('water-fill', 'fill-color', token('water').fill); set('water-shore', 'line-color', token('water').border);
  set('water-shore', 'line-width', token('water').width);
  set('built-area', 'fill-color', token('built_area').fill);
  builtSurfaceLayer?.updatePaint({ color: token('built_area').fill ?? '#F1F0EB',
    opacity: Number(token('built_area').opacity ?? 1),
    bufferMeters: token('built_area').includeParks ? 0 : Number(token('built_area').bufferMeters ?? 60),
    edgeMeters: Number(token('built_area').edgeMeters ?? 0) });
  set('forest', 'circle-color', token('terrain').forest);
  set('land-use', 'fill-color', categoryColor(token('land_use').colors ?? {}, '#D8D0C8'));
  set('poi-area', 'fill-color', buildingColor());
  set('land-use', 'fill-opacity', token('land_use').opacity);
  set('buildings', 'fill-color', buildingColor());
  set('buildings', 'fill-outline-color', token('building').border);
  for (const id of ['railway-base', 'railway-station']) {
    set(id, 'line-color', railTypePaint('baseColor', '#C36E69'));
    set(id, 'line-opacity', railTypePaint('baseOpacity', 1));
  }
  set('railway-base', 'line-width', railWidth('width'));
  set('railway-station', 'line-width', railWidth('stationWidth'));
  for (const kind of RAIL_TYPES) {
    const paint = token('railway').types?.[kind] ?? {};
    const id = `railway-dash-${kind}`;
    set(id, 'line-color', paint.dashColor ?? '#FFFFFF');
    set(id, 'line-opacity', Number(paint.dashOpacity ?? 1));
    set(id, 'line-width', railWidth('dashWidth'));
    set(id, 'line-dasharray', paint.dashPattern ?? [2, 4]);
    if (map.getLayer(id)) map.setLayoutProperty(
      id, 'visibility', paint.dashEnabled ? 'visible' : 'none');
  }
  set('district-fill', 'fill-color', token('district').fill); set('district-border', 'line-color', token('district').border);
  set('district-fill', 'fill-opacity', token('district').opacity);
  set('district-border', 'line-width', Number(token('district').width ?? 1));
  set('zone-fill', 'fill-color', token('zone').fill); set('zone-border', 'line-color', token('zone').border);
  set('zone-fill', 'fill-opacity', token('zone').opacity);
  set('zone-border', 'line-width', Number(token('zone').width ?? 1));
  set('procedural-objects', 'fill-color', token('procedural_objects').fill);
  updateTrafficPaint();
  const modeWidths = Object.entries(token('transit').typeWidths ?? {});
  set('transit', 'line-width', modeWidths.length ? ['match', ['downcase', ['to-string', ['get', 'kind']]],
    ...modeWidths.flatMap(([kind, width]) => [kind, Number(width)]),
    Number(token('transit').width ?? 3)] : Number(token('transit').width ?? 3));
  set('stations', 'circle-radius', Math.max(3, Number(token('transit').stationSize ?? 8) / 2));
  set('tv-line', 'line-width', Number(contextOptions.line_width ?? token('transit').width ?? 3));
  set('tv-station', 'circle-radius', Math.max(3, Number(
    contextOptions.station_size ?? token('transit').stationSize ?? 8) / 2));
  layout('pois', 'icon-size', Number(token('poi').size ?? 18) / 24);
  layout('transit-pois', 'icon-size', Number(token('poi').size ?? 18) / 24);
  if (map.getLayer('pois')) map.setFilter('pois', poiFilter());
  layout('tv-poi', 'icon-size', Number(contextOptions.facility_icon_size ?? 24) / 24);
  set('built-area', 'fill-opacity', token('built_area').opacity ?? 1);
  labelLayoutKey = '';
  const wm = token('watermark'); watermark.textContent = String(wm.text ?? 'CSLModernMap');
  watermark.style.opacity = String(wm.opacity ?? .4);
  watermark.classList.toggle('preview-watermark-left', wm.position === 'bl');
  for (const item of markers.values()) { const el = item.marker.getElement();
    if (item.group === 'transit-line') {
      // 与 layoutLabels 共用同一套规范，避免这里用旧样式（白底 + 线路色字）
      // 把徽章刷回去 —— 两处必须走同一个函数。
      paintTransitBadge(el, item.featureColor ?? '#4277C9');
    } else {
      el.style.color = labelColor(item.labelType, item.featureColor);
      el.style.fontSize = `${labelFontSize(item.labelType, item.transitStationName)}px`;
      el.style.fontFamily = labelFontFamily(item.labelType);
      applyLabelHalo(el, item.labelType);
    } }
  applyContextStyling();
  navigation.refreshStyle();
  scheduleLabels();
}
async function setLayerVisibility(layer: string, visible: boolean): Promise<void> {
  for (const id of groups[layer] ?? []) if (map.getLayer(id)) {
    let layerVisible = visible;
    if (layerVisible && layer === 'railway' && id.startsWith('railway-dash-')) {
      const kind = id.slice('railway-dash-'.length);
      layerVisible = Boolean(token('railway').types?.[kind]?.dashEnabled);
    }
    map.setLayoutProperty(id, 'visibility', layerVisible ? 'visible' : 'none');
  }
  for (const item of markers.values()) if (item.group === layer) item.marker.getElement().style.display = visible ? '' : 'none';
  if (layer === 'watermark') watermark.style.display = visible ? '' : 'none';
  if (currentManifest) currentManifest.layers[layer] = visible;
  if (layer === 'traffic') {
    for (const checkbox of document.querySelectorAll<HTMLInputElement>('input[data-layer="traffic"]')) checkbox.checked = visible;
    await syncTraffic();
  }
  if (layer === 'road' || layer === 'traffic') updateTrafficPaint();
  if (layer === 'poi' || layer === 'metro') applyContextStyling();
  if (layer === 'label') updateBaseTextLayers();
  if (layer === 'building') preview3d.refresh();
  labelLayoutKey = '';
  scheduleLabels();
  updateQuickLayers();
}
function applyContextStyling(): void {
  updateBaseTextLayers();
  if (!map.getLayer('transit')) return;
  const active = contextMode === 'transit';
  const transitLayers = (contextOptions.layers ?? {}) as Record<string, unknown>;
  const transitLayerVisible = (key: string, fallback = true): boolean =>
    transitLayers[key] == null ? fallback : transitLayers[key] !== false;
  const mask = Math.max(0, Math.min(1, Number(contextOptions.transit_mask ?? .6)));
  const fade = active ? Math.max(.08, 1-mask) : 1;
  const baseRoadAllowed = !active
    || (contextOptions.base_road !== false);
  builtSurfaceLayer?.setFade(fade);
  const set = (id: string, prop: Parameters<MapLibreMap['setPaintProperty']>[1], value: any) => {
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
  for (const layer of roadLayers) set(layer.id, `${layer.type}-opacity`, baseRoadAllowed ? fade : 0);
  const ordinaryPoiMode = String(contextOptions.ordinary_pois ?? 'hidden');
  set('pois', 'icon-opacity', active ? ordinaryPoiMode === 'visible' ? 1 : ordinaryPoiMode === 'dim' ? fade : 0 : 1);
  set('transit-pois', 'icon-opacity', active ? 0 : 1);
  set('transit', 'line-opacity', active ? 0 : 1);
  set('stations', 'circle-opacity', active ? 0 : 1);
  set('tv-line', 'line-opacity', active ? 1 : 0);
  set('tv-line', 'line-width', Number(contextOptions.line_width ?? token('transit').width ?? 3));
  set('tv-line', 'line-color', transitViewColor());
  set('tv-station', 'circle-opacity', active ? 1 : 0);
  set('tv-station', 'circle-radius', Math.max(3, Number(
    contextOptions.station_size ?? token('transit').stationSize ?? 8) / 2));
  if (map.getLayer('tv-poi')) map.setLayoutProperty(
    'tv-poi', 'icon-size', Number(contextOptions.facility_icon_size ?? 24) / 24);
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
  updatePoiOffsets(); updatePoiFilters();
  if (active) {
    map.setLayoutProperty('transit', 'visibility', 'none');
    map.setLayoutProperty('stations', 'visibility', 'none');
    map.setLayoutProperty('transit-pois', 'visibility', 'none');
    map.setLayoutProperty('tv-line', 'visibility',
      transitLayerVisible('lines') ? 'visible' : 'none');
    map.setLayoutProperty('tv-station', 'visibility',
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
  return {name: String(selected.properties?.name || feature.properties?.name || i18n.t('building', {ns: 'navigation'})), geometry: selected.geometry};
}

function selectableLayerIds(): string[] {
  return Object.keys(selectableObjects).filter(id => Boolean(map.getLayer(id)));
}
function featureAt(point: {x: number; y: number}): any | null {
  const layers = selectableLayerIds();
  if (!layers.length) return null;
  const radius = 5;
  const features = map.queryRenderedFeatures(
    [[point.x-radius, point.y-radius], [point.x+radius, point.y+radius]], {layers});
  const unique = new Map<string, any>();
  for (const feature of features) {
    const featureId = String(feature.properties?.featureId ?? feature.id ?? '');
    const key = `${feature.layer.id}:${featureId}`;
    if (!unique.has(key)) unique.set(key, feature);
  }
  return [...unique.values()].sort((a, b) =>
    (selectableObjects[b.layer.id]?.priority ?? 0)
    - (selectableObjects[a.layer.id]?.priority ?? 0)
    || Number(b.properties?.rank ?? 0)-Number(a.properties?.rank ?? 0))[0] ?? null;
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
  if (points.length === 1) { map.easeTo({center: points[0] as [number, number], zoom: Math.max(map.getZoom(), 15), duration: 260, bearing: outputBearing(outputRotation)}); return; }
  const bounds = new maplibregl.LngLatBounds();
  for (const point of points) bounds.extend(point as [number, number]);
  const width = map.getCanvas().clientWidth;
  map.fitBounds(bounds, {padding: {top: 65, bottom: 55, left: 35, right: Math.min(120, width / 4)},
    maxZoom: 17, duration: 260, bearing: outputBearing(outputRotation)});
}
function inspectFeature(feature: any): void {
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
  releaseTraffic(); navigation.clear(); preview3d.reset(); cancelLabelLayout();
  for (const item of markers.values()) item.marker.remove();
  markers.clear(); poiAnchorInputs = []; labelWidths.clear();
  for (const id of [...customLayers].reverse()) if (map.getLayer(id)) map.removeLayer(id); customLayers.length = 0;
  if (map.getSource('terrain-image')) map.removeSource('terrain-image');
  for (const id of Object.values(sourceIds)) if (map.getSource(id)) map.removeSource(id);
  if (map.getSource('transit-view-stations')) map.removeSource('transit-view-stations');
  if (map.getSource('transit-view-pois-displaced')) map.removeSource('transit-view-pois-displaced');
  if (map.getSource('map-pois-displaced')) map.removeSource('map-pois-displaced');
  transitStationDisplayFeatures = []; mapPoiDisplayFeatures = []; transitPoiDisplayFeatures = [];
  for (const layer of roadLayers) delete selectableObjects[layer.id];
  roadLayers = [];
  builtSurfaceLayer = null; pendingBuiltSurface = null;
}
type Box = [number, number, number, number];
function transitFeatureVisible(feature: any): boolean {
  const ids = new Set(selectedTransitRouteIds());
  return ids.has(Number(feature.properties?.lineId));
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
function geoDistance(a: [number, number], b: [number, number]): number {
  const rad = Math.PI / 180, lat1 = a[1] * rad, lat2 = b[1] * rad;
  const dLat = (b[1] - a[1]) * rad, dLon = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 12742000 * Math.asin(Math.min(1, Math.sqrt(h)));
}
function lineBadgePoints(feature: any, interval: number, maximum: number): [number, number][] {
  const coordinates = feature.geometry?.coordinates as [number, number][] | undefined;
  if (!coordinates || coordinates.length < 2 || maximum <= 0) return [];
  const segments = coordinates.slice(1).map((point, index) => geoDistance(coordinates[index], point));
  const total = segments.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return [];
  const gap = Math.max(80, interval);
  const count = Math.max(1, Math.min(maximum, Math.floor(total / gap) || 1));
  const targets = Array.from({length: count}, (_, index) => (index + .5) * total / count);
  const result: [number, number][] = [];
  for (const target of targets) {
    let traversed = 0;
    for (let index = 0; index < segments.length; index++) {
      const length = segments[index];
      if (target <= traversed + length || index === segments.length - 1) {
        const ratio = length > 0 ? Math.max(0, Math.min(1, (target - traversed) / length)) : 0;
        result.push([
          coordinates[index][0] + (coordinates[index + 1][0] - coordinates[index][0]) * ratio,
          coordinates[index][1] + (coordinates[index + 1][1] - coordinates[index][1]) * ratio,
        ]);
        break;
      }
      traversed += length;
    }
  }
  return result;
}
function resolvedLabelType(feature: any, group: string): string {
  if (group === 'transit') return 'stop';
  if (group === 'poi') return 'poi';
  return String(feature.properties?.labelType ?? 'default').toLowerCase();
}
function labelFontSize(labelType: string, transitStationName = false): number {
  if (contextMode === 'transit' && transitStationName) {
    return Math.max(6, Math.round(Number(contextOptions.station_name_size ?? 12)
      * Math.max(map.getCanvas().clientWidth, 1) / 1024));
  }
  const label = token('label');
  const metrics = label.metrics;
  if (metrics) {
    if (labelType === 'stop') return Math.max(8, Math.round(resolveTextSize(metrics.station?.size ?? 11)));
    let size = resolveTextSize(metrics.sizes?.[labelType] ?? label.sizes?.[labelType] ?? 12);
    if (labelType === 'poi') {
      if (typeof metrics.poiMaxSize === 'number') size = Math.min(size, metrics.poiMaxSize);
      if (contextMode === 'transit') size *= Number(contextOptions.label_scale ?? 1) * metrics.poiTransitScale;
    }
    return size;
  }
  const configured = Number(label.sizes?.[labelType] ?? label.size ?? 12);
  const contextScale = contextMode === 'transit' && labelType === 'stop'
    ? Number(contextOptions.label_scale ?? 1) : 1;
  return configured * contextScale;
}
function resolveTextSize(value: number | {fixed?: number; scaled?: number}): number {
  const scale = 512 * 2 ** map.getZoom() / (360 * 111195) / (4096 / 17280);
  return typeof value === 'number' ? value : Number(value.fixed ?? 0) + Number(value.scaled ?? 0) * scale;
}
function labelColor(labelType: string, featureColor?: string): string {
  if (featureColor) return featureColor;
  const label = token('label');
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
/** 该类别的描边颜色与宽度（px）；宽度由主题 stroke_width 换算到 1024 基准下发。 */
function labelStroke(labelType: string): {color: string; width: number} {
  const label = token('label');
  if (label.metrics) {
    if (labelType === 'poi') return {color: String(label.strokes?.poi?.color ?? '#FFFFFF'),
      width: label.metrics.poiStrokeRatio > 0 ? Math.max(1, Math.round(labelFontSize('poi') * label.metrics.poiStrokeRatio)) : 0};
    if (labelType === 'stop') return {color: String(label.metrics.station?.stroke_fill ?? '#FFFFFF'),
      width: Number(label.metrics.station?.stroke_width ?? (label.metrics.station?.halo === false ? 0 : 1))};
    return {color: String(label.strokes?.[labelType]?.color ?? '#FFFFFF'),
      width: resolveTextSize(label.metrics.strokeWidths?.[labelType] ?? 0)};
  }
  const strokes = (label.strokes ?? {}) as Record<string, any>;
  const entry = (strokes[labelType] ?? {}) as Record<string, any>;
  const width = Number(entry.width ?? 1);
  return {
    color: String(entry.color ?? '#FFFFFF'),
    width: Number.isFinite(width) ? width : 1,
  };
}
/** 把描边开关 + 颜色/宽度落到元素上；text-shadow 的形状写在 style.css 里。 */
function applyLabelHalo(el: HTMLElement, labelType: string): void {
  const stroke = labelStroke(labelType);
  const width = labelHalo(labelType) ? stroke.width : 0;
  el.classList.toggle('map-label-no-halo', width <= 0);
  if (width <= 0) return;
  el.style.setProperty('--label-stroke-width', `${width * 2}px`);
  el.style.setProperty('--label-stroke-c', stroke.color);
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
function nativeTextSize(labelType: string, stroke = false): ExpressionSpecification {
  const label = token('label');
  const value = stroke ? label.metrics?.strokeWidths?.[labelType] ?? 0
    : label.metrics?.sizes?.[labelType] ?? label.sizes?.[labelType] ?? label.size ?? 12;
  const cfg = token('baseText').road?.size_z ?? {};
  const multiplier: ExpressionSpecification = ['match', ['get', 'roadLevel'],
    'highway', Number(cfg.highway ?? 1.6), 'main', Number(cfg.main ?? cfg.road_wide ?? 1.25),
    'branch', Number(cfg.branch ?? cfg.road ?? 1),
    'special', Number(cfg.special ?? cfg.walk ?? .75),
    'beautification', Number(cfg.beautification ?? cfg.walk ?? .75), 1];
  // 与 Vellum 一样，继续放大时保持最终屏幕尺寸，不无限增大。
  const stops = Array.from({length: 11}, (_, i) => {
    const z = i + 8;
    const size = typeof value === 'number' ? value
      : Number(value.fixed ?? 0) + Number(value.scaled ?? 0) * 2 * renderScaleAt(z);
    const scaled = labelType === 'road' ? ['*', size, multiplier] : size;
    return [z, stroke ? scaled : ['max', 6, scaled]];
  }).flat();
  return ['interpolate', ['exponential', 2], ['zoom'], ...stops] as ExpressionSpecification;
}
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
    map.setPaintProperty(id, 'text-halo-width', labelHalo(labelType) ? nativeTextSize(labelType, true) : 0);
  }
}
async function loadSceneFonts(manifest: SceneManifest, base: string): Promise<void> {
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
    document.fonts.add(font);
    loadedFonts.add(asset.family);
  }));
}
function cancelLabelLayout(): void {
  window.clearTimeout(labelTimer); labelTimer = 0; labelDirty = false; labelLayoutTime = -Infinity;
}
function scheduleLabels(): void {
  labelDirty = true;
  if (labelTimer || !currentManifest) return;
  const delay = Math.max(0, 250 - (performance.now() - labelLayoutTime));
  labelTimer = window.setTimeout(() => {
    labelTimer = 0;
    if (labelDirty) { labelDirty = false; layoutLabels(); }
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
function layoutLabels(): void {
  if (!currentManifest || map.isMoving()) return;
  labelLayoutTime = performance.now();
  const renderedLayers = contextMode === 'transit'
    ? [contextOptions.facilities_enabled === false ? '' : 'tv-poi',
       String(contextOptions.ordinary_pois ?? 'hidden') === 'hidden' ? '' : 'pois']
    : currentManifest.layers.poi === false ? [] : ['pois', 'transit-pois'];
  const layers = renderedLayers.filter(id => id && map.getLayer(id));
  // GeoJSON 更新及瓦片切换期间，符号放置结果可能仍引用上一版要素索引。
  if (layers.some(id => !map.isSourceLoaded((map.getLayer(id) as {source: string}).source))) return;
  const rendered = layers.length ? map.queryRenderedFeatures({layers}) : [];
  const seen = new Set<string>();
  const icons = rendered.filter(f => { const id = String(f.properties?.featureId ?? f.id); if (seen.has(id)) return false; seen.add(id); return true; });
  const labelVisibility = (token('label').visibility ?? {}) as Record<string, boolean>;
  const poiNameVisibility = (token('poiVisibility').names ?? {}) as Record<string, boolean>;
  const key = JSON.stringify([currentRevision, map.getZoom(), map.getCenter(), map.getCanvas().clientWidth,
    map.getCanvas().clientHeight, icons.map(f => [f.id, f.geometry, f.properties?.stationAnchor, f.layer.id]).sort((a, b) => String(a[0]).localeCompare(String(b[0]))), currentManifest.layers,
    map.getBearing(), map.getPitch(), contextMode, contextOptions, token('transit').label, labelVisibility, token('baseText'),
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
  const radius = Number(token('poi').size ?? 18)/2;
  for (const f of icons) { if (f.geometry.type !== 'Point') continue;
    const p = poiScreenPoint(f), r = poiDimensions(f.layer.id === 'tv-poi')[0] / 2;
    reserve([p.x-r, p.y-r, p.x+r, p.y+r]); }
  const stationRadius = Math.max(3, Number(
    contextOptions.station_size ?? token('transit').stationSize ?? 8) / 2);
  if (contextMode === 'transit' && contextOptions.stations !== false) {
    for (const feature of transitStationDisplayFeatures) {
      const p = map.project(feature.geometry.coordinates as [number, number]);
      reserve([p.x-stationRadius, p.y-stationRadius, p.x+stationRadius, p.y+stationRadius]);
    }
  }
  // 附属名称只跟随已显示图标；显式空图标保留纯文字。
  const iconById = new Map(icons.map(f => [String(f.properties?.poiId ?? f.properties?.featureId ?? f.id), f]));
  const width = map.getCanvas().clientWidth, height = map.getCanvas().clientHeight;
  const sources = contextMode === 'transit'
    ? [...transitPoiDisplayFeatures, ...(String(contextOptions.ordinary_pois ?? 'hidden') === 'hidden'
      ? [] : mapPoiDisplayFeatures.filter((f: any) => f.properties?.owner !== 'transit'))]
    : currentManifest.layers.poi === false ? [] : mapPoiDisplayFeatures;
  const labelSeen = new Set<string>();
  const selectedRoutes = new Set(selectedTransitRouteIds());
  const iconGroups = effectivePoiIcons();
  const bounds = map.getBounds();
  const stationIcons = contextOptions.station_name_placement === 'station'
    && contextOptions.stations !== false && currentManifest.layers.metro !== false
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
    if (contextMode === 'transit' && properties.owner === 'transit' && contextOptions.facilities_enabled === false) return false;
    const point = poiScreenPoint(f);
    if (point.x < 0 || point.y < 0 || point.x > width || point.y > height) return false;
    return Boolean(properties.drawName) && poiNameVisibility[group] !== false
      && (contextMode !== 'transit' || group !== 'transit'
        || contextOptions.station_name_placement === 'icon');
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
    const contextVisible = stationSymbol || contextMode === 'transit' || currentManifest.layers.poi !== false;
    if (!contextVisible || f.geometry.type !== 'Point' || !f.properties?.[field]) continue;
    const p = poiScreenPoint(f);
    const labelType = stationSymbol ? 'stop' : resolvedLabelType(f, group);
    const transitStationName = contextMode === 'transit'
      && (stationSymbol || f.properties?.owner === 'transit');
    const fontSize = labelFontSize(labelType, transitStationName);
    if (fontSize < 6) continue;
    const font = `${fontSize}px ${labelFontFamily(labelType)}`;
    const text = String(f.properties[field]);
    const width = measuredLabelWidth(text, font)+10, height = fontSize+8;
    const gap = (stationSymbol ? stationRadius : contextMode === 'transit' && f.properties?.owner === 'transit'
      ? Number(contextOptions.facility_icon_size ?? 24) / 2 : radius) + 5;
    const offsets: [number, number][] = [
      [gap + width/2, 0], [-gap - width/2, 0],
      [0, -gap - height/2], [0, gap + height/2],
    ];
    const score = ([x, y]: [number, number]): number => overlapArea(
      [p.x+x-width/2, p.y+y-height/2, p.x+x+width/2, p.y+y+height/2]);
    const avoid = token('poi')[contextMode === 'transit' ? 'transitOverlapAvoidance' : 'overlapAvoidance'] !== false;
    const position = !avoid ? offsets[0]
      : offsets.reduce((best, candidate) => score(candidate) < score(best) ? candidate : best);
    const [offsetX, offsetY] = position;
    const box: Box = [p.x+offsetX-width/2, p.y+offsetY-height/2,
      p.x+offsetX+width/2, p.y+offsetY+height/2];
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
    applyLabelHalo(el, labelType);
  }
  const badge = token('transit').label ?? {};
  if (contextMode === 'transit' && badge.enabled !== false
      && contextOptions.line_label_enabled !== false
      && (contextOptions.layers ?? {}).lines !== false
     ) {
    const metrics = transitBadgeMetrics();
    const {style: badgeStyle, size: badgeSize} = metrics;
    const interval = Number(badge.repeatInterval ?? contextOptions.line_label_interval ?? 600);
    const maximum = Number(badge.maxPerLine ?? 4);
    const font = `${badgeStyle.fontWeight} ${badgeSize}px `
      + `"${badgeStyle.font}", "Microsoft YaHei", sans-serif`;
    for (const [featureIndex, feature] of (sourceData.transit?.features ?? []).entries()) {
      if (!transitFeatureVisible(feature)) continue;
      const name = String(feature.properties?.name ?? '').trim();
      if (!name) continue;
      const color = transitViewFeatureColor(feature, featureIndex);
      const width = Math.min(180, measuredLabelWidth(name, font))
        + (metrics.padX + metrics.outline) * 2;
      const height = badgeSize + (metrics.padY + metrics.outline) * 2;
      for (const [pointIndex, coordinate] of lineBadgePoints(feature, interval, maximum).entries()) {
        const p = map.project(coordinate), box: Box = [p.x-width/2, p.y-height/2, p.x+width/2, p.y+height/2];
        if (box[0] < 0 || box[1] < 0 || box[2] > map.getCanvas().clientWidth
            || box[3] > map.getCanvas().clientHeight || overlaps(box)) continue;
        reserve(box);
        const id = `badge:${feature.properties.featureId ?? feature.id ?? featureIndex}:${pointIndex}`;
        kept.add(id);
        const el = placeLabel(id, coordinate, [0, 0], {group: 'transit-line', labelType: 'transit-line', featureColor: color});
        el.classList.add('map-transit-badge');
        if (el.textContent !== name) el.textContent = name;
        paintTransitBadge(el, color, metrics);
      }
    }
  }
  for (const [id, item] of markers) if (!kept.has(id)) { item.marker.remove(); markers.delete(id); }
}
map.on('move', scheduleLabels);
map.on('moveend', scheduleLabels);
map.on('idle', scheduleLabels);
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
  json(url: URL, signal?: AbortSignal): Promise<any>;
  image(url: string): Promise<ImageBitmap>;
  imageUrlFor(url: string): string;
};
function httpProvider(base: string): ResourceProvider {
  return {
    base,
    async json(url: URL, signal?: AbortSignal) {
      const response = await fetch(url, {signal});
      if (!response.ok) throw new Error(`${url.pathname}: HTTP ${response.status}`);
      return response.json();
    },
    async image(url: string) {
      const response = await fetch(new URL(url, base));
      if (!response.ok) throw new Error(`image HTTP ${response.status}`);
      return createImageBitmap(await response.blob());
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
      const response = await fetch(dataUri);
      return createImageBitmap(await response.blob());
    },
    imageUrlFor(url: string) {
      const dataUri = embeddedImage(url);
      if (dataUri === undefined) throw new Error(i18n.t("main_embeddedProvider_9985ab4875", { p0: url }));
      return dataUri;
    },
  };
}
// 读取场景清单及资源，并把完整场景发布到预览地图。
async function loadScene(provider: ResourceProvider, requestedRevision: number): Promise<void> {
  const generation = ++loadGeneration;
  const manifestUrl = provider.base;
  const manifest = await provider.json(new URL(manifestUrl)) as SceneManifest;
  if (generation !== loadGeneration || manifest.revision < currentRevision || manifest.revision !== requestedRevision) {
    postEvent('sceneDiscarded', { revision: requestedRevision, outcome: 'stale' }); return;
  }
  if (manifest.schemaVersion !== 1) throw new Error(i18n.t("main_loadScene_2197b880cc", { p0: manifest.schemaVersion }));
  // 早期导出只保存 transit；普通地图的交通显隐使用 metro 分组。
  if (EMBEDDED && !('metro' in manifest.layers) && 'transit' in manifest.layers) manifest.layers.metro = manifest.layers.transit;
  const changed = manifest.sceneId !== currentSceneId;
  const previousManifest = currentManifest;
  const needs = (key: string) => changed || manifest.sourceRevisions[key] !== previousManifest?.sourceRevisions[key];
  trafficRequest?.abort();
  if (changed) {
    ++focusAnimationGeneration; map.stop(); navigation.simulation.stop();
    previewControls.resetSearch(); setFeatureHighlight('search-selection', null);
    clearWorldSelection(false); clearInspectorSelection(false); clearScene(); sourceData = {};
    for (const id of registeredIcons) if (map.hasImage(id)) map.removeImage(id);
    registeredIcons.clear(); currentSceneId = ''; currentManifest = null; sceneProvider = null;
  }
  const entries = Object.entries(manifest.sources).filter(([key]) => key !== 'terrain' && key !== 'traffic' && needs(key));
  const fetched = Object.fromEntries(await Promise.all(entries.map(async ([key, url]) => [key, await provider.json(new URL(url, manifestUrl))])));
  let atlas: ImageBitmap | null = null, atlasIndex: Record<string, any> = {};
  let terrain: ImageBitmap | null = null, terrainUrl: string | null = null;
  let builtSurface: ImageBitmap | null = null;
  try {
    if (needs('pois') || needs('transitViewPois')) { [atlas, atlasIndex] = await Promise.all([provider.image(manifest.poiAtlas.image), provider.json(new URL(manifest.poiAtlas.index, manifestUrl))]); }
    if (needs('terrain') && manifest.sources.terrain) {
      terrainUrl = provider.imageUrlFor(manifest.sources.terrain);
      terrain = await provider.image(manifest.sources.terrain);
    }
    if (needs('builtArea') && manifest.surfaceFields?.builtArea)
      builtSurface = await provider.image(manifest.surfaceFields.builtArea.url);
    if (needs('labelFonts')) await loadSceneFonts(manifest, manifestUrl);
    if (generation !== loadGeneration || manifest.revision < currentRevision) {
      postEvent('sceneDiscarded', { revision: requestedRevision, outcome: 'stale' }); return;
    }
    if (atlas) {
      const canvas = document.createElement('canvas'); const ctx = canvas.getContext('2d', {willReadFrequently: true})!;
      const searchIcons = new Map<string, ImageData>();
      for (const [id, entry] of Object.entries(atlasIndex)) {
        canvas.width = entry.width; canvas.height = entry.height;
        ctx.drawImage(atlas, entry.x, entry.y, entry.width, entry.height, 0, 0, entry.width, entry.height);
        const pixels = ctx.getImageData(0, 0, entry.width, entry.height);
        searchIcons.set(id, pixels);
        if (map.hasImage(id)) map.updateImage(id, pixels); else map.addImage(id, pixels, {pixelRatio: entry.pixelRatio});
        registeredIcons.add(id);
      }
      previewControls.setSearchIcons(searchIcons);
    }
    currentSceneId = manifest.sceneId; currentRevision = manifest.revision;
    currentManifest = manifest; currentTheme = manifest.theme; sceneProvider = provider;
    preview3d.setAvailable(EMBEDDED || !manifest.exportRegion);
    labelLayoutKey = '';
    map.setMaxBounds(manifest.exportRegion ? manifest.world.geoBounds : null);
    updateExportMask();
    if (builtSurface && manifest.surfaceFields?.builtArea) {
      pendingBuiltSurface = {image: builtSurface, spec: manifest.surfaceFields.builtArea};
      if (!changed) {
        if (map.getLayer('built-area')) map.removeLayer('built-area');
        if (map.getLayer('built-area-surface')) map.removeLayer('built-area-surface');
        builtSurfaceLayer = new SurfaceDistanceLayer(
          'built-area-surface', builtSurface, manifest.surfaceFields.builtArea,
          {color: manifest.surfaceFields.builtArea.color ?? token('built_area').fill ?? '#F1F0EB',
            opacity: Number(token('built_area').opacity ?? 1),
            bufferMeters: Number(manifest.surfaceFields.builtArea.bufferMeters ?? (token('built_area').includeParks ? 0 : token('built_area').bufferMeters ?? 60)),
            edgeMeters: Number(manifest.surfaceFields.builtArea.edgeMeters ?? token('built_area').edgeMeters ?? 0)});
        map.addLayer(builtSurfaceLayer, map.getLayer('land-use') ? 'land-use' : undefined);
        if (!customLayers.includes('built-area-surface')) customLayers.push('built-area-surface');
        pendingBuiltSurface = null;
      }
    } else if (!changed && needs('builtArea') && map.getSource('built-area')) {
      if (map.getLayer('built-area-surface')) map.removeLayer('built-area-surface');
      for (const layer of roadLayers) delete selectableObjects[layer.id];
  roadLayers = [];
  builtSurfaceLayer = null; pendingBuiltSurface = null;
      if (!map.getLayer('built-area')) map.addLayer({id: 'built-area', type: 'fill',
        source: 'built-area', paint: {'fill-color': token('built_area').fill ?? '#F1F0EB',
          'fill-opacity': token('built_area').opacity ?? 1}},
        map.getLayer('land-use') ? 'land-use' : undefined);
    }
    if (terrain && terrainUrl && manifest.terrainCorners?.length === 4) {
      // ImageSource 保持场景原点地址；MapLibre 自行读取，改为 data 地址会违反预览的连接安全策略。
      const image = {url: terrainUrl, coordinates: manifest.terrainCorners as [[number, number], [number, number], [number, number], [number, number]]};
      const source = map.getSource('terrain-image') as maplibregl.ImageSource | undefined;
      if (source) source.updateImage(image); else map.addSource('terrain-image', {type: 'image', ...image});
      if (!changed && !map.getLayer('terrain')) {
        map.addLayer({id: 'terrain', type: 'raster', source: 'terrain-image', paint: {'raster-fade-duration': 0}}, customLayers[0]);
        customLayers.unshift('terrain');
      }
    }
    for (const [key, id] of Object.entries(sourceIds)) {
      if (key === 'traffic') continue;
      if (!changed && !needsFromData(key, fetched)) continue;
      const data = fetched[key] ?? {type: 'FeatureCollection', features: []}; sourceData[key] = data;
      if (key === 'roads') {
        navigationRoadProperties.clear();
        for (const feature of data.features) {
          const properties = feature.properties, id = String(properties.roadId);
          if (properties.geometryRole === 'road-surface' || !navigationRoadProperties.has(id))
            navigationRoadProperties.set(id, properties);
        }
      }
      const source = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
      if (source) source.setData(data); else map.addSource(id, {type: 'geojson', data, maxzoom: key === 'labels' ? 14 : 18, buffer: 256, tolerance: 0, promoteId: 'featureId'});
    }
    if (changed) { addDomains(); pendingBuiltSurface = null; }
    else if (needsFromData('roads', fetched)) rebuildRoadLayers();
    if (needsFromData('buildings', fetched)) preview3d.setBuildings(sourceData.buildings?.features ?? []);
    applyRenderStack();
    applyTheme(manifest.theme);
    for (const [layer, visible] of Object.entries(manifest.layers)) {
      if (layer !== 'traffic') await setLayerVisibility(layer, visible);
    }
    await syncTraffic();
    if (generation !== loadGeneration) return;
    applyContextStyling();
    navigation.setScene(manifest.navigation?.schemaVersion === 1
      ? () => provider.json(new URL(manifest.navigation!.url, manifestUrl)) as Promise<NavigationPackage> : null,
      Boolean(manifest.navigationRouteOmitted));
    if (changed && manifest.navigationRoute && !EMBEDDED) void navigation.restoreRoute(manifest.navigationRoute);
    if (changed) { fitBounds(); labelLayoutKey = ''; }
    // 同场景增量更新保留用户当前的相机朝向。
    // idle 发生在工作进程处理、瓦片上传和渲染之后；两次动画帧回调可能早于 GeoJSON 处理完成。
    await new Promise<string>(resolve => {
      const finish = (result: string) => {
        clearTimeout(timer); map.off('idle', idle); map.off('render', stale);
        resolve(result);
      };
      const idle = () => finish('idle');
      const stale = () => { if (generation !== loadGeneration) finish('stale'); };
      const timer = setTimeout(() => finish('timeout'), 60000);
      map.on('idle', idle); map.on('render', stale); map.triggerRepaint();
    });
    if (generation !== loadGeneration) {
      postEvent('sceneDiscarded', {revision: requestedRevision, outcome: 'stale'}); return;
    }
    updateRoadWarning();
    if (changed || ['buildings', 'roads', 'districts', 'zones', 'pois', 'transitViewPois', 'stations'].some(key => needsFromData(key, fetched)))
      previewControls.rebuild(sourceData, !changed);
    updateQuickLayers(true);
    postEvent('sceneLoaded', {bundleId: new URL(manifestUrl).pathname.split('/').at(-2),
      completion: (manifest as any).completion ?? 'complete' });
  } finally {
    atlas?.close(); terrain?.close(); builtSurface?.close();
  }
}
function needsFromData(key: string, data: Record<string, any>): boolean { return Object.prototype.hasOwnProperty.call(data, key); }
async function dispatch(command: PreviewCommand): Promise<void> {
  let outcome = 'applied';
  try {
  if (typeof command.revision === 'number' && command.revision < currentRevision) {
    outcome = 'stale';
    if (command.type === 'loadScene') postEvent(
      'sceneDiscarded', { revision: command.revision, outcome });
    return;
  }
  if (command.type !== 'loadScene' && currentSceneId && command.sceneId &&
      (command.sceneId !== currentSceneId || command.revision !== currentRevision)) { outcome = 'stale'; return; }
  if (['setZoomPreset', 'fitBounds', 'fitGeoBounds',
    'focusGeoBounds', 'beginSelection', 'locateObject'].includes(command.type)) navigation.simulation.stop();
  switch (command.type) {
    case 'loadScene': if (command.manifestUrl && typeof command.revision === 'number') await loadScene(httpProvider(command.manifestUrl), command.revision); break;
    case 'applyTheme': if (command.theme) applyTheme(command.theme as FullTheme); break;
    case 'setUiAppearance': setUiAppearance(command.appearance); break;
    case 'setLanguage': if (command.language) await setLanguage(command.language); break;
    case 'applyRoadTheme': if (command.theme) applyTheme({ ...(currentTheme ?? {}), road: command.theme as RoadTheme } as FullTheme); break;
    case 'setLayerVisibility': if (command.layer) await setLayerVisibility(command.layer, Boolean(command.visible)); break;
    case 'setContextMode': setContextMode(command.mode ?? 'map', command.options ?? {}); break;
    case 'setZoomPreset': setZoomPreset(command.preset ?? 'city'); break;
    case 'setOutputRotation': applyOutputRotation(command.rotation ?? 0); break;
    case 'setRoadVisibility': await setLayerVisibility('road', Boolean(command.visible)); break;
    case 'fitBounds': fitBounds(); break;
    case 'fitGeoBounds': preview3d.reset(); if (command.bounds) map.fitBounds(command.bounds, {padding: 36, duration: 260, bearing: outputBearing(outputRotation)}); break;
    case 'focusGeoBounds': if (command.bounds) await focusGeoBounds(command.bounds); break;
    case 'beginSelection': preview3d.suspendSelection(); selectionMode = true; map.getCanvas().style.cursor = 'crosshair'; document.body.classList.add('selecting'); break;
    case 'clearSelection': clearWorldSelection(); break;
    case 'setSelection': selectedWorldBounds = command.worldBounds ?? null; drawWorldSelection(); break;
    case 'clearObjectSelection': clearInspectorSelection(); break;
    case 'restoreObjectSelection': restoreInspectorSelection(command.selection); break;
    case 'locateObject': locateInspectorObject(); break;
    case 'showHint': showHint(String(command.text ?? '')); break;
    case 'setPreviewUiState':
      if (currentManifest && command.layers) Object.assign(currentManifest.layers, command.layers);
      quickLayersEnabled = Boolean(command.enabled); updateQuickLayers(true, command.pendingLayers);
      previewControls.setGuideAvailable(command.guideAvailable !== false); break;
    case 'showMapInteractionGuide': previewControls.showGuide(true); break;
    case 'dispose': ++loadGeneration; releaseTraffic(); previewControls.dispose(); navigation.dispose(); cancelLabelLayout(); map.remove(); break;
  }
} catch (error) { outcome = 'error'; reportError(error);
  if (command.type === 'loadScene' && typeof command.revision === 'number') {
    postEvent('sceneDiscarded', { revision: command.revision, outcome: 'error',
      message: error instanceof Error ? error.message : String(error) });
  }
}
}

window.cslPreview = { dispatch };
window.chrome?.webview?.addEventListener('message', event => { const command = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
  void dispatch(command as PreviewCommand); });
function clearWorldSelection(report = true): void {
  selectedWorldBounds = null; selectionStart = null; selectionMode = false;
  selection.style.display = 'none'; map.dragPan.enable();
  map.getCanvas().style.cursor = ''; document.body.classList.remove('selecting');
  preview3d.restoreSelection();
  if (report) postEvent('selectionCleared');
}
function projectedRegion(bounds: [number, number, number, number]): maplibregl.Point[] {
  const [x0,z0,x1,z1] = bounds;
  return [[x0,z0],[x0,z1],[x1,z1],[x1,z0]].map(([x,z]) => map.project([-z/111195,x/111195]));
}
function drawWorldSelection(): void {
  if (!selectedWorldBounds || selectionStart) return;
  const points = projectedRegion(selectedWorldBounds);
  const left = Math.min(...points.map(p => p.x)), top = Math.min(...points.map(p => p.y));
  Object.assign(selection.style, {display: 'block', left: `${left}px`, top: `${top}px`,
    width: `${Math.max(...points.map(p => p.x))-left}px`, height: `${Math.max(...points.map(p => p.y))-top}px`});
}
let exportMask: SVGSVGElement | null = null;
function updateExportMask(): void {
  const bounds = currentManifest?.exportRegion;
  if (!bounds) { exportMask?.remove(); exportMask = null; return; }
  if (!exportMask) {
    exportMask = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    exportMask.id = 'export-region-mask';
    exportMask.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:2';
    document.getElementById('map')!.appendChild(exportMask);
  }
  const w = map.getCanvas().clientWidth, h = map.getCanvas().clientHeight;
  const points = projectedRegion(bounds);
  const polygon = points.map(p => `${p.x},${p.y}`).join(' L');
  exportMask.setAttribute('viewBox', `0 0 ${w} ${h}`);
  exportMask.innerHTML = `<path fill="#f3f4f6" fill-rule="evenodd" d="M0,0 H${w} V${h} H0 Z M${polygon} Z"/>`;
}
map.on('move', () => { drawWorldSelection(); updateExportMask(); });
map.on('resize', () => { drawWorldSelection(); updateExportMask(); });
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
window.addEventListener('keydown', event => { if (event.key==='Escape' && (selectionMode || selectionStart)) clearWorldSelection(); });
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
  if (suppressNextClick) { suppressNextClick = false; return; }
  if (selectionMode || selectionStart) return;
  if (navigation.handleClick(event)) return;
  const feature = featureAt(event.point);
  if (feature) inspectFeature(feature); else clearInspectorSelection();
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
  if (event.key === 'Escape' && !selectionStart) clearInspectorSelection();
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
  if (EMBEDDED && window.__CSL_SCENE__) {
    void loadEmbeddedScene(window.__CSL_SCENE__).catch(reportError);
  } else {
    setError('');
    postEvent('ready');
  }
});
