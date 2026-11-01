import type {ExpressionSpecification} from 'maplibre-gl';

type PointFeature = GeoJSON.Feature<GeoJSON.Point, Record<string, any>>;

export function stationAnchors(features: PointFeature[], stations: PointFeature[], visible: boolean): PointFeature[] {
  const byStop = new Map<number, PointFeature>();
  if (visible) for (const station of stations)
    for (const id of station.properties.memberStopIds ?? []) byStop.set(Number(id), station);
  return features.map(feature => {
    const station = feature.properties.sourceKind === 'stop'
      ? byStop.get(Number(feature.properties.sourceId)) : undefined;
    if (!station) return feature;
    return {...feature, properties: {...feature.properties, name: station.properties.name, stationAnchor: true},
      geometry: station.geometry};
  });
}

export function stationFacilityOffset(iconSize: number, stationSize: number): [number, number] {
  if (iconSize <= 0) return [0, 0];
  const stationRadius = Math.max(3, stationSize / 2) + 1.5, iconRadius = iconSize / 2;
  const distance = stationRadius + iconRadius + 3;
  return [distance, -distance];
}

export function stationIconOffset(iconSize: number, stationSize: number): ExpressionSpecification {
  const offset = stationFacilityOffset(iconSize, stationSize);
  // icon-offset 会乘 icon-size；这里将屏幕像素换回 24px 图集的单位。
  return ['case', ['==', ['get', 'stationAnchor'], true],
    ['literal', iconSize > 0 ? offset.map(value => value * 24 / iconSize) : offset], ['literal', [0, 0]]];
}
