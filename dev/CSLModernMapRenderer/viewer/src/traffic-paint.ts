import type {ExpressionSpecification} from 'maplibre-gl';

const stops: [number, string][] = [[0, '#28B878'], [.3, '#F4AE35'], [.6, '#E5483B'], [.85, '#A72728']];
const missing = '#D9DDDA';
export const missingRouteTrafficColor = '#0078D4';
export const completedRouteColor = '#92989F';

export function trafficColor(value: ExpressionSpecification, missingColor = missing): ExpressionSpecification {
  return ['case', ['>=', value, 0],
    ['step', value, stops[0][1], ...stops.slice(1).flat()] as ExpressionSpecification, missingColor];
}

export function mixedTrafficColor(value: number, neighbor: number, blend: number, missingColor = missing): string {
  const rgb = (congestion: number) => {
    const hex = stops.filter(([threshold]) => congestion >= threshold).at(-1)?.[1] ?? missingColor;
    return [1, 3, 5].map(start => parseInt(hex.slice(start, start+2), 16));
  };
  const a = rgb(value), b = rgb(neighbor);
  return `rgb(${a.map((channel, i) => Math.round(channel+(b[i]-channel)*blend)).join(',')})`;
}
