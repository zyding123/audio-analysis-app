"""End-to-end checks for the installed bundle; all user data stays in a temporary directory."""
import json
import tempfile
import wave
from pathlib import Path


class MemoryConfig:
    def __init__(self, module):
        self.values = dict(module.AppConfig.DEFAULTS)
        self.values[module.AppConfig.KEY_THEME] = module.THEME_DARK
    def get(self, key):
        return self.values[key]
    def set(self, key, value):
        self.values[key] = value


def run(module):
    import numpy as np
    import analyzer
    from charts import generate_charts
    from PySide6.QtWidgets import QApplication
    module._log = lambda *args: None
    with tempfile.TemporaryDirectory(prefix='audioanalysis-selftest-') as folder:
        root = Path(folder)
        t = np.arange(48000 * 3) / 48000
        tone = .1 * np.sin(2 * np.pi * 1000 * t)
        x = np.column_stack((tone, -tone))
        path = root / 'antiphase.wav'
        with wave.open(str(path), 'wb') as f:
            f.setnchannels(2); f.setsampwidth(2); f.setframerate(48000)
            f.writeframes((x * 32767).astype('<i2').tobytes())
        result = analyzer.analyze_file(str(path))
        assert not result.error, result.error
        assert 980 < result.centroid_power_hz < 1020, result.to_dict()
        assert result.bpm is None, result.to_dict()
        charts = generate_charts(str(path), str(root / 'charts'), 'antiphase.wav', lang='en')
        assert all(Path(p).stat().st_size > 1000 for p in charts.values())
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        win = module.MainWindow(MemoryConfig(module), data_dir=str(root / 'data'))
        win._theme_timer.stop()
        win._on_result(result, win._batch_id)
        win._write_json(str(root / 'results.json'), [result])
        win._write_csv(str(root / 'results.csv'), [result])
        assert json.loads((root / 'results.json').read_text())[0]['analysis_version'] == module.APP_VERSION
        win.shutdown()
        print(f'Audio Analysis v{module.APP_VERSION}: bundle self-test PASS', flush=True)
    return 0
