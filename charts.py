"""Phase-safe audio charts with bounded FFT batches and serialized Matplotlib access."""
import os
import threading
import uuid

import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from analyzer import AnalysisCancelled, check_cancel, decode_pcm, probe

BG, GRID, TXT, SUB, BLUE, DIM = '#0f1115', '#2a2f3a', '#e6e9ef', '#9aa3b2', '#5d9cec', '#8892a6'
DPI = 150
SPEC_FIGSIZE, CURVE_FIGSIZE = (11, 3.4), (11, 3.6)
_RENDER_LOCK = threading.Lock()
matplotlib.rcParams['font.sans-serif'] = ['PingFang SC', 'Hiragino Sans GB', 'Arial Unicode MS', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False


def load_audio(path, cancel=None):
    info = probe(path, cancel=cancel)
    return decode_pcm(path, info['channels'], cancel=cancel)


def _channels(pcm):
    return pcm[:, None] if pcm.ndim == 1 else pcm


def spectrogram_db(pcm, sr, cancel=None):
    """Root mean channel power, never waveform sum; cap display at about 4000 frames."""
    import librosa
    data = _channels(pcm)
    if len(data) < 2:
        raise ValueError('音频过短，无法计算频谱图')
    n_fft = min(2048, len(data))
    hop = max(512, int(np.ceil(len(data) / 4000)))
    power = None
    for ch in range(data.shape[1]):
        check_cancel(cancel)
        x = librosa.stft(data[:, ch], n_fft=n_fft, hop_length=hop)
        p = np.abs(x) ** 2
        power = p if power is None else power + p
    power /= data.shape[1]
    if not np.isfinite(power).all():
        raise ValueError('音频含无效样本')
    peak = float(power.max())
    if peak <= 1e-20:
        db = np.full(power.shape, -70.0)
    else:
        db = 10 * np.log10(np.maximum(power / peak, 1e-7))
    return db, hop, n_fft


def average_psd(pcm, sr, cancel=None):
    """Welch PSD averaged over channel powers, using bounded batches of complete windows."""
    from scipy import signal
    data = _channels(pcm)
    n = len(data)
    if n < 2:
        raise ValueError('音频过短，无法计算平均频谱')
    nperseg = min(8192, n)
    overlap = nperseg // 2
    hop = nperseg - overlap
    batch = max(1, 128 // data.shape[1])
    total, count = None, 0
    for start in range(0, n - nperseg + 1, batch * hop):
        check_cancel(cancel)
        part = data[start:min(n, start + nperseg + (batch - 1) * hop)]
        windows = (len(part) - nperseg) // hop + 1
        f, p = signal.welch(part, sr, nperseg=nperseg, noverlap=overlap, axis=0)
        p = p.mean(axis=1)
        total = p * windows if total is None else total + p * windows
        count += windows
    return f, total / count


def _figure(size):
    fig = Figure(figsize=size, dpi=DPI)
    FigureCanvasAgg(fig)
    fig.patch.set_facecolor(BG)
    ax = fig.add_subplot(111)
    ax.set_facecolor(BG)
    ax.tick_params(colors=SUB, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    return fig, ax


def render_spectrogram(pcm, sr, out_png, title, *, lang='zh', cancel=None):
    import librosa.display
    db, hop, n_fft = spectrogram_db(pcm, sr, cancel)
    fig, ax = _figure(SPEC_FIGSIZE)
    img = librosa.display.specshow(db, sr=sr, hop_length=hop, n_fft=n_fft,
                                  x_axis='time', y_axis='log', ax=ax,
                                  cmap='magma', vmin=-70, vmax=0)
    ax.set_xlabel('时间 (s)' if lang == 'zh' else 'Time (s)', color=SUB, fontsize=9)
    ax.set_ylabel('频率 (Hz)' if lang == 'zh' else 'Frequency (Hz)', color=SUB, fontsize=9)
    ax.set_title(title, color=TXT, fontsize=11)
    cb = fig.colorbar(img, ax=ax, format='%+2.0f dB')
    cb.ax.tick_params(colors=SUB, labelsize=8)
    cb.outline.set_edgecolor(GRID)
    fig.tight_layout()
    check_cancel(cancel)
    fig.savefig(out_png, facecolor=BG)
    fig.clear()
    return out_png


def render_spectrum_curve(pcm, sr, out_png, title, curve_label, *, lang='zh', cancel=None):
    f, p = average_psd(pcm, sr, cancel)
    fig, ax = _figure(CURVE_FIGSIZE)
    ax.semilogx(f[1:], 10 * np.log10(p[1:] + 1e-20), color=BLUE, lw=1.3, label=curve_label)
    ax.set_xlim(20, max(21, min(20000, sr / 2)))
    ax.set_xlabel('频率 (Hz, 对数)' if lang == 'zh' else 'Frequency (Hz, log)', color=SUB, fontsize=9)
    ax.set_ylabel('功率谱密度 (dB/Hz)' if lang == 'zh' else 'Power spectral density (dB/Hz)', color=SUB, fontsize=9)
    ax.set_title(title, color=TXT, fontsize=11)
    ax.grid(color=GRID, lw=0.4, alpha=0.5)
    ax.legend(loc='upper right', fontsize=9, framealpha=0.15)
    fig.tight_layout()
    check_cancel(cancel)
    fig.savefig(out_png, facecolor=BG)
    fig.clear()
    return out_png


def generate_charts(audio_path, out_dir, display_name, on_chart=None, lang='zh', cancel=None):
    os.makedirs(out_dir, exist_ok=True)
    pcm, sr = load_audio(audio_path, cancel)
    name = os.path.splitext(os.path.basename(display_name))[0]
    tag = uuid.uuid4().hex
    paths = {kind: os.path.join(out_dir, f'{tag}_{kind}.png')
             for kind in ('spectrogram', 'spectrum')}
    while not _RENDER_LOCK.acquire(timeout=0.1):
        check_cancel(cancel)
    try:
        check_cancel(cancel)
        title = f'频谱图 · {name}（各声道功率汇总，相对峰值 dB）' if lang == 'zh' else f'Spectrogram · {name} (channel power, dB re peak)'
        render_spectrogram(pcm, sr, paths['spectrogram'], title, lang=lang, cancel=cancel)
        if on_chart:
            on_chart('spectrogram', paths['spectrogram'])
        title = f'平均频谱（Welch PSD） · {name}' if lang == 'zh' else f'Average spectrum (Welch PSD) · {name}'
        render_spectrum_curve(pcm, sr, paths['spectrum'], title, name, lang=lang, cancel=cancel)
        if on_chart:
            on_chart('spectrum', paths['spectrum'])
    except BaseException:
        for path in paths.values():
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
    finally:
        _RENDER_LOCK.release()
    return paths
