"""在规范文字坐标中采样已排好的条带，不重新测量或排版。"""
import math

import numpy as np
from PIL import Image


def sample_path(run, strips, bounds, text_height, pad, density=4):
    left, top, right, bottom = bounds
    width = max(1, math.ceil((right-left)*density/4))
    height = max(1, math.ceil((bottom-top)*density/4))
    xx, yy = np.meshgrid(left+(np.arange(width)+.5)*(right-left)/width-.5,
                        top+(np.arange(height)+.5)*(bottom-top)/height-.5)
    best = np.full(xx.shape, np.inf)
    u, v = np.zeros(xx.shape), np.zeros(xx.shape)
    for x, y, angle, offset, span in strips:
        dx, dy = xx-x, yy-y
        distance = dx*dx+dy*dy
        chosen = distance < best
        a = np.radians(angle)
        u[chosen] = offset+span/2 + dx[chosen]*np.cos(a)+dy[chosen]*np.sin(a)
        v[chosen] = text_height/2 - dx[chosen]*np.sin(a)+dy[chosen]*np.cos(a)
        best[chosen] = distance[chosen]
    u += pad
    v += pad
    tw, th = run.size
    valid = (u >= 0) & (v >= 0) & (u < tw-1) & (v < th-1)
    sx, sy = np.clip(u, 0, tw-1), np.clip(v, 0, th-1)
    ix, iy = sx.astype(int), sy.astype(int)
    fx, fy = (sx-ix)[..., None], (sy-iy)[..., None]
    pixels = np.asarray(run, dtype=float)
    pixels[..., :3] *= pixels[..., 3:4]/255
    jx, jy = np.minimum(ix+1, tw-1), np.minimum(iy+1, th-1)
    out = ((pixels[iy, ix]*(1-fx)+pixels[iy, jx]*fx)*(1-fy)
           +(pixels[jy, ix]*(1-fx)+pixels[jy, jx]*fx)*fy)
    alpha = out[..., 3:4]
    out[..., :3] = np.divide(out[..., :3]*255, alpha,
                             out=np.zeros_like(out[..., :3]), where=alpha > 0)
    out[~valid] = 0
    return Image.fromarray(np.uint8(np.clip(out, 0, 255)))
