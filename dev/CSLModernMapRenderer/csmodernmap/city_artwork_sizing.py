"""Resolve explicit theme pixels once at the artwork boundary."""
from . import pixel_sizes

REFERENCE_EDGE = 4096


def prepare_style(style, document, canvas, *, sizing=None, session=None, element_scale=1.0):
    if sizing is not None:
        raise ValueError('Reference length overrides are retired; use elementScale')
    return pixel_sizes.prepare_style(style, element_scale)
