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

依赖：ffmpeg / ffprobe（参考级 EBU R128 实现 libebur128 的封装）、numpy、librosa。
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict

import numpy as np

# 判定“满幅”的阈值：float32 全幅为 1.0，16bit 满幅约 0.99997
FULLSCALE_THRESHOLD = 0.999
# 削波区间合并的最大间隔（秒）：小于该间隔的离散削波点合并为一段
CLIP_MERGE_GAP_S = 0.25
# 判定“真削波”的最小连续样本数：真实削波会形成平顶（连续多个满幅样本）；
# 孤立 1~2 个样本越过阈值多为“接近满幅的正常峰值”，不算削波
MIN_CLIP_RUN = 3


class AnalysisCancelled(Exception):
    """分析被用户主动取消（用于“停止”按钮及时响应长文件）。"""


@dataclass
class ClipRange:
    """一段削波区间。"""
    start: float          # 起始时间（秒）
    end: float            # 结束时间（秒）
    channel: int = 0      # 声道索引（从 1 开始）


@dataclass
class AnalysisResult:
    path: str = ""
    filename: str = ""
    sample_rate: int = 0
    channels: int = 0
    duration: float = 0.0

    integrated_lufs: float = 0.0     # 积分响度（LUFS）
    loudness_range_lu: float = 0.0   # LRA（LU）
    true_peak_dbtp: float = 0.0      # 真峰值（dBTP）
    sample_peak_dbfs: float = 0.0    # 样本峰值（dBFS，0.0 = 满幅）
    centroid_magnitude_hz: float = 0.0  # 频谱质心·幅度加权（Hz；行业标准口径）
    centroid_power_hz: float = 0.0      # 频谱质心·功率加权（Hz；旧版口径）
    bpm: float = 0.0                 # 速度（BPM；0 = 未测出/不适用）

    clipped: bool = False            # 是否存在满幅削波
    clipped_samples: int = 0
    total_samples: int = 0
    clip_percent: float = 0.0        # 削波样本占比（0~1）
    clip_ranges: list = field(default_factory=list)

    error: str = ""

    # ---- 便捷属性 ----
    @property
    def clip_percent_display(self) -> float:
        return round(self.clip_percent * 100, 4)

    @property
    def true_peak_ok(self) -> bool:
        """真峰值是否超过 0 dBTP（存在采样点间削波风险）。"""
        return self.true_peak_dbtp <= 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["clip_ranges"] = [
            {"start": round(r.start, 3), "end": round(r.end, 3),
             "channel": r.channel} for r in self.clip_ranges
        ]
        d["bpm"] = round(self.bpm, 1)
        d["clip_percent_display"] = self.clip_percent_display
        d["true_peak_ok"] = self.true_peak_ok
        # 对外导出保持标准 JSON：无穷值用 null 表示（严格解析器不接受 -Infinity）
        for key in ("true_peak_dbtp", "sample_peak_dbfs", "integrated_lufs",
                    "loudness_range_lu"):
            if isinstance(d.get(key), float) and not np.isfinite(d[key]):
                d[key] = None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisResult":
        """从 to_dict 的输出重建（用于持久化恢复）。"""

        def _fin(v, default=0.0):
            """None → -inf 还原（to_dict 会把无穷写成 null）。"""
            if v is None:
                return float("-inf")
            return v if isinstance(v, (int, float)) else default

        res = cls(
            path=d.get("path", ""),
            filename=d.get("filename", ""),
            sample_rate=d.get("sample_rate", 0),
            channels=d.get("channels", 0),
            duration=d.get("duration", 0.0),
            integrated_lufs=_fin(d.get("integrated_lufs")),
            loudness_range_lu=_fin(d.get("loudness_range_lu")),
            true_peak_dbtp=_fin(d.get("true_peak_dbtp")),
            sample_peak_dbfs=_fin(d.get("sample_peak_dbfs")),
            centroid_magnitude_hz=d.get("centroid_magnitude_hz", 0.0),
            centroid_power_hz=d.get("centroid_power_hz", 0.0),
            bpm=d.get("bpm", 0.0),
            clipped=d.get("clipped", False),
            clipped_samples=d.get("clipped_samples", 0),
            total_samples=d.get("total_samples", 0),
            clip_percent=d.get("clip_percent", 0.0),
            error=d.get("error", ""),
        )
        res.clip_ranges = [
            ClipRange(start=r.get("start", 0.0), end=r.get("end", 0.0),
                      channel=r.get("channel", 1))
            for r in d.get("clip_ranges", [])
        ]
        return res


