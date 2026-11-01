import * as maplibregl from 'maplibre-gl';
import type {JumpToOptions, Map as MapLibreMap} from 'maplibre-gl';
import {i18n} from '../i18n';
import {setButtonContent, type ControlIcon} from '../control-icons';
import {completedRouteColor, missingRouteTrafficColor, mixedTrafficColor} from '../traffic-paint';
import {geo} from './geometry';
import {advance, angularDelta, approachZoom, nextManeuver, routeTrack, turnToward} from './simulation-motion';
import type {RouteResult} from './types';
import './simulation.css';
import type {Preview3D} from '../preview-3d';

const en = {start: 'Simulate', title: 'Simulation', exit: 'Exit', play: 'Play', pause: 'Pause',
  resume: 'Resume', replay: 'Replay', restart: 'Restart', overview: 'Overview', follow: 'Follow',
  view: 'View', flat: '2D', tilt: '2.5D', buildings: '3D buildings',
  buildingHeight: 'Buildings use exported model heights; missing heights remain flat.', orientation: 'Orientation', north: 'North up', heading: 'Heading up',
  speed: 'Speed', zoom: 'Zoom', smooth: 'Smooth turns', progress: 'Progress', settings: 'Settings',
  remainingDistance: 'Distance remaining', remainingTime: 'Simulation time remaining',
  left: 'Turn left', right: 'Turn right', arrive: 'Destination', arrived: 'Arrived', unnamed: 'Unnamed road',
  seconds: '{{seconds}} s', minutes: '{{minutes}} min {{seconds}} s'};
const zh = {start: '模拟导航', title: '模拟导航', exit: '退出', play: '开始', pause: '暂停',
  resume: '继续', replay: '再播放', restart: '重新开始', overview: '全览', follow: '恢复跟随',
  view: '视图', flat: '2D', tilt: '2.5D', buildings: '立体建筑',
  buildingHeight: '建筑采用导出的模型高度；缺失高度的建筑保持平面。', orientation: '朝向', north: '正北朝上', heading: '车头朝上',
  speed: '速度', zoom: '缩放', smooth: '平滑转向', progress: '进度', settings: '设置',
  remainingDistance: '剩余路程', remainingTime: '模拟剩余时间',
  left: '左转', right: '右转', arrive: '到达终点', arrived: '已到达', unnamed: '未命名道路',
  seconds: '{{seconds}} 秒', minutes: '{{minutes}} 分 {{seconds}} 秒'};
i18n.addResourceBundle('en', 'simulation', en);
i18n.addResourceBundle('zh-CN', 'simulation', zh);
i18n.addResourceBundle('zh-TW', 'simulation', {...zh, start: '模擬導航', title: '模擬導航', exit: '退出',
  pause: '暫停', resume: '繼續', restart: '重新開始', overview: '全覽', follow: '恢復跟隨',
  view: '視圖', buildings: '立體建築', buildingHeight: '建築採用匯出的模型高度；缺失高度的建築保持平面。',
  orientation: '朝向', heading: '車頭朝上', zoom: '縮放', smooth: '平滑轉向', progress: '進度', settings: '設定',
  remainingDistance: '剩餘路程', remainingTime: '模擬剩餘時間', left: '左轉', right: '右轉', arrive: '抵達終點', arrived: '已抵達'});
const text = (key: string, values: Record<string, number> = {}): string =>
  i18n.t(key, {ns: 'simulation', ...values, defaultValue: en[key as keyof typeof en] ?? key});
const distance = (m: number): string => m >= 1000 ? `${(m/1000).toFixed(1)} km` : `${Math.round(m)} m`;
const simulationIcons: Record<string, ControlIcon> = {
  start: 'navigation', exit: 'logout', settings: 'adjustments', pause: 'player-pause', follow: 'navigation',
  restart: 'player-skip-back', overview: 'zoom-scan', play: 'player-play', resume: 'player-play', replay: 'arrow-back-up',
};

