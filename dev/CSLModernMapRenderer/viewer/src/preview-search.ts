// 搜索只合并同一源对象；同名或同图标不能证明是同一个地点。
import {i18n} from './i18n';

export type SearchFeature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, any>>;
export type SearchSources = Record<string, {features: SearchFeature[]}>;
export type Candidate = {
  name: string; names: string[]; kind: 'building' | 'road' | 'area' | 'poi';
  key: string; feature: SearchFeature; iconKey?: string; context?: string;
  categoryId: string; categoryName: string; categoryLocale: string;
};
export type SearchGroup = {key: string; label: string; locale: string; items: Candidate[]};

export function searchCandidates(sources: SearchSources): Candidate[] {
  const unique = new Map<string, Candidate>();
  const add = (key: string, kind: Candidate['kind'], feature: SearchFeature, poi = false) => {
    const props = feature.properties, name = String(props.name ?? '').trim();
    const existing = unique.get(key);
    const category = props.categoryId && props.categoryName && props.categoryLocale ? props : existing;
    const item: Candidate = {
      key, kind: existing?.kind ?? kind, name: name || existing?.name || '',
      names: [...new Set([...(existing?.names ?? []), name].filter(Boolean))],
      feature: existing?.feature ?? feature, iconKey: poi ? props.iconKey : existing?.iconKey,
      categoryId: category?.categoryId ?? '', categoryName: category?.categoryName ?? '',
      categoryLocale: category?.categoryLocale ?? '',
      context: String(props.context || props.roadName || props.districtName || existing?.context || ''),
    };
    if (!poi && props.geometryRole === 'road-surface') item.feature = feature;
    unique.set(key, item);
  };
  for (const [source, kind, id] of [
    ['buildings', 'building', 'buildingId'], ['roads', 'road', 'roadId'],
    ['districts', 'area', 'areaId'], ['zones', 'area', 'areaId'],
  ] as const) for (const feature of sources[source]?.features ?? [])
    add(`${source}:${feature.properties[id] ?? feature.properties.featureId ?? feature.id}`, kind, feature);

  const seenPois = new Set<string>();
  for (const source of ['pois', 'transitViewPois', 'stations']) for (const feature of sources[source]?.features ?? []) {
    const p = feature.properties, id = String(p.poiId ?? p.featureId ?? feature.id);
    if (seenPois.has(id)) continue;
    seenPois.add(id);
    const key = p.stationId ? `station:${p.stationId}` : p.sourceKind === 'building' ? `buildings:${p.sourceId}`
      : p.sourceKind === 'park' ? `zones:${p.sourceId}` : `poi:${id}`;
    add(key, 'poi', feature, true);
  }
  return [...unique.values()].filter(item => item.name);
}

export function searchGroups(candidates: Candidate[], query: string): SearchGroup[] {
  const normalize = (value: string) => value.normalize('NFKC').toLocaleLowerCase(i18n.language);
  query = normalize(query.trim());
  if (!query) return [];
  const groups = new Map<string, SearchGroup>();
  const ranked = candidates.map(item => {
    const label = item.categoryName || i18n.t(`search_group_${item.kind}`);
    const ranks = item.names.map(normalize).map(name => name === query ? 0 : name.startsWith(query) ? 1
      : name.includes(query) ? 2 : 4);
    return {item, label, rank: Math.min(...ranks, normalize(label).includes(query) ? 3 : 4)};
  }).filter(item => item.rank < 4).sort((a, b) => a.rank - b.rank
    || a.item.name.localeCompare(b.item.name, i18n.language) || a.item.key.localeCompare(b.item.key));
  // 按已排序结果首次出现的顺序建组，保留组内最佳匹配的优先级。
  for (const {item, label} of ranked) {
    const key = item.categoryId ? `category:${item.categoryId}` : item.kind;
    let group = groups.get(key);
    if (!group) {
      group = {key, label, locale: item.categoryLocale, items: []}; groups.set(key, group);
    }
    group.items.push(item);
  }
  return [...groups.values()];
}
