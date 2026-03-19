import importlib.util
import traceback
import logging
import os
from glob import glob
from typing import List

import pandas as pd

from indicators.base import IndicatorResult

log = logging.getLogger("plugin_manager")

INDICATORS_DIR = os.path.join(os.path.dirname(__file__), "..", "indicators")


def _load_plugins() -> list:
    """
    Auto-load tất cả *.py trong indicators/, loại trừ base.py và __init__.py.
    Sort alphabetical → deterministic load order (cross-platform).
    Returns list of loaded modules.
    """
    pattern = os.path.join(INDICATORS_DIR, "*.py")
    paths   = sorted(glob(pattern))  # alphabetical, cross-platform

    plugins = []
    for path in paths:
        filename = os.path.basename(path)
        if filename in ("base.py", "__init__.py"):
            continue

        module_name = f"indicators.{filename[:-3]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                log.error(f"plugin {filename} invalid spec — skipped")
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "analyze"):
                log.warning(f"plugin {filename} missing analyze() — skipped")
                continue

            plugins.append(module)
            log.debug(f"plugin loaded: {filename}")

        except Exception as e:
            log.error(
                f"plugin {filename} failed to load: {e}\n"
                f"{traceback.format_exc()}"
            )

    return plugins


# Load một lần lúc import — deterministic, không reload mỗi symbol
_PLUGINS = _load_plugins()


def run_all(
    df        : pd.DataFrame,
    symbol    : str,
    timeframe : str = "1MO",
) -> List[IndicatorResult]:
    """
    Chạy tất cả plugin đã load với df/symbol/timeframe.

    - Mỗi plugin wrap trong try/except — 1 plugin lỗi không crash symbol
    - Chỉ trả về result có signal (không None)
    - Log ERROR nếu plugin raise exception

    Returns:
        List[IndicatorResult] — chỉ các result có signal != None
    """
    results = []

    for plugin in _PLUGINS:
        plugin_name = getattr(plugin, "__name__", str(plugin))
        try:
            result = plugin.analyze(df=df, symbol=symbol, timeframe=timeframe)

            if not isinstance(result, dict):
                log.error(
                    f"plugin {plugin_name} / {symbol}: "
                    f"invalid return type {type(result).__name__} (expected dict)"
                )
                continue

            if result.get("signal") is not None:
                results.append(result)

        except Exception as e:
            log.error(f"plugin {plugin_name} / {symbol}: {type(e).__name__}: {e}")

    return results
