export type Point = [number, number];
export type Box = [number, number, number, number];
export type Candidate<T> = [number, number, string, T];
export type Placed = [number, number, Box, string[]];

export function candidate_points(path: Point[], step: number, limit: number,
  width: number, height: number): Point[];
export function group_candidates<T>(candidates: Candidate<T>[], distance: number):
  [number, number, string[], T[]][];
export function obstacle_score(box: Box, obstacles: Point[], canvas: Point, padding: number):
  [number, number, number];
export function placement_reason(x: number, y: number, box: Box, keys: string[], minGap: number,
  repeatInterval: number, maximum: number, placed: Placed[], counts: Record<string, number>,
  blocked: (box: Box) => boolean): string;
export function rank_candidates<T>(items: [number, number, number, number, T][]): void;
