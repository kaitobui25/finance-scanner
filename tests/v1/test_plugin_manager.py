import logging
import pandas as pd
from unittest.mock import Mock
import core.plugin_manager as pm

def test_plugin_manager_run_all_success_and_filter():
    # Mock valid plugin that returns a signal
    plugin_valid = Mock(__name__="plugin_valid")
    plugin_valid.analyze.return_value = {
        "indicator": "TEST",
        "signal": "BULLISH",
    }
    
    # Mock plugin that returns signal=None
    plugin_none = Mock(__name__="plugin_none")
    plugin_none.analyze.return_value = {
        "indicator": "TEST2",
        "signal": None,
    }
    
    # Backup original plugins
    original_plugins = pm._PLUGINS
    pm._PLUGINS = [plugin_valid, plugin_none]
    
    try:
        df = pd.DataFrame()
        results = pm.run_all(df, symbol="AAPL")
        
        # Should only return results with signal != None
        assert len(results) == 1
        assert results[0]["signal"] == "BULLISH"
    finally:
        pm._PLUGINS = original_plugins

def test_plugin_manager_run_all_exception_handling(caplog):
    # Mock plugin that raises an exception
    plugin_error = Mock(__name__="plugin_error")
    plugin_error.analyze.side_effect = ValueError("Some weird error")
    
    # Mock a second valid plugin to ensure it still runs after the erroneous one
    plugin_valid = Mock(__name__="plugin_valid")
    plugin_valid.analyze.return_value = {
        "indicator": "TEST3",
        "signal": "BEARISH",
    }
    
    original_plugins = pm._PLUGINS
    pm._PLUGINS = [plugin_error, plugin_valid]
    
    try:
        with caplog.at_level(logging.ERROR):
            df = pd.DataFrame()
            results = pm.run_all(df, symbol="AAPL")
            
            # The valid plugin should still produce a result
            assert len(results) == 1
            assert results[0]["signal"] == "BEARISH"
            
            # Verify the error was logged
            assert "plugin_error" in caplog.text
            assert "ValueError: Some weird error" in caplog.text
    finally:
        pm._PLUGINS = original_plugins
