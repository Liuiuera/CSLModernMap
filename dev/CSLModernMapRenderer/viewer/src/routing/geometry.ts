import type {XYZ} from './types';

export const distance = (a: XYZ, b: XYZ): number => Math.hypot(a[0]-b[0], a[1]-b[1], a[2]-b[2]);
export const planarDistance = (a: XYZ, b: XYZ): number => Math.hypot(a[0]-b[0], a[2]-b[2]);
export const geo = (p: XYZ): [number, number] => [-p[2]/111195, p[0]/111195];

export function cumulative(points: XYZ[]): number[] {
  const result = [0];
  for (let i = 1; i < points.length; i++) result.push(result[i-1] + distance(points[i-1], points[i]));
  return result;
}

export function pointAt(points: XYZ[], lengths: number[], fraction: number): XYZ {
  const target = Math.max(0, Math.min(1, fraction)) * lengths.at(-1)!;
  for (let i = 1; i < points.length; i++) {
    const span = lengths[i] - lengths[i-1];
    if (target <= lengths[i] && span > 0) {
      const t = (target - lengths[i-1])/span;
      return points[i-1].map((v, k) => v + t*(points[i][k]-v)) as XYZ;
    }
  }
  return [...points.at(-1)!];
}

export function slice(points: XYZ[], lengths: number[], from: number, to: number): XYZ[] {
  const reverse = from > to;
  const a = Math.min(from, to), b = Math.max(from, to), total = lengths.at(-1)!;
  const result = [pointAt(points, lengths, a),
    ...points.filter((_, i) => lengths[i] > a*total && lengths[i] < b*total),
    pointAt(points, lengths, b)];
  return reverse ? result.reverse() : result;
}
