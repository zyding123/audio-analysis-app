#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音频分析引擎（离线测量，无需实时播放）。

测量项：
  1. 积分响度  Integrated Loudness (EBU R128 / ITU-R BS.1770)，单位 LUFS
  2. 响度范围  Loudness Range (LRA)，单位 LU（EBU Tech 3342）
  3. 频谱质心  Spectral Centroid，单位 Hz（同时给出幅度加权 / 功率加权两种口径）
  4. 满幅削波检测：样本峰值 / 削波样本占比 / 削波时间区间
  5. 真峰值    True Peak，单位 dBTP（用于发现采样点间削波）
  6. 速度      BPM（节拍自动估计）

依赖：FFmpeg / ffprobe、numpy、librosa。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

from version import APP_VERSION
from dataclasses import dataclass, field, asdict

import numpy as np

# 判定“满幅”的阈值：float32 全幅为 1.0，16bit 满幅约 0.99997
FULLSCALE_THRESHOLD = 0.999
# 削波区间合并的最大间隔（秒）：小于该间隔的离散削波点合并为一段
CLIP_MERGE_GAP_S = 0.25
# 满幅平台候选的最小长度；同时要求同极性和平坦度，结果不等同于听感失真。
MIN_CLIP_RUN = 3


class AnalysisCancelled(Exception):
    """分析被用户主动取消（用于“停止”按钮及时响应长文件）。"""


@dataclass
class ClipRange:
    """一段削波区间。"""
    start: float          # 起始时间（秒）
    end: float            # 结束时间（秒）
    channel: int = 0      # 声道索引（从 1 开始）


