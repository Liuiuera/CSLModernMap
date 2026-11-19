// 固定世界坐标的区域表面。原始轮廓供绘制和拾取共同使用。
import earcut, {flatten} from 'earcut';
import maplibregl, {type CustomLayerInterface, type CustomRenderMethodInput, type Map as MapLibreMap} from 'maplibre-gl';
import polygonClipping, {type MultiPolygon, type Pair, type Polygon} from 'polygon-clipping';
import {program, rgb} from './surface-distance-layer';

type GL = WebGLRenderingContext | WebGL2RenderingContext;
type AreaFeature = GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon, Record<string, unknown>>;
type AreaPaint = {colors: string[]; opacity: number; border: string; width: number; fade: number};
type Extent = [number, number, number, number];
type Area = {feature: AreaFeature; geometry: MultiPolygon; bounds: Extent; first: number; count: number; palette: number};
type RGBA = [number, number, number, number];

function rgba(value: string): RGBA {
  const hex = value.trim();
  return [...rgb(hex), hex.length === 9 ? parseInt(hex.slice(7), 16)/255 : 1];
}

function bounds(points: Pair[]): Extent {
  const box: Extent = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of points) {
    box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], y);
    box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], y);
  }
  return box;
}

function samePoint(a: Pair, b: Pair): boolean { return a[0] === b[0] && a[1] === b[1]; }

function normal(a: Pair, b: Pair): Pair {
  const x = b[0]-a[0], y = b[1]-a[1], length = Math.hypot(x, y);
  return [-y/length, x/length];
}

function appendBorder(target: number[], points: Pair[]): void {
  const length = points.length;
  const normals = points.map((point, i) => normal(point, points[(i+1)%length]));
  const joins = points.map((_, i) => {
    const before = normals[(i+length-1)%length], after = normals[i];
    const x = before[0]+after[0], y = before[1]+after[1];
    const size = Math.hypot(x, y);
    const divisor = Math.max(x*after[0]+y*after[1], size/2);
    return divisor ? [x/divisor, y/divisor] : after;
  });
  const add = (index: number, sign: number) =>
    target.push(...points[index], joins[index][0]*sign, joins[index][1]*sign);
  for (let i = 0; i < length; ++i) {
    const j = (i+1)%length;
    add(i, 1); add(i, -1); add(j, 1);
    add(j, 1); add(i, -1); add(j, -1);
  }
}

function prepare(features: AreaFeature[]) {
  const fill: number[] = [], border: number[] = [], areas: Area[] = [];
  for (const feature of features) {
    const geometry = feature.geometry.type === 'Polygon'
      ? [feature.geometry.coordinates] as MultiPolygon : feature.geometry.coordinates as MultiPolygon;
    const first = fill.length/4;
    for (const polygon of geometry) {
      const rings = polygon.map(ring => {
        const clean = ring.filter((point, i) => !i || !samePoint(point, ring[i-1]));
        if (clean.length > 1 && samePoint(clean[0], clean.at(-1)!)) clean.pop();
        return clean.map(([lng, lat]): Pair => {
          const point = maplibregl.MercatorCoordinate.fromLngLat([lng, lat]);
          return [point.x-.5, point.y-.5];
        });
      });
      if (!rings[0] || rings[0].length < 3) continue;
      const valid = rings.filter(ring => ring.length >= 3);
      const {vertices, holes} = flatten(valid);
      for (const index of earcut(vertices, holes)) fill.push(vertices[2*index], vertices[2*index+1], 0, 0);
      for (const ring of valid) appendBorder(border, ring);
    }
    if (fill.length/4 > first) areas.push({feature, geometry, bounds: bounds(geometry.flat(2)),
      first, count: fill.length/4-first, palette: Number(feature.properties.paletteIndex ?? 0)});
  }
  const vertices = new Float32Array(fill.length+border.length);
  vertices.set(fill);
  vertices.set(border, fill.length);
  return {vertices, areas, borderFirst: fill.length/4, borderCount: border.length/4};
}

export class AreaSurfaceLayer implements CustomLayerInterface {
  readonly type = 'custom';
  readonly renderingMode = '2d';
  visible = true;
  private data: ReturnType<typeof prepare>;
  private map?: MapLibreMap;
  private gl?: GL;
  private buffer?: WebGLBuffer;
  private draw?: WebGLProgram;
  private position = 0;
  private offset = 0;
  private matrix?: WebGLUniformLocation | null;
  private color?: WebGLUniformLocation | null;
  private halfWidth?: WebGLUniformLocation | null;
  private fillColors: RGBA[] = [];
  private borderColor: RGBA = [0, 0, 0, 1];
  private opacity = 0;
  private width = 0;
  private fade = 1;

