import * as maplibregl from 'maplibre-gl';
import type {ExpressionSpecification, GeoJSONSource, Map as MapLibreMap, MapMouseEvent} from 'maplibre-gl';
import {i18n} from '../i18n';
import {controlIcon, setButtonContent, type ControlIcon} from '../control-icons';
import {RoutingClient} from './client';
import {cumulative, geo, slice} from './geometry';
import {RoutingError} from './types';
import {completedRouteColor, missingRouteTrafficColor, trafficColor} from '../traffic-paint';
import {NavigationSimulation} from './simulation';
import type {Preview3D} from '../preview-3d';
import {routeStrokeFeatures, type RouteStroke} from './route-stroke';
import {arrowOutlineWidth, turnArrowFeatures} from './turn-arrows';
import type {Edge, Endpoint, NavigationPackage, RouteResult, RoutingProfile, XYZ} from './types';
import {searchGroups, type Candidate, type SearchFeature} from '../preview-search';
import {pointAt} from './geometry';
import {compareCandidates} from './candidates';
import type {TransitCandidate, TransitPreference} from './types';
import './ui.css';

const routeTrafficColor: ExpressionSpecification = ['case',
  ['boolean', ['feature-state', 'completed'], false], completedRouteColor,
  ['interpolate', ['linear'], ['get', 'trafficBlend'],
    0, trafficColor(['get', 'congestion'], missingRouteTrafficColor),
    1, trafficColor(['get', 'trafficNeighbor'], missingRouteTrafficColor)]];

const english = {
  transitSort: 'Sort routes', transitBalanced: 'Overall', lessWalking: 'Less walking', fewerTransfers: 'Fewer transfers',
  walkingLess: 'Less walking', transfersLess: 'Fewer transfers', routeShorter: 'Shorter route',
  STALE_QUERY: 'Navigation data changed. Choose points again or retry.',
  morePlaces: 'Show more places', station: 'Station',
  placePlaceholder: 'Click the map or enter a place', pickOrigin: 'Choose a start on the map or search for a place.',
  pickDestination: 'Next, choose a destination on the map or search for a place.',
  noPlaces: 'No places found. Click the map to choose.', mapPointRequired: 'This object has no reference point. Choose a position on the map.',
  roadReference: 'Near the road midpoint', buildingReference: 'Reference point inside building', areaReference: 'Reference point inside area',
  backRoutes: 'Back to routes', shortTransitNotice: 'Walking along roads · No timetable',
  transferWalk: 'Transfer walk', walkTo: 'Walk to', longTransfer: 'Long transfer walk', accessReference: 'Approximate access, outside route distance',
  title: 'Routes', start: 'Choose start', end: 'Choose destination', swap: 'Swap', clear: 'Clear',
  cancel: 'Cancel', close: 'Close',
  transit: 'Public transport', drivingMode: 'Driving', building: 'Building',
  transitNotice: 'Walking follows roads in both directions. Access links are approximate; no timetable or arrival estimate.',
  schematicNotice: 'Some transit sections show stop connections because exported route geometry is unavailable.',
  walk: 'Walk', ride: 'Ride', stops: 'stops', transfers: 'transfers', walkingOnly: 'Walk along roads', unnamedStop: 'Unnamed stop',
  transitDistance: 'Route length (schematic sections included)', TRANSIT_UNAVAILABLE: 'This map has no public transport navigation data.',
  routeLength: 'Route length', schematic: 'schematic',
  preparing: 'Preparing road data…', computing: 'Finding a road route…',
  ready: 'Choose a start and destination on the map.',
  origin: 'Start', destination: 'Destination', driving: 'Driving routes',
  durationZero: '0 min', durationUnderMinute: 'Under 1 min', durationMinutes: 'About {{minutes}} min',
  durationHours: 'About {{hours}} hr {{minutes}} min', speedLimitEstimate: 'Speed-limit estimate',
  trafficEstimate: 'Speed-limit and export-time traffic estimate',
  routeDetails: 'Route details',
  snapshotNotice: 'Export-time traffic · Planning reference', via: 'Via', retry: 'Retry',
  informationNotice: 'Some route data is missing.', clearRoute: 'Clear route',
  timeNotice: 'Time is estimated from speed limits and export-time traffic, with an allowance for waiting.',
  preference: 'Route preference', balanced: 'Recommended', shortest: 'Shortest distance',
  balancedDescription: 'Route selection considers speed limits, road class and export-time traffic. Time is estimated separately; this is not necessarily the fastest route.',
  TRAFFIC_MISSING: 'Some sections have no traffic data.',
  SPEED_LIMIT_MISSING: 'Some sections have no traffic data.',
  PREFAB_SPEED_LIMIT: 'This map uses preset road limits. Re-export with the updated mod to include effective limits.',
  unnamed: 'Unnamed road',
  coordinate: 'X, Z (metres)', useCoordinate: 'Use coordinates',
  quality: 'Native car lanes; turn restrictions and traffic-mod rules are unverified.',
  access: 'Dotted access lines are unverified and excluded from driving distance.',
  fit: 'Show route', details: 'Road data details',
  coordinateSelection: 'Choose using coordinates',
  availableDirections: 'Available road directions', excludedRoads: 'Roads excluded due to missing or invalid data',
  outsideRoute: 'The selected route left the exported area and was not included. Choose a route within this map.',
  captured: 'Snapshot', range: 'Routing is limited to this exported area.',
  depart: 'Start along', continue: 'Continue along', left: 'Turn left onto', right: 'Turn right onto', arrive: 'Arrive',
  NO_PATH: 'No route was found with the available data and rules.',
  SNAP_NOT_FOUND: 'No suitable road was found within 300 metres.',
  DATA_INCOMPLETE: 'This map lacks verified car access or road connections. Export it again with an updated mod.',
  WORKER_FAILED: 'Route calculation failed. Retry to restart it.', QUERY_TIMEOUT: 'Route calculation timed out. Choose closer points or retry.',
  INVALID_ENDPOINT: 'Enter two finite coordinates separated by a comma.',
  RULE_COVERAGE_INCOMPLETE: 'This map cannot verify all turn restrictions.',
};
const chinese: typeof english = {
  transitSort: '方案排序', transitBalanced: '综合', lessWalking: '少步行', fewerTransfers: '少换乘',
  walkingLess: '步行较少', transfersLess: '换乘较少', routeShorter: '路线较短',
  STALE_QUERY: '导航数据已变化，请重新选点或重试。',
  morePlaces: '显示更多地点', station: '站点',
  placePlaceholder: '点击地图或输入地点', pickOrigin: '请在地图上点击起点，或输入地点名称。', pickDestination: '接着选择终点，可点击地图或输入地点。',
  noPlaces: '未找到地点，可点击地图选择。', mapPointRequired: '此对象缺少参考点，请在地图上选择位置。',
  roadReference: '道路中点附近', buildingReference: '建筑内参考点', areaReference: '区域内参考点',
  backRoutes: '返回路线方案', shortTransitNotice: '按道路步行 · 无班次时间',
  transferWalk: '换乘步行', walkTo: '步行至', longTransfer: '长距离换乘步行', accessReference: '接入示意，不计入路线距离',
  title: '路线', start: '选择起点', end: '选择终点', swap: '互换', clear: '清空', cancel: '取消', close: '关闭',
  preparing: '正在准备道路数据…', computing: '正在查找道路路线…',
  transit: '公共交通', drivingMode: '驾车', building: '建筑',
  transitNotice: '步行按道路双向寻路，短接入线为示意；不含班次和预计到达时间。',
  schematicNotice: '部分乘车段缺少导出路径，以站间连线示意。',
  walk: '步行', ride: '乘坐', stops: '站', transfers: '次换乘', walkingOnly: '沿道路步行', unnamedStop: '未命名站点',
  transitDistance: '路线长度（含示意段）', TRANSIT_UNAVAILABLE: '这份地图没有公共交通导航数据。',
  routeLength: '路线长度', schematic: '示意',
  ready: '请在地图上选择起点和终点。', unnamed: '未命名道路',
  origin: '起点', destination: '终点', driving: '驾车路线',
  durationZero: '0 分钟', durationUnderMinute: '不足 1 分钟', durationMinutes: '约 {{minutes}} 分钟',
  durationHours: '约 {{hours}} 小时 {{minutes}} 分钟', speedLimitEstimate: '按限速估算',
  trafficEstimate: '按限速与导出时路况估算',
  routeDetails: '路线详情',
  snapshotNotice: '导出时路况 · 规划参考', via: '途经', retry: '重试',
  informationNotice: '部分路线数据缺失。', clearRoute: '清空路线',
  timeNotice: '用时按限速与导出时路况估算，已计入等候时间。',
  preference: '路线偏好', balanced: '综合推荐', shortest: '最短距离',
  balancedDescription: '选路综合限速、道路等级和导出时路况；用时独立估算，综合推荐不一定是耗时最短的路线。',
  TRAFFIC_MISSING: '部分路段无路况',
  SPEED_LIMIT_MISSING: '部分路段无路况',
  PREFAB_SPEED_LIMIT: '当前地图使用道路预设限速；用更新后的模组重新导出可包含实际生效限速。',
  coordinate: 'X, Z（米）', useCoordinate: '使用坐标', quality: '根据导出的道路计算；转向限制及交通模组规则尚未校验。',
  access: '虚线接入位置待确认，不计入汽车行驶距离。', fit: '查看整条路线', details: '道路数据详情',
  captured: '数据时间', range: '本地图仅支持导出范围内算路。', depart: '从这里沿', continue: '继续沿', left: '左转进入', right: '右转进入', arrive: '到达终点',
  coordinateSelection: '按坐标选择',
  availableDirections: '可用道路方向', excludedRoads: '因信息缺失或异常而排除的道路',
  outsideRoute: '选中路线超出导出范围，未随本地图保存。请在范围内重新选择路线。',
  NO_PATH: '在当前数据和规则下未找到路线。', SNAP_NOT_FOUND: '300 米内没有适合当前模式的道路。',
  DATA_INCOMPLETE: '这份地图缺少可靠的汽车准入或道路连接信息，请使用更新后的模组重新导出。',
  WORKER_FAILED: '路线计算未能完成，可重新选择端点后重试。', QUERY_TIMEOUT: '本次计算超时，请缩短查询范围或重试。',
  INVALID_ENDPOINT: '请输入用逗号分隔的两个有效坐标。', RULE_COVERAGE_INCOMPLETE: '这份地图还不能校验完整转向规则。',
};
i18n.addResourceBundle('en', 'navigation', english);
i18n.addResourceBundle('zh-CN', 'navigation', chinese);
i18n.addResourceBundle('zh-TW', 'navigation', {...chinese, title: '路線', start: '選擇起點', end: '選擇終點', swap: '互換', clear: '清空', cancel: '取消', close: '關閉'});
const text = (key: string, values: Record<string, number> = {}): string => i18n.t(key, {ns: 'navigation', ...values, defaultValue: english[key as keyof typeof english] ?? key});
const actionIcons: Record<string, ControlIcon> = {
  title: 'route', close: 'x', drivingMode: 'car', transit: 'bus', start: 'map-pin', end: 'flag',
  swap: 'arrows-up-down', cancel: 'x', retry: 'refresh', useCoordinate: 'check', fit: 'zoom-scan',
  clearRoute: 'trash',
};
const lineIds = ['navigation-casing', 'navigation-road', 'navigation-access', 'navigation-building', 'navigation-building-outline', 'navigation-transit', 'navigation-walk', 'navigation-schematic', 'navigation-stops', 'navigation-turn-fill', 'navigation-turn-outline', 'navigation-alternative-casing', 'navigation-alternative-road'];
const profiles: RoutingProfile[] = ['car-balanced', 'car-shortest'];
const LONG_TRANSFER_METERS = 800;
const profileLabel = (profile: RoutingProfile) => text(profile === 'transit' ? 'transit' : profile === 'car-balanced' ? 'balanced' : 'shortest');
const distanceLabel = (meters: number) => meters >= 1000 ? `${(meters/1000).toFixed(2)} km` : `${Math.round(meters)} m`;
const durationLabel = (seconds: number): string => {
  if (seconds === 0) return text('durationZero');
  if (seconds < 60) return text('durationUnderMinute');
  const minutes = Math.ceil(seconds/60);
  return minutes < 60 ? text('durationMinutes', {minutes})
    : text('durationHours', {hours: Math.floor(minutes/60), minutes: minutes%60});
};

