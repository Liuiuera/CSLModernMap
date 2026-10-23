# -*- coding: utf-8 -*-
"""硬件配置识别：按可用内存 / CPU 核数输出低/中/高配置档与推荐默认值。

低配专项#4（6.5.0）：低配机器默认关闭 2× 抗锯齿、平滑降档、开启低内存
自动分块，避免"默认路径直接撞峰值"；档位推荐仅为初始默认值，
用户在界面仍可随时手动覆盖（现有控件即覆盖入口）。
"""

import os
import sys

# 档位阈值（可用内存 GB / 逻辑核心数），满足任一即降档
_LOW_MEM_GB = 8.0    # 可用内存 < 8GB 或核数 ≤ 4 → 低配
_MID_MEM_GB = 16.0   # 可用内存 < 16GB 或核数 ≤ 8 → 中配
_LOW_CORES = 4
_MID_CORES = 8

# 各档推荐默认值（与 GUI 2.0 控件选项一一对应）
TIER_RECOMMEND = {
    "低": {
        "aa": False,           # 关闭 2× 抗锯齿（最省 CPU/内存）
        "memfriendly": True,   # 低内存模式（超大图自动分块）
    },
    "中": {
        "aa": True,
        "memfriendly": False,
    },
    "高": {
        "aa": True,
        "memfriendly": False,
    },
}


def _memory_gb(available: bool = False) -> float | None:
    """在不强制依赖 psutil 的情况下读取物理内存容量。"""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return (st.ullAvailPhys if available else st.ullTotalPhys) / (1024 ** 3)
        try:
            import psutil  # noqa: PLC0415  可选依赖，非 Windows 兜底
            memory = psutil.virtual_memory()
            return (memory.available if available else memory.total) / (1024 ** 3)
        except Exception:
            pass
    except Exception:
        pass
    return None


def total_memory_gb() -> float | None:
    """读取物理内存总量，无法检测时返回 None。"""
    return _memory_gb()


def available_memory_gb() -> float | None:
    """读取当前可用内存，无法检测时由调用方串行处理。"""
    return _memory_gb(available=True)


def cpu_cores() -> int:
    """逻辑核心数；检测失败回退 4。"""
    return os.cpu_count() or 4


def detect_tier() -> dict:
    """检测配置档并返回推荐默认值。

    返回: {
        "tier": "低" | "中" | "高",
        "mem_gb": float | None,   # None = 内存检测失败
        "cores": int,
        "recommend": {aa, memfriendly, water_smooth},
    }
    """
    mem_gb = total_memory_gb()
    cores = cpu_cores()
    if (mem_gb is not None and mem_gb < _LOW_MEM_GB) or cores <= _LOW_CORES:
        tier = "低"
    elif (mem_gb is not None and mem_gb < _MID_MEM_GB) or cores <= _MID_CORES:
        tier = "中"
    else:
        tier = "高"
    return {
        "tier": tier,
        "mem_gb": mem_gb,
        "cores": cores,
        "recommend": dict(TIER_RECOMMEND[tier]),
    }


def automatic_antialias() -> bool:
    """沿用现有硬件分档规则，而不是保存的用户选项。"""
    return bool(detect_tier()["recommend"]["aa"])
