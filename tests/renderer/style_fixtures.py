"""Explicit minimal pixel themes for tests that render only selected layers."""
from csmodernmap._paths import resource_dir
from csmodernmap.style import Style, load_style
from csmodernmap.theme_contract import field_default


def pixel_style(data):
    return Style({"size_contract": "pixel-v2", "roads": field_default(("roads",))}).merged(data)


def complete_style(overrides):
    """Use the shipped theme when a test compiles a complete preview scene."""
    return load_style(str(resource_dir('styles') / 'modern.yaml')).merged(overrides)
