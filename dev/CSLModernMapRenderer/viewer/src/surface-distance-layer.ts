// 使用距离场绘制预览地图的表面图层。
import { i18n } from './i18n';
import * as maplibregl from 'maplibre-gl';
import {
  type CustomLayerInterface,
  type CustomRenderMethodInput,
  type Map as MapLibreMap,
} from 'maplibre-gl';

export type SurfaceDistanceSpec = {
  zero: number;
  stepMeters: number;
  worldBounds: [number, number, number, number];
};

type SurfacePaint = {
  color: string;
  opacity: number;
  bufferMeters: number;
  edgeMeters: number;
};

function shader(gl: WebGLRenderingContext | WebGL2RenderingContext,
                type: number, source: string): WebGLShader {
  const result = gl.createShader(type);
  if (!result) throw new Error(i18n.t("surface-distance-layer_shader_7f59b1d267"));
  gl.shaderSource(result, source);
  gl.compileShader(result);
  if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(result) ?? i18n.t("surface-distance-layer_shader_9bd78d521b");
    gl.deleteShader(result);
    throw new Error(message);
  }
  return result;
}

function program(gl: WebGLRenderingContext | WebGL2RenderingContext,
                 vertex: string, fragment: string): WebGLProgram {
  const result = gl.createProgram();
  if (!result) throw new Error(i18n.t("surface-distance-layer_program_efe4a34f88"));
  const vs = shader(gl, gl.VERTEX_SHADER, vertex);
  const fs = shader(gl, gl.FRAGMENT_SHADER, fragment);
  gl.attachShader(result, vs); gl.attachShader(result, fs); gl.linkProgram(result);
  gl.deleteShader(vs); gl.deleteShader(fs);
  if (!gl.getProgramParameter(result, gl.LINK_STATUS)) {
    const message = gl.getProgramInfoLog(result) ?? i18n.t("surface-distance-layer_program_ebc193f5d6");
    gl.deleteProgram(result); throw new Error(message);
  }
  return result;
}

function rgb(value: string): [number, number, number] {
  const text = String(value || '#F1F0EB').replace('#', '');
  const full = text.length === 3 ? [...text].map(ch => ch + ch).join('') : text;
  return [
    parseInt(full.slice(0, 2), 16) / 255,
    parseInt(full.slice(2, 4), 16) / 255,
    parseInt(full.slice(4, 6), 16) / 255,
  ];
}

/* MapLibre 自定义图层使用与 CPU 相同的有符号距离平滑规则。 */
export class SurfaceDistanceLayer implements CustomLayerInterface {
  readonly type = 'custom' as const;
  readonly renderingMode = '2d' as const;
  private map?: MapLibreMap;
  private glProgram?: WebGLProgram;
  private buffer?: WebGLBuffer;
  private texture?: WebGLTexture;
  private paint: SurfacePaint;
  private fade = 1;
  private readonly imageWidth: number;
  private readonly imageHeight: number;
  private readonly sourceCanvas: HTMLCanvasElement;

  constructor(
    readonly id: string,
    image: ImageBitmap,
    private readonly spec: SurfaceDistanceSpec,
    paint: SurfacePaint,
  ) {
    this.paint = paint;
    this.imageWidth = image.width;
    this.imageHeight = image.height;
    this.sourceCanvas = document.createElement('canvas');
    this.sourceCanvas.width = image.width; this.sourceCanvas.height = image.height;
    this.sourceCanvas.getContext('2d')!.drawImage(image, 0, 0);
  }

  updatePaint(paint: Partial<SurfacePaint>): void {
    this.paint = {...this.paint, ...paint}; this.map?.triggerRepaint();
  }

  setFade(value: number): void {
    this.fade = Math.max(0, Math.min(1, value)); this.map?.triggerRepaint();
  }