export class NavigationSimulation {
  readonly startButton: HTMLButtonElement;
  private readonly settings = document.createElement('div');
  private readonly instruction = document.createElement('div');
  private readonly footer = document.createElement('div');
  private readonly view = document.createElement('select');
  private readonly orientation = document.createElement('select');
  private readonly speed = document.createElement('input');
  private readonly zoom = document.createElement('input');
  private readonly smooth = document.createElement('input');
  private readonly progress = document.createElement('input');
  private readonly speedValue = document.createElement('output');
  private readonly turn = document.createElement('span');
  private readonly nextDistance = document.createElement('strong');
  private readonly nextRoad = document.createElement('span');
  private readonly remainingTime = document.createElement('strong');
  private readonly remainingDistance = document.createElement('strong');
  private readonly play: HTMLButtonElement;
  private readonly followButton: HTMLButtonElement;
  private readonly marker: maplibregl.Marker;
  private route: RouteResult | null = null;
  private track: ReturnType<typeof routeTrack> | null = null;
  private savedCamera: JumpToOptions | null = null;
  private saved3D = false;
  private meters = 0;
  private playing = false;
  private entering = false;
  private following = true;
  private speedKmh = 60;
  private heading = 0;
  private bearing = 0;
  private pitch = 0;
  private turnDistance = 0;
  private frame = 0;
  private previousTime = 0;
  private uiTime = 0;
  private displayedMeters = -1;
  private readonly languageChanged = () => this.translate();
  private readonly visibilityChanged = () => {
    if (document.hidden) { this.pause(); cancelAnimationFrame(this.frame); this.frame = 0; }
    else this.wake();
  };
  private readonly resized = () => this.wake();
  private readonly entered = () => {
    this.entering = false; this.previousTime = performance.now();
    this.bearing = this.map.getBearing(); this.pitch = this.map.getPitch(); this.wake();
  };
  private readonly mapGesture = (event: {originalEvent?: Event}) => {
    if (this.active && event.originalEvent) this.detach();
  };