# --------------------------------------------------------------------------- #
# 二进制定位
# --------------------------------------------------------------------------- #

def _locate_binary(name: str):
    """依次在 PATH、PyInstaller 打包目录、macOS .app 资源目录中查找可执行文件。"""
    exe = shutil.which(name)
    if exe:
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
    return None


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

def probe(path: str) -> dict:
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
    proc = subprocess.run(cmd, capture_output=True, text=True)
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

def measure_loudness(path: str):
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
    proc = subprocess.run(cmd, capture_output=True, text=True)
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")

    # 只解析最终 Summary 段（逐帧日志可能含初始值，需规避）
    idx = combined.rfind("Summary:")
    section = combined[idx:] if idx >= 0 else combined

    # 数值或 -inf（纯静音时 ffmpeg 输出 -inf，需正确解析而非回退为 0）
    _num = r"(-?(?:\d+(?:\.\d+)?|[iI][nN][fF](?:inity)?))"
    integrated = _search_float(section, r"I:\s*" + _num)
    lra = _search_float(section, r"LRA:\s*" + _num)
    # 真峰值：peak=true 时 Summary 中仅此一个 "Peak:"，单位随版本可能为 dBFS 或 dBTP
    true_peak = _search_float(section, r"Peak:\s*" + _num + r"\s*dB")

    if integrated is None and lra is None:
        raise RuntimeError("响度测量失败：" + (combined.strip() or "未知错误"))

    return (
        integrated if integrated is not None else 0.0,
        lra if lra is not None else 0.0,
        true_peak if true_peak is not None else 0.0,
    )


def _search_float(text: str, pattern: str):
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def downmix_mono(pcm: np.ndarray) -> np.ndarray:
    """立体声 → 单声道（带反相相消保护）。

    常规情况取 (L+R)/2；当左右声道明显反相（差信号能量显著大于和信号，
    相关系数约 < -0.23）时改取 (L-R)/2——避免频谱质心 / BPM / 频谱图表把
    “有声音的反相立体声”测成静音（相互抵消），也覆盖带相位处理的素材。
    """
    if pcm.ndim < 2 or pcm.shape[1] < 2:
        return pcm if pcm.ndim == 1 else pcm[:, 0]
    m_sum = pcm.mean(axis=1)
    m_diff = (pcm[:, 0].astype(np.float64) - pcm[:, 1].astype(np.float64)) / 2.0
    e_sum = float(np.dot(m_sum, m_sum))
    e_diff = float(np.dot(m_diff, m_diff))
    if e_diff > e_sum * 1.3:
        return m_diff.astype(m_sum.dtype, copy=False)
    return m_sum


# --------------------------------------------------------------------------- #
# PCM 解码
# --------------------------------------------------------------------------- #

