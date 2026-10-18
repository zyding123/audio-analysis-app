#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图表生成模块：把音频渲染成可视化图表（深色报告风格）。

产出两张图：
  1. 频谱图（Spectrogram）——时频热力图（对数频率轴，magma 配色，-70~0 dB）
  2. 平均频谱曲线（Welch PSD）——横轴频率（对数）、纵轴功率谱密度（dB/Hz）

设计说明：
  · 无 GUI 依赖（matplotlib Agg 后端 + Figure API），可在后台线程安全调用；
  · 音频解码复用 analyzer.decode_pcm（ffmpeg，支持全部常见音频格式）；
  · 中文字体优先 PingFang SC（macOS 系统字体）。
"""

import os
import uuid

import numpy as np

import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from analyzer import decode_pcm, downmix_mono, probe

# ---- 报告配色（深色主题，与既有报告风格保持一致）----
BG = "#0f1115"        # 背景
GRID = "#2a2f3a"      # 网格
TXT = "#e6e9ef"       # 主文字
SUB = "#9aa3b2"       # 次级文字
BLUE = "#5d9cec"      # 主曲线
DIM = "#8892a6"       # 参考线/标注

matplotlib.rcParams["font.sans-serif"] = [
    "PingFang SC", "Hiragino Sans GB", "Heiti SC", "Arial Unicode MS", "sans-serif"]
matplotlib.rcParams["axes.unicode_minus"] = False

# 输出图尺寸（150 dpi：宽 1650 px，适合报告/汇报使用）
SPEC_FIGSIZE = (11.0, 3.4)
CURVE_FIGSIZE = (11.0, 3.6)
DPI = 150

# 图表标题（按界面语言）
_TITLES = {
    "zh": {
        "spec": "频谱图 · {name}（对数频率，相对峰值 dB）",
        "curve": "平均频谱（Welch PSD） · {name}",
        "curve_label": "{name}（本曲目）",
    },
    "en": {
        "spec": "Spectrogram · {name} (log frequency, dB re peak)",
        "curve": "Average Spectrum (Welch PSD) · {name}",
        "curve_label": "{name}",
    },
}


def _style_ax(ax):
    ax.set_facecolor(BG)
    ax.grid(color=GRID, lw=0.4, alpha=0.5)
    ax.tick_params(colors=SUB, labelsize=8)
    for sp in ax.spines.values():
        sp.set_color(GRID)


def load_mono(audio_path):
    """解码音频为单声道 float32（复用分析引擎的 ffmpeg 解码；带反相相消保护）。"""
    info = probe(audio_path)
    pcm, sr = decode_pcm(audio_path, int(info.get("channels") or 0))
    mono = downmix_mono(pcm)
    return np.ascontiguousarray(mono, dtype=np.float32), int(sr)


def render_spectrogram(mono, sr, out_png, title):
    """频谱图（时频热力图，对数频率，相对峰值 dB）→ PNG 路径。"""
    import librosa
    import librosa.display

    S = librosa.stft(mono, n_fft=2048, hop_length=512)
    db = librosa.amplitude_to_db(np.abs(S), ref=np.max)

    fig = Figure(figsize=SPEC_FIGSIZE, dpi=DPI)
    FigureCanvasAgg(fig)
    fig.patch.set_facecolor(BG)
    ax = fig.add_subplot(111)
    img = librosa.display.specshow(db, sr=sr, hop_length=512,
                                   x_axis="time", y_axis="log", ax=ax,
                                   cmap="magma", vmin=-70, vmax=0)
    ax.set_xlabel("时间 (s)", color=SUB, fontsize=9)
    ax.set_ylabel("频率 (Hz)", color=SUB, fontsize=9)
    ax.set_title(title, color=TXT, fontsize=11)
    ax.tick_params(colors=SUB, labelsize=8)
    cb = fig.colorbar(img, ax=ax, format="%+2.0f dB")
    cb.ax.tick_params(colors=SUB, labelsize=8)
    cb.outline.set_edgecolor(GRID)
    fig.tight_layout()
    fig.savefig(out_png, facecolor=BG)
    return out_png


def render_spectrum_curve(mono, sr, out_png, title, curve_label):
    """平均频谱曲线（Welch PSD）→ PNG 路径。

    分段/重叠长度随实际样本数自适应（支持 50ms 级短音频；极短 <2 样本时抛错）。
    """
    from scipy import signal as scisig

    n = int(mono.size)
    if n < 2:
        raise ValueError("音频过短，无法计算平均频谱")
    nperseg = max(2, min(8192, n))
    noverlap = nperseg // 2
    f, p = scisig.welch(mono, sr, nperseg=nperseg, noverlap=noverlap)

    fig = Figure(figsize=CURVE_FIGSIZE, dpi=DPI)
    FigureCanvasAgg(fig)
    fig.patch.set_facecolor(BG)
    ax = fig.add_subplot(111)
    ax.semilogx(f, 10 * np.log10(p + 1e-12), color=BLUE, lw=1.3,
                label=curve_label)
    ax.axvline(1000, color=DIM, lw=0.7, ls=":", alpha=0.7)
    ax.axvline(5000, color=DIM, lw=0.7, ls=":", alpha=0.7)
    y0 = ax.get_ylim()[0]
    ax.text(1050, y0 + 2, "1 kHz", color=SUB, fontsize=8)
    ax.text(5200, y0 + 2, "5 kHz", color=SUB, fontsize=8)
    ax.set_xlim(20, 20000)
    ax.set_xlabel("频率 (Hz, 对数)", color=SUB, fontsize=9)
    ax.set_ylabel("功率谱密度 (dB/Hz)", color=SUB, fontsize=9)
    ax.set_title(title, color=TXT, fontsize=11)
    ax.legend(loc="upper right", fontsize=9, framealpha=0.15)
    _style_ax(ax)
    fig.tight_layout()
    fig.savefig(out_png, facecolor=BG)
    return out_png


def _safe_stem(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    cleaned = "".join(ch if ch not in '/\\:*?"<>|' else "_" for ch in stem).strip()
    return cleaned or "audio"


def generate_charts(audio_path, out_dir, display_name, on_chart=None, lang="zh"):
    """生成两张图表。

    audio_path:   音频文件路径
    out_dir:      输出目录（自动创建）
    display_name: 图标题中显示的名称（如 "爱已成空1.mp3"）
    on_chart:     可选回调 on_chart(kind, png_path)——每张图完成时调用一次，
                  kind ∈ {"spectrogram", "spectrum"}（供 UI 流式展示）
    lang:         图表标题语言（"zh" / "en"）

    返回 {"spectrogram": png1, "spectrum": png2}
    """
    try:
        return _generate_impl(audio_path, out_dir, display_name, on_chart, lang)
    except Exception as e:  # noqa: BLE001
        # 缓存损坏类错误 → 清理 JIT 缓存后重试一次（自愈）
        from analyzer import clear_numba_cache, is_likely_cache_error
        if is_likely_cache_error(e):
            clear_numba_cache()
            return _generate_impl(audio_path, out_dir, display_name, on_chart, lang)
        raise


def _generate_impl(audio_path, out_dir, display_name, on_chart=None, lang="zh"):
    os.makedirs(out_dir, exist_ok=True)
    t = _TITLES.get(lang, _TITLES["zh"])
    title_name = os.path.splitext(os.path.basename(display_name))[0] or display_name
    tag = f"{_safe_stem(audio_path)}_{uuid.uuid4().hex[:8]}"

    mono, sr = load_mono(audio_path)

    p_spec = os.path.join(out_dir, f"{tag}_spectrogram.png")
    render_spectrogram(mono, sr, p_spec, t["spec"].format(name=title_name))
    if on_chart:
        on_chart("spectrogram", p_spec)

    p_curve = os.path.join(out_dir, f"{tag}_spectrum.png")
    render_spectrum_curve(mono, sr, p_curve,
                          t["curve"].format(name=title_name),
                          t["curve_label"].format(name=title_name))
    if on_chart:
        on_chart("spectrum", p_curve)

    return {"spectrogram": p_spec, "spectrum": p_curve}


if __name__ == "__main__":
    # 命令行自测：python charts.py <音频文件> [输出目录]
    import sys

    if len(sys.argv) < 2:
        print("用法: python charts.py <音频文件> [输出目录]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/charts_demo"
    paths = generate_charts(
        sys.argv[1], out, os.path.basename(sys.argv[1]),
        on_chart=lambda k, p: print(f"  {k}: {p}"))
    print("完成，共", len(paths), "张图")