  constructor(private readonly map: MapLibreMap, private readonly root: HTMLElement,
    private readonly progressChanged: (meters: number | null) => void, private readonly preview3d: Preview3D) {
    this.startButton = this.button('start', () => this.start());
    this.startButton.className = 'navigation-simulate'; this.startButton.hidden = true;
    this.settings.className = 'navigation-simulation-settings';
    this.instruction.className = 'navigation-simulation-instruction';
    this.footer.className = 'navigation-simulation-footer';
    const heading = document.createElement('div'); heading.className = 'navigation-heading';
    const title = document.createElement('h2'); title.dataset.simulationKey = 'title';
    heading.append(title, this.button('exit', () => { this.stop(); this.startButton.focus(); }));
    const options = document.createElement('div'); options.className = 'navigation-simulation-options';
    this.select(this.view, ['flat', 'tilt', 'buildings']); this.view.value = 'tilt';
    this.view.title = text('buildingHeight');
    this.select(this.orientation, ['north', 'heading']); this.orientation.value = 'heading';
    this.range(this.speed, 20, 360, 10, 60); this.range(this.zoom, 14, 21, .1, 18);
    this.smooth.type = 'checkbox'; this.smooth.checked = true;
    this.speedValue.textContent = '60 km/h';
    options.append(this.field('view', this.view), this.field('orientation', this.orientation),
      this.field('speed', this.speed, this.speedValue), this.field('zoom', this.zoom), this.field('smooth', this.smooth));
    const settingsToggle = this.button('settings', () => {
      const expanded = this.settings.classList.toggle('expanded'); settingsToggle.setAttribute('aria-expanded', String(expanded));
    });
    settingsToggle.className = 'navigation-simulation-settings-toggle'; settingsToggle.setAttribute('aria-expanded', 'false');
    this.settings.append(heading, options);
    this.turn.className = 'navigation-simulation-turn'; this.turn.setAttribute('aria-hidden', 'true');
    const maneuver = document.createElement('div'); maneuver.append(this.nextDistance, this.nextRoad);
    this.instruction.append(this.turn, maneuver);
    const metrics = document.createElement('div'); metrics.className = 'navigation-simulation-metrics';
    for (const [key, value] of [['remainingTime', this.remainingTime], ['remainingDistance', this.remainingDistance]] as const) {
      const item = document.createElement('div'), label = document.createElement('span'); label.dataset.simulationKey = key;
      item.append(value, label); metrics.append(item);
    }
    const actions = document.createElement('div'); actions.className = 'navigation-actions';
    this.play = this.button('pause', () => this.playing ? this.pause() : this.resume());
    delete this.play.dataset.simulationKey; this.play.className = 'navigation-simulation-play';
    this.followButton = this.button('follow', () => this.follow());
    actions.append(this.play, this.button('restart', () => { this.pause(); this.seek(0); this.follow(); }),
      this.button('overview', () => this.overview()), this.followButton, settingsToggle);
    this.range(this.progress, 0, 1000, 1, 0); this.progress.className = 'navigation-simulation-progress';
    this.progress.style.setProperty('--navigation-completed-color', completedRouteColor);
    this.progress.dataset.simulationLabel = 'progress';
    this.footer.append(metrics, this.progress, actions);
    this.settings.hidden = this.instruction.hidden = this.footer.hidden = true;
    root.append(this.settings, this.instruction, this.footer);
    const element = document.createElement('div'); element.className = 'navigation-car';
    element.innerHTML = '<svg viewBox="0 0 36 42" width="36" height="42" aria-hidden="true"><path d="M18 3L32 36L18 29L4 36Z" stroke-width="3" stroke-linejoin="round"/></svg>';
    this.marker = new maplibregl.Marker({element, rotationAlignment: 'map', pitchAlignment: 'viewport'});
    this.view.onchange = () => {
      this.preview3d.setEnabled(this.view.value === 'buildings', false);
      this.refreshBuildings();
      if (!this.following) this.map.easeTo({pitch: this.targetPitch(), duration: 250});
      this.wake();
    };
    this.orientation.onchange = () => this.follow();
    this.smooth.onchange = () => this.wake();
    this.speed.oninput = () => {
      this.advanceClock(performance.now()); this.speedKmh = Number(this.speed.value);
      this.speedValue.textContent = `${this.speedKmh} km/h`; this.updateUI();
    };
    this.zoom.oninput = () => { this.follow(); this.map.setZoom(Number(this.zoom.value)); };
    this.progress.oninput = () => { if (this.track) this.seek(Number(this.progress.value)/1000*this.track.total); };
    map.on('pitchstart', this.mapGesture); map.on('dragstart', this.mapGesture); map.on('zoomstart', this.mapGesture); map.on('rotatestart', this.mapGesture);
    map.on('resize', this.resized);
    document.addEventListener('visibilitychange', this.visibilityChanged);
    i18n.on('languageChanged', this.languageChanged); this.translate();
  }

  get active(): boolean { return this.savedCamera !== null; }
  get followingCamera(): boolean { return this.active && this.following; }

  private button(key: string, action: () => void): HTMLButtonElement {
    const b = document.createElement('button'); b.type = 'button'; b.dataset.simulationKey = key;
    setButtonContent(b, text(key), simulationIcons[key]); b.onclick = action; return b;
  }
  private select(select: HTMLSelectElement, keys: string[]): void {
    for (const key of keys) { const option = document.createElement('option'); option.value = key;
      option.dataset.simulationKey = key; select.append(option); }
  }
  private range(input: HTMLInputElement, min: number, max: number, step: number, value: number): void {
    input.type = 'range'; input.min = String(min); input.max = String(max); input.step = String(step); input.value = String(value);
  }
  private field(key: string, control: HTMLElement, output?: HTMLElement): HTMLLabelElement {
    const label = document.createElement('label'), caption = document.createElement('span'); caption.dataset.simulationKey = key;
    control.dataset.simulationLabel = key; label.append(caption, control); if (output) label.append(output); return label;
  }
  private translate(): void {
    for (const el of this.root.querySelectorAll<HTMLElement>('[data-simulation-key]')) {
      const key = el.dataset.simulationKey!;
      if (el instanceof HTMLButtonElement) setButtonContent(el, text(key), simulationIcons[key]);
      else el.textContent = text(key);
    }
    for (const el of this.root.querySelectorAll<HTMLElement>('[data-simulation-label]')) el.setAttribute('aria-label', text(el.dataset.simulationLabel!));
    this.view.title = text('buildingHeight'); setButtonContent(this.startButton, text('start'), 'navigation'); this.updateUI();
  }

