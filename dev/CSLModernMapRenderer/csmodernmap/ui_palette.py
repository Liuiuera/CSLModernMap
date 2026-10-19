"""Shared light and dark colors for WinUI resources."""

from __future__ import annotations

#: 软件图标的深蓝阴影色；用于白底文字、细图标和焦点。
PRIMARY = "#2F7FAF"
PRIMARY_DARK = "#25688F"
#: 软件图标的主底色；用于进度、基线和小面积强调。
ACCENT = "#69B7E6"

#: 冷白 / 石墨青灰中性阶。
RIBBON_BG = "#F7FAFC"
SURFACE = "#FFFFFF"
# 功能区染色只在不透明的客户端背景上合成一次。
RIBBON_MATERIAL = "rgba(247, 250, 252, 185)"
TEXT = "#25313A"
TEXT_MUTED = "#65737E"
DEFAULT_ICON = "#405B6B"
SEPARATOR = "#DCE5EB"
CONTROL_BORDER = "#C7D3DC"

#: 交互状态。
#: 注意 ``SELECTED_BG`` 现在**不再代表按钮的「已开启」态**（那是 ``TOGGLE_*``），
#: 只用于按下态、文本选中高亮、画廊选中项这些"淡青底"场合。
HOVER_BG = "#EDF7FC"
HOVER_BORDER = "#B6DDF2"
SELECTED_BG = "#DCEFFA"
SELECTED_BORDER = "#69B7E6"
PRESSED_BG = "#C7E5F5"
FOCUS_BORDER = PRIMARY

#: 按钮的「已开启」态，大按钮与小按钮共用。比普通悬停再深一阶：
#: 大按钮的图标本身是浅蓝圆角块（PRIMARY_TILE），选中底需有足够色差
#: → 只剩一圈薄边，看着像"半透明渐变"。浅蓝底上必须配深蓝文字，白字会糊。
TOGGLE_BG = "#D5EAF7"
TOGGLE_HOVER_BG = "#C4E1F2"
TOGGLE_PRESSED_BG = "#AFD5EB"
TOGGLE_BORDER = "#69B7E6"
TOGGLE_TEXT = "#245F84"

#: 图标色块与危险色。
PRIMARY_TILE = "#E1F2FB"
NEUTRAL_TILE = "#E8EEF2"
#: 语义色均选用可在白底上清晰显示的中深色，底色为同色极浅阶。
OUTPUT = "#18794E"
OUTPUT_TILE = "#E2F3E8"
CREATE = "#946200"
CREATE_TILE = "#F7EDCF"
SHARE = "#5262A3"
SHARE_TILE = "#EAECF7"
TOOL = "#42627A"
TOOL_TILE = "#E6EDF2"
DANGER = "#C34A42"
DANGER_TILE = "#F8E3E1"

#: 面板标题与数字步进器箭头使用可读的图标深蓝。
PANEL_TITLE = PRIMARY

#: 上下文标签仍需区分，但限制在蓝色族。
CONTEXT_TRANSIT = PRIMARY
CONTEXT_METRO = "#5262A3"
CONTEXT_TITLE_TEXT = "#FFFFFF"

#: 语义域 → (图标/前景色, 色块底色)。
#: 大按钮用色块底区分（22px 的小按钮放不下色块，只靠前景色）。
DOMAIN_COLORS: dict[str, tuple[str, str]] = {
    "accent": (PRIMARY, PRIMARY_TILE),       # 地图、图层、视图
    "output": (OUTPUT, OUTPUT_TILE),         # 导出、保存
    "create": (CREATE, CREATE_TILE),         # 快速创作、城市展示
    "share": (SHARE, SHARE_TILE),            # 复制、网页地图
    "tool": (TOOL, TOOL_TILE),               # 设置、帮助、诊断
    "danger": (DANGER, DANGER_TILE),         # 清理类破坏性操作
    "neutral": (DEFAULT_ICON, NEUTRAL_TILE),
}

DEFAULT_DOMAIN = "neutral"


def domain_color(domain: str) -> tuple[str, str]:
    """返回 ``(前景色, 色块底色)``；未知域回退 neutral。"""
    return DOMAIN_COLORS.get(str(domain or DEFAULT_DOMAIN),
                             DOMAIN_COLORS[DEFAULT_DOMAIN])


def domain_foreground(domain: str) -> str:
    """只要前景色——小按钮（无底色）用它。"""
    return domain_color(domain)[0]