def decode_pcm(path: str, channels: int):
    """
    解码为 float32 PCM，返回 (pcm[n_samples, n_channels], sample_rate)。
    采样率从 ffprobe 获取（与 probe 一致）。
    """
    ffmpeg, _ = ensure_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")

    info = probe(path)
    sr = info["sample_rate"]

    cmd = [
        ffmpeg, "-v", "error",
        "-i", path,
        "-map", "a:0",
        "-f", "f32le",
        "-c:a", "pcm_f32le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="ignore").strip() or "解码失败")

    raw = proc.stdout
    if not raw:
        return np.zeros((0, channels), dtype=np.float32), sr

    n_ch = channels if channels > 0 else 1
    arr = np.frombuffer(raw, dtype="<f4")
    if n_ch > 1:
        arr = arr.reshape(-1, n_ch)
    else:
        arr = arr.reshape(-1, 1)
    return arr, sr


# --------------------------------------------------------------------------- #
# 削波检测
# --------------------------------------------------------------------------- #

def detect_clipping(pcm: np.ndarray, sample_rate: int):
    """
    返回 (clipped_samples, total_samples, clip_ranges)。
    pcm 形状 (n_samples, n_channels)。
    """
    total = int(pcm.size)
    mask = np.abs(pcm) >= FULLSCALE_THRESHOLD

    if pcm.ndim == 1:
        mask = mask[:, None]

    ranges = []
    clipped_samples = 0
    if mask.any():
        gap = max(int(sample_rate * CLIP_MERGE_GAP_S), 1)
        n_ch = mask.shape[1]
        for ch in range(n_ch):
            col = mask[:, ch]
            idx = np.flatnonzero(col)
            if idx.size < MIN_CLIP_RUN:
                continue
            # ① 严格连续段：相邻样本严格差 1 才算「连续满幅」
            strict_splits = np.flatnonzero(np.diff(idx) > 1) + 1
            strict_runs = np.split(idx, strict_splits)
            # ② 过滤孤立峰值：连续点数 < MIN_CLIP_RUN 视为「接近满幅的正常峰值」
            #    （真实削波会形成平顶；孤立 1~2 点越过阈值不算削波）
            solid = [r for r in strict_runs if len(r) >= MIN_CLIP_RUN]
            if not solid:
                continue
            solid_idx = np.concatenate(solid)
            clipped_samples += int(solid_idx.size)
            # ③ 报告层合并：间隔 < gap 的削波段合并为一段（便于阅读）
            merge_splits = np.flatnonzero(np.diff(solid_idx) > gap) + 1
            for run in np.split(solid_idx, merge_splits):
                s, e = int(run[0]), int(run[-1])
                ranges.append(ClipRange(
                    start=float(s) / sample_rate,
                    end=float(e) / sample_rate,
                    channel=ch + 1,
                ))

    return clipped_samples, total, ranges


# --------------------------------------------------------------------------- #
# 频谱质心（双口径）
# --------------------------------------------------------------------------- #

def spectral_centroids(pcm_mono: np.ndarray, sample_rate: int,
                       frame: int = 2048, hop: int = 512):
    """
    频谱质心（频率对频谱的"重心"），同时给出两种加权口径，单位 Hz：

      · 幅度加权（行业标准口径，教科书 / librosa / Essentia 定义）：
            centroid = Σ(f · |X(f)|) / Σ|X(f)|
      · 功率加权（旧版工具口径）：
            centroid = Σ(f · |X(f)|²) / Σ|X(f)|²

    整体统计取"逐帧质心的时间算术平均"——与 librosa（sequential frame
    centroids + np.mean）的标准用法一致，保证跨工具数值可比。

    返回 (magnitude_weighted_hz, power_weighted_hz)；无法计算时返回 (0.0, 0.0)。
    """
    n = len(pcm_mono)
    if n < frame or sample_rate <= 0:
        return 0.0, 0.0

    window = np.hanning(frame)
    freqs = np.fft.rfftfreq(frame, 1.0 / sample_rate)

    cents_mag = []
    cents_pow = []
    for start in range(0, n - frame + 1, hop):
        seg = pcm_mono[start:start + frame] * window
        mag = np.abs(np.fft.rfft(seg))

        w_m = float(mag.sum())
        if w_m > 1e-12:
            cents_mag.append(float(np.sum(freqs * mag)) / w_m)

        pw = mag * mag
        w_p = float(pw.sum())
        if w_p > 1e-12:
            cents_pow.append(float(np.sum(freqs * pw)) / w_p)

    mag_hz = float(np.mean(cents_mag)) if cents_mag else 0.0
    pow_hz = float(np.mean(cents_pow)) if cents_pow else 0.0
    return mag_hz, pow_hz


# --------------------------------------------------------------------------- #
# JIT 缓存自愈（防御性工具）
# --------------------------------------------------------------------------- #

_CACHE_ERROR_MARKERS = (
    "decompress", "incorrect header", "zlib", "cache",
    "corrupt", "unpickling", "invalid load key", "bad magic",
)


def is_likely_cache_error(exc: BaseException) -> bool:
    """判断异常是否疑似"JIT / 缓存损坏"类错误（用于触发清理重试）。"""
    try:
        msg = str(exc).lower()
    except Exception:  # noqa: BLE001
        return False
    return any(m in msg for m in _CACHE_ERROR_MARKERS)


def clear_numba_cache() -> bool:
    """清理 numba 用户级 JIT 缓存（用于"缓存损坏"类错误的自愈）。

    返回 True 表示执行了清理；任何失败静默忽略（不影响分析主流程）。
    会在 ~/Library/Logs/AudioAnalysis-cache-heal.log 留痕，便于排障。
    """
    cache_dir = ""
    try:
        try:
            from numba.misc.appdirs import AppDirs
            cache_dir = AppDirs(appname="numba", appauthor=False).user_cache_dir
        except Exception:  # noqa: BLE001
            cache_dir = os.path.expanduser("~/Library/Caches/numba")
        if cache_dir and os.path.isdir(cache_dir):
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)
            try:
                import time as _t
                with open(os.path.expanduser(
                        "~/Library/Logs/AudioAnalysis-cache-heal.log"), "a") as f:
                    f.write(f"[{_t.strftime('%Y-%m-%d %H:%M:%S')}] "
                            f"cleared numba cache: {cache_dir}\n")
            except Exception:  # noqa: BLE001
                pass
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# --------------------------------------------------------------------------- #
# 速度（BPM）
# --------------------------------------------------------------------------- #

