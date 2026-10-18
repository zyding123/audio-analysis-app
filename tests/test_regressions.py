import csv
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import analyzer as a
import charts


def wav(path, data, sr=48000):
    x = np.asarray(data)
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1 if x.ndim == 1 else x.shape[1])
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())


def sine(hz=1000, seconds=1, amplitude=.1, sr=48000):
    return amplitude * np.sin(2 * np.pi * hz * np.arange(int(seconds * sr)) / sr)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.wav'

    def test_near_fullscale_sines_are_not_flat_plateaus(self):
        for hz in (50, 100, 1000):
            with self.subTest(hz=hz):
                count, _, ranges = a.detect_clipping(sine(hz, amplitude=.9995), 48000)
                self.assertEqual(count, 0)
                self.assertEqual(ranges, [])

    def test_flat_plateau_crossing_block_boundary(self):
        x = np.zeros(65540)
        x[65535:65538] = 1
        count, _, ranges = a.detect_clipping(x, 48000)
        self.assertEqual(count, 3)
        self.assertAlmostEqual(ranges[0].end, 65538 / 48000)

    def test_plateau_is_per_channel(self):
        clean = sine()
        clipped = np.clip(1.5 * sine(amplitude=1), -1, 1)
        count, _, ranges = a.detect_clipping(np.column_stack((clean, clipped)), 48000)
        self.assertGreater(count, 0)
        self.assertEqual({r.channel for r in ranges}, {2})

    def test_isolated_overrange_sample_is_reported(self):
        x = np.zeros(100)
        x[50] = 1.2
        count, _, _ = a.detect_clipping(x, 48000)
        self.assertEqual(count, 1)

    def test_channel_phase_does_not_change_spectrum(self):
        center, side = sine(440, amplitude=.05), sine(1000, amplitude=.2)
        x = np.column_stack((center + side, center - side))
        y = x.copy(); y[:, 1] *= -1
        c1, c2 = a.spectral_centroids(x, 48000), a.spectral_centroids(y, 48000)
        np.testing.assert_allclose(c1, c2, rtol=1e-10)
        self.assertLess(c1[0], 990)  # Center component is present, not discarded.
        f, p = charts.average_psd(x, 48000)
        self.assertGreater(p[np.argmin(abs(f - 440))], 1e-6)
        _, p2 = charts.average_psd(y, 48000)
        np.testing.assert_allclose(p, p2, rtol=1e-10)

    def test_batched_welch_matches_full_welch(self):
        from scipy.signal import welch
        x = np.random.default_rng(5).normal(size=(900000, 3))
        f, p = charts.average_psd(x, 48000)
        f2, p2 = welch(x, 48000, nperseg=8192, noverlap=4096, axis=0)
        np.testing.assert_array_equal(f, f2)
        np.testing.assert_allclose(p, p2.mean(axis=1), rtol=1e-12)

    def test_short_and_silent_spectrum(self):
        f, p = charts.average_psd(sine(seconds=.02), 48000)
        self.assertEqual(len(f), 481)
        db, _, _ = charts.spectrogram_db(np.zeros(4800), 48000)
        self.assertTrue(np.all(db == -70))

    def test_spectrogram_preserves_antiphase_audio(self):
        x = sine(seconds=.1)
        db1, _, _ = charts.spectrogram_db(np.column_stack((x, x)), 48000)
        db2, _, _ = charts.spectrogram_db(np.column_stack((x, -x)), 48000)
        np.testing.assert_allclose(db1, db2)
        self.assertGreater(float(db2.max()), -1)

    def test_pcm_is_mapped_and_original_samples_are_retained(self):
        wav(self.path, sine(seconds=.02))
        pcm, sr = a.decode_pcm(str(self.path), 1)
        self.assertIsInstance(pcm, np.memmap)
        self.assertEqual(pcm.shape, (960, 1))
        self.assertEqual(sr, 48000)

    def test_silent_audio_has_null_metrics_in_strict_json(self):
        wav(self.path, np.zeros(4800))
        r = a.analyze_file(str(self.path))
        self.assertEqual(r.error, '')
        self.assertIsNone(r.integrated_lufs)
        self.assertIsNone(r.true_peak_dbtp)
        self.assertIsNone(r.sample_peak_dbfs)
        json.dumps(r.to_dict(), allow_nan=False)

    def test_corrupt_file_does_not_export_success_values(self):
        self.path.write_bytes(b'broken audio')
        d = a.analyze_file(str(self.path)).to_dict()
        self.assertTrue(d['error'])
        for k in a.METRIC_FIELDS:
            self.assertIsNone(d[k])
        self.assertIsNone(d['clipped'])

    def test_partial_failure_preserves_valid_measurements_only(self):
        wav(self.path, sine(seconds=.1))
        with mock.patch.object(a, 'measure_loudness', side_effect=RuntimeError('test error')):
            r = a.analyze_file(str(self.path))
        self.assertIsNone(r.integrated_lufs)
        self.assertIsNotNone(r.centroid_magnitude_hz)
        self.assertIsNotNone(r.sample_peak_dbfs)
        self.assertIn('test error', r.error)

    def test_cancellation_terminates_child(self):
        flag = threading.Event()
        timer = threading.Timer(.15, flag.set)
        timer.start()
        start = time.monotonic()
        with self.assertRaises(a.AnalysisCancelled):
            a.run_process([sys.executable, '-c', 'import time;time.sleep(10)'], cancel=flag.is_set)
        timer.join()
        self.assertLess(time.monotonic() - start, 2)

    def test_cancelled_analysis_produces_no_result(self):
        with self.assertRaises(a.AnalysisCancelled):
            a.analyze_file(str(self.path), cancel=lambda: True)

    def test_source_revision_mismatch_invalidates_exports(self):
        wav(self.path, sine(seconds=1))
        with mock.patch.object(a, 'estimate_bpm', return_value=None):
            r = a.analyze_file(str(self.path))
        self.assertFalse(r.is_stale)
        self.path.write_bytes(b'new revision')
        self.assertTrue(r.is_stale)
        self.assertIsNone(r.to_dict()['integrated_lufs'])
        self.assertIsNotNone(r.to_dict(for_export=False)['integrated_lufs'])
        self.assertIn('源文件', r.to_dict()['error'])

    def test_legacy_failed_record_is_migrated_conservatively(self):
        r = a.AnalysisResult.from_dict(dict(error='broken', integrated_lufs=0, true_peak_dbtp=0, clipped=False))
        self.assertIsNone(r.integrated_lufs)
        self.assertIsNone(r.true_peak_dbtp)
        self.assertIsNone(r.clipped)
        self.assertTrue(r.warnings)

    def test_missing_metric_stays_null_after_roundtrip(self):
        r = a.AnalysisResult.from_dict(a.AnalysisResult(error='test').to_dict())
        self.assertIsNone(r.integrated_lufs)
        self.assertIsNone(r.true_peak_ok)

    def test_stationary_tone_does_not_get_an_arbitrary_bpm(self):
        self.assertIsNone(a.estimate_bpm(sine(seconds=3), 48000))

    def test_120_bpm_clicks(self):
        x = np.zeros(48000 * 10)
        click = sine(seconds=.08, amplitude=.8) * np.exp(-np.arange(3840) / 48000 * 60)
        for st in range(0, len(x), 24000):
            x[st:st + len(click)] += click[:min(len(click), len(x) - st)]
        bpm = a.estimate_bpm(np.column_stack((x, -x)), 48000)
        self.assertIsNotNone(bpm)
        self.assertLess(abs(bpm - 120), 4)

    def test_failed_ffmpeg_summary_is_not_accepted(self):
        import subprocess
        proc = subprocess.CompletedProcess([], 1, '', 'Summary:\n I: -70.0\n LRA: 0.0\n Peak: -inf dBFS')
        with mock.patch.object(a, 'run_process', return_value=proc):
            with self.assertRaises(RuntimeError):
                a.measure_loudness('broken.wav')

    def test_parallel_real_charts_complete_without_matplotlib_races(self):
        from concurrent.futures import ThreadPoolExecutor
        wav(self.path, sine(seconds=.1))
        def render(index):
            return charts.generate_charts(str(self.path), self.tmp.name, str(index), lang='en')
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(render, range(3)))
        for result in results:
            self.assertEqual(set(result), {'spectrogram', 'spectrum'})
            self.assertTrue(all(Path(p).stat().st_size > 1000 for p in result.values()))

    def test_cancelled_chart_wait_does_not_block_on_plot_lock(self):
        charts._RENDER_LOCK.acquire()
        try:
            with mock.patch.object(charts, 'load_audio', return_value=(sine(seconds=.1), 48000)):
                with self.assertRaises(a.AnalysisCancelled):
                    charts.generate_charts('memory.wav', self.tmp.name, 'cancelled',
                                           cancel=lambda: True)
        finally:
            charts._RENDER_LOCK.release()


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main as m
        from PySide6.QtWidgets import QApplication
        cls.m = m
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        m._log = lambda *args: None

    def setUp(self):
        from smoke_test import MemoryConfig
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.win = self.m.MainWindow(MemoryConfig(self.m), data_dir=self.tmp.name)
        self.win._theme_timer.stop()
        self.addCleanup(self.win.shutdown)
        self.path = Path(self.tmp.name) / 'track.wav'
        wav(self.path, sine(seconds=.1))

    def pump_until(self, pred, seconds=5):
        deadline = time.monotonic() + seconds
        while not pred() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(pred())

    @staticmethod
    def delayed(path, cancel=None):
        for _ in range(50):
            a.check_cancel(cancel)
            time.sleep(.005)
        return a.AnalysisResult(path=path, filename=Path(path).name, integrated_lufs=-14)

    def test_clear_restores_controls_and_drops_old_results(self):
        with mock.patch.object(self.m, 'analyze_file', self.delayed):
            self.win.add_files([str(self.path)])
            self.win.clear_all()
            self.pump_until(lambda: self.win._worker is None)
        self.assertEqual(self.win._results, [])
        self.assertTrue(self.win.btn_open.isEnabled())
        self.assertFalse(self.win.btn_stop.isEnabled())

    def test_new_input_after_clear_is_kept(self):
        with mock.patch.object(self.m, 'analyze_file', self.delayed):
            self.win.add_files([str(self.path)])
            self.win.clear_all()
            self.win.add_files([str(self.path)])
            self.pump_until(lambda: self.win._worker is None)
        self.assertEqual(len(self.win._results), 1)

    def test_replace_during_analysis_discards_old_batch(self):
        self.win.config.set(self.m.AppConfig.KEY_APPEND_ON_DROP, False)
        p2 = Path(self.tmp.name) / 'new.wav'; wav(p2, sine(seconds=.1))
        with mock.patch.object(self.m, 'analyze_file', self.delayed):
            self.win.add_files([str(self.path)])
            self.win.add_files([str(p2)])
            self.pump_until(lambda: self.win._worker is None)
        self.assertEqual([r.filename for r in self.win._results], ['new.wav'])

    def test_worker_updates_are_on_gui_thread(self):
        from PySide6.QtCore import QThread
        seen = []
        original = self.win._render_row
        def render(r):
            seen.append(QThread.currentThread() is self.app.thread())
            original(r)
        self.win._render_row = render
        with mock.patch.object(self.m, 'analyze_file', self.delayed):
            self.win.add_files([str(self.path)])
            self.pump_until(lambda: self.win._worker is None)
        self.assertEqual(seen, [True])

    def test_failed_row_retains_playback_data_and_exports_blanks(self):
        r = a.AnalysisResult(path=str(self.path), filename='track.wav', error='broken')
        self.win._on_result(r, self.win._batch_id)
        self.win.table.setCurrentCell(0, 0)
        self.assertIsNotNone(self.win._selected_result())
        self.assertEqual(self.win.table.item(0, 3).text(), '—')
        csvpath = Path(self.tmp.name) / 'export.csv'
        self.win._write_csv(str(csvpath), [r])
        with csvpath.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][5:9], ['', '', '', ''])
        self.assertEqual(rows[1][11], '')
        self.assertEqual(rows[1][14], '')

    def test_shutdown_cancels_and_joins_analysis(self):
        with mock.patch.object(self.m, 'analyze_file', self.delayed):
            self.win.add_files([str(self.path)])
            worker = self.win._worker
            self.win.shutdown()
        self.assertFalse(worker.isRunning())

    def test_language_updates_headers(self):
        self.m.set_language('en')
        self.win.retranslate()
        self.assertEqual(self.win.table.horizontalHeaderItem(0).text(), 'Filename')
        self.m.set_language('zh')

    def test_reopened_chart_is_not_pruned_while_visible(self):
        from PySide6.QtWidgets import QDialog
        for index in range(7):
            dlg = QDialog(self.win)
            dlg._worker = None
            dlg._user_closed = True
            self.win._chart_dialogs[str(index)] = dlg
        visible = self.win._chart_dialogs['0']
        visible.show()
        self.win._prune_chart_dialogs(keep='6')
        self.assertIn('0', self.win._chart_dialogs)
        self.assertEqual(len(self.win._chart_dialogs), 6)

    def test_file_changed_during_chart_generation_triggers_regeneration(self):
        def generate(path, out, name, on_chart=None, lang='zh', cancel=None):
            for _ in range(30):
                a.check_cancel(cancel)
                time.sleep(.005)
            for kind in ('spectrogram', 'spectrum'):
                from PySide6.QtGui import QImage
                target = str(Path(out) / (str(time.monotonic_ns()) + '.png'))
                image = QImage(2, 2, QImage.Format_RGB32); image.fill(0); image.save(target)
                on_chart(kind, target)
        r = a.AnalysisResult(path=str(self.path), filename='track.wav')
        with mock.patch.object(charts, 'generate_charts', generate):
            self.win._on_result(r, self.win._batch_id)
            self.win.open_charts_for_row(0)
            dlg = self.win._chart_dialogs[str(self.path)]
            oldkey = dlg._generation_key
            wav(self.path, sine(seconds=.2))
            dlg.refresh_if_stale()
            self.pump_until(lambda: dlg._worker is None and len(dlg._paths) == 2)
        self.assertNotEqual(dlg._generation_key, oldkey)
        self.assertEqual(dlg._generation_key, dlg._cache_key())
        self.assertIn(dlg._cache_key(), dlg._cache)


if __name__ == '__main__':
    unittest.main()
