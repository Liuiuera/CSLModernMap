"""定义 CMM 会话与外部编辑器导出器之间的不可变数据边界。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..cmm.session import DocumentSession


@dataclass(frozen=True)
class ExternalExportRequest:
    output_root: Path
    theme_id: str = ""
    theme: Mapping[str, Any] = field(default_factory=dict)
    visibility: Mapping[str, bool] = field(default_factory=dict)
    line_ids: tuple[int, ...] | None = None
    render_ui: Mapping[str, Any] | None = None
    render_job: Mapping[str, Any] | None = None
    rmp_scene: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_root", Path(self.output_root))
        if self.line_ids is not None:
            object.__setattr__(self, "line_ids", tuple(int(value) for value in self.line_ids))


@dataclass(frozen=True)
class ExternalExportResult:
    directory: Path
    files: tuple[Path, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportSnapshot:
    """为两个导出器提供同一站点身份和源文档。"""
    session: DocumentSession
    transit: Any

    @classmethod
    def capture(cls, session: DocumentSession) -> "ExportSnapshot":
        return cls(session=session, transit=session.transit_model())

    @property
    def document(self):
        return self.session.document

    @property
    def stations(self):
        return self.transit.stations

    @property
    def stop_station(self) -> dict[int, str]:
        return {stop_id: str(station.id) for station in self.stations
                for stop_id in station.member_stop_ids}

    @property
    def city_name(self) -> str:
        origin = self.document.metadata.origin
        return str(origin.source_name or "City")

    @property
    def source_game(self) -> str:
        return str(self.document.metadata.origin.game or "")


def safe_name(name: str) -> str:
    value = "".join("_" if char in '<>:"/\\|?*' or ord(char) < 32 else char
                    for char in str(name)).strip(" .")
    return value or "City"


def unique_directory(root: Path, stem: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    base = root / safe_name(stem)
    target = base
    suffix = 2
    while target.exists():
        target = root / f"{base.name}-{suffix}"
        suffix += 1
    target.mkdir()
    return target