  onAdd(map: MapLibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext): void {
    this.map = map;
    this.glProgram = program(gl, `
      precision highp float;
      attribute vec2 a_position;
      attribute vec2 a_uv;
      uniform mat4 u_matrix;
      varying vec2 v_uv;
      void main() { gl_Position = u_matrix * vec4(a_position, 0.0, 1.0); v_uv = a_uv; }
    `, `
      precision highp float;
      uniform sampler2D u_field;
      uniform vec3 u_color;
      uniform float u_opacity;
      uniform float u_zero;
      uniform float u_step;
      uniform float u_buffer;
      uniform float u_edge;
      uniform vec2 u_tex_size;
      varying vec2 v_uv;
      float fieldAt(vec2 uv) {
        vec2 bytes = texture2D(u_field, clamp(uv, vec2(0.0), vec2(1.0))).rg * 255.0;
        return (bytes.x * 256.0 + bytes.y - u_zero) * u_step;
      }
      void main() {
        vec2 pixel = v_uv * u_tex_size - 0.5;
        vec2 base = floor(pixel);
        vec2 fraction = fract(pixel);
        vec2 uv00 = (base + 0.5) / u_tex_size;
        vec2 one = 1.0 / u_tex_size;
        float distanceMeters = mix(
          mix(fieldAt(uv00), fieldAt(uv00 + vec2(one.x, 0.0)), fraction.x),
          mix(fieldAt(uv00 + vec2(0.0, one.y)), fieldAt(uv00 + one), fraction.x),
          fraction.y);
        float coverage = u_edge <= 0.0001
          ? step(distanceMeters, u_buffer)
          : 1.0 - smoothstep(u_buffer - u_edge, u_buffer + u_edge, distanceMeters);
        float alpha = coverage * u_opacity;
        gl_FragColor = vec4(u_color * alpha, alpha);
      }
    `);
    const [minX, minZ, maxX, maxZ] = this.spec.worldBounds;
    const points = [
      [minX, minZ, 0, 0], [maxX, minZ, 1, 0],
      [minX, maxZ, 0, 1], [maxX, maxZ, 1, 1],
    ].flatMap(([x, z, u, v]) => {
      const p = maplibregl.MercatorCoordinate.fromLngLat({lng: -z / 111195, lat: x / 111195});
      return [p.x, p.y, u, v];
    });
    this.buffer = gl.createBuffer() ?? undefined;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer ?? null);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(points), gl.STATIC_DRAW);
    this.texture = gl.createTexture() ?? undefined;
    gl.bindTexture(gl.TEXTURE_2D, this.texture ?? null);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0);
    // 红绿通道编码单个十六位整数；着色器手动双线性插值可避免硬件线性采样的字节进位伪影。
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA,
      gl.UNSIGNED_BYTE, this.sourceCanvas);
  }

  render(gl: WebGLRenderingContext | WebGL2RenderingContext,
         options: CustomRenderMethodInput): void {
    if (!this.glProgram || !this.buffer || !this.texture) return;
    // 二维地表按绘制顺序合成；显式图形状态避免 WebView2 残留深度或裁剪状态抑制整屏矩形。
    gl.disable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE); gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.glProgram);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    const position = gl.getAttribLocation(this.glProgram, 'a_position');
    const uv = gl.getAttribLocation(this.glProgram, 'a_uv');
    gl.enableVertexAttribArray(position); gl.enableVertexAttribArray(uv);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 16, 0);
    gl.vertexAttribPointer(uv, 2, gl.FLOAT, false, 16, 8);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.glProgram, 'u_matrix'), false,
      options.defaultProjectionData.mainMatrix as Float32Array);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.uniform1i(gl.getUniformLocation(this.glProgram, 'u_field'), 0);
    gl.uniform3fv(gl.getUniformLocation(this.glProgram, 'u_color'), rgb(this.paint.color));
    gl.uniform1f(gl.getUniformLocation(this.glProgram, 'u_opacity'),
      this.paint.opacity * this.fade);
    gl.uniform1f(gl.getUniformLocation(this.glProgram, 'u_zero'), this.spec.zero);
    gl.uniform1f(gl.getUniformLocation(this.glProgram, 'u_step'), this.spec.stepMeters);
    gl.uniform1f(gl.getUniformLocation(this.glProgram, 'u_buffer'), this.paint.bufferMeters);
    gl.uniform1f(gl.getUniformLocation(this.glProgram, 'u_edge'), this.paint.edgeMeters);
    gl.uniform2f(gl.getUniformLocation(this.glProgram, 'u_tex_size'),
      this.imageWidth, this.imageHeight);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  onRemove(_map: MapLibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext): void {
    if (this.texture) gl.deleteTexture(this.texture);
    if (this.buffer) gl.deleteBuffer(this.buffer);
    if (this.glProgram) gl.deleteProgram(this.glProgram);
    this.texture = undefined; this.buffer = undefined; this.glProgram = undefined;
    this.map = undefined;
  }
}
