// 桌面与离线地图共用的搜索、快捷图层和操作说明。
import {i18n} from './i18n';
import {controlIcon, setButtonContent, type ControlIcon} from './control-icons';
import type {NavigationControl} from './routing/ui';
import {searchCandidates, searchGroups, type Candidate, type SearchFeature as Feature,
  type SearchSources as Sources, type SearchGroup} from './preview-search';
import './preview-controls.css';

type TextKey = 'search' | 'searchHelp' | 'empty' | 'building' | 'road' | 'area' | 'layers' | 'fit' | 'help'
  | 'title' | 'move' | 'moveHelp' | 'view' | 'viewHelp' | 'find' | 'findHelp' | 'navigate' | 'navigateHelp'
  | 'gotIt' | 'terrain' | 'built_area' | 'land_use' | 'water' | 'railway' | 'traffic' | 'metro' | 'transit'
  | 'poi' | 'label' | 'district' | 'zone' | 'watermark' | 'poi_area' | 'procedural_objects' | 'baseMap'
  | 'transportFacilities' | 'labelsOther' | 'close' | 'zoomIn' | 'zoomOut' | 'collapse' | 'expand'
  | 'searchRoutes' | 'actions';
type EdgeGroup = 'search' | 'layers' | 'view';
function text(key: TextKey): string {
  if (['search', 'searchHelp', 'empty', 'findHelp'].includes(key)) return i18n.t(`search_${key}`);
  return i18n.t(`controls_${key}`);
}

const layerGroups: Array<{title: TextKey; items: Array<[TextKey, ControlIcon]>}> = [
  {title: 'baseMap', items: [['terrain', 'mountain'], ['built_area', 'building-community'], ['land_use', 'map'],
    ['water', 'droplet'], ['road', 'road'], ['building', 'building'], ['railway', 'track']]},
  {title: 'transportFacilities', items: [['traffic', 'traffic-lights'], ['metro', 'train'], ['transit', 'train'],
    ['poi_area', 'border-outer'], ['poi', 'map-pin']]},
  {title: 'labelsOther', items: [['label', 'typography'], ['district', 'border-outer'], ['zone', 'border-all'],
    ['procedural_objects', 'cube'], ['watermark', 'copyright']]},
];

export class PreviewControls {
  private readonly root = document.createElement('section');
  private readonly top = document.createElement('div');
  private readonly input = document.createElement('input');
  private readonly results = document.createElement('div');
  private readonly searchStatus = document.createElement('div');
  private readonly layers = document.createElement('div');
  private readonly layerPanel = document.createElement('div');
  private readonly layerToggle = document.createElement('button');
  private readonly layerClose = document.createElement('button');
  private readonly layerTitle = document.createElement('h2');
  private readonly sections: Array<{section: HTMLElement; heading: HTMLElement; key: TextKey}> = [];
  private readonly abort = new AbortController();
  private readonly right = document.createElement('div');
  private readonly collapseToggle = document.createElement('button');
  private readonly edgeTabs = new Map<EdgeGroup, HTMLButtonElement>();
  private controlsCollapsed = false;
  private openGroup: EdgeGroup | null = null;
  private readonly viewSize = new ResizeObserver(([entry]) => {
    document.documentElement.style.setProperty('--preview-controls-right', `${Math.ceil(entry.contentRect.width) + 24}px`);
  });
  private readonly help = document.createElement('button');
  private readonly fit = document.createElement('button');
  private readonly zoomIn = document.createElement('button');
  private readonly zoomOut = document.createElement('button');
  private readonly dialog = document.createElement('dialog');
  private readonly confirm = document.createElement('button');
  private readonly title = document.createElement('h2');
  private readonly guideRows: Array<{title: HTMLElement; description: HTMLElement; key: TextKey; help: TextKey}> = [];
  private readonly checks = new Map<string, {label: HTMLElement; input: HTMLInputElement}>();
  private candidates: Candidate[] = [];
  private groups: SearchGroup[] = [];
  private rows: Array<{element: HTMLElement; activate: () => void; parent?: HTMLElement}> = [];
  private collapsed = new Set<string>();
  private limits = new Map<string, number>();
  private icons = new Map<string, ImageData>();
  private selected = -1;
  private pending = new Set<string>();
  private guideAutomatic = false;
  private acknowledged = false;
  private readonly languageChanged = () => this.translate();