BPM_TARGET_SR = 22050   # BPM 分析统一重采样到该采样率（保证跨文件结果一致）
BPM_HOP_LENGTH = 256    # 帧移；22050/256 ≈ 86 帧/秒，兼顾 BPM 分辨率与速度
BPM_MAX_ANALYZE_S = 120  # 长音频只取中间该秒数（避免长前奏/尾奏干扰并提速）


def estimate_bpm(pcm_mono: np.ndarray, sample_rate: int) -> float:
    """
    自动估计速度（BPM，beats per minute）。

    方法：librosa 的 tempogram 节拍估计（onset 强度包络 → 自相关 → 对数正态
    先验聚合，取时间中位数）。为保证不同来源文件可比，统一重采样到 22050 Hz、
    帧移 256；超过 3 分钟的音频只取中间 120 秒分析。

    注意：自动节拍估计对 2 倍/半速（及 1.5 倍三连音）存在天然歧义，
    边界处可能相差一个倍频；返回值保留一位小数供人工复核。

    返回：BPM（float）。不可用/失败/近似静音时返回 0.0。
    """

    def _compute() -> float:
        import librosa
        from librosa.feature.rhythm import tempo as _tempo

        y = pcm_mono
        if y.ndim > 1:
            y = y.mean(axis=1)
        y = np.ascontiguousarray(y, dtype=np.float32)

        sr = int(sample_rate) if sample_rate else BPM_TARGET_SR
        if y.size < sr * 2:  # 少于 2 秒，无法可靠估计
            return 0.0
        if float(np.abs(y).max()) < 1e-4:  # 近似静音
            return 0.0

        # 长音频取中间 120 秒（在重采样前裁剪，按原采样率换算）
        if y.size > sr * 180:
            center = y.size // 2
            half = int(sr * (BPM_MAX_ANALYZE_S / 2))
            y = y[center - half: center + half]

        if sr != BPM_TARGET_SR:
            y = librosa.resample(y, orig_sr=sr, target_sr=BPM_TARGET_SR)
            sr = BPM_TARGET_SR

        t = _tempo(y=y, sr=sr, hop_length=BPM_HOP_LENGTH, aggregate=np.median)
        bpm = float(np.atleast_1d(t)[0])
        return bpm if np.isfinite(bpm) else 0.0

    try:
        return _compute()
    except Exception as e:  # noqa: BLE001
        # 缓存损坏类错误 → 清理 JIT 缓存后重试一次（自愈）
        if is_likely_cache_error(e):
            clear_numba_cache()
            try:
                return _compute()
            except Exception:  # noqa: BLE001
                return 0.0
        return 0.0


