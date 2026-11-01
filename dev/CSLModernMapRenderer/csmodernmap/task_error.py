"""保留跨线程和工作进程的任务诊断。"""
from __future__ import annotations

import sys
import traceback
from . import __version__, i18n
from .cmm.file_format import MapFileError


class TaskFailure(RuntimeError):
    def __init__(self, diagnostic: dict):
        self.diagnostic = diagnostic
        super().__init__(diagnostic["exceptionMessage"])


def capture_error(error, *, stage="", input_error=False) -> dict:
    if isinstance(error, TaskFailure):
        return dict(error.diagnostic)
    category = "internal"
    if input_error or isinstance(error, MapFileError):
        category = "input"
    elif isinstance(error, PermissionError):
        category = "permission"
    elif isinstance(error, FileNotFoundError):
        category = "missing_file"
    elif isinstance(error, MemoryError):
        category = "memory"
    return {
        "version": __version__, "stage": stage,
        "exceptionType": type(error).__name__ if isinstance(error, BaseException) else "Unavailable",
        "exceptionMessage": str(error), "category": category,
        "traceback": "".join(traceback.format_exception(error))
            if isinstance(error, BaseException) and error.__traceback__ is not None else "Unavailable (no traceback was captured)",
    }


def error_payload(error, *, operation="operation", stage="", **metadata) -> dict:
    diagnostic = capture_error(error, stage=stage)
    operation = diagnostic.setdefault("operation", operation)
    if not diagnostic["stage"]:
        diagnostic["stage"] = stage
    category = diagnostic["category"]
    reason = diagnostic["exceptionMessage"] if category == "input" else i18n.tr("ui.failure." + category)
    title = i18n.tr("ui.failure.operation." + operation) if operation in {
        "metro", "preview", "export", "open", "operation"} else i18n.tr("ui.failure.operation.operation")
    payload = {**diagnostic, **metadata, "message": title + ("。" if i18n.LANG.startswith("zh") else ". ") + reason}
    print("\n".join(f"{key}: {value}" for key, value in payload.items()), file=sys.stderr)
    return payload