  constructor(readonly id: string, features: AreaFeature[], private readonly observationScale: () => number) {
    this.data = prepare(features);
  }

  setFeatures(features: AreaFeature[]): void {
    this.data = prepare(features);
    this.upload();
    this.map?.triggerRepaint();
  }

  updatePaint(paint: AreaPaint): void {
    this.fillColors = paint.colors.map(rgba);
    this.borderColor = rgba(paint.border);
    this.opacity = paint.opacity;
    this.width = paint.width;
    this.fade = paint.fade;
    this.map?.triggerRepaint();
  }

  pick(rectangle: Polygon) {
    if (!this.visible) return null;
    const [x0, y0, x1, y1] = bounds(rectangle[0]);
    for (let i = this.data.areas.length-1; i >= 0; --i) {
      const area = this.data.areas[i], b = area.bounds;
      if (x1 < b[0] || b[2] < x0 || y1 < b[1] || b[3] < y0) continue;
      if (polygonClipping.intersection(area.geometry, rectangle).length) return area.feature;
    }
    return null;
  }

  onAdd(map: MapLibreMap, gl: GL): void {
    this.map = map;
    this.gl = gl;
    this.draw = program(gl, `
      precision highp float;
      attribute vec2 a_position;
      attribute vec2 a_offset;
      uniform mat4 u_matrix;
      uniform float u_half_width;
      void main() {
        gl_Position = u_matrix * vec4(a_position + a_offset * u_half_width, 0.0, 1.0);
      }
    `, `
      precision mediump float;
      uniform vec4 u_color;
      void main() { gl_FragColor = vec4(u_color.rgb * u_color.a, u_color.a); }
    `);
    this.position = gl.getAttribLocation(this.draw, 'a_position');
    this.offset = gl.getAttribLocation(this.draw, 'a_offset');
    this.matrix = gl.getUniformLocation(this.draw, 'u_matrix');
    this.color = gl.getUniformLocation(this.draw, 'u_color');
    this.halfWidth = gl.getUniformLocation(this.draw, 'u_half_width');
    this.buffer = gl.createBuffer() ?? undefined;
    this.upload();
  }

  private upload(): void {
    if (!this.gl || !this.buffer) return;
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffer);
    this.gl.bufferData(this.gl.ARRAY_BUFFER, this.data.vertices, this.gl.STATIC_DRAW);
  }

  render(gl: GL, options: CustomRenderMethodInput): void {
    if (!this.visible || !this.fade || !this.data.areas.length || !this.draw || !this.buffer) return;
    gl.disable(gl.CULL_FACE); gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.draw);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.enableVertexAttribArray(this.position);
    gl.enableVertexAttribArray(this.offset);
    gl.vertexAttribPointer(this.position, 2, gl.FLOAT, false, 16, 0);
    gl.vertexAttribPointer(this.offset, 2, gl.FLOAT, false, 16, 8);

    // 先用双精度移到城市原点，再转为 GPU 矩阵，避免高倍缩放抖动。
    const projection = options.defaultProjectionData.mainMatrix;
    const local = new Float32Array(projection);
    for (let i = 0; i < 4; ++i) local[12+i] = projection[12+i] + .5*(projection[i]+projection[4+i]);
    gl.uniformMatrix4fv(this.matrix ?? null, false, local);

    gl.uniform1f(this.halfWidth ?? null, 0);
    const alpha = this.opacity*this.fade;
    if (alpha && this.fillColors.length) {
      for (const {palette, first, count} of this.data.areas) {
        const [r, g, b, a] = this.fillColors[palette%this.fillColors.length];
        gl.uniform4f(this.color ?? null, r, g, b, a*alpha);
        gl.drawArrays(gl.TRIANGLES, first, count);
      }
    }
    // 和矢量图层一样，所有面都先于边线绘制，重叠处按原要素顺序混合。
    if (this.width) {
      const half = this.width*this.observationScale()/(1024*2**this.map!.getZoom());
      gl.uniform1f(this.halfWidth ?? null, half);
      const [r, g, b, a] = this.borderColor;
      gl.uniform4f(this.color ?? null, r, g, b, a*this.fade);
      gl.drawArrays(gl.TRIANGLES, this.data.borderFirst, this.data.borderCount);
    }
    gl.disableVertexAttribArray(this.position);
    gl.disableVertexAttribArray(this.offset);
  }

  onRemove(_map: MapLibreMap, gl: GL): void {
    if (this.buffer) gl.deleteBuffer(this.buffer);
    if (this.draw) gl.deleteProgram(this.draw);
    this.buffer = undefined; this.draw = undefined; this.gl = undefined; this.map = undefined;
  }
}