# --------------------------------------------------------------------------- #
# 汇总入口
# --------------------------------------------------------------------------- #

def analyze_file(path: str, cancel=None) -> AnalysisResult:
    """对一个音频文件执行完整测量。

    cancel: 可选回调（无参、返回 bool）。返回 True 表示用户已请求取消，
            在阶段边界抛出 AnalysisCancelled——用于「停止」按钮对长文件
            及时响应（不会等到整个文件分析完）。
    """
    res = AnalysisResult(path=path, filename=os.path.basename(path))

    def _check_cancel():
        if cancel is not None and cancel():
            raise AnalysisCancelled()

    ffmpeg, _ = ensure_ffmpeg()
    if not ffmpeg:
        res.error = "未找到 ffmpeg，请先安装：brew install ffmpeg"
        return res

    # 1. 基础信息
    _check_cancel()
    try:
        info = probe(path)
        res.sample_rate = info["sample_rate"]
        res.channels = info["channels"]
        res.duration = info["duration"]
    except AnalysisCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        res.error = f"读取失败：{e}"
        return res

    # 2. 响度 / LRA / 真峰值
    _check_cancel()
    try:
        lufs, lra, tp = measure_loudness(path)
        res.integrated_lufs = lufs
        res.loudness_range_lu = lra
        res.true_peak_dbtp = tp
    except AnalysisCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        res.error += f"响度测量失败：{e}；"

    # 3. 解码 + 削波 + 速度 + 频谱质心
    _check_cancel()
    try:
        pcm, sr = decode_pcm(path, res.channels)
        res.total_samples = int(pcm.size)
        _check_cancel()   # 解码完成（耗时阶段之一），及时响应停止

        if pcm.size > 0:
            peak = float(np.max(np.abs(pcm)))
            res.sample_peak_dbfs = 20.0 * np.log10(peak) if peak > 0 else float("-inf")

            clipped, total, ranges = detect_clipping(pcm, sr)
            res.clipped_samples = clipped
            res.total_samples = total
            res.clip_percent = clipped / total if total else 0.0
            res.clipped = len(ranges) > 0   # 以「真削波段」为准（孤立峰值不算）
            res.clip_ranges = ranges

            # 降混单声道（带反相相消保护，避免反相立体声被当成静音）
            mono = downmix_mono(pcm)
            # 速度（BPM）：内部自带异常保护，单点失败不影响其他测量项
            _check_cancel()   # BPM 前（BPM 为最耗时阶段）
            res.bpm = estimate_bpm(mono, sr)
            _check_cancel()
            # 频谱质心（幅度加权 / 功率加权双口径）
            (res.centroid_magnitude_hz,
             res.centroid_power_hz) = spectral_centroids(mono, sr)
    except AnalysisCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        res.error += f"削波/频谱分析失败：{e}"

    return res


def clip_summary(res: AnalysisResult, max_ranges: int = 6) -> str:
    """生成削波情况的可读摘要。"""
    if not res.clipped:
        return "无满幅削波"
    parts = [f"占比 {res.clip_percent_display}%"]
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
