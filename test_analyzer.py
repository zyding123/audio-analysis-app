#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自测脚本：生成几段已知参数的测试音频，验证测量引擎。

生成内容：
  · sine_clean.wav    纯净 1kHz 正弦（-20 dBFS，无削波）
  · sine_clipped.wav  1kHz 正弦被硬限幅到满幅（有明显削波平台）
  · noise.wav         白噪声（高频能量大 → 频谱质心偏高）
  · stereo_clip.wav   双声道，仅右声道削波（验证分声道检测）
  · click_120bpm.wav  120 BPM 脉冲节拍（验证 BPM 检测）
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from analyzer import analyze_file  # noqa: E402

SR = 48000


def write_wav(path, data, sr=SR):
    """data: (n_samples,) 或 (n_samples, n_channels) float32 in [-1, 1]"""
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    n_ch = data.shape[1]
    pcm16 = np.clip(data, -1.0, 1.0)
    pcm16 = (pcm16 * 32767).astype("<i2")
    interleaved = pcm16.reshape(-1, n_ch).tobytes()

    import struct
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(n_ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(interleaved)


def main():
    t = np.arange(SR * 3) / SR  # 3 秒

    # 1. 纯净正弦 -20 dBFS
    amp = 10 ** (-20 / 20)
    sine = amp * np.sin(2 * np.pi * 1000 * t)
    write_wav(os.path.join(HERE, "test_sine_clean.wav"), sine)

    # 2. 削波正弦：放大到 ±1.5 再限幅（产生削波平台）
    loud = 1.5 * np.sin(2 * np.pi * 1000 * t)
    clipped = np.clip(loud, -1.0, 1.0)
    write_wav(os.path.join(HERE, "test_sine_clipped.wav"), clipped)

    # 3. 白噪声
    rng = np.random.default_rng(42)
    noise = 0.2 * rng.standard_normal(len(t))
    write_wav(os.path.join(HERE, "test_noise.wav"), noise)

    # 4. 双声道：左正常正弦，右削波正弦
    stereo = np.stack([sine, clipped], axis=1)
    write_wav(os.path.join(HERE, "test_stereo_clip.wav"), stereo)

    # 5. 120 BPM 节拍脉冲（验证 BPM 检测，10 秒）
    seconds = 10
    beat = np.zeros(SR * seconds, dtype=np.float32)
    interval = 60.0 / 120.0  # 120 BPM → 每 0.5 秒一拍
    t_click = np.linspace(0, 0.08, int(0.08 * SR), endpoint=False)
    click = np.sin(2 * np.pi * 1000 * t_click) * np.exp(-t_click * 60)
    k = 0
    while k * interval < seconds:
        st = int(k * interval * SR)
        en = min(st + len(click), len(beat))
        beat[st:en] += click[:en - st]
        k += 1
    write_wav(os.path.join(HERE, "test_click_120bpm.wav"), beat)

    print("=== 测量结果 ===\n")
    for name in ["test_sine_clean.wav", "test_sine_clipped.wav",
                 "test_noise.wav", "test_stereo_clip.wav",
                 "test_click_120bpm.wav"]:
        path = os.path.join(HERE, name)
        r = analyze_file(path)
        print(f"【{name}】")
        print(f"  时长: {r.duration:.2f}s | {r.channels}ch | {r.sample_rate}Hz")
        print(f"  LUFS: {r.integrated_lufs:+.2f} | LRA: {r.loudness_range_lu:.2f} LU")
        print(f"  真峰值: {r.true_peak_dbtp:+.2f} dBTP | 样本峰值: {r.sample_peak_dbfs:+.2f} dBFS")
        print(f"  频谱质心·幅度加权: {r.centroid_magnitude_hz:.0f} Hz | "
              f"功率加权: {r.centroid_power_hz:.0f} Hz")
        print(f"  BPM: {r.bpm:.1f}")
        print(f"  削波: {'是' if r.clipped else '否'} "
              f"| 占比 {r.clip_percent_display:.3f}% "
              f"| 区间数 {len(r.clip_ranges)}")
        if r.clip_ranges:
            for cr in r.clip_ranges[:3]:
                print(f"    → CH{cr.channel} {cr.start:.3f}–{cr.end:.3f}s")
        if r.error:
            print(f"  ⚠ 错误: {r.error}")
        print()


if __name__ == "__main__":
    main()