def file_signature(path):
    """Nanosecond timestamp and byte count identify the measured source revision."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


METRIC_FIELDS = ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp",
                 "sample_peak_dbfs", "centroid_magnitude_hz", "centroid_power_hz",
                 "bpm", "clip_percent")


@dataclass
class AnalysisResult:
    path: str = ""
    filename: str = ""
    sample_rate: int = 0
    channels: int = 0
    duration: float = 0.0
    integrated_lufs: float | None = None
    loudness_range_lu: float | None = None
    true_peak_dbtp: float | None = None
    sample_peak_dbfs: float | None = None
    centroid_magnitude_hz: float | None = None
    centroid_power_hz: float | None = None
    bpm: float | None = None
    clipped: bool | None = None
    clipped_samples: int = 0
    total_samples: int = 0
    clip_percent: float | None = None
    clip_ranges: list = field(default_factory=list)
    error: str = ""
    warnings: list = field(default_factory=list)
    source_mtime_ns: int | None = None
    source_size: int | None = None
    analysis_version: str = APP_VERSION

    @property
    def is_stale(self):
        if self.source_mtime_ns is None or self.source_size is None:
            return False  # Legacy records remain readable, with a provenance warning.
        return file_signature(self.path) != (self.source_mtime_ns, self.source_size)

    @property
    def clip_percent_display(self):
        return None if self.clip_percent is None else round(self.clip_percent * 100, 4)

    @property
    def true_peak_ok(self):
        return None if self.true_peak_dbtp is None else self.true_peak_dbtp <= 0.0

    def to_dict(self, *, for_export=True):
        d = asdict(self)
        d["clip_ranges"] = [dict(start=round(r.start, 6), end=round(r.end, 6),
                                 channel=r.channel) for r in self.clip_ranges]
        d["bpm"] = round(self.bpm, 1) if self.bpm is not None else None
        d["clip_percent_display"] = self.clip_percent_display
        d["true_peak_ok"] = self.true_peak_ok
        d["source_changed"] = self.is_stale
        for key in METRIC_FIELDS:
            if d[key] is not None and not np.isfinite(d[key]):
                d[key] = None
        if for_export and self.is_stale:
            for key in METRIC_FIELDS:
                d[key] = None
            d.update(clipped=None, clip_ranges=[], clipped_samples=None,
                     clip_percent_display=None, true_peak_ok=None)
            d["error"] = (self.error + "；" if self.error else "") + "源文件已更新或不存在，请重新分析"
        return d

    @classmethod
    def from_dict(cls, d):
        values = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        values["clip_ranges"] = [ClipRange(start=r.get("start", 0), end=r.get("end", 0),
                                           channel=r.get("channel", 1))
                                 for r in d.get("clip_ranges", [])]
        for key in METRIC_FIELDS:
            v = values.get(key)
            values[key] = v if isinstance(v, (int, float)) and np.isfinite(v) else None
        if "analysis_version" not in d:
            values["analysis_version"] = "legacy"
            values["warnings"] = list(d.get("warnings", [])) + ["旧版记录缺少源文件版本信息，建议重新分析"]
            if d.get("error"):
                for key in METRIC_FIELDS:
                    if values.get(key) == 0:
                        values[key] = None
                if not d.get("clipped"):
                    values["clipped"] = None
        return cls(**values)


def check_cancel(cancel):
    if cancel is not None and cancel():
        raise AnalysisCancelled()


def run_process(cmd, *, cancel=None, stdout=subprocess.PIPE, text=False):
    """Poll a child process so Stop can terminate ffprobe/ffmpeg mid-file."""
    check_cancel(cancel)
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=stdout,
                            stderr=subprocess.PIPE, text=text)
    try:
        while True:
            check_cancel(cancel)
            try:
                out, err = proc.communicate(timeout=0.1)
                return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        proc.terminate()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        raise


# --------------------------------------------------------------------------- #
# 二进制定位
# --------------------------------------------------------------------------- #

def _locate_binary(name: str):
    """依次在 PATH、PyInstaller 打包目录、macOS .app 资源目录中查找可执行文件。"""
    exe = shutil.which(name)
    if exe and not getattr(sys, "frozen", False):
        return exe

    bases = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(meipass)  # onedir 模式：Contents/MacOS

        exe_dir = os.path.dirname(sys.executable)  # Contents/MacOS
        bases.append(exe_dir)

        contents = os.path.dirname(exe_dir)  # .app/Contents
        if contents.endswith("Contents"):
            bases.append(os.path.join(contents, "Resources"))
            bases.append(os.path.join(contents, "Frameworks"))

    for base in bases:
        if not base:
            continue
        for sub in ("", "bin"):
            cand = os.path.join(base, sub, name)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
    return exe


def ensure_ffmpeg():
    """返回 (ffmpeg_path, ffprobe_path)；找不到时尝试用 static-ffmpeg 兜底。"""
    ffmpeg = _locate_binary("ffmpeg")
    ffprobe = _locate_binary("ffprobe")

    if not ffmpeg or not ffprobe:
        try:
            import contextlib
            import io
            import static_ffmpeg
            # 抑制 static-ffmpeg 的下载进度条输出
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                static_ffmpeg.add_paths()  # 首次调用会下载静态二进制
        except Exception:
            pass
        ffmpeg = ffmpeg or _locate_binary("ffmpeg")
        ffprobe = ffprobe or _locate_binary("ffprobe")

    return ffmpeg, ffprobe


# --------------------------------------------------------------------------- #
# 基础信息
# --------------------------------------------------------------------------- #

def probe(path: str, cancel=None) -> dict:
    """读取采样率、声道数、时长。"""
    ffmpeg, ffprobe = ensure_ffmpeg()
    if not ffprobe:
        raise RuntimeError("未找到 ffprobe，请安装 ffmpeg（brew install ffmpeg）")

    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,duration",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    proc = run_process(cmd, cancel=cancel, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "无法读取音频流")

    import json
    data = json.loads(proc.stdout or "{}")

    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("文件中没有音频流")

    stream = streams[0]
    fmt = data.get("format") or {}

    sample_rate = int(stream.get("sample_rate") or 0)
    channels = int(stream.get("channels") or 0)
    duration = float(stream.get("duration") or fmt.get("duration") or 0.0)

    return {"sample_rate": sample_rate, "channels": channels, "duration": duration}


# --------------------------------------------------------------------------- #
# 响度测量（EBU R128）
# --------------------------------------------------------------------------- #

def measure_loudness(path: str, cancel=None):
    """
    通过 ffmpeg ebur128 过滤器测量积分响度 / LRA / 真峰值。
    返回 (integrated_lufs, lra_lu, true_peak_dbtp)。
    """
    ffmpeg, _ = ensure_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")

    cmd = [
        ffmpeg, "-hide_banner", "-nostats",
        "-i", path,
        "-map", "0:a:0",   # 只取第一个音频流（视频文件忽略视频轨）
        "-vn",             # 禁用视频输出
        "-af", "ebur128=peak=true:framelog=none",
        "-f", "null", "-",
    ]
    proc = run_process(cmd, cancel=cancel, text=True)
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")

    if proc.returncode != 0:
        raise RuntimeError("响度测量失败：" + (proc.stderr or "ffmpeg 解码失败")[-2000:])

    # 只解析最终 Summary 段（逐帧日志可能含初始值，需规避）
    idx = combined.rfind("Summary:")
    section = combined[idx:] if idx >= 0 else combined

    # 数值或 -inf（纯静音时 ffmpeg 输出 -inf，需正确解析而非回退为 0）
    _num = r"(-?(?:\d+(?:\.\d+)?|[iI][nN][fF](?:inity)?))"
    integrated = _search_float(section, r"I:\s*" + _num)
    lra = _search_float(section, r"LRA:\s*" + _num)
    # 真峰值：peak=true 时 Summary 中仅此一个 "Peak:"，单位随版本可能为 dBFS 或 dBTP
    true_peak = _search_float(section, r"Peak:\s*" + _num + r"\s*dB")

    if integrated is None or lra is None or true_peak is None:
        raise RuntimeError("响度测量失败：" + (combined.strip() or "未知错误"))

    return (
        integrated if integrated > -70 and np.isfinite(integrated) else None,
        lra if np.isfinite(lra) else None,
        true_peak if np.isfinite(true_peak) else None,
    )


def _search_float(text: str, pattern: str):
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def downmix_mono(pcm):
    """Conventional mono downmix for callers that explicitly want mono.

    Analysis uses separate channel spectra/onsets, never this lossy waveform sum.
    """
    return pcm.mean(axis=1) if pcm.ndim > 1 else pcm


def decode_pcm(path, channels, cancel=None):
    """Decode to an anonymous temporary file and map it, instead of holding bytes in RAM."""
    ffmpeg, _ = ensure_ffmpeg()
    info = probe(path, cancel=cancel)
    n_ch = channels if channels > 0 else info["channels"]
    with tempfile.TemporaryFile() as raw:
        cmd = [ffmpeg, "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0",
               "-vn", "-f", "f32le", "-c:a", "pcm_f32le", "-"]
        proc = run_process(cmd, cancel=cancel, stdout=raw)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode(errors="replace")[-2000:] or "解码失败")
        size = raw.tell()
        if not size:
            raise RuntimeError("音频流没有可解码的样本")
        if n_ch <= 0 or size % (4 * n_ch):
            raise RuntimeError("解码数据与声道数不匹配")
        pcm = np.memmap(raw, dtype="<f4", mode="r", shape=(size // (4 * n_ch), n_ch))
    return pcm, info["sample_rate"]


def iter_pcm_blocks(pcm, cancel=None, block=65536):
    for start in range(0, len(pcm), block):
        check_cancel(cancel)
        part = pcm[start:start + block]
        if not np.isfinite(part).all():
            raise ValueError("音频含 NaN/Infinity 无效样本")
        yield start, part


def sample_peak(pcm, cancel=None):
    peak = 0.0
    for _, part in iter_pcm_blocks(pcm, cancel):
        peak = max(peak, float(np.max(np.abs(part))))
    return 20 * np.log10(peak) if peak > 0 else None


def detect_clipping(pcm, sample_rate, cancel=None):
    """Conservative full-scale plateau/over-range candidates, with block-boundary continuity.

    Three samples merely near 1.0 are not sufficient: same-polarity samples must
    also form a flat plateau. This heuristic does not establish audible distortion.
    """
    data = pcm[:, None] if pcm.ndim == 1 else pcm
    total = int(data.size)
    gap = max(int(sample_rate * CLIP_MERGE_GAP_S), 1)
    ranges, clipped_samples = [], 0
    for ch in range(data.shape[1]):
        pending = None
        merged = []
        def emit(run):
            nonlocal clipped_samples
            if run is None:
                return
            start, end, _, over = run
            if end - start + 1 < MIN_CLIP_RUN and not over:
                return
            clipped_samples += end - start + 1
            if merged and start - merged[-1][1] <= gap:
                merged[-1][1] = end
            else:
                merged.append([start, end])
        for offset, part in iter_pcm_blocks(data, cancel):
            x = np.asarray(part[:, ch])
            high = np.abs(x) >= FULLSCALE_THRESHOLD
            link = (high[1:] & high[:-1] & (np.signbit(x[1:]) == np.signbit(x[:-1]))
                    & (np.abs(np.diff(x)) <= 1e-7))
            starts = np.flatnonzero(high & np.r_[True, ~link])
            ends = np.flatnonzero(high & np.r_[~link, True])
            for st, en in zip(starts, ends):
                run = (offset + int(st), offset + int(en), float(x[en]),
                       bool(np.any(np.abs(x[st:en + 1]) > 1)))
                if (pending is not None and run[0] == pending[1] + 1
                        and np.signbit(x[st]) == np.signbit(pending[2])
                        and abs(float(x[st]) - pending[2]) <= 1e-7):
                    pending = (pending[0], run[1], run[2], pending[3] or run[3])
                else:
                    emit(pending)
                    pending = run
            if pending is not None and pending[1] < offset + len(x) - 1:
                emit(pending)
                pending = None
        emit(pending)
        ranges.extend(ClipRange(start=s / sample_rate, end=(e + 1) / sample_rate,
                                channel=ch + 1) for s, e in merged)
    return clipped_samples, total, ranges


def spectral_centroids(pcm, sample_rate, frame=2048, hop=512, cancel=None):
    """Average frame centroids from summed channel magnitudes/powers (phase-safe)."""
    data = pcm[:, None] if pcm.ndim == 1 else pcm
    if len(data) < 2 or sample_rate <= 0:
        return None, None
    frame = min(frame, len(data))
    window = np.hanning(frame)[:, None]
    freqs = np.fft.rfftfreq(frame, 1 / sample_rate)
    sums = np.zeros(2)
    counts = np.zeros(2, dtype=int)
    for i, start in enumerate(range(0, len(data) - frame + 1, hop)):
        if i % 128 == 0:
            check_cancel(cancel)
        seg = data[start:start + frame]
        if not np.isfinite(seg).all():
            raise ValueError("音频含无效样本")
        mag = np.abs(np.fft.rfft(seg * window, axis=0))
        for k, weights in enumerate((mag.sum(axis=1), (mag * mag).sum(axis=1))):
            mass = float(weights.sum())
            if mass > 1e-12:
                sums[k] += float(np.dot(freqs, weights)) / mass
                counts[k] += 1
    return tuple(float(sums[k] / counts[k]) if counts[k] else None for k in range(2))


# --------------------------------------------------------------------------- #
# 速度（BPM）
# --------------------------------------------------------------------------- #

BPM_TARGET_SR = 22050   # BPM 分析统一重采样到该采样率（保证跨文件结果一致）
BPM_HOP_LENGTH = 256    # 帧移；22050/256 ≈ 86 帧/秒，兼顾 BPM 分辨率与速度
BPM_MAX_ANALYZE_S = 120  # 长音频只取中间该秒数（避免长前奏/尾奏干扰并提速）


def estimate_bpm(pcm, sample_rate, cancel=None):
    """Combine per-channel onset envelopes, preserving center and side information."""
    check_cancel(cancel)
    data = pcm[:, None] if pcm.ndim == 1 else pcm
    sr = int(sample_rate)
    if sr <= 0 or len(data) < sr * 2:
        return None
    if len(data) > sr * BPM_MAX_ANALYZE_S:
        start = (len(data) - sr * BPM_MAX_ANALYZE_S) // 2
        data = data[start:start + sr * BPM_MAX_ANALYZE_S]
    if np.max(np.abs(data)) < 1e-4:
        return None
    import librosa
    from scipy.signal import find_peaks
    from librosa.feature.rhythm import tempo as estimate_tempo
    envelopes = []
    for ch in range(data.shape[1]):
        check_cancel(cancel)
        y = np.ascontiguousarray(data[:, ch], dtype=np.float32)
        if sr != BPM_TARGET_SR:
            y = librosa.resample(y, orig_sr=sr, target_sr=BPM_TARGET_SR)
        envelopes.append(librosa.onset.onset_strength(y=y, sr=BPM_TARGET_SR,
                                                       hop_length=BPM_HOP_LENGTH))
    onset = np.mean(envelopes, axis=0)
    margin = int(0.25 * BPM_TARGET_SR / BPM_HOP_LENGTH)
    usable = onset[margin:-margin] if len(onset) > 2 * margin else onset
    strength = float(np.max(usable)) if usable.size else 0
    peaks, _ = find_peaks(usable, height=strength * 0.2, prominence=strength * 0.1,
                         distance=max(1, int(BPM_TARGET_SR / BPM_HOP_LENGTH * 0.15)))
    if strength < 0.1 or len(peaks) < 3:
        return None
    check_cancel(cancel)
    tempo = estimate_tempo(onset_envelope=onset, sr=BPM_TARGET_SR,
                                       hop_length=BPM_HOP_LENGTH, aggregate=np.median)
    check_cancel(cancel)
    bpm = float(np.atleast_1d(tempo)[0])
    return bpm if np.isfinite(bpm) and bpm > 0 else None


def analyze_file(path, cancel=None):
    res = AnalysisResult(path=os.path.abspath(path), filename=os.path.basename(path))
    signature = file_signature(path)
    if signature:
        res.source_mtime_ns, res.source_size = signature
    try:
        check_cancel(cancel)
        info = probe(path, cancel=cancel)
        res.sample_rate, res.channels, res.duration = info["sample_rate"], info["channels"], info["duration"]
    except AnalysisCancelled:
        raise
    except Exception as exc:
        res.error = f"读取失败：{exc}"
        return res
    try:
        res.integrated_lufs, res.loudness_range_lu, res.true_peak_dbtp = measure_loudness(path, cancel=cancel)
    except AnalysisCancelled:
        raise
    except Exception as exc:
        res.error = f"响度测量失败：{exc}"
    try:
        pcm, sr = decode_pcm(path, res.channels, cancel=cancel)
        res.total_samples = int(pcm.size)
        res.sample_peak_dbfs = sample_peak(pcm, cancel)
        count, total, ranges = detect_clipping(pcm, sr, cancel=cancel)
        res.clipped_samples, res.clip_ranges = count, ranges
        res.clip_percent, res.clipped = count / total, bool(ranges)
        res.centroid_magnitude_hz, res.centroid_power_hz = spectral_centroids(pcm, sr, cancel=cancel)
        try:
            res.bpm = estimate_bpm(pcm, sr, cancel=cancel)
            if res.bpm is None:
                res.warnings.append("节拍证据不足或音频过短，未提供 BPM")
        except AnalysisCancelled:
            raise
        except Exception as exc:
            res.warnings.append(f"BPM 测量不可用：{exc}")
        if res.duration < 30:
            res.warnings.append("音频短于 30 秒，LRA/BPM 仅供参考")
        if res.sample_peak_dbfs is None:
            res.integrated_lufs = res.loudness_range_lu = res.true_peak_dbtp = None
            res.warnings.append("纯静音：响度、峰值与频谱指标不适用")
    except AnalysisCancelled:
        raise
    except Exception as exc:
        res.error += ("；" if res.error else "") + f"样本/频谱分析失败：{exc}"
    check_cancel(cancel)
    if res.is_stale:
        res.error += ("；" if res.error else "") + "分析期间源文件发生变化，请重新分析"
    return res


def clip_summary(res: AnalysisResult, max_ranges: int = 6) -> str:
    """生成削波情况的可读摘要。"""
    if res.clipped is None:
        return "未测量"
    if not res.clipped:
        return "未检出满幅平台（不代表无其他失真）"
    parts = [f"疑似满幅平台占比 {res.clip_percent_display}%"]
    for i, r in enumerate(res.clip_ranges[:max_ranges]):
        if r.channel > 1:
            parts.append(f"CH{r.channel} {r.start:.2f}–{r.end:.2f}s")
        else:
            parts.append(f"{r.start:.2f}–{r.end:.2f}s")
    if len(res.clip_ranges) > max_ranges:
        parts.append(f"…等 {len(res.clip_ranges)} 段")
    return "；".join(parts)


if __name__ == "__main__":
    # 命令行自测：python analyzer.py <音频文件> [...]
    if len(sys.argv) < 2:
        print("用法：python analyzer.py <音频文件> [...]")
        sys.exit(1)

    import json as _json
    for f in sys.argv[1:]:
        r = analyze_file(f)
        print(_json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