from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class ThemeTokens:
    mode: str
    colors: Mapping[str, str]

    def __getattr__(self, name: str) -> str:
        try:
            return self.colors[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def domain_color(self, domain: str) -> tuple[str, str]:
        names = {
            "accent": ("PRIMARY", "PRIMARY_TILE"),
            "output": ("OUTPUT", "OUTPUT_TILE"),
            "create": ("CREATE", "CREATE_TILE"),
            "share": ("SHARE", "SHARE_TILE"),
            "tool": ("TOOL", "TOOL_TILE"),
            "danger": ("DANGER", "DANGER_TILE"),
            "neutral": ("DEFAULT_ICON", "NEUTRAL_TILE"),
        }
        fg, bg = names.get(domain, names["neutral"])
        return self.colors[fg], self.colors[bg]

    def domain_foreground(self, domain: str) -> str:
        return self.domain_color(domain)[0]


_light = {name: value for name, value in list(globals().items())
          if name.isupper() and isinstance(value, str) and value.startswith(("#", "rgba"))}
_light.update(
    WINDOW="#e9eef3", PANEL="#f7f9fb", RAISED="#ffffff", INPUT="#ffffff",
    ALT="#eef2f6", DISABLED="#9aa4af", PLACEHOLDER="#8a97a5",
    SCROLL="#9daab6", ERROR_BG="#fdecec", ERROR_BORDER="#e8a9a9",
    TOOLTIP="#ffffff", TOOLTIP_TEXT="#25313A", SELECTED_TEXT="#25313A",
    APP_BUTTON_TEXT="#ffffff", APP_BUTTON_BG=PRIMARY,
    APP_BUTTON_HOVER=ACCENT, APP_BUTTON_PRESSED=PRIMARY_DARK,
    INNER_PANEL="rgba(247, 249, 251, 170)",
    INNER_TITLE="rgba(238, 242, 246, 180)",
    INNER_CARD="rgba(255, 255, 255, 225)",
    INNER_BORDER="rgba(219, 226, 234, 225)",
    INNER_STATUS="rgba(241, 244, 247, 185)",
)
LIGHT = ThemeTokens("light", MappingProxyType(_light))
_dark = dict(_light)
_dark.update(
    WINDOW="#171C22", PANEL="#202730", SURFACE="#202730", RIBBON_BG="#202730",
    RAISED="#2A3440", INPUT="#192129", ALT="#252E38",
    TEXT="#E8EDF3", TEXT_MUTED="#ADB9C6", DISABLED="#788592", PLACEHOLDER="#98A6B5",
    DEFAULT_ICON="#BED0DF", SEPARATOR="#35414E", CONTROL_BORDER="#465565",
    PRIMARY="#78C4F0", PRIMARY_DARK="#57ACDF", ACCENT="#78C4F0",
    HOVER_BG="#303E4C", HOVER_BORDER="#62859D", SELECTED_BG="#234D69",
    SELECTED_BORDER="#78C4F0", SELECTED_TEXT="#EAF6FF", PRESSED_BG="#3A5165",
    FOCUS_BORDER="#78C4F0", TOGGLE_BG="#234D69", TOGGLE_HOVER_BG="#2D607F",
    TOGGLE_PRESSED_BG="#3A708F", TOGGLE_BORDER="#78C4F0", TOGGLE_TEXT="#EAF6FF",
    PRIMARY_TILE="#263E50", NEUTRAL_TILE="#35414E", OUTPUT="#79D8A7",
    OUTPUT_TILE="#244438", CREATE="#E7C479", CREATE_TILE="#493E28",
    SHARE="#AFB9F0", SHARE_TILE="#363D59", TOOL="#A4C5DD", TOOL_TILE="#304351",
    DANGER="#FFADA5", DANGER_TILE="#513332", PANEL_TITLE="#78C4F0",
    CONTEXT_TRANSIT="#78C4F0", CONTEXT_METRO="#AFB9F0", CONTEXT_TITLE_TEXT="#152B3A",
    SCROLL="#718497",
    ERROR_BG="#513332", ERROR_BORDER="#C57E77", TOOLTIP="#2A3440", TOOLTIP_TEXT="#E8EDF3",
    APP_BUTTON_TEXT="#152B3A", APP_BUTTON_BG="#78C4F0",
    APP_BUTTON_HOVER="#9CD6F6", APP_BUTTON_PRESSED="#57ACDF",
    RIBBON_MATERIAL="#202730", INNER_PANEL="#202730", INNER_TITLE="#252E38",
    INNER_CARD="#202730", INNER_BORDER="#465565", INNER_STATUS="#202730",
)
DARK = ThemeTokens("dark", MappingProxyType(_dark))


def tokens_for(mode: str) -> ThemeTokens:
    return DARK if mode == "dark" else LIGHT
