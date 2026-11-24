import type {Map as MapLibreMap, GeoJSONSourceSpecification, LayerSpecification} from 'maplibre-gl';

export type PreviewQuality = {
  pixelScale: number;
  lineDetail: 'standard' | 'fine';
  polygonDetail: 'standard' | 'fine';
  textPercent: number;
  linePercent: number;
  iconPercent: number;
};

export const DEFAULT_PREVIEW_QUALITY: PreviewQuality = {
  pixelScale: 1, lineDetail: 'standard', polygonDetail: 'standard', textPercent: 100, linePercent: 100, iconPercent: 100,
};

export function htmlPreviewQuality(preset: string): PreviewQuality {
  switch (preset) {
    case 'smooth': return {...DEFAULT_PREVIEW_QUALITY, pixelScale: .75};
    case 'standard': return {...DEFAULT_PREVIEW_QUALITY};
    case 'fine': return {...DEFAULT_PREVIEW_QUALITY, pixelScale: 1.25, lineDetail: 'fine', polygonDetail: 'fine'};
    default: throw new Error('Invalid HTML quality');
  }
}

export function readPreviewQuality(value: unknown): PreviewQuality {
  if (!value || typeof value !== 'object') throw new Error('Invalid preview quality settings');
  const {pixelScale, lineDetail, polygonDetail, textPercent = 100, linePercent = 100, iconPercent = 100} = value as PreviewQuality;
  if (![.75, 1, 1.25].includes(pixelScale) ||
      !['standard', 'fine'].includes(lineDetail) || !['standard', 'fine'].includes(polygonDetail) ||
      ![textPercent, linePercent, iconPercent].every(n => Number.isInteger(n) && n >= 50 && n <= 200))
    throw new Error('Invalid preview quality settings');
  return {pixelScale, lineDetail, polygonDetail, textPercent, linePercent, iconPercent};
}

export function previewTolerance(key: string, quality: PreviewQuality): number {
  if (['roads', 'labels', 'railway'].includes(key)) return quality.lineDetail === 'fine' ? .375 : 1;
  if (['buildings', 'zones'].includes(key)) return quality.polygonDetail === 'fine' ? .125 : .375;
  return 0;
}

// Recreate only the affected sources and their layers through public APIs.
// The original GeoJSON, camera, layer order, and visibility remain authoritative.
export function applyPreviewQuality(map: MapLibreMap, previous: PreviewQuality, next: PreviewQuality): void {
  const style = map.getStyle();
  const sources = ['roads', 'labels', 'railway', 'buildings', 'zones']
    .filter(id => map.getSource(id) && previewTolerance(id, previous) !== previewTolerance(id, next));
  if (sources.length) {
    const layers = style.layers.filter(layer => 'source' in layer && sources.includes(String(layer.source)));
    for (const layer of layers) map.removeLayer(layer.id);
    for (const id of sources) {
      const source = style.sources[id] as GeoJSONSourceSpecification;
      map.removeSource(id);
      map.addSource(id, {...source, tolerance: previewTolerance(id, next)});
    }
    for (const layer of layers) {
      const index = style.layers.findIndex(item => item.id === layer.id);
      const before = style.layers.slice(index + 1).find(item => map.getLayer(item.id));
      map.addLayer(layer as LayerSpecification, before?.id);
    }
  }
  map.setPixelRatio(window.devicePixelRatio * next.pixelScale);
}