  constructor(private readonly navigation: NavigationControl,
    private readonly locate: (feature: Feature) => void,
    private readonly requestLayer: (layer: string, visible: boolean) => void,
    fitMap: () => void,
    zoomMap: (delta: number) => void,
    private readonly guideEvent: (type: string) => void,
    private readonly focusMap: () => void,
    showLayers: boolean) {
    this.root.className = 'preview-controls'; this.root.hidden = true;
    this.top.className = 'preview-search-row';
    const search = document.createElement('div'); search.className = 'preview-search';
    this.input.type = 'search'; this.input.autocomplete = 'off'; this.input.setAttribute('role', 'combobox');
    this.input.setAttribute('aria-autocomplete', 'list'); this.input.setAttribute('aria-controls', 'preview-search-results');
    this.input.setAttribute('aria-haspopup', 'tree');
    this.input.setAttribute('aria-describedby', 'preview-search-help');
    this.results.id = 'preview-search-results'; this.results.className = 'preview-search-results';
    this.results.setAttribute('role', 'tree'); this.results.hidden = true;
    this.searchStatus.className = 'preview-search-status'; this.searchStatus.setAttribute('role', 'status');
    this.searchStatus.hidden = true;
    const hint = document.createElement('span'); hint.id = 'preview-search-help'; hint.className = 'preview-sr-only';
    search.append(this.input, hint, this.results, this.searchStatus); this.top.append(search);
    navigation.mount(this.top, () => {
      this.closeSearch();
      if (this.controlsCollapsed && this.openGroup !== 'search') this.setOpenGroup('search', false);
    });
    this.input.addEventListener('input', () => { navigation.hidePanel(); this.search(); });
    this.input.addEventListener('focus', () => { navigation.hidePanel(); this.search(); });
    this.results.onmousedown = event => event.preventDefault();
    this.input.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.stopPropagation(); this.closeSearch(); }
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); if (!this.rows.length) return;
        const down = event.key === 'ArrowDown';
        this.selected = this.selected < 0 ? (down ? 0 : this.rows.length - 1)
          : (this.selected + (down ? 1 : -1) + this.rows.length) % this.rows.length;
        this.updateSelected();
      } else if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && this.selected >= 0) {
        const row = this.rows[this.selected], expanded = row.element.getAttribute('aria-expanded');
        event.preventDefault();
        if (expanded === (event.key === 'ArrowLeft' ? 'true' : 'false')) row.activate();
        else if (event.key === 'ArrowLeft' && row.parent) {
          this.selected = this.rows.findIndex(item => item.element === row.parent); this.updateSelected();
        } else if (event.key === 'ArrowRight' && expanded === 'true') {
          this.selected++; this.updateSelected();
        }
      } else if (event.key === 'Enter' && this.rows.length) {
        event.preventDefault();
        if (this.selected >= 0) this.rows[this.selected].activate();
        else {
          const first = this.rows.find(row => row.element.classList.contains('preview-search-option'));
          (first ?? this.rows[0]).activate();
        }
      }
    });
    this.layers.className = 'preview-layers'; this.layerPanel.className = 'preview-layer-panel';
    this.layers.hidden = !showLayers;
    this.layerPanel.id = 'preview-layer-panel'; this.layerPanel.hidden = true;
    this.layerPanel.setAttribute('role', 'region'); this.layerTitle.id = 'preview-layer-title';
    this.layerPanel.setAttribute('aria-labelledby', this.layerTitle.id);
    const layerHeading = document.createElement('div'); layerHeading.className = 'preview-layer-heading';
    this.layerClose.type = 'button'; this.layerClose.onclick = () => this.closeLayers(true);
    layerHeading.append(this.layerTitle, this.layerClose); this.layerPanel.append(layerHeading);
    this.layerToggle.type = 'button'; this.layerToggle.setAttribute('aria-controls', this.layerPanel.id);
    this.layerToggle.setAttribute('aria-expanded', 'false');
    this.layerToggle.onclick = () => {
      this.layerPanel.hidden = !this.layerPanel.hidden;
      this.layerToggle.setAttribute('aria-expanded', String(!this.layerPanel.hidden));
      if (!this.layerPanel.hidden) this.layerClose.focus();
    };
    this.layerPanel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.stopPropagation(); this.closeLayers(true); }
    });
    document.addEventListener('pointerdown', event => {
      if (!this.layerPanel.hidden && !this.layers.contains(event.target as Node)) this.closeLayers();
      if (!search.contains(event.target as Node)) this.closeSearch();
      // 选取路线端点时，地图点击仍由导航处理，不能提前取消选点。
      if (this.controlsCollapsed && this.openGroup && !navigation.selectingPoint
        && (event.target as Element).classList.contains('maplibregl-canvas')) this.setOpenGroup(null);
    }, {signal: this.abort.signal});
    for (const group of layerGroups) {
      const section = document.createElement('section'), heading = document.createElement('h3');
      section.className = 'preview-layer-group'; heading.id = `preview-layer-${group.title}`;
      section.setAttribute('aria-labelledby', heading.id); section.append(heading); this.layerPanel.append(section);
      this.sections.push({section, heading, key: group.title});
      for (const [key, icon] of group.items) {
        const row = document.createElement('label'), label = document.createElement('span'), input = document.createElement('input');
        row.className = 'preview-layer-row'; input.type = 'checkbox'; input.dataset.layer = key;
        input.setAttribute('role', 'switch'); input.className = 'preview-layer-switch';
        input.onchange = () => { this.pending.add(key); input.disabled = true; this.requestLayer(key, input.checked); };
        row.append(controlIcon(icon), label, input); section.append(row); this.checks.set(key, {label, input});
      }
    }
    this.layers.append(this.layerPanel, this.layerToggle);
    this.right.className = 'maplibregl-ctrl preview-view-actions';
    this.fit.type = this.help.type = this.zoomIn.type = this.zoomOut.type = 'button';
    this.fit.onclick = fitMap; this.help.onclick = () => this.showGuide(false);
    this.zoomIn.onclick = () => zoomMap(1); this.zoomOut.onclick = () => zoomMap(-1);
    this.right.append(this.fit, this.zoomIn, this.zoomOut, this.help);
    this.top.id = 'preview-search-row'; this.layers.id = 'preview-layers'; this.right.id = 'preview-view-actions';
    for (const [group, target] of [['search', this.top], ['layers', this.layers], ['view', this.right]] as const) {
      if (group === 'layers' && !showLayers) continue;
      const tab = document.createElement('button'); tab.type = 'button';
      tab.className = `preview-edge-tab preview-edge-${group}`;
      tab.setAttribute('aria-controls', target.id); tab.setAttribute('aria-expanded', 'false');
      tab.onclick = () => this.setOpenGroup(this.openGroup === group ? null : group);
      this.edgeTabs.set(group, tab);
      (group === 'view' ? this.right : this.root).append(tab);
    }
    this.collapseToggle.type = 'button'; this.collapseToggle.className = 'preview-collapse-toggle';
    this.collapseToggle.onclick = () => {
      this.controlsCollapsed = !this.controlsCollapsed;
      document.documentElement.classList.toggle('preview-controls-collapsed', this.controlsCollapsed);
      this.setOpenGroup(null); this.translateCollapse();
    };
    this.right.append(this.collapseToggle);
    this.viewSize.observe(this.right);
    this.dialog.className = 'preview-guide'; this.dialog.setAttribute('aria-labelledby', 'preview-guide-title');
    this.dialog.setAttribute('aria-describedby', 'preview-guide-content');
    this.title.id = 'preview-guide-title'; const content = document.createElement('div');
    content.className = 'preview-guide-content'; content.id = 'preview-guide-content'; content.append(this.title);
    for (const [key, help] of [['move', 'moveHelp'], ['view', 'viewHelp'], ['find', 'findHelp'], ['navigate', 'navigateHelp']] as const) {
      const row = document.createElement('div'), title = document.createElement('div'), description = document.createElement('div');
      row.className = 'preview-guide-row'; title.className = 'preview-guide-row-title'; description.className = 'preview-guide-description';
      row.append(title, description); content.append(row); this.guideRows.push({title, description, key, help});
    }
    const footer = document.createElement('div'); footer.className = 'preview-guide-footer';
    this.confirm.type = 'button'; this.confirm.className = 'preview-guide-confirm'; this.confirm.autofocus = true;
    this.confirm.onclick = () => { this.acknowledged = true; this.dialog.close(); };
    footer.append(this.confirm); this.dialog.append(content, footer);
    this.dialog.addEventListener('close', () => {
      if (this.acknowledged) this.guideEvent('mapInteractionGuideAcknowledged');
      this.guideEvent('mapInteractionGuideClosed');
      if (this.guideAutomatic) this.focusMap(); else this.help.focus();
    });
    this.dialog.addEventListener('keydown', event => event.stopPropagation());
    this.root.append(this.top, this.layers); document.body.append(this.root, this.dialog);
    this.translate(); i18n.on('languageChanged', this.languageChanged);
  }

  readonly viewControl = {
    onAdd: (): HTMLElement => this.right,
    onRemove: (): void => this.right.remove(),
  };

  rebuild(sources: Sources, preserveSearch = false): void {
    if (!preserveSearch) { this.closeSearch(); this.input.value = ''; }
    this.pending.clear();
    this.candidates = searchCandidates(sources); this.root.hidden = false;
    this.navigation.setSearchCandidates(this.candidates, this.locate);
    if (preserveSearch && (!this.results.hidden || !this.searchStatus.hidden)) {
      this.groups = searchGroups(this.candidates, this.input.value);
      this.renderResults();
    }
  }

  resetSearch(): void { this.closeSearch(); this.input.value = ''; this.candidates = []; this.navigation.setSearchCandidates([], this.locate); }
  setSearchIcons(icons: Map<string, ImageData>): void { this.icons = icons; }

  setLayers(layers: Record<string, boolean>, available: Set<string>, enabled: boolean, settled = false, pending?: string[]): void {
    if (settled) this.pending.clear();
    if (pending) this.pending = new Set(pending);
    for (const [key, {input}] of this.checks) {
      input.parentElement!.hidden = !available.has(key) || !(key in layers);
      input.checked = layers[key] !== false; input.disabled = !enabled || this.pending.has(key);
    }
    for (const {section} of this.sections) section.hidden = !section.querySelector('label:not([hidden])');
  }

  setGuideAvailable(available: boolean): void { this.help.disabled = !available; }
  setZoomAvailability(zoomIn: boolean, zoomOut: boolean): void {
    this.zoomIn.disabled = !zoomIn; this.zoomOut.disabled = !zoomOut;
  }

  showGuide(automatic: boolean): void {
    if (this.dialog.open || this.root.hidden || this.help.disabled) return;
    this.closeSearch(); this.guideAutomatic = automatic; this.acknowledged = false;
    this.dialog.showModal(); this.guideEvent('mapInteractionGuideOpened');
  }

  private setOpenGroup(group: EdgeGroup | null, closeNavigation = true): void {
    this.closeSearch(); this.closeLayers();
    if (closeNavigation) this.navigation.hidePanel();
    this.openGroup = group;
    document.documentElement.dataset.previewOpenGroup = group ?? '';
    for (const [key, tab] of this.edgeTabs) tab.setAttribute('aria-expanded', String(group === key));
    if (group === 'layers') {
      this.layerPanel.hidden = false; this.layerToggle.setAttribute('aria-expanded', 'true');
    }
    const active = document.activeElement as HTMLElement | null;
    if (active && !active.checkVisibility()) (group ? this.edgeTabs.get(group)! : this.collapseToggle).focus();
  }

  private translateCollapse(): void {
    setButtonContent(this.collapseToggle, text(this.controlsCollapsed ? 'expand' : 'collapse'), 'arrows-up-down');
    this.collapseToggle.setAttribute('aria-expanded', String(!this.controlsCollapsed));
  }

  private translate(): void {
    this.input.placeholder = text('search'); this.input.setAttribute('aria-label', text('search'));
    this.results.setAttribute('aria-label', text('search'));
    this.root.querySelector('#preview-search-help')!.textContent = text('searchHelp');
    setButtonContent(this.layerToggle, text('layers'), 'stack-2'); this.layerTitle.textContent = text('layers');
    setButtonContent(this.layerClose, text('close'), 'x');
    setButtonContent(this.fit, text('fit'), 'zoom-scan'); setButtonContent(this.help, text('help'), 'help');
    setButtonContent(this.zoomIn, text('zoomIn'), 'zoom-in'); setButtonContent(this.zoomOut, text('zoomOut'), 'zoom-out');
    this.translateCollapse();
    for (const [group, tab] of this.edgeTabs) setButtonContent(tab,
      text(group === 'search' ? 'searchRoutes' : group === 'layers' ? 'layers' : 'actions'),
      group === 'search' ? 'search' : group === 'layers' ? 'stack-2' : 'adjustments');
    this.title.textContent = text('title'); setButtonContent(this.confirm, text('gotIt'), 'check');
    for (const {heading, key} of this.sections) heading.textContent = text(key);
    for (const row of this.guideRows) { row.title.textContent = text(row.key); row.description.textContent = text(row.help); }
    for (const [key, {label}] of this.checks) label.textContent = text(key as TextKey);
    if (!this.results.hidden || !this.searchStatus.hidden) this.search();
  }

  private search(): void {
    this.groups = searchGroups(this.candidates, this.input.value);
    this.collapsed.clear(); this.limits.clear(); this.renderResults();
  }

  private renderResults(focusId?: string): void {
    this.results.replaceChildren(); this.rows = []; this.selected = -1;
    const addRow = (element: HTMLElement, activate: () => void, parent?: HTMLElement) => {
      element.setAttribute('role', 'treeitem'); element.setAttribute('aria-selected', 'false');
      this.rows.push({element, activate, parent});
    };
    this.groups.forEach((group, groupIndex) => {
      const section = document.createElement('div'), heading = document.createElement('div');
      section.id = `preview-search-group-${groupIndex}`; section.className = 'preview-search-group';
      const expanded = !this.collapsed.has(group.key);
      section.setAttribute('aria-expanded', String(expanded));
      heading.className = 'preview-search-heading'; heading.id = `${section.id}-label`;
      section.setAttribute('aria-labelledby', heading.id);
      const arrow = document.createElement('span'), label = document.createElement('span'), count = document.createElement('span');
      arrow.textContent = expanded ? '▾' : '▸'; arrow.setAttribute('aria-hidden', 'true');
      label.textContent = group.label; if (group.locale) label.lang = group.locale;
      count.textContent = i18n.t('search_count', {count: group.items.length,
        number: group.items.length.toLocaleString(i18n.language)});
      count.className = 'preview-search-count'; heading.append(arrow, label, count);
      const toggle = () => {
        if (expanded) this.collapsed.add(group.key); else this.collapsed.delete(group.key);
        this.renderResults(section.id);
      };
      heading.onclick = toggle; addRow(section, toggle); section.append(heading); this.results.append(section);
      if (!expanded) return;
      const children = document.createElement('div'); children.setAttribute('role', 'group'); section.append(children);
      const limit = this.limits.get(group.key) ?? 5;
      group.items.slice(0, limit).forEach((item, index) => {
        const option = document.createElement('div'); option.id = `${section.id}-item-${index}`;
        option.className = 'preview-search-option';
        const name = document.createElement('span'); name.textContent = item.name;
        const pixels = item.iconKey ? this.icons.get(item.iconKey) : undefined;
        if (pixels) {
          const icon = document.createElement('canvas'); icon.width = pixels.width; icon.height = pixels.height;
          icon.getContext('2d')!.putImageData(pixels, 0, 0); icon.setAttribute('aria-hidden', 'true'); option.append(icon);
        } else option.append(controlIcon(item.kind === 'poi' ? 'map-pin' : item.kind === 'area' ? 'border-outer' : item.kind));
        option.append(name); const activate = () => this.choose(item);
        option.onclick = activate; addRow(option, activate, section); children.append(option);
      });
      if (limit < group.items.length) {
        const more = document.createElement('div'); more.id = `${section.id}-more`; more.className = 'preview-search-more';
        more.textContent = i18n.t('search_more');
        const activate = () => {
          this.limits.set(group.key, limit + 20); this.renderResults(`${section.id}-item-${limit}`);
        };
        more.onclick = activate; addRow(more, activate, section); children.append(more);
      }
    });
    this.results.hidden = !this.groups.length;
    this.searchStatus.hidden = !this.input.value.trim() || !!this.groups.length;
    this.searchStatus.textContent = text('empty');
    this.input.setAttribute('aria-expanded', String(!this.results.hidden)); this.input.removeAttribute('aria-activedescendant');
    if (focusId) { this.selected = this.rows.findIndex(row => row.element.id === focusId); this.updateSelected(); }
  }

  private updateSelected(): void {
    this.rows.forEach(({element}, index) => element.setAttribute('aria-selected', String(index === this.selected)));
    const selected = this.rows[this.selected]?.element;
    if (selected) {
      this.input.setAttribute('aria-activedescendant', selected.id);
      (selected.querySelector('.preview-search-heading') ?? selected).scrollIntoView({block: 'nearest'});
    }
  }

  private choose(item: Candidate): void { this.locate(item.feature); this.input.value = item.name; this.closeSearch(); }
  private closeLayers(focus = false): void {
    this.layerPanel.hidden = true; this.layerToggle.setAttribute('aria-expanded', 'false');
    if (focus) {
      if (this.controlsCollapsed) { this.setOpenGroup(null); this.edgeTabs.get('layers')!.focus(); }
      else this.layerToggle.focus();
    }
  }
  private closeSearch(): void {
    this.results.hidden = true; this.searchStatus.hidden = true; this.rows = []; this.groups = []; this.selected = -1;
    this.input.setAttribute('aria-expanded', 'false'); this.input.removeAttribute('aria-activedescendant');
  }

  dispose(): void {
    this.abort.abort();
    document.documentElement.classList.remove('preview-controls-collapsed');
    delete document.documentElement.dataset.previewOpenGroup;
    this.viewSize.disconnect();
    i18n.off('languageChanged', this.languageChanged); this.root.remove(); this.dialog.remove();
  }
}