  setRoute(route: RouteResult | null): void {
    if (this.route === route) return;
    this.stop(); this.route = route;
    this.track = route && route.costProfile !== 'transit' && route.distanceM > 0 ? routeTrack(route.geometry) : null;
    this.startButton.hidden = this.track === null;
  }
  setRouteStrokes(features: GeoJSON.Feature[]): void {
    const total = Number(features.at(-1)?.properties?.routeEnd ?? 0);
    const runs: {color: string; start: number; end: number}[] = [];
    for (const {properties: p} of features) {
      const color = mixedTrafficColor(p!.congestion, p!.trafficNeighbor, p!.trafficBlend, missingRouteTrafficColor);
      const previous = runs.at(-1);
      if (previous?.color === color && previous.end === p!.routeStart) previous.end = p!.routeEnd;
      else runs.push({color, start: p!.routeStart, end: p!.routeEnd});
    }
    const stops = runs.flatMap(({color, start, end}) => [`${color} ${start/total*100}%`, `${color} ${end/total*100}%`]);
    this.progress.style.setProperty('--navigation-traffic', total > 0 ? `linear-gradient(to right,${stops.join(',')})` : '#D9DDDA');
    this.displayedMeters = -1;
    this.updateUI();
  }
  private start(): void {
    if (!this.track || this.active) return;
    this.map.stop();
    this.saved3D = this.preview3d.enabled;
    this.savedCamera = {center: this.map.getCenter(), zoom: this.map.getZoom(), bearing: this.map.getBearing(),
      pitch: this.map.getPitch(), padding: {...this.map.getPadding()}};
    this.preview3d.setEnabled(this.view.value === 'buildings', false);
    this.root.classList.add('navigation-simulation-active');
    this.settings.hidden = this.instruction.hidden = this.footer.hidden = false;
    this.meters = 0; this.heading = this.track.heading(0);
    this.bearing = this.orientation.value === 'north' ? 0 : this.heading; this.pitch = this.targetPitch();
    this.marker.setLngLat(geo(this.track.at(0))).addTo(this.map);
    this.following = true; this.playing = true; this.previousTime = performance.now();
    this.refreshBuildings(); this.updateUI();
    this.entering = true;
    this.map.once('moveend', this.entered);
    this.map.easeTo({center: geo(this.track.at(0)), zoom: this.targetZoom(),
      bearing: this.bearing, pitch: this.pitch, padding: this.followPadding(),
      duration: 1500, easing: t => t*t*(3-2*t)});
    this.wake(); this.play.focus();
  }
  stop(restoreCamera = true): void {
    if (!this.active) return;
    this.playing = false; cancelAnimationFrame(this.frame); this.frame = 0;
    this.progressChanged(null);
    this.cancelEntry();
    this.marker.remove();
    this.preview3d.setEnabled(this.saved3D, false);
    this.root.classList.remove('navigation-simulation-active');
    this.settings.hidden = this.instruction.hidden = this.footer.hidden = true;
    const camera = this.savedCamera!; this.savedCamera = null; this.map.stop();
    if (restoreCamera) this.map.jumpTo(camera);
  }
  private advanceClock(now: number): number {
    const dt = (now-this.previousTime)/1000; this.previousTime = now;
    if (this.playing && !this.entering && this.track) {
      this.meters = advance(this.meters, this.speedKmh, dt, this.track.total);
      if (this.meters >= this.track.total) this.playing = false;
    }
    return dt;
  }
  private pause(): void {
    if (!this.active) return;
    this.advanceClock(performance.now()); this.playing = false; this.updateUI();
  }
  private resume(): void {
    if (!this.track) return;
    if (this.meters >= this.track.total) this.seek(0);
    this.playing = true; this.previousTime = performance.now(); this.updateUI(); this.wake();
  }
  private seek(meters: number): void {
    if (!this.track) return;
    this.cancelEntry();
    this.meters = meters; this.previousTime = performance.now(); this.heading = this.track.heading(meters);
    if (meters >= this.track.total) this.playing = false;
    this.updateUI(); this.render(0); this.wake();
  }
  private cancelEntry(): void {
    if (!this.entering) return;
    this.map.off('moveend', this.entered); this.entering = false; this.map.stop();
    this.bearing = this.map.getBearing(); this.pitch = this.map.getPitch();
    this.previousTime = performance.now();
  }
  private detach(): void { this.cancelEntry(); this.following = false; this.updateUI(); }
  private follow(): void {
    this.cancelEntry();
    this.map.stop(); this.following = true; this.bearing = this.map.getBearing(); this.pitch = this.map.getPitch(); this.updateUI(); this.wake();
  }
  private overview(): void {
    if (!this.route) return;
    this.detach(); this.view.value = 'flat'; this.preview3d.reset();
    const bounds = new maplibregl.LngLatBounds(); this.route.geometry.forEach(p => bounds.extend(geo(p)));
    this.map.fitBounds(bounds, {bearing: 0, pitch: 0, maxZoom: 18, duration: 300,
      padding: {top: 130, bottom: 210, left: window.innerWidth > 680 ? 330 : 35, right: 35}});
  }
  private targetPitch(): number { return this.view.value === 'flat' ? 0 : 52; }
  private targetZoom(): number {
    return approachZoom(Number(this.zoom.value), this.turnDistance);
  }
  private followPadding() {
    return {top: this.map.getContainer().clientHeight*.16, bottom: 0,
      left: window.innerWidth <= 680 ? 0 : 310, right: 0};
  }
  set3DEnabled(enabled: boolean): void {
    if (!this.active) return;
    this.view.value = enabled ? 'buildings' : 'flat'; this.detach();
  }
  refreshBuildings(): void { this.preview3d.refresh(); }
  private render(dt: number): boolean {
    if (!this.track) return false;
    const smooth = this.smooth.checked, center = geo(this.track.at(this.meters));
    const targetHeading = this.track.heading(this.meters, smooth ? 18 : 2);
    this.heading = smooth ? turnToward(this.heading, targetHeading, dt, .12, 180) : targetHeading;
    const headingPending = Math.abs(angularDelta(this.heading, targetHeading)) > .01;
    if (!headingPending) this.heading = targetHeading;
    const markerCenter = this.marker.getLngLat();
    if (markerCenter.lng !== center[0] || markerCenter.lat !== center[1]) this.marker.setLngLat(center);
    if (this.marker.getRotation() !== this.heading) this.marker.setRotation(this.heading);
    if (!this.following || this.entering) return headingPending;
    const target = this.orientation.value === 'north' ? 0 : this.track.heading(this.meters+(smooth ? 8 : 0), smooth ? 24 : 2);
    this.bearing = smooth ? turnToward(this.bearing, target, dt) : target;
    this.pitch += (this.targetPitch()-this.pitch)*(1-Math.exp(-dt/.22));
    if (Math.abs(angularDelta(this.bearing, target)) <= .01) this.bearing = target;
    if (Math.abs(this.targetPitch()-this.pitch) <= .01) this.pitch = this.targetPitch();
    const padding = this.followPadding();
    const currentZoom = this.map.getZoom(), targetZoom = this.targetZoom();
    const deltaZoom = (targetZoom-currentZoom)*(1-Math.exp(-dt/3));
    // 自动缩放独立于转向开关；小于千分之一级时收敛，暂停后不保留浮点尾巴。
    const zoom = Math.abs(targetZoom-currentZoom) <= .001 ? targetZoom
      : currentZoom+Math.max(-.15*dt, Math.min(.15*dt, deltaZoom));
    const last = this.map.getCenter(), oldPadding = this.map.getPadding();
    if (Math.abs(last.lng-center[0]) > 1e-10 || Math.abs(last.lat-center[1]) > 1e-10
        || Math.abs(angularDelta(this.map.getBearing(), this.bearing)) > 1e-8
        || Math.abs(this.map.getPitch()-this.pitch) > 1e-8 || Math.abs(currentZoom-zoom) > 1e-10
        || Math.abs((oldPadding.top ?? 0)-padding.top) > .1 || oldPadding.left !== padding.left)
      this.map.jumpTo({center, bearing: this.bearing, pitch: this.pitch, zoom, padding});
    return headingPending || Math.abs(angularDelta(this.bearing, target)) > .01
      || this.pitch !== this.targetPitch() || zoom !== targetZoom;
  }
  private updateUI(): void {
    const key = this.playing ? 'pause' : this.track && this.meters >= this.track.total ? 'replay' : this.meters > 0 ? 'resume' : 'play';
    this.play.dataset.simulationKey = key; setButtonContent(this.play, text(key), simulationIcons[key]);
    this.followButton.hidden = this.following;
    if (!this.track || !this.route) return;
    const next = nextManeuver(this.route, this.meters);
    this.turnDistance = Math.min(next.remainingM, this.track.straightAhead(this.meters));
    const remaining = Math.max(0, this.track.total-this.meters), seconds = Math.ceil(remaining/(this.speedKmh/3.6));
    this.remainingDistance.textContent = distance(remaining);
    this.remainingTime.textContent = seconds < 60 ? text('seconds', {seconds})
      : text('minutes', {minutes: Math.floor(seconds/60), seconds: seconds%60});
    this.updateProgress();
    this.progress.setAttribute('aria-valuetext', `${Math.round(this.meters/this.track.total*100)}%`);
    this.turn.textContent = next.kind === 'left' ? '↰' : next.kind === 'right' ? '↱' : '⚑';
    this.nextDistance.textContent = remaining < .1 ? text('arrived') : distance(next.remainingM);
    this.nextRoad.textContent = text(next.kind)+(next.kind === 'arrive' ? '' : ` · ${next.roadName || text('unnamed')}`);
  }
  private wake(): void {
    if (!this.active || this.frame || document.hidden) return;
    this.previousTime = performance.now(); this.frame = requestAnimationFrame(this.tick);
  }
  private readonly tick = (now: number): void => {
    this.frame = 0;
    if (!this.active || document.hidden) return;
    const dt = this.advanceClock(now);
    if (now-this.uiTime >= 150 || !this.playing) { this.updateUI(); this.uiTime = now; }
    const settling = this.render(dt);
    this.updateProgress();
    if (this.playing || this.entering || settling) this.frame = requestAnimationFrame(this.tick);
  };
  private updateProgress(): void {
    if (!this.track) return;
    if (this.displayedMeters !== this.meters) {
      this.displayedMeters = this.meters;
      this.progress.value = String(this.meters/this.track.total*1000);
      this.progress.style.setProperty('--navigation-completed', `${this.meters/this.track.total*100}%`);
    }
    if (this.active) this.progressChanged(this.meters);
  }
  dispose(): void {
    this.stop(); this.settings.remove(); this.instruction.remove(); this.footer.remove();
    this.map.off('pitchstart', this.mapGesture); this.map.off('dragstart', this.mapGesture); this.map.off('zoomstart', this.mapGesture); this.map.off('rotatestart', this.mapGesture);
    this.map.off('resize', this.resized);
    document.removeEventListener('visibilitychange', this.visibilityChanged); i18n.off('languageChanged', this.languageChanged);
  }
}