export type NavigationRoadStyle = {
  properties: (edge: Edge) => Record<string, unknown>;
  width: (casing: boolean) => ExpressionSpecification;
  widthMeters: (edge: Edge) => number;
};

export class NavigationControl {
  readonly simulation: NavigationSimulation;
  private routeStrokes: GeoJSON.Feature[] = [];
  private progressMeters: number | null = null;
  private completedStrokes = 0;
  private routeArrows: GeoJSON.Feature[] = [];
  private completedArrows = 0;
  private readonly root = document.createElement('section');
  private readonly panel = document.createElement('div');
  private panelOpened: () => void = () => {};
  private readonly message = document.createElement('div');
  private readonly summary = document.createElement('div');
  private readonly steps = document.createElement('ol');
  private readonly coordinates = document.createElement('input');
  private readonly modes = document.createElement('div');
  private profile: RoutingProfile = 'car-balanced';
  private readonly routes = new Map<RoutingProfile, RouteResult>();
  private transitRoutes: TransitCandidate[] = [];
  private selectedCandidateId: string | null = null;
  private transitSort: TransitPreference = 'balanced';
  private readonly routeErrors = new Map<RoutingProfile, string>();
  private busy = false;
  private readonly routeDetails = document.createElement('details');
  private readonly coordinateDetails = document.createElement('details');
  private readonly resultActions = document.createElement('div');
  private readonly cancelButton: HTMLButtonElement;
  private readonly retryButton: HTMLButtonElement;
  private readonly startButton: HTMLElement;
  private readonly endButton: HTMLElement;
  private readonly placeInputs = {origin: document.createElement('input'), destination: document.createElement('input')};
  private readonly placeResults = document.createElement('div');
  private readonly morePlaces: HTMLButtonElement;
  private placeLimit = 8;
  private searchCandidates: Candidate[] = [];
  private placeMatches: Candidate[] = [];
  private focusedPlace = -1;
  private composing = false;
  private locatePlace: (feature: SearchFeature) => void = () => {};
  private readonly transitDetails = document.createElement('section');
  private readonly transitSteps = document.createElement('ol');
  private readonly transitSummary = document.createElement('p');
  private readonly backButton: HTMLButtonElement;
  private detailsOpen = false;
  private listScroll = 0;
  private selectedLeg: number | null = null;
  private origin: Endpoint | null = null;
  private destination: Endpoint | null = null;
  private get result(): RouteResult | null {
    return this.profile === 'transit'
      ? this.transitRoutes.find(route => route.candidateId === this.selectedCandidateId) ?? null
      : this.routes.get(this.profile) ?? null;
  }
  private client: RoutingClient | null = null;
  private loading: Promise<RoutingClient> | null = null;
  private loader: (() => Promise<NavigationPackage>) | null = null;
  private epoch = 0;
  private operation = 0;
  private picking: 'origin' | 'destination' | null = null;
  private marks: maplibregl.Marker[] = [];
  private popup: maplibregl.Popup | null = null;
  private longPress = 0;
  private previewTimer = 0;
  private readonly languageChanged = () => this.translate();
  private readonly keydown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') { if (this.simulation.active) this.simulation.stop(); else this.cancel(); }
  };

  constructor(private readonly map: MapLibreMap,
    private readonly selectionActive: () => boolean,
    private readonly planningStarted: () => void,
    private readonly placeAt: (point: {x: number; y: number}) => Endpoint['place'],
    private readonly roadStyle: NavigationRoadStyle, private readonly preview3d: Preview3D) {
    this.root.className = 'navigation-control'; this.root.hidden = true;
    const toggle = this.button('title', () => {
      if (this.simulation.active) { this.simulation.stop(); return; }
      this.panel.hidden = !this.panel.hidden; this.popup?.remove();
      if (!this.panel.hidden) this.panelOpened();
      if (this.panel.hidden) this.cancel(); else { this.planningStarted(); this.activateMissingEndpoint(); }
    });
    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', () => toggle.setAttribute('aria-expanded', String(!this.panel.hidden)));
    this.panel.className = 'navigation-panel'; this.panel.hidden = true;
    const heading = document.createElement('div'); heading.className = 'navigation-heading';
    const title = document.createElement('h2'); title.dataset.navigationKey = 'title';
    const close = this.button('close', () => { this.cancel(); this.panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); toggle.focus(); });
    close.className = 'navigation-close';
    heading.append(title, close);
    this.modes.className = 'navigation-modes';
    for (const mode of ['drivingMode', 'transit'] as const) {
      const button = this.button(mode, () => void this.setMode(mode === 'transit'));
      button.dataset.mode = mode; this.modes.append(button);
    }
    const endpointButtons = document.createElement('div'); endpointButtons.className = 'navigation-endpoints';
    this.startButton = this.endpointInput('origin');
    this.endButton = this.endpointInput('destination');
    const swap = this.button('swap', () => void this.rematchEndpoints(this.profile, [this.destination, this.origin]));
    swap.dataset.navigationLabel = 'swap'; swap.classList.add('navigation-swap');
    endpointButtons.append(this.startButton, this.endButton, swap);
    this.cancelButton = this.button('cancel', () => this.cancel()); this.cancelButton.hidden = true;
    this.retryButton = this.button('retry', () => { this.cancel(); void this.query(); }); this.retryButton.hidden = true;
    this.coordinates.type = 'text'; this.coordinates.setAttribute('aria-label', text('coordinate'));
    const form = document.createElement('form'); form.className = 'navigation-actions';
    const submit = this.button('useCoordinate', () => {}); submit.type = 'submit';
    form.append(this.coordinates, submit);
    form.addEventListener('submit', event => {
      event.preventDefault();
      const parts = this.coordinates.value.trim().split(/[,，\s]+/), values = parts.map(Number);
      if (parts.length !== 2 || parts.some(part => !part) || !values.every(Number.isFinite)) { this.message.textContent = text('INVALID_ENDPOINT'); return; }
      if (!this.picking) this.picking = this.origin ? 'destination' : 'origin';
      void this.choose([values[0], 0, values[1]]);
    });
    const coordinateTitle = document.createElement('summary');
    coordinateTitle.dataset.navigationKey = 'coordinateSelection'; this.coordinateDetails.append(coordinateTitle, form);
    this.coordinateDetails.hidden = true;
    this.message.setAttribute('role', 'status'); this.message.setAttribute('aria-live', 'polite');
    this.message.className = 'navigation-status';
    this.placeResults.id = 'navigation-place-results'; this.placeResults.className = 'navigation-place-results';
    this.placeResults.setAttribute('role', 'listbox'); this.placeResults.hidden = true;
    this.morePlaces = this.button('morePlaces', () => {
      if (!this.picking) return;
      const key = this.picking, previous = this.placeLimit;
      this.searchPlaces(key, previous+8); this.focusedPlace = previous; this.markPlaceFocus(key);
      this.placeInputs[key].focus();
    });
    this.morePlaces.className = 'navigation-more-places'; this.morePlaces.hidden = true;
    this.summary.className = 'navigation-routes'; this.summary.setAttribute('aria-label', text('driving'));
    const stepsTitle = document.createElement('summary'); stepsTitle.dataset.navigationKey = 'routeDetails';
    this.routeDetails.className = 'navigation-steps'; this.routeDetails.append(stepsTitle, this.steps); this.routeDetails.hidden = true;
    this.transitDetails.className = 'navigation-transit-details'; this.transitDetails.hidden = true;
    this.transitDetails.tabIndex = -1;
    this.backButton = this.button('backRoutes', () => this.showTransitDetails(false));
    this.transitSteps.className = 'navigation-itinerary';
    this.transitDetails.append(this.backButton, this.transitSummary, this.transitSteps);
    this.resultActions.className = 'navigation-actions'; this.resultActions.hidden = true;
    this.simulation = new NavigationSimulation(map, this.root, meters => this.drawProgress(meters), preview3d);
    this.resultActions.append(this.button('fit', () => this.fitRoute()), this.simulation.startButton);
    const statusActions = document.createElement('div'); statusActions.className = 'navigation-actions';
    statusActions.append(this.cancelButton, this.retryButton);
    const clear = this.button('clearRoute', () => this.clearRoute()); clear.className = 'navigation-clear';
    this.panel.append(heading, this.modes, endpointButtons, this.placeResults, this.morePlaces, this.coordinateDetails, this.message, statusActions,
      this.summary, this.transitDetails, this.resultActions, this.routeDetails, clear);
    for (const name of ['click', 'dblclick', 'pointerdown', 'touchstart'] as const)
      this.panel.addEventListener(name, event => event.stopPropagation());
    this.root.append(toggle, this.panel); document.body.append(this.root);
    this.root.prepend(toggle);
    this.translate(); i18n.on('languageChanged', this.languageChanged);
    window.addEventListener('keydown', this.keydown);
    map.on('contextmenu', event => { if (!this.selectionActive() && this.loader) { event.preventDefault(); this.menu(event.lngLat); } });
    map.on('touchstart', event => {
      if (event.points.length !== 1 || this.selectionActive() || !this.loader) return;
      this.longPress = window.setTimeout(() => this.menu(event.lngLat), 650);
    });
    for (const name of ['touchmove', 'touchend', 'dragstart'] as const)
      map.on(name, () => window.clearTimeout(this.longPress));
  }

  private button(key: string, action: () => void): HTMLButtonElement {
    const button = document.createElement('button'); button.type = 'button';
    button.dataset.navigationKey = key;
    if (actionIcons[key]) setButtonContent(button, text(key), actionIcons[key]); else button.textContent = text(key);
    button.addEventListener('click', action); return button;
  }

  private endpointInput(key: 'origin' | 'destination'): HTMLElement {
    const row = document.createElement('div'); row.className = 'navigation-endpoint';
    const label = document.createElement('label'); label.className = 'navigation-endpoint-label';
    label.dataset.navigationKey = key; label.htmlFor = `navigation-${key}`;
    const input = this.placeInputs[key]; input.id = label.htmlFor; input.type = 'text'; input.autocomplete = 'off'; input.className = 'navigation-endpoint-name';
    input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', 'navigation-place-results'); input.setAttribute('aria-expanded', 'false');
    input.addEventListener('focus', () => { if (this.picking !== key) this.begin(key); input.select(); });
    input.addEventListener('pointerdown', () => { if (this.picking !== key) this.begin(key); });
    row.addEventListener('click', event => {
      if (event.target !== input) input.focus();
    });
    input.addEventListener('compositionstart', () => { this.composing = true; });
    input.addEventListener('compositionend', () => { this.composing = false; this.searchPlaces(key); });
    input.addEventListener('input', () => {
      this.operation++; this.client?.cancel(); this.busy = false; this.picking = key;
      this.selectedLeg = null; this.detailsOpen = false; this.updateDetailView();
      this.cancelButton.hidden = false; this.retryButton.hidden = true; this.coordinateDetails.hidden = false;
      this.startButton.setAttribute('aria-pressed', String(key === 'origin')); this.endButton.setAttribute('aria-pressed', String(key === 'destination'));
      this.map.getCanvas().style.cursor = 'crosshair';
      this.renderCards();
      this.searchPlaces(key);
    });
    input.addEventListener('keydown', event => {
      if (event.isComposing || this.composing || event.keyCode === 229) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); this.cancel(); return; }
      if (event.key === 'Enter') {
        event.preventDefault();
        if (this.focusedPlace >= 0) this.submitPlace(this.placeMatches[this.focusedPlace]);
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (this.placeResults.hidden) this.searchPlaces(key);
        if (!this.placeMatches.length) return;
        this.focusedPlace = this.focusedPlace < 0 ? event.key === 'ArrowDown' ? 0 : this.placeMatches.length-1
          : (this.focusedPlace+(event.key === 'ArrowDown' ? 1 : -1)+this.placeMatches.length)%this.placeMatches.length;
        this.markPlaceFocus(key);
      }
    });
    const access = document.createElement('span'); access.className = 'navigation-endpoint-access';
    row.append(label, input, access); return row;
  }

  setSearchCandidates(candidates: Candidate[], locate: (feature: SearchFeature) => void): void {
    this.searchCandidates = candidates; this.locatePlace = locate;
    if (this.picking && document.activeElement === this.placeInputs[this.picking]) this.searchPlaces(this.picking);
  }

  private closePlaces(): void {
    this.placeMatches = []; this.focusedPlace = -1; this.placeResults.replaceChildren(); this.placeResults.hidden = true;
    this.morePlaces.hidden = true;
    for (const input of Object.values(this.placeInputs)) {
      input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant');
    }
  }

  private searchPlaces(key: 'origin' | 'destination', limit = 8): void {
    const query = this.placeInputs[key].value;
    this.closePlaces();
    if (!query.trim()) { this.message.textContent = text(key === 'origin' ? 'pickOrigin' : 'pickDestination'); return; }
    const matches = searchGroups(this.searchCandidates, query).flatMap(group => group.items);
    this.placeLimit = limit; this.placeMatches = matches.slice(0, limit); this.morePlaces.hidden = matches.length <= limit;
    this.placeResults.hidden = !this.placeMatches.length;
    this.placeInputs[key].setAttribute('aria-expanded', String(!this.placeResults.hidden));
    this.message.textContent = text(this.placeMatches.length ? key === 'origin' ? 'pickOrigin' : 'pickDestination' : 'noPlaces');
    this.placeMatches.forEach((candidate, index) => {
      const option = document.createElement('div'); option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false');
      option.id = `navigation-place-${index}`;
      option.setAttribute('aria-posinset', String(index+1)); option.setAttribute('aria-setsize', String(matches.length));
      const props = candidate.feature.properties;
      const kind = props.geometryRole === 'station' || props.sourceKind === 'stop' || props.stationId
        ? text('station') : i18n.t(`search_group_${candidate.kind}`);
      option.textContent = `${candidate.name} · ${candidate.categoryName || kind}${candidate.context ? ` · ${candidate.context}` : ''}`;
      option.addEventListener('pointerdown', event => event.preventDefault());
      option.addEventListener('click', () => this.submitPlace(candidate)); this.placeResults.append(option);
    });
  }

  private markPlaceFocus(key: 'origin' | 'destination'): void {
    [...this.placeResults.children].forEach((option, index) => {
      option.setAttribute('aria-selected', String(index === this.focusedPlace));
      if (index === this.focusedPlace) {
        this.placeInputs[key].setAttribute('aria-activedescendant', option.id);
        option.scrollIntoView({block: 'nearest'});
      }
    });
  }

  private submitPlace(candidate: Candidate): void {
    const feature = candidate.feature, geometry = feature.geometry;
    let anchor = feature.properties.navigationAnchor as number[] | undefined;
    let context = candidate.kind === 'road' ? 'roadReference' : candidate.kind === 'building' ? 'buildingReference' : 'areaReference';
    if (geometry.type === 'Point') { anchor = geometry.coordinates; context = ''; }
    else if (!anchor && geometry.type === 'LineString') {
      const points = geometry.coordinates.map(p => [p[1]*111195, 0, -p[0]*111195] as XYZ);
      anchor = geo(pointAt(points, cumulative(points), .5)); context = 'roadReference';
    }
    this.operation++; this.client?.cancel(); this.closePlaces();
    if (!anchor) { this.locatePlace(feature); this.message.textContent = text('mapPointRequired'); return; }
    void this.choose([anchor[1]*111195, 0, -anchor[0]*111195], {name: candidate.name, geometry,
      key: candidate.key, kind: candidate.kind, context: context || undefined});
  }

  private activateMissingEndpoint(): void {
    if (!this.origin) this.begin('origin'); else if (!this.destination) this.begin('destination');
  }

  mount(container: HTMLElement, opened: () => void): void {
    container.append(this.root); this.panelOpened = opened;
  }

  hidePanel(): void {
    this.cancel(); this.popup?.remove(); this.panel.hidden = true;
    this.root.querySelector('button')?.setAttribute('aria-expanded', 'false');
  }

  get selectingPoint(): boolean { return this.picking !== null; }

  private translate(): void {
    for (const element of this.root.querySelectorAll<HTMLElement>('[data-navigation-key]')) {
      const key = element.dataset.navigationKey!;
      if (element instanceof HTMLButtonElement && actionIcons[key]) setButtonContent(element, text(key), actionIcons[key]);
      else element.textContent = text(key);
    }
    this.root.setAttribute('aria-label', text('title'));
    for (const element of this.root.querySelectorAll<HTMLElement>('[data-navigation-label]')) element.setAttribute('aria-label', text(element.dataset.navigationLabel!));
    this.coordinates.placeholder = text('coordinate'); this.coordinates.setAttribute('aria-label', text('coordinate'));
    this.renderEndpoints(); this.renderCards(); this.displayResult();
    if (!this.result) this.message.textContent = text(this.busy ? 'computing' : this.picking === 'origin' ? 'pickOrigin' : this.picking ? 'pickDestination' : 'ready');
    if (this.picking && document.activeElement === this.placeInputs[this.picking] && !this.composing) this.searchPlaces(this.picking);
  }

  setScene(loader: (() => Promise<NavigationPackage>) | null, routeOmitted = false): void {
    this.loader = loader; this.root.hidden = loader === null;
    if (routeOmitted) { this.panel.hidden = false; this.root.querySelector('button')?.setAttribute('aria-expanded', 'true'); this.message.textContent = text('outsideRoute'); this.planningStarted(); }
  }

  async restoreRoute(value: Record<string, unknown>): Promise<void> {
    const operation = ++this.operation;
    try {
      const client = await this.ensureClient();
      if (operation !== this.operation || value.snapshotId !== client.data.snapshotId
          || value.graphRevision !== client.data.graphRevision || value.costRevision !== client.data.costRevision) return;
      const origin = value.origin as Endpoint, destination = value.destination as Endpoint;
      if (!origin?.snap || !destination?.snap || !Array.isArray(origin.position) || !Array.isArray(destination.position)) return;
      this.profile = value.costProfile === 'transit' && client.data.transit ? 'transit'
        : value.costProfile === 'car-balanced' && client.data.costModel?.id === 'car-balanced-v1' ? 'car-balanced' : 'car-shortest';
      this.origin = origin; this.destination = destination;
      this.panel.hidden = false; this.root.querySelector('button')?.setAttribute('aria-expanded', 'true'); await this.query();
    } catch (error) { this.fail(error, operation); }
  }

  clear(): void {
    this.panel.hidden = true; this.clearRoute(); this.client?.stop(); this.client = null; this.loading = null; this.loader = null; this.epoch++;
    this.searchCandidates = [];
    this.root.hidden = true; this.panel.hidden = true;
    this.root.querySelector('button')?.setAttribute('aria-expanded', 'false');
    this.profile = 'car-balanced';
    this.displayResult();
    for (const details of [this.coordinateDetails, this.routeDetails]) details.open = false;
    for (const id of lineIds) if (this.map.getLayer(id)) this.map.removeLayer(id);
    if (this.map.getSource('navigation')) this.map.removeSource('navigation');
  }

  dispose(): void {
    this.clear(); this.simulation.dispose(); this.root.remove(); i18n.off('languageChanged', this.languageChanged);
    window.removeEventListener('keydown', this.keydown); window.clearTimeout(this.longPress);
    window.clearTimeout(this.previewTimer);
  }

  private clearRoute(): void {
    this.cancel(); this.origin = this.destination = null; this.resetResults(); this.draw();
    if (!this.panel.hidden) this.activateMissingEndpoint();
  }

  private cancel(): void {
    this.operation++; this.client?.cancel(); this.picking = null; this.busy = false;
    this.coordinateDetails.hidden = true;
    this.cancelButton.hidden = true; this.retryButton.hidden = !this.origin || !this.destination || this.result !== null;
    this.startButton.setAttribute('aria-pressed', 'false'); this.endButton.setAttribute('aria-pressed', 'false');
    this.closePlaces(); this.popup?.remove();
    window.clearTimeout(this.previewTimer);
    for (const marker of this.marks) {
      const value = marker.getElement().classList.contains('origin') ? this.origin : this.destination;
      if (value) marker.setLngLat(geo(value.position));
    }
    this.map.getCanvas().style.cursor = ''; this.renderEndpoints(); this.renderCards(); this.displayResult();
    if (!this.result) this.message.textContent = text('ready');
  }

  private begin(endpoint: 'origin' | 'destination'): void {
    this.simulation.stop();
    this.panelOpened();
    this.cancel(); this.panel.hidden = false; this.picking = endpoint;
    this.selectedLeg = null; this.detailsOpen = false; this.updateDetailView();
    this.planningStarted();
    this.root.querySelector('button')?.setAttribute('aria-expanded', 'true');
    this.startButton.setAttribute('aria-pressed', String(endpoint === 'origin'));
    this.endButton.setAttribute('aria-pressed', String(endpoint === 'destination'));
    this.message.textContent = text(endpoint === 'origin' ? 'pickOrigin' : 'pickDestination'); this.map.getCanvas().style.cursor = 'crosshair';
    this.cancelButton.hidden = false; this.retryButton.hidden = true;
    this.coordinateDetails.hidden = false;
  }

  handleClick(event: MapMouseEvent): boolean {
    if (!this.picking || this.selectionActive()) return false;
    void this.choose([event.lngLat.lat*111195, 0, -event.lngLat.lng*111195], this.placeAt(event.point)); return true;
  }

  private menu(location: maplibregl.LngLat): void {
    this.popup?.remove();
    const content = document.createElement('div'); content.className = 'navigation-actions';
    for (const endpoint of ['origin', 'destination'] as const) content.append(this.button(endpoint === 'origin' ? 'start' : 'end', () => {
      this.begin(endpoint); void this.choose([location.lat*111195, 0, -location.lng*111195], this.placeAt(this.map.project(location)));
    }));
    content.append(this.button('close', () => this.popup?.remove()));
    this.popup = new maplibregl.Popup({closeButton: false}).setLngLat(location).setDOMContent(content).addTo(this.map);
  }

  private async ensureClient(): Promise<RoutingClient> {
    if (this.client) return this.client;
    if (!this.loader) throw new RoutingError('DATA_INCOMPLETE');
    const epoch = this.epoch, loader = this.loader;
    if (!this.loading) this.loading = loader().then(data => {
      if (epoch !== this.epoch) throw new RoutingError('CANCELLED');
      if (this.profile === 'car-balanced' && data.costModel?.id !== 'car-balanced-v1') this.profile = 'car-shortest';
      this.client = new RoutingClient(data, epoch); return this.client;
    }).finally(() => { if (epoch === this.epoch) this.loading = null; });
    return this.loading;
  }

  private activeProfiles(): RoutingProfile[] {
    return this.profile === 'transit' ? ['transit'] : profiles;
  }

  private async setMode(transit: boolean): Promise<void> {
    if (transit === (this.profile === 'transit')) return;
    await this.rematchEndpoints(transit ? 'transit' : 'car-balanced', [this.origin, this.destination]);
  }

  private async rematchEndpoints(profile: RoutingProfile, endpoints: (Endpoint | null)[]): Promise<void> {
    this.cancel(); const operation = ++this.operation;
    this.resetResults(); this.draw();
    try {
      const client = await this.ensureClient();
      if (operation !== this.operation) return;
      if (profile === 'car-balanced' && client.data.costModel?.id !== 'car-balanced-v1') profile = 'car-shortest';
      const snapped: (Endpoint | null)[] = [];
      for (const endpoint of endpoints) {
        snapped.push(endpoint ? {...endpoint, snap: (await client.snap(endpoint.position, profile))[0]} : null);
        if (operation !== this.operation) return;
      }
      this.profile = profile;
      [this.origin, this.destination] = snapped;
      this.renderEndpoints(); this.draw();
      if (this.origin && this.destination) await this.query(); else this.activateMissingEndpoint();
    } catch (error) {
      if (operation !== this.operation) return;
      this.renderEndpoints(); this.draw(); this.fail(error, operation);
    }
  }

  private fail(error: unknown, operation: number): void {
    if (operation !== this.operation || (error instanceof RoutingError && error.code === 'CANCELLED')) return;
    this.busy = false; this.cancelButton.hidden = !this.picking;
    this.retryButton.hidden = !this.origin || !this.destination || this.result !== null;
    this.renderCards();
    this.message.textContent = text(error instanceof RoutingError ? error.code : 'WORKER_FAILED');
  }

  private async choose(position: XYZ, place?: Endpoint['place']): Promise<void> {
    const endpoint = this.picking ?? (this.origin ? 'destination' : 'origin');
    this.client?.cancel(); const operation = ++this.operation;
    this.closePlaces(); this.message.textContent = text('preparing'); this.cancelButton.hidden = false;
    try {
      const client = await this.ensureClient();
      if (operation !== this.operation) return;
      const snaps = await client.snap(position, this.profile);
      if (operation !== this.operation) return;
      this[endpoint] = {position, snap: snaps[0], place};
      this.placeInputs[endpoint].blur();
      this.cancel(); this.resetResults(); this.coordinateDetails.open = false; this.draw();
      if (!this.origin || !this.destination) this.activateMissingEndpoint(); else void this.query();
    } catch (error) { this.fail(error, operation); }
  }

  private async query(): Promise<void> {
    if (!this.origin || !this.destination) return;
    this.planningStarted();
    const origin = this.origin.snap, destination = this.destination.snap;
    const operation = ++this.operation;
    this.resetResults(); this.busy = true; this.cancelButton.hidden = false;
    this.draw(); this.renderCards(); this.message.textContent = text('computing');
    try {
      const client = await this.ensureClient();
      if (operation !== this.operation) return;
      const results = new Map<RoutingProfile, RouteResult>(), errors = new Map<RoutingProfile, string>();
      let transit: TransitCandidate[] = [];
      // 候选整组作为一次 Worker 请求；驾车两个 profile 继续顺序查询。
      for (const profile of this.activeProfiles()) {
        if (profile === 'car-balanced' && client.data.costModel?.id !== 'car-balanced-v1') continue;
        try {
          if (profile === 'transit') transit = await client.candidates(origin, destination);
          else results.set(profile, await client.route(origin, destination, profile));
        }
        catch (error) {
          if (operation !== this.operation) return;
          if (!(error instanceof RoutingError) || error.code !== 'NO_PATH') throw error;
          errors.set(profile, error.code);
        }
        if (operation !== this.operation) return;
      }
      this.routes.clear(); this.routeErrors.clear();
      for (const [profile, result] of results) this.routes.set(profile, result);
      for (const [profile, error] of errors) this.routeErrors.set(profile, error);
      this.busy = false; this.cancelButton.hidden = true;
      this.transitRoutes = transit; this.selectedCandidateId = transit[0]?.candidateId ?? null;
      this.retryButton.hidden = this.result !== null;
      this.renderCards(); this.draw(); this.displayResult();
      if (!this.result) this.message.textContent = text(this.routeErrors.get(this.profile) ?? 'NO_PATH');
      if (this.profile !== 'transit' && this.routes.size) this.fitGeometry([...this.routes.values()].flatMap(route => route.geometry));
    } catch (error) { this.fail(error, operation); }
  }

  private resetResults(): void {
    this.detailsOpen = false; this.selectedLeg = null;
    this.transitRoutes = []; this.selectedCandidateId = null; this.transitSort = 'balanced';
    this.routes.clear(); this.routeErrors.clear(); this.busy = false;
    this.retryButton.hidden = true; this.routeDetails.open = false;
    this.renderEndpoints(); this.renderCards(); this.displayResult();
  }

  private renderEndpoints(): void {
    for (const [key, button] of [['origin', this.startButton], ['destination', this.endButton]] as const) {
      const endpoint = this[key];
      const name = endpoint ? endpoint.place?.name || endpoint.snap.name || text('unnamed') : '';
      const input = this.placeInputs[key]; input.placeholder = text('placePlaceholder');
      if (this.picking !== key || document.activeElement !== input) input.value = name;
      button.querySelector('.navigation-endpoint-access')!.textContent = endpoint
        ? `${endpoint.place?.context ? `${text(endpoint.place.context)} · ` : ''}${endpoint.snap.name || text('unnamed')} · ${distanceLabel(endpoint.snap.distanceM)} · ${text('accessReference')}` : '';
      button.setAttribute('aria-pressed', String(this.picking === key));
    }
  }

  private renderCards(): void {
    this.summary.setAttribute('aria-label', text(this.profile === 'transit' ? 'transit' : 'driving'));
    for (const button of this.modes.querySelectorAll<HTMLButtonElement>('button'))
      button.setAttribute('aria-pressed', String((button.dataset.mode === 'transit') === (this.profile === 'transit')));
    this.summary.replaceChildren();
    if (this.profile === 'transit' && !this.busy && !this.transitRoutes.length) return;
    if (!this.busy && !this.routes.size && !this.transitRoutes.length && !this.routeErrors.size) return;
    if (this.profile !== 'transit') {
      const heading = document.createElement('p'); heading.className = 'navigation-section-title'; heading.textContent = text('driving');
      this.summary.append(heading);
    }
    if (this.profile === 'transit' && this.transitRoutes.length > 1) {
      const sorting = document.createElement('div'); sorting.className = 'navigation-route-sorting';
      sorting.setAttribute('role', 'group'); sorting.setAttribute('aria-label', text('transitSort'));
      for (const [preference, key] of [['balanced', 'transitBalanced'], ['less-walking', 'lessWalking'], ['fewer-transfers', 'fewerTransfers']] as const) {
        const button = this.button(key, () => {
          const scroll = this.panel.scrollTop; this.transitSort = preference; this.renderCards(); this.panel.scrollTop = scroll;
          this.summary.querySelector<HTMLButtonElement>(`[data-sort="${preference}"]`)?.focus({preventScroll: true});
        });
        button.dataset.sort = preference; button.setAttribute('aria-pressed', String(this.transitSort === preference)); sorting.append(button);
      }
      this.summary.append(sorting);
    }
    const entries: Array<{profile: RoutingProfile; result?: RouteResult; candidate?: TransitCandidate}> = this.profile === 'transit'
      ? this.transitRoutes.length
        ? [...this.transitRoutes].sort((a, b) => compareCandidates(a, b, this.transitSort)).map(candidate => ({profile: 'transit', result: candidate, candidate}))
        : [{profile: 'transit'}]
      : this.activeProfiles().map(profile => ({profile, result: this.routes.get(profile)}));
    for (const {profile, result, candidate} of entries) {
      if (profile === 'car-balanced' && this.client && this.client.data.costModel?.id !== 'car-balanced-v1') continue;
      const card = document.createElement('button'); card.type = 'button'; card.className = 'navigation-route-card';
      card.classList.toggle('transit', profile === 'transit');
      card.dataset.profile = profile;
      if (candidate) card.dataset.candidateId = candidate.candidateId;
      card.setAttribute('aria-pressed', String(candidate ? candidate.candidateId === this.selectedCandidateId : profile === this.profile));
      card.disabled = !result || this.busy;
      const label = document.createElement('span'); label.className = 'navigation-route-label'; label.textContent = profileLabel(profile);
      label.prepend(controlIcon(profile === 'transit' ? 'bus' : 'car'));
      const duration = document.createElement('span'); duration.className = 'navigation-duration';
      duration.textContent = this.busy ? text('computing') : result && profile !== 'transit'
        ? durationLabel(result.durationSeconds!) : text(this.routeErrors.get(profile) ?? 'NO_PATH');
      if (result && profile === 'transit') duration.textContent = result.boardings
        ? `${Math.max(0, result.boardings-1)} ${text('transfers')} · ${text('walk')} ${distanceLabel(result.walkingDistanceM!)}` : text('walkingOnly');
      duration.classList.toggle('has-estimate', result?.durationSeconds != null);
      if (result && profile !== 'transit') duration.title = text(result.durationModel === 'car-speed-traffic-v2' ? 'timeNotice' : 'speedLimitEstimate');
      card.append(label, duration);
      if (result) {
        const distance = document.createElement('span'); distance.className = 'navigation-distance';
        distance.textContent = `${distanceLabel(result.distanceM)}${result.durationSeconds !== null ? ` · ${text(result.durationModel === 'car-speed-traffic-v2' ? 'trafficEstimate' : 'speedLimitEstimate')}` : ''}`;
        if (profile === 'transit') distance.textContent = `${text(result.warningCodes.includes('TRANSIT_SCHEMATIC') ? 'transitDistance' : 'routeLength')} ${distanceLabel(result.distanceM)}`;
        const roads = profile === 'transit' ? result.legs?.filter(leg => leg.mode === 'ride').map(leg => leg.name || text('transit')) ?? [] : result.roadNames;
        const via = document.createElement('span'); via.className = 'navigation-via';
        via.textContent = roads.length ? `${text('via')} ${roads.slice(0, 2).join(' · ')}${roads.length > 2 ? '…' : ''}` : text(profile === 'transit' ? 'walkingOnly' : 'unnamed');
        card.append(distance, via);
        if (profile === 'transit') {
          label.hidden = true; via.replaceChildren();
          for (const [index, leg] of (result.legs ?? []).entries()) {
            if (index) via.append(' → ');
            const badge = document.createElement('span'); badge.className = 'navigation-line-badge';
            badge.textContent = leg.mode === 'ride' ? `${leg.transitKind} · ${leg.name || text('transit')}` : text('walk');
            if (leg.color) badge.style.borderColor = leg.color;
            via.append(badge);
          }
          const long = result.legs?.find(leg => leg.walkKind === 'transfer' && leg.distanceM > LONG_TRANSFER_METERS);
          if (long) {
            const notice = document.createElement('span'); notice.className = 'navigation-transfer-notice';
            notice.textContent = `${text('longTransfer')} ${distanceLabel(long.distanceM)}`; card.append(notice);
          }
          if (candidate && this.transitRoutes.length > 1) {
            const advantages = document.createElement('span'); advantages.className = 'navigation-route-advantages';
            for (const [key, metric] of [
              ['walkingLess', (route: TransitCandidate) => route.walkingDistanceM!],
              ['transfersLess', (route: TransitCandidate) => Math.max(0, route.boardings!-1)],
              ['routeShorter', (route: TransitCandidate) => route.distanceM],
            ] as const) if (this.transitRoutes.every(other => other === candidate || metric(candidate) < metric(other))) {
              const badge = document.createElement('span'); badge.textContent = text(key); advantages.append(badge);
            }
            card.append(advantages);
          }
        }
      }
      card.addEventListener('click', () => {
        if (candidate) {
          this.selectTransitCandidate(candidate.candidateId);
          this.summary.querySelector<HTMLButtonElement>(`.navigation-route-card[data-candidate-id="${candidate.candidateId}"]`)?.focus({preventScroll: true});
          return;
        }
        this.simulation.stop();
        this.profile = profile; this.selectedLeg = null;
        this.renderCards(); this.draw(); this.displayResult();
        this.summary.querySelector<HTMLButtonElement>(`[data-profile="${profile}"]`)?.focus();
      });
      this.summary.append(card);
      if (profile === 'transit' && result) {
        const details = this.button('routeDetails', () => {
          this.selectTransitCandidate(candidate!.candidateId); this.showTransitDetails(true);
        });
        details.dataset.candidateId = candidate!.candidateId;
        details.setAttribute('aria-label', `${text('routeDetails')} · ${duration.textContent} · ${result.legs!.filter(leg => leg.mode === 'ride').map(leg => leg.name).join(' → ') || text('walkingOnly')}`);
        details.className = 'navigation-open-details'; this.summary.append(details);
      }
    }
    this.updateDetailView();
  }

  private selectTransitCandidate(id: string): void {
    if (this.picking) this.cancel();
    if (this.selectedCandidateId === id) return;
    this.simulation.stop(); this.selectedCandidateId = id; this.selectedLeg = null;
    this.renderCards(); this.draw(); this.displayResult();
  }

  private updateDetailView(): void {
    this.transitDetails.hidden = !this.detailsOpen || this.profile !== 'transit' || !this.result;
    this.summary.hidden = !this.transitDetails.hidden;
    this.panel.classList.toggle('navigation-details-open', !this.transitDetails.hidden);
  }

  private showTransitDetails(open: boolean): void {
    this.detailsOpen = open;
    if (open) this.listScroll = this.panel.scrollTop;
    this.updateDetailView();
    if (open) { this.panel.scrollTop = 0; this.backButton.focus({preventScroll: true}); }
    else {
      this.panel.scrollTop = this.listScroll;
      this.summary.querySelector<HTMLButtonElement>(`.navigation-open-details[data-candidate-id="${this.selectedCandidateId}"]`)?.focus({preventScroll: true});
    }
  }

  private fitRoute(): void {
    if (!this.result) return;
    this.fitGeometry(this.result.geometry);
  }

  private fitGeometry(points: XYZ[]): void {
    this.preview3d.reset();
    const bounds = new maplibregl.LngLatBounds(); points.forEach(p => bounds.extend(geo(p)));
    const panel = this.panel.getBoundingClientRect(), canvas = this.map.getCanvas(), narrow = window.innerWidth <= 520;
    this.map.fitBounds(bounds, {padding: {top: narrow ? 90 : 40, right: 40,
      left: narrow ? 40 : Math.min(panel.right+24, canvas.clientWidth*.45),
      bottom: narrow ? Math.min(panel.height+40, canvas.clientHeight*.65) : 40},
      maxZoom: 18, bearing: this.map.getBearing(), duration: 300});
  }

  private displayResult(): void {
    this.steps.replaceChildren();
    this.transitSteps.replaceChildren();
    this.resultActions.hidden = this.routeDetails.hidden = !this.result;
    const result = this.result;
    this.routeDetails.hidden = !result || result.costProfile === 'transit';
    this.updateDetailView();
    this.simulation.setRoute(result);
    if (result) {
      const missing = ['SPEED_LIMIT_MISSING', 'TRAFFIC_MISSING', 'PREFAB_SPEED_LIMIT'].find(code => result.warningCodes.includes(code));
      this.message.textContent = text(missing ?? 'snapshotNotice');
      if (result.costProfile === 'transit') {
        const notice = document.createElement('details'), title = document.createElement('summary'), detail = document.createElement('p');
        title.textContent = text('shortTransitNotice'); detail.textContent = text('transitNotice'); notice.append(title, detail);
        this.message.replaceChildren(notice);
      }
    }
    if (result?.legs) {
      this.transitSummary.textContent = `${result.boardings ? `${Math.max(0, result.boardings-1)} ${text('transfers')}` : text('walkingOnly')} · ${text('walk')} ${distanceLabel(result.walkingDistanceM!)} · ${text('routeLength')} ${distanceLabel(result.distanceM)}`;
      const endpoint = (key: 'origin' | 'destination', mark: string) => {
        const item = document.createElement('li'); item.className = 'navigation-itinerary-endpoint';
        item.textContent = `${mark} · ${this[key]?.place?.name || this[key]?.snap.name || text(key)}`;
        this.transitSteps.append(item);
      };
      endpoint('origin', 'A');
      for (const [index, leg] of result.legs.entries()) {
        const item = document.createElement('li'); item.className = `navigation-leg ${leg.mode}`;
        const select = document.createElement('button'); select.type = 'button'; select.className = 'navigation-segment';
        select.setAttribute('aria-pressed', String(this.selectedLeg === index));
        select.textContent = leg.mode === 'walk' ? `${text(leg.walkKind === 'transfer' ? 'transferWalk' : 'walk')} · ${distanceLabel(leg.distanceM)}${leg.toStopRef ? ` · ${text('walkTo')} ${leg.toStopRef.name || text('unnamedStop')}` : ''}`
          : `${text('ride')} ${leg.name || text('transit')} · ${leg.fromStop || text('unnamedStop')} → ${leg.toStop || text('unnamedStop')} · ${leg.stops} ${text('stops')}`;
        select.addEventListener('click', () => {
          this.selectedLeg = index;
          this.transitSteps.querySelectorAll('.navigation-segment').forEach((button, i) => button.setAttribute('aria-pressed', String(i === index)));
          const data = this.client!.data.transit!, edges = new Map(data.edges.map(edge => [edge.id, edge]));
          const points = result.traversals.slice(leg.traversalStart, leg.traversalEnd).flatMap(part => {
            const edge = edges.get(part.edgeId)!, geometry = data.geometries[edge.geometryRef];
            return slice(geometry, cumulative(geometry), edge.reverse ? 1-part.fromFraction : part.fromFraction,
              edge.reverse ? 1-part.toFraction : part.toFraction);
          });
          this.fitGeometry(points);
        });
        item.append(select);
        if (leg.color) item.style.setProperty('--line-color', leg.color);
        if (leg.schematic) {
          const notice = document.createElement('p'); notice.textContent = text('schematicNotice'); item.append(notice);
        }
        if (leg.walkKind === 'transfer' && leg.distanceM > LONG_TRANSFER_METERS) {
          const notice = document.createElement('p'); notice.className = 'navigation-transfer-notice';
          notice.textContent = `${text('longTransfer')} ${distanceLabel(leg.distanceM)}`; item.append(notice);
        }
        if (leg.mode === 'walk' && leg.fromStopRef) {
          const from = document.createElement('p'); from.textContent = `${leg.fromStopRef.name || text('unnamedStop')} → ${leg.toStopRef?.name || this.destination?.place?.name || text('destination')}`; item.append(from);
        }
        if (leg.stopSequence) {
          const stops = document.createElement('details'), title = document.createElement('summary'), sequence = document.createElement('ol');
          title.textContent = `${text('ride')} ${leg.stops} ${text('stops')}`;
          for (const stop of leg.stopSequence.slice(1)) {
            const row = document.createElement('li'); row.textContent = stop.name || text('unnamedStop'); sequence.append(row);
          }
          stops.append(title, sequence); item.append(stops);
        }
        this.transitSteps.append(item);
      }
      endpoint('destination', 'B');
      return;
    }
    for (const step of result?.maneuvers ?? []) {
      const item = document.createElement('li'); item.textContent = step.kind === 'arrive' ? text('arrive') : `${text(step.kind)} ${step.roadName || text('unnamed')} · ${Math.round(step.distanceM)} m`;
      this.steps.append(item);
    }
  }

  private draw(): void {
    for (const marker of this.marks) marker.remove(); this.marks = [];
    const features: GeoJSON.Feature[] = [];
    this.routeStrokes = [];
    this.routeArrows = [];
    if (this.result && this.client) {
      const data = this.profile === 'transit' ? this.client.data.transit! : this.client.data;
      const byId = new Map(data.edges.map(edge => [edge.id, edge]));
      const alternatives = this.profile === 'transit' ? [] : [...this.routes.values()].filter(route =>
        route !== this.result && JSON.stringify(route.traversals) !== JSON.stringify(this.result!.traversals));
      for (const route of [...alternatives, this.result]) {
        const alternative = route !== this.result;
        const strokes: RouteStroke[] = [];
        for (const traversal of route.traversals) {
          const edge = byId.get(traversal.edgeId)!, points = data.geometries[edge.geometryRef];
          const from = edge.reverse ? 1-traversal.fromFraction : traversal.fromFraction;
          const to = edge.reverse ? 1-traversal.toFraction : traversal.toFraction;
          strokes.push({properties: {...this.roadStyle.properties(edge), alternative, routeProfile: route.costProfile,
            mode: edge.mode, color: edge.color, schematic: edge.schematic ?? false, congestion: edge.congestion ?? -1},
            points: slice(points, cumulative(points), from, to)});
          if (edge.mode === 'RIDE') for (const point of [points[0], points.at(-1)!])
            features.push({type: 'Feature', properties: {mode: 'STOP', color: edge.color}, geometry: {type: 'Point', coordinates: geo(point)}});
        }
        const roadFeatures = routeStrokeFeatures(strokes, !alternative && this.profile !== 'transit' ? 2 : Infinity)
          .map((feature, id) => ({...feature, id: alternative ? `alternative:${id}` : id}));
        features.push(...roadFeatures);
        if (!alternative && this.profile !== 'transit') this.routeStrokes = roadFeatures;
      }
      this.routeArrows = turnArrowFeatures(this.client.data, this.result, this.roadStyle.widthMeters);
      features.push(...this.routeArrows);
    }
    for (const endpoint of ['origin', 'destination'] as const) {
      const value = this[endpoint]; if (!value) continue;
      if (value.place) features.push({type: 'Feature', properties: {mode: 'BUILDING', color: endpoint === 'origin' ? '#0067c0' : '#c52a3a'}, geometry: value.place.geometry});
      const element = document.createElement('button'); element.type = 'button'; element.className = `navigation-pin ${endpoint}`;
      setButtonContent(element, text(endpoint), endpoint === 'origin' ? 'map-pin' : 'flag');
      element.setAttribute('aria-label', text(endpoint === 'origin' ? 'start' : 'end'));
      const marker = new maplibregl.Marker({element, draggable: true}).setLngLat(geo(value.position)).addTo(this.map);
      marker.on('dragstart', () => { this.cancel(); this.resetResults();
        const source = this.map.getSource('navigation') as GeoJSONSource | undefined; source?.setData({type: 'FeatureCollection', features: []}); });
      marker.on('drag', () => {
        window.clearTimeout(this.previewTimer);
        const operation = this.operation;
        this.previewTimer = window.setTimeout(() => {
          const point = marker.getLngLat(), position: XYZ = [point.lat*111195, 0, -point.lng*111195];
          void this.ensureClient().then(client => client.snap(position, this.profile)).then(snaps => {
            if (operation !== this.operation) return;
            const source = this.map.getSource('navigation') as GeoJSONSource | undefined;
            source?.setData({type: 'FeatureCollection', features: [{type: 'Feature', properties: {mode: 'ACCESS'},
              geometry: {type: 'LineString', coordinates: [geo(position), geo(snaps[0].position)]}}]});
          }).catch(error => this.fail(error, operation));
        }, 150);
      });
      marker.on('dragend', () => { const point = marker.getLngLat(); this.picking = endpoint; void this.choose([point.lat*111195, 0, -point.lng*111195], this.placeAt(this.map.project(point))); });
      this.marks.push(marker);
      features.push({type: 'Feature', properties: {mode: 'ACCESS'}, geometry: {type: 'LineString', coordinates: [geo(value.position), geo(value.snap.position)]}});
    }
    const collection: GeoJSON.FeatureCollection = {type: 'FeatureCollection', features};
    const source = this.map.getSource('navigation') as GeoJSONSource | undefined;
    if (source) { this.map.removeFeatureState({source: 'navigation'}); source.setData(collection); }
    else {
      if (!this.loader) return;
      this.map.addSource('navigation', {type: 'geojson', data: collection, tolerance: 0});
      for (const [id, casing] of [[lineIds[11], true], [lineIds[12], false]] as const)
        this.map.addLayer({id, type: 'line', source: 'navigation', filter: ['==', ['get', 'alternative'], true],
          layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {
            'line-color': casing ? '#69717A' : routeTrafficColor, 'line-opacity': .7,
            'line-color-transition': {duration: 0}, 'line-width': this.roadStyle.width(casing)}});
      this.map.addLayer({id: lineIds[0], type: 'line', source: 'navigation', filter: ['all', ['!=', ['get', 'alternative'], true], ['in', ['get', 'mode'], ['literal', ['GROUND', 'ELEVATED', 'TUNNEL', 'RIDE']]]], layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {'line-color': ['case', ['==', ['get', 'mode'], 'RIDE'], '#ffffff', '#69717A'], 'line-width': this.roadStyle.width(true)}});
      this.map.addLayer({id: lineIds[1], type: 'line', source: 'navigation', filter: ['all', ['!=', ['get', 'alternative'], true], ['in', ['get', 'mode'], ['literal', ['GROUND', 'ELEVATED', 'TUNNEL']]]], layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {'line-color': routeTrafficColor, 'line-color-transition': {duration: 0}, 'line-width': this.roadStyle.width(false)}});
      this.map.addLayer({id: lineIds[2], type: 'line', source: 'navigation', filter: ['==', ['get', 'mode'], 'ACCESS'], paint: {'line-color': '#64748b', 'line-width': 2, 'line-dasharray': [2, 2]}});
      this.map.addLayer({id: lineIds[3], type: 'fill', source: 'navigation', filter: ['==', ['get', 'mode'], 'BUILDING'], paint: {'fill-color': ['get', 'color'], 'fill-opacity': .25}});
      this.map.addLayer({id: lineIds[4], type: 'line', source: 'navigation', filter: ['==', ['get', 'mode'], 'BUILDING'], paint: {'line-color': ['get', 'color'], 'line-width': 3}});
      this.map.addLayer({id: lineIds[5], type: 'line', source: 'navigation', filter: ['all', ['==', ['get', 'mode'], 'RIDE'], ['!=', ['get', 'schematic'], true]], layout: {'line-cap': 'round', 'line-join': 'round'}, paint: {'line-color': ['get', 'color'], 'line-width': 6}});
      this.map.addLayer({id: lineIds[6], type: 'line', source: 'navigation', filter: ['==', ['get', 'mode'], 'WALK'], paint: {'line-color': '#64748b', 'line-width': 4, 'line-dasharray': [1, 1]}});
      this.map.addLayer({id: lineIds[7], type: 'line', source: 'navigation', filter: ['all', ['==', ['get', 'mode'], 'RIDE'], ['==', ['get', 'schematic'], true]], paint: {'line-color': ['get', 'color'], 'line-width': 6, 'line-dasharray': [2, 1]}});
      this.map.addLayer({id: lineIds[8], type: 'circle', source: 'navigation', filter: ['==', ['get', 'mode'], 'STOP'], paint: {'circle-color': '#ffffff', 'circle-radius': 5, 'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 2}});
      const arrowFilter: ExpressionSpecification = ['all', ['==', ['get', 'mode'], 'TURN_ARROW'], ['>=', ['zoom'], ['get', 'minZoom']]];
      const arrowOpacity: ExpressionSpecification = ['case', ['boolean', ['feature-state', 'completed'], false], 0, 1];
      this.map.addLayer({id: lineIds[9], type: 'fill', source: 'navigation', filter: arrowFilter,
        paint: {'fill-color': '#ffffff', 'fill-opacity': arrowOpacity, 'fill-opacity-transition': {duration: 0}}});
      this.map.addLayer({id: lineIds[10], type: 'line', source: 'navigation', filter: arrowFilter,
        layout: {'line-join': 'round'}, paint: {'line-color': '#195ba5', 'line-width': arrowOutlineWidth,
          'line-opacity': arrowOpacity, 'line-opacity-transition': {duration: 0}}});
    }
    const progress = this.progressMeters;
    this.progressMeters = NaN; this.completedStrokes = 0; this.completedArrows = 0;
    this.simulation.setRouteStrokes(this.routeStrokes);
    this.drawProgress(this.simulation.active ? progress : null);
    this.simulation.refreshBuildings();
  }

  private drawProgress(meters: number | null): void {
    if ((this.progressMeters === null) !== (meters === null)) {
      for (const id of ['navigation-alternative-casing', 'navigation-alternative-road'])
        if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', meters === null ? 'visible' : 'none');
    }
    if (this.progressMeters === meters) return;
    this.progressMeters = meters;
    const target = meters ?? -1;
    while (this.completedArrows > 0 && this.routeArrows[this.completedArrows-1].properties!.passedAt > target) {
      const feature = this.routeArrows[--this.completedArrows];
      this.map.setFeatureState({source: 'navigation', id: feature.id!}, {completed: false});
    }
    while (this.completedArrows < this.routeArrows.length && this.routeArrows[this.completedArrows].properties!.passedAt <= target) {
      const feature = this.routeArrows[this.completedArrows++];
      this.map.setFeatureState({source: 'navigation', id: feature.id!}, {completed: true});
    }
    while (this.completedStrokes > 0 && this.routeStrokes[this.completedStrokes-1].properties!.routeEnd > target) {
      const feature = this.routeStrokes[--this.completedStrokes];
      this.map.setFeatureState({source: 'navigation', id: feature.id!}, {completed: false});
    }
    while (this.completedStrokes < this.routeStrokes.length && this.routeStrokes[this.completedStrokes].properties!.routeEnd <= target) {
      const feature = this.routeStrokes[this.completedStrokes++];
      this.map.setFeatureState({source: 'navigation', id: feature.id!}, {completed: true});
    }
  }

  refreshStyle(): void {
    if (this.result) this.draw();
    for (const [id, casing] of [['navigation-casing', true], ['navigation-road', false],
      ['navigation-alternative-casing', true], ['navigation-alternative-road', false]] as const)
      if (this.map.getLayer(id)) this.map.setPaintProperty(id, 'line-width', this.roadStyle.width(casing));
    this.simulation.refreshBuildings();
  }
}
