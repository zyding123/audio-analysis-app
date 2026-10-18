#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音频分析 — macOS 桌面 GUI。

特性：
  · 拖入音频文件/文件夹，后台自动分析，无需实时播放
  · 逐文件测量：积分响度 LUFS / 响度范围 LRA / 真峰值 / 频谱质心 / 满幅削波 / 速度 BPM
  · 表格展示结果，可导出 CSV / JSON
  · 选中记录即可试听（播放 / 暂停按钮，空格键控制）
  · 支持浅色 / 深色 / 跟随系统主题，内置设置对话框
"""

import csv
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field

from PySide6.QtCore import (
    Qt, QThread, QEvent, QObject, QSettings, QTimer, QSize, QUrl,
    QPointF, QRectF, Signal, Slot,
)
from PySide6.QtGui import (
    QColor, QDragEnterEvent, QDropEvent, QFont,
    QPainter, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFileDialog, QAbstractItemView, QMessageBox,
    QDialog, QFormLayout, QGroupBox, QComboBox, QDoubleSpinBox, QCheckBox,
    QSplitter, QListWidget, QListWidgetItem, QLineEdit, QStyle, QAbstractSpinBox,
    QSizePolicy, QScrollArea,
)

# 播放引擎（QtMultimedia）：不可用时其余功能仍正常工作，播放按钮自动禁用
try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    _PLAYER_AVAILABLE = True
except Exception:  # noqa: BLE001
    QAudioOutput = None  # type: ignore
    QMediaPlayer = None  # type: ignore
    _PLAYER_AVAILABLE = False

from analyzer import (
    AnalysisCancelled, AnalysisResult, analyze_file, clip_summary, ensure_ffmpeg,
)


def _log(msg: str):
    """写诊断日志（append + 立即落盘，崩溃时也能保留最后执行到哪一步）。"""
    try:
        import time
        path = os.path.join(os.path.expanduser("~"), "Library", "Logs",
                            "AudioAnalysis.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# =========================================================================
# 应用版本
# =========================================================================

APP_VERSION = "1.0.0"   # 首个公开发布版本（与 GitHub Release tag / Info.plist 保持一致）


# =========================================================================
# 国际化（中文 / 英语）
# =========================================================================

_LANG = "zh"

_T = {
    "app_title": ("音频分析", "Audio Analysis"),
    "main_title": ("音频分析", "Audio Analysis"),
    "btn_library": ("☰ 曲库", "☰ Library"),
    "library_title": ("🎵 曲库", "🎵 Library"),
    "library_search_ph": ("搜索歌曲…", "Search songs…"),
    "btn_add": ("添加", "Add"),
    "btn_multi": ("多选", "Select"),
    "btn_delete": ("删除", "Delete"),
    "library_hint": ("双击歌曲开始分析", "Double-click a song to analyze"),
    "btn_open": ("打开文件…", "Open Files…"),
    "btn_clear": ("清空列表", "Clear List"),
    "btn_stop": ("停止", "Stop"),
    "btn_play": ("播放", "Play"),
    "btn_pause": ("暂停", "Pause"),
    "tip_play": ("播放选中的音频（空格键）", "Play selected audio (Space)"),
    "tip_pause": ("暂停播放（空格键）", "Pause playback (Space)"),
    "seek_tip": ("拖动可定位播放位置；红色竖线 = 满幅削波位置",
                 "Drag to seek; red line = full-scale clipping position"),
    "status_need_select": ("请先在表格中选择一条记录", "Select a record in the table first"),
    "status_play_failed": ("播放失败：", "Playback failed: "),
    "status_player_unavailable": ("播放组件不可用（缺少 QtMultimedia）",
                                  "Player unavailable (QtMultimedia missing)"),
    # ── 图表窗口 ──
    "tip_table_dblclick": ("双击任意记录可打开频谱图表（声谱图 + 平均频谱曲线）",
                           "Double-click a record to open charts (spectrogram + spectrum)"),
    "chart_generating": ("正在生成图表（首次约需几秒）…", "Generating charts…"),
    "chart_step_curve": ("频谱图完成，正在绘制平均频谱曲线…",
                         "Spectrogram done, rendering spectrum curve…"),
    "chart_done": ("生成完成", "Done"),
    "chart_spec_caption": ("频谱图（时频能量分布，对数频率刻度）",
                           "Spectrogram (time–frequency, log frequency)"),
    "chart_curve_caption": ("平均频谱曲线（Welch PSD）",
                            "Average Spectrum Curve (Welch PSD)"),
    "chart_save_spec": ("保存频谱图…", "Save Spectrogram…"),
    "chart_save_curve": ("保存频谱曲线…", "Save Spectrum…"),
    "chart_saved_to": ("已保存到：", "Saved to: "),
    "chart_gen_failed": ("图表生成失败：", "Chart generation failed: "),
    "chart_close": ("关闭", "Close"),
    "btn_select": ("多选", "Select"),
    "btn_delete_selected": ("删除选中", "Delete Selected"),
    "btn_export_csv": ("导出 CSV", "Export CSV"),
    "btn_export_json": ("导出 JSON", "Export JSON"),
    "btn_settings": ("⚙ 设置", "⚙ Settings"),
    "col_filename": ("文件名", "Filename"),
    "col_duration": ("时长", "Duration"),
    "col_channels": ("声道/采样率", "Ch/SR"),
    "col_lufs": ("LUFS 积分响度", "LUFS Integrated"),
    "col_lra": ("LRA (LU)", "LRA (LU)"),
    "col_true_peak": ("真峰值 dBTP", "True Peak dBTP"),
    "col_centroid_mag": ("频谱质心 幅度加权", "Centroid Magnitude"),
    "col_centroid_pow": ("频谱质心 功率加权", "Centroid Power"),
    "col_centroid_mag_tip": ("幅度加权（行业标准口径）：Σf·|X| / Σ|X|，单位 Hz。"
                             "逐帧质心的时间算术平均，与 librosa / 教科书定义一致。",
                             "Magnitude-weighted centroid (industry standard): "
                             "Σf·|X|/Σ|X| Hz, time-averaged over frames."),
    "col_centroid_pow_tip": ("功率加权（旧版口径）：Σf·|X|² / Σ|X|²，单位 Hz。"
                             "逐帧质心的时间算术平均；数值通常明显低于幅度加权。",
                             "Power-weighted centroid (legacy): Σf·|X|²/Σ|X|² Hz, "
                             "time-averaged over frames."),
    "col_bpm": ("BPM", "BPM"),
    "col_bpm_tip": ("自动检测速度（BPM，整数显示）；受节拍强弱影响，可能出现半速/倍速歧义",
                    "Auto-detected tempo (integer); may be ambiguous by ×2/÷2"),
    "col_clip": ("削波", "Clipping"),
    "col_clip_detail": ("削波位置/占比", "Clip Location/Ratio"),
    "settings_title": ("设置", "Settings"),
    "group_appearance": ("外观", "Appearance"),
    "label_theme": ("主题：", "Theme:"),
    "theme_light": ("浅色", "Light"),
    "theme_dark": ("深色", "Dark"),
    "theme_auto": ("跟随系统", "Follow System"),
    "group_language": ("语言", "Language"),
    "label_language": ("语言：", "Language:"),
    "lang_zh": ("中文", "中文"),
    "lang_en": ("English", "English"),
    "theme_hint": ("「跟随系统」会在系统切换深浅色时自动跟随（macOS 控制中心）。",
                    "Follow System auto-tracks macOS light/dark mode."),
    "group_analysis": ("分析", "Analysis"),
    "label_target_lufs": ("目标 LUFS 参考值：", "Target LUFS:"),
    "label_tp_warn": ("真峰值警告阈值：", "True Peak Warning Threshold:"),
    "lufs_hint": ("常见平台：QQ 音乐/网易云约 -14，Spotify -14，YouTube -14，Apple Music -16，广播 -23。",
                   "Common targets: Spotify/YouTube -14, Apple Music -16, broadcast -23."),
    "group_behavior": ("行为", "Behavior"),
    "label_append_on_drop": ("拖入新文件时追加到当前列表（取消勾选则清空再分析）",
                             "Append when dropping new files (clear otherwise)"),
    "label_clear_on_start": ("启动时自动清空上次结果列表", "Clear results on startup"),
    "label_auto_add_library": ("拖入分析界面时自动加入曲库",
                               "Auto-add to library when dropped into analysis"),
    "btn_save": ("保存", "Save"),
    "btn_cancel": ("取消", "Cancel"),
    "status_ready": ("就绪", "Ready"),
    "status_analyzing": ("分析中…", "Analyzing…"),
    "status_progress": ("分析中… {done}/{total}", "Analyzing… {done}/{total}"),
    "status_done": ("完成，共 {n} 个结果", "Done, {n} result(s)"),
    "status_cleared": ("已清空列表", "List cleared"),
    "status_deleted": ("已删除 {n} 条记录", "Deleted {n} record(s)"),
    "status_multi_on": ("多选模式：点按选择，点「删除选中」移除",
                        "Selection mode: click to select, then Delete Selected"),
    "status_multi_off": ("已退出多选", "Exited selection mode"),
    "status_none_selected": ("未选择任何记录", "No record selected"),
    "status_no_audio": ("未找到可分析的音频文件", "No analyzable audio files found"),
    "status_file_missing": ("文件不存在：", "File not found: "),
    "status_stopping": ("正在停止…", "Stopping…"),
    "status_stopped": ("已停止（保留 {n} 个结果）", "Stopped ({n} result(s) kept)"),
    "status_exported": ("已导出：", "Exported: "),
    "export_failed": ("导出失败", "Export failed"),
    "clip_yes": ("⚠ 有削波", "⚠ Clipped"),
    "clip_maybe": ("可能削波", "Possibly clipped"),
    "clip_ok": ("正常", "OK"),
    "no_clip": ("无满幅削波", "No full-scale clipping"),
    "drop_title": ("将音频文件或文件夹拖到这里", "Drop audio files or folders here"),
    "drop_hint": ("支持 WAV / AIFF / FLAC / MP3 / M4A / AAC / OGG 等 · 后台自动分析，无需播放",
                   "Supports WAV / AIFF / FLAC / MP3 / M4A / AAC / OGG · auto-analyzes in background"),
}


def tr(key: str, **kwargs) -> str:
    idx = 0 if _LANG == "zh" else 1
    text = _T.get(key, (key, key))[idx]
    if kwargs:
        return text.format(**kwargs)
    return text


def set_language(lang: str):
    global _LANG
    _LANG = "zh" if lang == "zh" else "en"


# CSV 导出表头（按当前语言）
_CSV_HEADERS = {
    "zh": ["文件名", "路径", "时长(s)", "声道", "采样率", "LUFS积分响度",
           "LRA(LU)", "真峰值(dBTP)", "样本峰值(dBFS)",
           "频谱质心·幅度加权(Hz)", "频谱质心·功率加权(Hz)",
           "是否削波", "削波样本数", "削波占比(%)", "削波位置", "BPM", "错误"],
    "en": ["Filename", "Path", "Duration(s)", "Channels", "Sample Rate",
           "Integrated LUFS", "LRA(LU)", "True Peak(dBTP)", "Sample Peak(dBFS)",
           "Centroid Mag.(Hz)", "Centroid Pow.(Hz)",
           "Clipped", "Clipped Samples", "Clip Percent(%)", "Clip Positions",
           "BPM", "Error"],
}

AUDIO_EXTS = {
    # 音频格式
    ".wav", ".aiff", ".aif", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".oga",
    ".opus", ".wma", ".caf", ".ape",
    # 视频格式（只分析其中的音频轨）
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".wmv",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp", ".vob",
}

# 文件对话框过滤器中展示的音频/视频格式
MEDIA_FILE_FILTER = (
    "音频/视频文件 (*.wav *.aiff *.aif *.flac *.mp3 *.m4a *.aac *.ogg *.opus "
    "*.mp4 *.mov *.m4v *.mkv *.webm *.avi *.wmv *.ts *.mpg *.mpeg);;所有文件 (*)"
)


def collect_audio_files(paths):
    result = []
    seen = set()
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    full = os.path.join(root, f)
                    if os.path.splitext(f)[1].lower() in AUDIO_EXTS:
                        if full not in seen:
                            seen.add(full)
                            result.append(full)
        elif os.path.isfile(p):
            if p not in seen:
                seen.add(p)
                result.append(p)
    return result


# =========================================================================
# 曲库
# =========================================================================

@dataclass
class LibraryItem:
    path: str = ""
    filename: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "filename": self.filename}

    @classmethod
    def from_dict(cls, d: dict) -> "LibraryItem":
        return cls(path=d.get("path", ""), filename=d.get("filename", ""))


class LibraryPanel(QWidget):
    """左侧曲库边栏：暂存歌曲（不立即分析），双击才进入右侧分析界面。"""

    activate_requested = Signal(str)   # 双击条目 → 文件路径
    data_changed = Signal()            # 增删后发射（用于持久化）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("libraryPanel")
        self.setAcceptDrops(True)
        self._items = []            # list[LibraryItem]
        self._selection_mode = False
        self.setMinimumWidth(230)
        self.setMaximumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.title_label = QLabel(tr("library_title"))
        self.title_label.setObjectName("libraryTitle")
        layout.addWidget(self.title_label)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("library_search_ph"))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._refresh)
        layout.addWidget(self.search_box)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton(tr("btn_add"))
        self.btn_add.clicked.connect(self._open_add_dialog)
        self.btn_multi = QPushButton(tr("btn_multi"))
        self.btn_multi.setCheckable(True)
        self.btn_multi.clicked.connect(self._toggle_selection)
        self.btn_delete = QPushButton(tr("btn_delete"))
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_multi)
        btn_row.addWidget(self.btn_delete)
        layout.addLayout(btn_row)

        self.hint_label = QLabel(tr("library_hint"))
        self.hint_label.setObjectName("dropHint")
        layout.addWidget(self.hint_label)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        self.list_widget.itemSelectionChanged.connect(self._update_delete_btn)
        layout.addWidget(self.list_widget, stretch=1)

    def retranslate(self):
        self.title_label.setText(tr("library_title"))
        self.search_box.setPlaceholderText(tr("library_search_ph"))
        self.btn_add.setText(tr("btn_add"))
        self.btn_multi.setText(tr("btn_multi"))
        self.btn_delete.setText(tr("btn_delete"))
        self.hint_label.setText(tr("library_hint"))

    # ---- 数据访问 ----
    def set_items(self, items):
        self._items = list(items)
        self._refresh()

    def get_items(self):
        return list(self._items)

    def add_paths(self, paths):
        existing = {it.path for it in self._items}
        added = 0
        for p in paths:
            if p and p not in existing:
                self._items.append(LibraryItem(path=p, filename=os.path.basename(p)))
                existing.add(p)
                added += 1
        if added:
            self._refresh()
            self.data_changed.emit()

    # ---- 内部 ----
    def _refresh(self):
        query = self.search_box.text().strip().lower()
        self.list_widget.clear()
        for it in self._items:
            if query and query not in it.filename.lower():
                continue
            item = QListWidgetItem(it.filename)
            item.setData(Qt.UserRole, it.path)
            item.setToolTip(it.path)
            self.list_widget.addItem(item)

    def _on_double_click(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self.activate_requested.emit(path)

    def _update_delete_btn(self):
        has = len(self.list_widget.selectedItems()) > 0
        self.btn_delete.setEnabled(has and self._selection_mode)

    def _toggle_selection(self):
        self._selection_mode = self.btn_multi.isChecked()
        self.list_widget.setSelectionMode(
            QAbstractItemView.MultiSelection if self._selection_mode
            else QAbstractItemView.SingleSelection)
        if not self._selection_mode:
            self.list_widget.clearSelection()
        self._update_delete_btn()

    def _delete_selected(self):
        paths = {it.data(Qt.UserRole) for it in self.list_widget.selectedItems()}
        if not paths:
            return
        self._items = [it for it in self._items if it.path not in paths]
        self._refresh()
        self._update_delete_btn()
        self.data_changed.emit()

    def _open_add_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "添加歌曲到曲库", "", MEDIA_FILE_FILTER)
        if files:
            self.add_paths(files)

    # ---- 拖拽 ----
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        self.add_paths(collect_audio_files(paths))


# =========================================================================
# 配置（持久化到 QSettings，macOS 下存到 ~/Library/Preferences/）
# =========================================================================

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_AUTO = "auto"


class AppConfig:
    KEY_THEME = "ui/theme"                       # light / dark / auto
    KEY_LANGUAGE = "ui/language"                 # zh / en
    KEY_TARGET_LUFS = "analysis/target_lufs"     # float, 用于显示偏差
    KEY_TRUE_PEAK_WARN = "analysis/tp_warn_db"   # float, 真峰值警告阈值
    KEY_APPEND_ON_DROP = "behavior/append_on_drop"     # bool
    KEY_CLEAR_ON_START = "behavior/clear_on_start"     # bool
    KEY_AUTO_ADD_LIBRARY = "behavior/auto_add_library"  # bool

    DEFAULTS = {
        KEY_THEME: THEME_AUTO,
        KEY_LANGUAGE: "zh",
        KEY_TARGET_LUFS: -14.0,
        KEY_TRUE_PEAK_WARN: -1.0,
        KEY_APPEND_ON_DROP: True,
        KEY_CLEAR_ON_START: False,
        KEY_AUTO_ADD_LIBRARY: False,
    }

    def __init__(self):
        self.s = QSettings("WorkBuddy", "AudioBatchAnalyzer")

    def get(self, key):
        default = self.DEFAULTS.get(key)
        v = self.s.value(key, default)
        # 类型修正
        if isinstance(default, bool):
            if isinstance(v, str):
                return v.lower() in ("1", "true", "yes", "on")
            return bool(v)
        if isinstance(default, int) and isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return default
        if isinstance(default, float) and isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return default
        return v

    def set(self, key, value):
        self.s.setValue(key, value)
        self.s.sync()


# =========================================================================
# 主题
# =========================================================================

STYLE_LIGHT = """
QWidget { background: #ffffff; color: #333333; }
QLabel#title { font-size: 20px; font-weight: 700; color: #222; }
QLabel#status { color: #888; }
QPushButton {
    color: #333333; background: #f8f8f8;
    border: 1px solid #cccccc; border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover { background: #eef5fc; border-color: #4a90d9; }
QPushButton:pressed { background: #d8e8f7; }
QPushButton:disabled { color: #bbbbbb; background: #f5f5f5; border-color: #e0e0e0; }
QFrame#dropZone {
    border: 2px dashed #bbbbbb; border-radius: 12px;
    background: #fafafa;
}
QFrame#dropZone:hover { border-color: #4a90d9; background: #f2f8ff; }
QLabel#dropText { font-size: 16px; font-weight: 600; color: #666; }
QLabel#dropHint { font-size: 12px; color: #999; }
QTableWidget {
    background: #ffffff; alternate-background-color: #f7f7f7;
    gridline-color: #e5e5e5; color: #333333;
    selection-background-color: #cfe4f7; selection-color: #1a1a1a;
}
QHeaderView::section {
    background: #f0f0f0; color: #333333;
    padding: 4px 6px; border: 1px solid #d0d0d0;
}
QProgressBar {
    background: #f0f0f0; color: #333333;
    border: 1px solid #cccccc; border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk { background: #4a90d9; border-radius: 3px; }
QComboBox, QDoubleSpinBox, QSpinBox {
    background: #ffffff; color: #333333;
    border: 1px solid #cccccc; border-radius: 4px;
    padding: 3px 8px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #cccccc;
    background: #f0f0f0;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid #333333;
    margin-right: 4px;
}
QGroupBox {
    border: 1px solid #e0e0e0; border-radius: 6px;
    margin-top: 12px; padding-top: 8px; color: #333;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QCheckBox { color: #333; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QLabel#libraryTitle { font-size: 16px; font-weight: 700; color: #333333; }
QLineEdit {
    background: #ffffff; color: #333333;
    border: 1px solid #cccccc; border-radius: 4px;
    padding: 4px 8px;
}
QListWidget {
    background: #ffffff; color: #333333;
    border: 1px solid #dddddd; border-radius: 4px;
}
QListWidget::item { padding: 5px; }
QListWidget::item:selected { background: #cfe4f7; color: #1a1a1a; }
/* 播放控制条文字 */
QLabel#timeLabel { color: #666666; font-size: 12px; }
QLabel#nowPlaying { color: #555555; font-size: 12px; }
"""


STYLE_DARK = """
QWidget { background: #1e1e1e; color: #e8e8e8; }
QLabel#title { font-size: 20px; font-weight: 700; color: #f0f0f0; }
QLabel#status { color: #aaaaaa; }
QPushButton {
    color: #e8e8e8; background: #3a3a3a;
    border: 1px solid #555555; border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover { background: #4a4a4a; border-color: #4a90d9; }
QPushButton:pressed { background: #2a2a2a; }
QPushButton:disabled { color: #777777; background: #2a2a2a; border-color: #3a3a3a; }
QFrame#dropZone {
    border: 2px dashed #555555; border-radius: 12px;
    background: #2a2a2a;
}
QFrame#dropZone:hover { border-color: #4a90d9; background: #333333; }
QLabel#dropText { font-size: 16px; font-weight: 600; color: #e0e0e0; }
QLabel#dropHint { font-size: 12px; color: #aaaaaa; }
QTableWidget {
    background: #252525; alternate-background-color: #2e2e2e;
    gridline-color: #3a3a3a; color: #e8e8e8;
    selection-background-color: #2c5d8f; selection-color: #ffffff;
}
QHeaderView::section {
    background: #2e2e2e; color: #e8e8e8;
    padding: 4px 6px; border: 1px solid #3a3a3a;
}
QProgressBar {
    background: #2a2a2a; color: #e8e8e8;
    border: 1px solid #3a3a3a; border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk { background: #4a90d9; border-radius: 3px; }
QComboBox, QDoubleSpinBox, QSpinBox {
    background: #2a2a2a; color: #e8e8e8;
    border: 1px solid #555555; border-radius: 4px;
    padding: 3px 8px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #777;
    background: #4a4a4a;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid #e8e8e8;
    margin-right: 4px;
}
QComboBox QAbstractItemView {
    background: #2a2a2a; color: #e8e8e8;
    selection-background-color: #4a90d9;
}
QGroupBox {
    border: 1px solid #3a3a3a; border-radius: 6px;
    margin-top: 12px; padding-top: 8px; color: #e0e0e0;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QCheckBox { color: #e0e0e0; spacing: 6px; }
QDialog { background: #1e1e1e; }
QToolTip {
    background: #3a3a3a; color: #e8e8e8;
    border: 1px solid #555;
}
QLabel#libraryTitle { font-size: 16px; font-weight: 700; color: #e0e0e0; }
QLineEdit {
    background: #2a2a2a; color: #e8e8e8;
    border: 1px solid #555555; border-radius: 4px;
    padding: 4px 8px;
}
QLineEdit:focus { border-color: #4a90d9; }
QListWidget {
    background: #252525; color: #e8e8e8;
    border: 1px solid #3a3a3a; border-radius: 4px;
}
QListWidget::item { padding: 5px; }
QListWidget::item:selected { background: #2c5d8f; color: #ffffff; }
/* 播放控制条文字 */
QLabel#timeLabel { color: #aaaaaa; font-size: 12px; }
QLabel#nowPlaying { color: #bbbbbb; font-size: 12px; }
"""


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(30, 30, 30))
    p.setColor(QPalette.WindowText, QColor(232, 232, 232))
    p.setColor(QPalette.Base, QColor(37, 37, 37))
    p.setColor(QPalette.AlternateBase, QColor(46, 46, 46))
    p.setColor(QPalette.Text, QColor(232, 232, 232))
    p.setColor(QPalette.Button, QColor(58, 58, 58))
    p.setColor(QPalette.ButtonText, QColor(232, 232, 232))
    p.setColor(QPalette.Highlight, QColor(74, 144, 217))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.PlaceholderText, QColor(140, 140, 140))
    p.setColor(QPalette.ToolTipBase, QColor(58, 58, 58))
    p.setColor(QPalette.ToolTipText, QColor(232, 232, 232))
    p.setColor(QPalette.Link, QColor(74, 144, 217))
    p.setColor(QPalette.BrightText, QColor(255, 107, 107))
    return p


def detect_system_dark() -> bool:
    """通过读取系统 plist 检测深浅色（纯 Python，不用 subprocess / Qt 接口）。"""
    _log("detect_system_dark: 读 plist")
    try:
        import plistlib
        path = os.path.expanduser("~/Library/Preferences/.GlobalPreferences.plist")
        with open(path, "rb") as f:
            data = plistlib.load(f)
        style = data.get("AppleInterfaceStyle", "")
        dark = "Dark" in str(style)
        _log(f"detect_system_dark: style={style!r}, dark={dark}")
        return dark
    except Exception as e:
        _log(f"detect_system_dark: 异常 {e!r}")
        return False


def resolve_theme(theme_setting: str) -> str:
    """配置值 → 实际生效主题（auto 解析为 light/dark）。"""
    if theme_setting == THEME_AUTO:
        return THEME_DARK if detect_system_dark() else THEME_LIGHT
    return theme_setting


_theme_state = {"applying": False, "last": None}


def apply_theme(app: QApplication, theme: str):
    """theme: THEME_LIGHT / THEME_DARK。设置全局样式 + 调色板。

    注意：setStyle / setPalette 会触发 StyleChange / PaletteChange 事件，
    进而回调 MainWindow.changeEvent；若无重入保护会无限递归导致崩溃。
    """
    if theme not in (THEME_LIGHT, THEME_DARK):
        theme = THEME_LIGHT
    if _theme_state["applying"]:
        _log(f"apply_theme({theme}): 重入保护触发，跳过")
        return  # 重入保护：打断 setStyle→StyleChange→changeEvent→apply_theme 递归
    if _theme_state["last"] == theme:
        _log(f"apply_theme({theme}): 主题未变，跳过")
        return  # 主题未变化，跳过

    _theme_state["applying"] = True
    try:
        _log(f"apply_theme({theme}): setStyle Fusion 前")
        app.setStyle("Fusion")
        _log(f"apply_theme({theme}): setStyle 完成")
        if theme == THEME_DARK:
            _log(f"apply_theme({theme}): setPalette dark 前")
            app.setPalette(_dark_palette())
            _log(f"apply_theme({theme}): setPalette dark 完成")
            app.setStyleSheet(STYLE_DARK)
        else:
            # 浅色模式：恢复默认调色板，仅覆盖 stylesheet
            app.setPalette(app.style().standardPalette())
            app.setStyleSheet(STYLE_LIGHT)
        _theme_state["last"] = theme
        _log(f"apply_theme({theme}): 完成")
    finally:
        _theme_state["applying"] = False


# =========================================================================
# 设置对话框
# =========================================================================

class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(f"{tr('settings_title')} · v{APP_VERSION}")
        self.setMinimumWidth(460)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ── 外观 ──
        self.gb_appearance = QGroupBox(tr("group_appearance"))
        form_a = QFormLayout(self.gb_appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("theme_light"), THEME_LIGHT)
        self.theme_combo.addItem(tr("theme_dark"), THEME_DARK)
        self.theme_combo.addItem(tr("theme_auto"), THEME_AUTO)
        cur = self.config.get(AppConfig.KEY_THEME)
        idx_map = {THEME_LIGHT: 0, THEME_DARK: 1, THEME_AUTO: 2}
        self.theme_combo.setCurrentIndex(idx_map.get(cur, 2))
        self.label_theme = QLabel(tr("label_theme"))
        form_a.addRow(self.label_theme, self.theme_combo)
        self.theme_hint = QLabel(tr("theme_hint"))
        self.theme_hint.setWordWrap(True)
        self.theme_hint.setObjectName("dropHint")
        form_a.addRow("", self.theme_hint)
        root.addWidget(self.gb_appearance)

        # ── 语言 ──
        self.gb_language = QGroupBox(tr("group_language"))
        form_lang = QFormLayout(self.gb_language)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(tr("lang_zh"), "zh")
        self.lang_combo.addItem(tr("lang_en"), "en")
        cur_lang = self.config.get(AppConfig.KEY_LANGUAGE)
        self.lang_combo.setCurrentIndex(0 if cur_lang == "zh" else 1)
        self.label_language = QLabel(tr("label_language"))
        form_lang.addRow(self.label_language, self.lang_combo)
        root.addWidget(self.gb_language)

        # ── 分析 ──
        self.gb_analysis = QGroupBox(tr("group_analysis"))
        form_an = QFormLayout(self.gb_analysis)
        self.target_lufs = QDoubleSpinBox()
        self.target_lufs.setRange(-30.0, -6.0)
        self.target_lufs.setSingleStep(0.5)
        self.target_lufs.setSuffix(" LUFS")
        self.target_lufs.setValue(float(self.config.get(AppConfig.KEY_TARGET_LUFS)))
        self.label_target_lufs = QLabel(tr("label_target_lufs"))
        form_an.addRow(self.label_target_lufs, self.target_lufs)
        self.tp_warn = QDoubleSpinBox()
        self.tp_warn.setRange(-6.0, 6.0)
        self.tp_warn.setSingleStep(0.5)
        self.tp_warn.setSuffix(" dBTP")
        self.tp_warn.setValue(float(self.config.get(AppConfig.KEY_TRUE_PEAK_WARN)))
        self.label_tp_warn = QLabel(tr("label_tp_warn"))
        form_an.addRow(self.label_tp_warn, self.tp_warn)
        self.lufs_hint = QLabel(tr("lufs_hint"))
        self.lufs_hint.setWordWrap(True)
        self.lufs_hint.setObjectName("dropHint")
        form_an.addRow("", self.lufs_hint)
        root.addWidget(self.gb_analysis)

        # ── 行为 ──
        self.gb_behavior = QGroupBox(tr("group_behavior"))
        v_b = QVBoxLayout(self.gb_behavior)
        self.append_check = QCheckBox(tr("label_append_on_drop"))
        self.append_check.setChecked(bool(self.config.get(AppConfig.KEY_APPEND_ON_DROP)))
        v_b.addWidget(self.append_check)
        self.clear_start_check = QCheckBox(tr("label_clear_on_start"))
        self.clear_start_check.setChecked(bool(self.config.get(AppConfig.KEY_CLEAR_ON_START)))
        v_b.addWidget(self.clear_start_check)
        self.auto_add_library_check = QCheckBox(tr("label_auto_add_library"))
        self.auto_add_library_check.setChecked(bool(self.config.get(AppConfig.KEY_AUTO_ADD_LIBRARY)))
        v_b.addWidget(self.auto_add_library_check)
        root.addWidget(self.gb_behavior)

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton(tr("btn_cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton(tr("btn_save"))
        self.btn_save.clicked.connect(self._save)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_save)
        root.addLayout(btn_row)

    def retranslate(self):
        self.setWindowTitle(f"{tr('settings_title')} · v{APP_VERSION}")
        self.gb_appearance.setTitle(tr("group_appearance"))
        self.gb_language.setTitle(tr("group_language"))
        self.gb_analysis.setTitle(tr("group_analysis"))
        self.gb_behavior.setTitle(tr("group_behavior"))
        self.theme_combo.setItemText(0, tr("theme_light"))
        self.theme_combo.setItemText(1, tr("theme_dark"))
        self.theme_combo.setItemText(2, tr("theme_auto"))
        self.label_theme.setText(tr("label_theme"))
        self.label_language.setText(tr("label_language"))
        self.label_target_lufs.setText(tr("label_target_lufs"))
        self.label_tp_warn.setText(tr("label_tp_warn"))
        self.theme_hint.setText(tr("theme_hint"))
        self.lufs_hint.setText(tr("lufs_hint"))
        self.append_check.setText(tr("label_append_on_drop"))
        self.clear_start_check.setText(tr("label_clear_on_start"))
        self.auto_add_library_check.setText(tr("label_auto_add_library"))
        self.btn_cancel.setText(tr("btn_cancel"))
        self.btn_save.setText(tr("btn_save"))

    def _save(self):
        _log("_save: 开始")
        idx_map = {0: THEME_LIGHT, 1: THEME_DARK, 2: THEME_AUTO}
        theme = idx_map[self.theme_combo.currentIndex()]
        _log(f"_save: 主题={theme}")
        self.config.set(AppConfig.KEY_THEME, theme)
        self.config.set(AppConfig.KEY_LANGUAGE,
                        "zh" if self.lang_combo.currentIndex() == 0 else "en")
        self.config.set(AppConfig.KEY_TARGET_LUFS, float(self.target_lufs.value()))
        self.config.set(AppConfig.KEY_TRUE_PEAK_WARN, float(self.tp_warn.value()))
        self.config.set(AppConfig.KEY_APPEND_ON_DROP, bool(self.append_check.isChecked()))
        self.config.set(AppConfig.KEY_CLEAR_ON_START, bool(self.clear_start_check.isChecked()))
        self.config.set(AppConfig.KEY_AUTO_ADD_LIBRARY, bool(self.auto_add_library_check.isChecked()))
        _log("_save: 其余设置已保存，即将 accept")
        self.accept()
        _log("_save: accept 完成")


# =========================================================================
# 播放进度条
# =========================================================================

class SeekBar(QWidget):
    """播放进度条（自绘）：轨道 + 进度 + 竖条播放头 + 削波位置红色标记。

    交互约定：
      · 点击 / 拖动任意位置直接定位（按下即发出 seekStarted，松开发出 seekFinished）
      · 拖动中 valueChanged 实时发出（外部做时间预览）
      · 播放头视觉为细竖条（3×18px，精准感），但抓取热区仍为半径 7px（与旧版
        圆形手柄一致，共 14px 宽）——在热区内按下为"抓住拖动"（保持相对偏移，
        适合微调），热区外按下为"点击跳转"（播放头先跳到该处再跟随拖动）
      · set_marks() 设置削波位置（毫秒列表），绘制为红色竖线（可多处）；
        标记、播放头与鼠标交互共用同一套坐标映射，保证所见即所得。
    """

    seekStarted = Signal()
    seekFinished = Signal(int)
    valueChanged = Signal(int)

    TRACK_H = 4      # 轨道高度
    HEAD_W = 3.0     # 播放头竖条宽度（细，显精准）
    HEAD_H = 18.0    # 播放头竖条高度
    HIT_R = 7.0      # 播放头抓取命中半径（触摸面积与旧圆形手柄一致）
    MARK_H = 15      # 削波标记线高度（保证肉眼可见）
    MARK_W = 2.0     # 削波标记线宽

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._minimum = 0
        self._maximum = 0
        self._value = 0
        self._marks = []          # 削波位置（毫秒，float）
        self._dragging = False
        self._grab_offset = 0.0   # 抓住播放头时的相对偏移（像素）

    # ---- 数据接口（与 QSlider 保持同名，便于调用方统一）----
    def setRange(self, lo, hi):
        lo, hi = int(lo), int(hi)
        self._minimum = lo
        self._maximum = max(hi, lo)
        self._value = max(self._minimum, min(self._maximum, self._value))
        self.update()

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum

    def setValue(self, v):
        v = max(self._minimum, min(self._maximum, int(v)))
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)
            self.update()

    def value(self):
        return self._value

    def set_marks(self, marks_ms):
        """设置削波标记（毫秒位置列表）。"""
        self._marks = sorted(float(m) for m in marks_ms)
        self.update()

    # ---- 坐标映射（标记 / 播放头 / 交互共用，保证三者对齐）----
    def _track_span(self):
        left = self.HIT_R + 1.0
        right = max(left + 1.0, self.width() - self.HIT_R - 1.0)
        return left, right

    def _value_to_x(self, v):
        left, right = self._track_span()
        if self._maximum <= self._minimum:
            return left
        frac = (float(v) - self._minimum) / (self._maximum - self._minimum)
        return left + frac * (right - left)

    def _x_to_value(self, x):
        left, right = self._track_span()
        if self._maximum <= self._minimum:
            return self._minimum
        frac = (float(x) - left) / (right - left)
        frac = max(0.0, min(1.0, frac))
        return int(round(self._minimum + frac * (self._maximum - self._minimum)))

    # ---- 鼠标交互 ----
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.isEnabled():
            return
        x = event.position().x()
        handle_x = self._value_to_x(self._value)
        if abs(x - handle_x) <= self.HIT_R:
            # 按在播放头热区内（±HIT_R）：抓住拖动，保持相对偏移（精准微调）
            self._grab_offset = x - handle_x
        else:
            # 按在热区外：播放头先跳到该位置，再跟随拖动
            self._grab_offset = 0.0
            self.setValue(self._x_to_value(x))
        self._dragging = True
        self.seekStarted.emit()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.setValue(
                self._x_to_value(event.position().x() - self._grab_offset))
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.LeftButton:
            x = event.position().x() - self._grab_offset
            self._dragging = False
            self._grab_offset = 0.0
            self.setValue(self._x_to_value(x))
            self.seekFinished.emit(self.value())
            event.accept()

    # ---- 绘制 ----
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        mid = self.height() / 2.0
        left, right = self._track_span()
        dark = self.palette().color(QPalette.Window).lightness() < 128
        enabled = self.isEnabled()

        if enabled:
            track_col = QColor(58, 58, 58) if dark else QColor(214, 214, 214)
            prog_col = QColor(74, 144, 217)
            # 播放头：深色主题白 / 浅色主题深蓝——在轨道与进度条上都保持高对比
            handle_col = QColor(232, 232, 232) if dark else QColor(36, 85, 140)
            mark_col = QColor(255, 59, 48)          # 醒目红（两主题通用）
        else:
            track_col = QColor(58, 58, 58) if dark else QColor(224, 224, 224)
            prog_col = QColor(110, 110, 110) if dark else QColor(190, 190, 190)
            handle_col = QColor(120, 120, 120) if dark else QColor(160, 160, 160)
            mark_col = QColor(255, 59, 48, 80)      # 禁用时淡化

        r = self.TRACK_H / 2.0
        # 轨道
        p.setPen(Qt.NoPen)
        p.setBrush(track_col)
        p.drawRoundedRect(QRectF(left, mid - r, right - left, self.TRACK_H), r, r)

        # 已播放进度
        if self._maximum > self._minimum:
            px = self._value_to_x(self._value)
            if px > left + 0.5:
                p.setBrush(prog_col)
                p.drawRoundedRect(QRectF(left, mid - r, px - left, self.TRACK_H), r, r)

        # 播放头（细竖条：视觉精准；抓取热区仍为 HIT_R 半径，方便点按拖动）
        cx = self._value_to_x(self._value)
        p.setPen(QPen(handle_col, self.HEAD_W, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, mid - self.HEAD_H / 2.0),
                   QPointF(cx, mid + self.HEAD_H / 2.0))

        # 削波标记（红竖线，画在最上层，任何状态下都可见）
        if self._marks and self._maximum > self._minimum:
            p.setPen(QPen(mark_col, self.MARK_W))
            top = mid - self.MARK_H / 2.0
            bottom = mid + self.MARK_H / 2.0
            for m in self._marks:
                mx = self._value_to_x(m)
                if left - 1.0 <= mx <= right + 1.0:
                    p.drawLine(QPointF(mx, top), QPointF(mx, bottom))


# =========================================================================
# 拖拽区
# =========================================================================

class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setMinimumHeight(120)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self.icon = QLabel("🎵")
        self.icon.setAlignment(Qt.AlignCenter)
        # 显式指定 macOS 的 Apple Color Emoji 字体，避免回退到非彩色字体造成的方框占位
        emoji_font = QFont()
        emoji_font.setFamilies(["Apple Color Emoji", "Segoe UI Emoji",
                                "Noto Color Emoji", "sans-serif"])
        emoji_font.setPointSize(28)
        self.icon.setFont(emoji_font)
        self.text = QLabel(tr("drop_title"))
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setObjectName("dropText")
        self.hint = QLabel(tr("drop_hint"))
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setObjectName("dropHint")
        layout.addWidget(self.icon)
        layout.addWidget(self.text)
        layout.addWidget(self.hint)

    def retranslate(self):
        self.text.setText(tr("drop_title"))
        self.hint.setText(tr("drop_hint"))

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)


# =========================================================================
# 后台分析线程
# =========================================================================

class AnalyzeWorker(QThread):
    result_ready = Signal(object)
    progress = Signal(int, int)
    finished_all = Signal()

    def __init__(self, files, parent=None):
        super().__init__(parent)
        self.files = list(files)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        total = len(self.files)
        for i, f in enumerate(self.files):
            if self._stop:
                break
            try:
                res = analyze_file(f, cancel=lambda: self._stop)
            except AnalysisCancelled:
                break  # 用户取消：不产出该文件结果
            except Exception as e:  # noqa: BLE001
                res = AnalysisResult(path=f, filename=os.path.basename(f),
                                     error=str(e))
            self.result_ready.emit(res)
            self.progress.emit(i + 1, total)
        self.finished_all.emit()


# =========================================================================
# 图表窗口（频谱图 + 平均频谱曲线）
# =========================================================================

class ChartWorker(QThread):
    """后台生成音频图表（首次运行会加载 matplotlib，约需数秒）。"""

    chart_ready = Signal(str, str)   # (kind, png_path)
    failed = Signal(str)

    def __init__(self, audio_path, out_dir, display_name, lang="zh", parent=None):
        super().__init__(parent)
        self.audio_path = audio_path
        self.out_dir = out_dir
        self.display_name = display_name
        self.lang = lang

    def run(self):
        try:
            from charts import generate_charts  # 延迟导入（matplotlib 较重）
            generate_charts(
                self.audio_path, self.out_dir, self.display_name,
                on_chart=lambda kind, path: self.chart_ready.emit(kind, path),
                lang=self.lang)
        except Exception as e:  # noqa: BLE001
            _log(f"ChartWorker 失败: {e!r}")
            self.failed.emit(f"{type(e).__name__}: {e}")


class ChartDialog(QDialog):
    """图表详情窗口（非模态）：频谱图 + 平均频谱曲线，可保存为 PNG。

    · 打开后自动在后台生成两张图，边生成边显示；
    · 生成结果写入共享 cache（key = 文件路径 + 修改时间），重复打开秒显示；
    · 引例由主窗口持有（关闭只是隐藏），避免生成线程与销毁竞争。
    """

    def __init__(self, res: AnalysisResult, cache: dict = None, parent=None):
        super().__init__(parent)
        self.res = res
        self._cache = cache if cache is not None else {}
        self._worker = None
        self._paths = {}       # kind -> png 路径
        self._pixmaps = {}     # kind -> 原始尺寸 QPixmap

        self.setWindowTitle(f"{tr('app_title')} · {res.filename}")
        self.resize(1180, 840)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # 顶部：文件名 + 状态
        head = QHBoxLayout()
        self.title_label = QLabel(res.filename)
        fnt = self.title_label.font()
        fnt.setBold(True)
        fnt.setPointSize(fnt.pointSize() + 2)
        self.title_label.setFont(fnt)
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.status_label = QLabel(tr("chart_generating"))
        head.addWidget(self.status_label)
        root.addLayout(head)

        # 图区（可滚动）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(6)

        self.spec_caption = QLabel(tr("chart_spec_caption"))
        self.spec_caption.setObjectName("chartCaption")
        self.spec_label = QLabel(tr("chart_generating"))
        self.spec_label.setAlignment(Qt.AlignCenter)
        self.spec_label.setMinimumHeight(230)
        bl.addWidget(self.spec_caption)
        bl.addWidget(self.spec_label)

        self.curve_caption = QLabel(tr("chart_curve_caption"))
        self.curve_caption.setObjectName("chartCaption")
        self.curve_label = QLabel(tr("chart_generating"))
        self.curve_label.setAlignment(Qt.AlignCenter)
        self.curve_label.setMinimumHeight(230)
        bl.addWidget(self.curve_caption)
        bl.addWidget(self.curve_label)
        bl.addStretch(1)
        self.scroll.setWidget(body)
        root.addWidget(self.scroll, stretch=1)

        # 底部按钮
        btn_row = QHBoxLayout()
        self.btn_save_spec = QPushButton(tr("chart_save_spec"))
        self.btn_save_spec.setEnabled(False)
        self.btn_save_spec.clicked.connect(lambda: self._save("spectrogram"))
        self.btn_save_curve = QPushButton(tr("chart_save_curve"))
        self.btn_save_curve.setEnabled(False)
        self.btn_save_curve.clicked.connect(lambda: self._save("spectrum"))
        btn_row.addWidget(self.btn_save_spec)
        btn_row.addWidget(self.btn_save_curve)
        btn_row.addStretch(1)
        self.btn_close = QPushButton(tr("chart_close"))
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        self._file_mtime = self._read_mtime()
        self._user_closed = False   # 用户是否已关闭过本窗口（供主窗口 LRU 清理）
        self._start()

    def closeEvent(self, event):
        self._user_closed = True
        super().closeEvent(event)

    def _read_mtime(self):
        try:
            return os.path.getmtime(self.res.path)
        except OSError:
            return 0.0

    def refresh_if_stale(self):
        """文件被覆盖/更新后重新生成图表（窗口复用时防止显示旧图）。"""
        cur = self._read_mtime()
        if cur == self._file_mtime:
            return
        self._file_mtime = cur
        _log(f"ChartDialog: 检测到文件已更新，重新生成 {self.res.path}")
        if self._worker is None or not self._worker.isRunning():
            self._restart()
        # 若生成仍在进行：不打断（下次打开窗口时会再次校验）

    def _restart(self):
        """清空旧图并重新开始生成。"""
        self._paths.clear()
        self._pixmaps.clear()
        self.spec_label.setPixmap(QPixmap())  # 清除旧图
        self.curve_label.setPixmap(QPixmap())
        self.spec_label.setText(tr("chart_generating"))
        self.curve_label.setText(tr("chart_generating"))
        self.btn_save_spec.setEnabled(False)
        self.btn_save_curve.setEnabled(False)
        self.status_label.setText(tr("chart_generating"))
        self._start()

    # ---- 生成流程 ----
    def _cache_key(self):
        try:
            mtime = os.path.getmtime(self.res.path)
        except OSError:
            mtime = 0
        return (self.res.path, mtime)

    def _start(self):
        if self._worker is not None and self._worker.isRunning():
            return
        cached = self._cache.get(self._cache_key())
        if cached and all(os.path.isfile(p) for p in cached.values()):
            _log("ChartDialog: 命中缓存，直接显示")
            for kind, path in cached.items():
                self._on_chart_ready(kind, path)
            self.status_label.setText(tr("chart_done"))
            return

        out_dir = os.path.join(tempfile.gettempdir(), "audioanalysis_charts")
        self._worker = ChartWorker(self.res.path, out_dir,
                                   self.res.filename, lang=_LANG, parent=self)
        self._worker.chart_ready.connect(self._on_chart_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(str, str)
    def _on_chart_ready(self, kind, png_path):
        self._paths[kind] = png_path
        pm = QPixmap(png_path)
        if not pm.isNull():
            self._pixmaps[kind] = pm
            self._rescale()
        if kind == "spectrogram":
            self.btn_save_spec.setEnabled(not pm.isNull())
            self.status_label.setText(tr("chart_step_curve"))
        else:
            self.btn_save_curve.setEnabled(not pm.isNull())
            self.status_label.setText(tr("chart_done"))
            self._cache[self._cache_key()] = dict(self._paths)

    @Slot(str)
    def _on_failed(self, msg):
        self.status_label.setText(tr("chart_gen_failed"))
        for lb in (self.spec_label, self.curve_label):
            lb.setText("⚠️ " + tr("chart_gen_failed") + msg)

    # ---- 显示（按容器宽度平滑缩放）----
    def _rescale(self):
        target = max(420, self.scroll.viewport().width() - 28)
        for kind, label in (("spectrogram", self.spec_label),
                            ("spectrum", self.curve_label)):
            pm = self._pixmaps.get(kind)
            if pm is None:
                continue
            if pm.width() > target:
                label.setPixmap(pm.scaledToWidth(target, Qt.SmoothTransformation))
            else:
                label.setPixmap(pm)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    # ---- 保存 ----
    def _save(self, kind):
        png = self._paths.get(kind)
        if not png or not os.path.isfile(png):
            return
        is_spec = (kind == "spectrogram")
        stem = os.path.splitext(self.res.filename)[0]
        suffix = "频谱图" if is_spec else "频谱曲线"
        default = os.path.join(os.path.expanduser("~/Desktop"),
                               f"{stem}_{suffix}.png")
        fn, _ = QFileDialog.getSaveFileName(
            self, tr("chart_save_spec") if is_spec else tr("chart_save_curve"),
            default, "PNG 图片 (*.png)")
        if not fn:
            return
        try:
            import shutil
            shutil.copyfile(png, fn)
            self.status_label.setText(tr("chart_saved_to") + fn)
        except OSError as e:
            self.status_label.setText(f"⚠️ {e}")


# =========================================================================
# 主窗口
# =========================================================================

class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, data_dir: str = ""):
        super().__init__()
        self.config = config
        # 数据目录（自动化测试可传入独立目录实现隔离；默认为用户的实际数据目录）
        self._data_dir = data_dir or self._default_data_dir()
        self._worker = None
        self._pending_files = []
        self._stop_requested = False
        self._batch_id = 0         # 任务批次代际：作废旧批次的残留信号
        self._really_quit = False  # 仅 Dock 右键退出 / Cmd+Q 时为 True
        self._results = []         # 数据源（持久化到磁盘）
        self._selection_mode = False  # 多选模式

        # 播放器（QtMultimedia）
        self._player = None
        self._audio_output = None
        self._player_source_path = ""   # 当前装载到播放器的文件路径
        self._seeking = False           # 进度条拖动中
        self._was_playing_before_seek = False

        # 图表窗口（双击记录打开）
        self._chart_cache = {}          # (path, mtime) -> {kind: png 路径}
        self._chart_dialogs = {}        # path -> ChartDialog（关闭仅隐藏，复用）

        self.setWindowTitle(tr("app_title"))
        self.resize(1380, 760)

        self._build_ui()
        self._setup_player()
        self._check_ffmpeg()

        # 「跟随系统」用定时器轮询检测，避免 changeEvent 事件重入导致的崩溃
        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._poll_system_theme)
        self._theme_timer.start(2000)  # 每 2 秒检测一次

        # 恢复上次记录（除非用户设置「启动时清空」）
        if self.config.get(AppConfig.KEY_CLEAR_ON_START):
            self._results = []
            self._save_results()
        else:
            self._load_results()
        self._render_all()

        # 恢复曲库（曲库独立持久化，不受「启动时清空」影响）
        self._load_library()

        self.retranslate()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 顶部栏：曲库开关 + 标题
        top_bar = QHBoxLayout()
        self.btn_library = QPushButton(tr("btn_library"))
        self.btn_library.setCheckable(True)
        self.btn_library.setChecked(True)
        self.btn_library.clicked.connect(self.toggle_library)
        self.title_label = QLabel(tr("main_title"))
        self.title_label.setObjectName("title")
        top_bar.addWidget(self.btn_library)
        top_bar.addWidget(self.title_label)
        top_bar.addStretch(1)
        root.addLayout(top_bar)

        # 分割器：左侧曲库 + 右侧分析
        self.splitter = QSplitter(Qt.Horizontal)

        # 左侧曲库边栏
        self.library_panel = LibraryPanel()
        self.library_panel.activate_requested.connect(self.analyze_from_library)
        self.library_panel.data_changed.connect(self._save_library)

        # 右侧分析面板
        self.analysis_panel = QWidget()
        analysis = QVBoxLayout(self.analysis_panel)
        analysis.setContentsMargins(0, 0, 0, 0)
        analysis.setSpacing(12)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.add_files)
        analysis.addWidget(self.drop_zone)

        # 按钮行
        btn_row = QHBoxLayout()

        self.btn_open = QPushButton(tr("btn_open"))
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_clear = QPushButton(tr("btn_clear"))
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_stop = QPushButton(tr("btn_stop"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_analysis)
        self.btn_select = QPushButton(tr("btn_select"))
        self.btn_select.setCheckable(True)
        self.btn_select.clicked.connect(self.toggle_selection_mode)
        self.btn_delete = QPushButton(tr("btn_delete_selected"))
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_export_csv = QPushButton(tr("btn_export_csv"))
        self.btn_export_csv.clicked.connect(lambda: self.export_results("csv"))
        self.btn_export_json = QPushButton(tr("btn_export_json"))
        self.btn_export_json.clicked.connect(lambda: self.export_results("json"))
        self.btn_export_csv.setEnabled(False)
        self.btn_export_json.setEnabled(False)
        self.btn_settings = QPushButton(tr("btn_settings"))
        self.btn_settings.clicked.connect(self.open_settings)

        btn_row.addWidget(self.btn_open)
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_select)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_export_csv)
        btn_row.addWidget(self.btn_export_json)
        btn_row.addWidget(self.btn_settings)
        analysis.addLayout(btn_row)

        # 结果表（11 列：文件信息 / 响度指标 / 双口径质心 / 削波 / BPM）
        self.table = QTableWidget(0, 11)
        self._apply_column_headers()   # 列头 + 列头 tooltip（语言切换时复用）
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col, width in enumerate([240, 62, 92, 100, 62, 88, 128, 128, 78, 150, 62]):
            self.table.setColumnWidth(col, width)
        header.setSectionResizeMode(0, QHeaderView.Stretch)   # 文件名自适应
        header.setSectionResizeMode(9, QHeaderView.Stretch)   # 削波详情自适应
        # 双击任意记录 → 打开频谱图表窗口
        self.table.setToolTip(tr("tip_table_dblclick"))
        self.table.cellDoubleClicked.connect(self.open_charts_for_row)
        analysis.addWidget(self.table, stretch=1)

        # 底部状态
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setMaximum(100)
        self.status = QLabel(tr("status_ready"))
        self.status.setObjectName("status")
        bottom.addWidget(self.progress, stretch=1)
        bottom.addWidget(self.status)
        analysis.addLayout(bottom)

        # 底部播放控制条：播放/暂停 + 当前曲目 + 可拖拽进度条 + 时间
        player_bar = QHBoxLayout()
        player_bar.setSpacing(10)

        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setIconSize(QSize(18, 18))
        self.btn_play.setFixedWidth(44)
        self.btn_play.setToolTip(tr("tip_play"))
        self.btn_play.clicked.connect(self.play_selected)
        self.btn_pause = QPushButton()
        self.btn_pause.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPause))
        self.btn_pause.setIconSize(QSize(18, 18))
        self.btn_pause.setFixedWidth(44)
        self.btn_pause.setToolTip(tr("tip_pause"))
        self.btn_pause.clicked.connect(self.pause_playback)
        player_bar.addWidget(self.btn_play)
        player_bar.addWidget(self.btn_pause)

        self.now_playing_label = QLabel("")
        self.now_playing_label.setObjectName("nowPlaying")
        self.now_playing_label.setMinimumWidth(110)
        player_bar.addWidget(self.now_playing_label)

        self.seek_slider = SeekBar()
        self.seek_slider.setToolTip(tr("seek_tip"))
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setEnabled(False)  # 未装载音频时不可拖动
        self.seek_slider.seekStarted.connect(self._on_seek_started)
        self.seek_slider.seekFinished.connect(self._on_seek_finished)
        self.seek_slider.valueChanged.connect(self._on_seek_value_changed)
        player_bar.addWidget(self.seek_slider, stretch=1)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setMinimumWidth(105)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        player_bar.addWidget(self.time_label)
        analysis.addLayout(player_bar)

        self.splitter.addWidget(self.library_panel)
        self.splitter.addWidget(self.analysis_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setSizes([280, 880])
        root.addWidget(self.splitter, stretch=1)

    # ------------------------------------------------------- 曲库
    def toggle_library(self):
        self.library_panel.setVisible(self.btn_library.isChecked())

    def _apply_column_headers(self):
        """（重新）应用结果表列头与列头说明（随当前语言）。"""
        self.table.setHorizontalHeaderLabels([
            tr("col_filename"), tr("col_duration"), tr("col_channels"),
            tr("col_lufs"), tr("col_lra"), tr("col_true_peak"),
            tr("col_centroid_mag"), tr("col_centroid_pow"),
            tr("col_clip"), tr("col_clip_detail"), tr("col_bpm"),
        ])
        # 表头提示：两个质心列的口径说明 + BPM 说明
        for idx, tip_key in ((6, "col_centroid_mag_tip"),
                             (7, "col_centroid_pow_tip"),
                             (10, "col_bpm_tip")):
            hdr_item = self.table.horizontalHeaderItem(idx)
            if hdr_item:
                hdr_item.setToolTip(tr(tip_key))

    def retranslate(self):
        """切换语言后重新应用所有界面文本。"""
        self.setWindowTitle(tr("app_title"))
        self.btn_library.setText(tr("btn_library"))
        self.title_label.setText(tr("main_title"))
        self.btn_open.setText(tr("btn_open"))
        self.btn_clear.setText(tr("btn_clear"))
        self.btn_stop.setText(tr("btn_stop"))
        self.btn_select.setText(tr("btn_select"))
        self.btn_delete.setText(tr("btn_delete_selected"))
        self.btn_export_csv.setText(tr("btn_export_csv"))
        self.btn_export_json.setText(tr("btn_export_json"))
        self.btn_settings.setText(tr("btn_settings"))
        if _PLAYER_AVAILABLE:
            self.btn_play.setToolTip(tr("tip_play"))
            self.btn_pause.setToolTip(tr("tip_pause"))
            self.seek_slider.setToolTip(tr("seek_tip"))
        self.table.setToolTip(tr("tip_table_dblclick"))
        self._apply_column_headers()   # 表头随语言更新
        self.drop_zone.retranslate()
        self.library_panel.retranslate()
        self._render_all()  # 重新渲染表格（削波状态文本随语言更新）
        self.status.setText(tr("status_ready"))

    def analyze_from_library(self, path: str):
        """双击曲库条目 → 加入右侧分析队列开始分析。"""
        if not os.path.isfile(path):
            self.status.setText(tr("status_file_missing") + path)
            return
        self._pending_files.append(path)
        self._start_worker()

    @staticmethod
    def _default_data_dir() -> str:
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", "AudioBatchAnalyzer")

    def _library_file(self) -> str:
        return os.path.join(self._data_dir, "library.json")

    def _save_library(self):
        try:
            path = self._library_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = [it.to_dict() for it in self.library_panel.get_items()]
            # 原子写：先写临时文件再替换，避免中途中断导致半写损坏
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            _log(f"_save_library 失败: {e!r}")

    def _load_library(self):
        try:
            path = self._library_file()
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = [LibraryItem.from_dict(d) for d in data]
            self.library_panel.set_items(items)
        except Exception as e:  # noqa: BLE001
            _log(f"_load_library 失败: {e!r}")

    # ------------------------------------------------------- 主题/系统跟随
    def _poll_system_theme(self):
        """定时检测系统主题变化（仅在「跟随系统」时生效）。"""
        if self.config.get(AppConfig.KEY_THEME) == THEME_AUTO:
            resolved = resolve_theme(THEME_AUTO)
            if resolved != _theme_state["last"]:
                _log(f"_poll_system_theme: 检测到变化 {_theme_state['last']} -> {resolved}")
                apply_theme(QApplication.instance(), resolved)

    def open_settings(self):
        _log("open_settings: 打开设置对话框")
        old_lang = _LANG
        dlg = SettingsDialog(self.config, self)
        _log("open_settings: 进入 exec")
        if dlg.exec() == QDialog.Accepted:
            _log("open_settings: exec 返回 Accepted")
            theme = resolve_theme(self.config.get(AppConfig.KEY_THEME))
            _log(f"open_settings: resolve_theme={theme}，即将 apply_theme")
            apply_theme(QApplication.instance(), theme)
            # 语言切换
            new_lang = self.config.get(AppConfig.KEY_LANGUAGE)
            if new_lang != old_lang:
                _log(f"open_settings: 语言切换 {old_lang} -> {new_lang}")
                set_language(new_lang)
                self.retranslate()
            # 显示相关设置（响度目标 / 真峰值阈值）变化后，统一刷新已有记录
            self._render_all()
            _log("open_settings: apply_theme 完成")
        else:
            _log("open_settings: exec 返回非 Accepted")

    # ------------------------------------------------- 关闭窗口 vs 退出
    def closeEvent(self, event):
        """点左上角红叉时只隐藏窗口（保留记录与 Dock 图标），不退出。

        只有通过 Dock 右键「退出」或 Cmd+Q 才会真正退出。
        """
        if self._really_quit:
            _log("closeEvent: 真正退出")
            event.accept()
        else:
            _log("closeEvent: 隐藏窗口（保留记录，Dock 图标仍在）")
            self._stop_playback()
            event.ignore()
            self.hide()

    def quit_app(self):
        """真正退出：先允许关闭，再触发 quit。"""
        _log("quit_app: 准备真正退出")
        self._really_quit = True
        # 停止并等待后台线程结束，避免 QThread 在运行中被销毁（退出警告/崩溃风险）
        if self._worker is not None:
            try:
                self._worker.stop()
                self._worker.wait(5000)
            except Exception:  # noqa: BLE001
                pass
        for dlg in list(self._chart_dialogs.values()):
            w = getattr(dlg, "_worker", None)
            if w is not None:
                try:
                    w.wait(8000)   # 图表生成无法中断，尽力等待完成
                except Exception:  # noqa: BLE001
                    pass
        QApplication.instance().quit()

    # ----------------------------------------------------------------- 任务
    @Slot(list)
    def add_files(self, paths):
        files = collect_audio_files(paths)
        if not files:
            self.status.setText(tr("status_no_audio"))
            return
        # 若开启「自动加入曲库」，同时把这些文件加入曲库
        if self.config.get(AppConfig.KEY_AUTO_ADD_LIBRARY):
            self.library_panel.add_paths(files)
        # 若设置为不追加，则清空旧结果再分析（同时作废旧批次的残留信号）
        if not self.config.get(AppConfig.KEY_APPEND_ON_DROP):
            self._batch_id += 1
            self._pending_files = []
            self._results = []
            self.table.setRowCount(0)
            self._save_results()
        self._pending_files.extend(files)
        self._start_worker()

    def open_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "", MEDIA_FILE_FILTER)
        if files:
            self.add_files(files)

    def _start_worker(self):
        # 注意：不能只判断 isRunning()——worker 线程可能已结束、但其
        # result/finished 信号仍在事件队列中未处理。此时若直接启动新 worker，
        # 旧 worker 排队的“最后一批结果”会因代际过期被丢弃（快速连续导入丢文件）。
        # 因此只要引用还在（= 其 finished 信号尚未处理完），就等待接力。
        if self._worker is not None:
            return
        if not self._pending_files:
            return

        batch = self._pending_files
        self._pending_files = []
        self._batch_id += 1              # 新一批任务的代际
        wid = self._batch_id
        self._stop_requested = False

        self.btn_stop.setEnabled(True)
        self.btn_open.setEnabled(False)
        self.status.setText(tr("status_analyzing"))

        self._worker = AnalyzeWorker(batch)
        # 通过 lambda 捕获批次号：过期批次的信号在处理器里被丢弃
        self._worker.result_ready.connect(lambda r, w=wid: self._on_result(r, w))
        self._worker.progress.connect(lambda d, t, w=wid: self._on_progress(w, d, t))
        self._worker.finished_all.connect(lambda w=wid: self._on_finished(w))
        self._worker.start()

    @Slot(object, int)
    def _on_result(self, res: AnalysisResult, wid: int):
        if wid != self._batch_id:
            return  # 过期批次的结果（已被清空 / 已被新任务替换）：丢弃
        self._results.append(res)
        self._render_row(res)
        self._save_results()
        self._update_buttons()

    @Slot(int, int, int)
    def _on_progress(self, wid: int, done: int, total: int):
        if wid != self._batch_id:
            return
        if total:
            self.progress.setValue(int(done / total * 100))
            self.status.setText(tr("status_progress", done=done, total=total))

    @Slot(int)
    def _on_finished(self, wid: int):
        self._worker = None
        if wid != self._batch_id:
            # 过期批次结束：其信号已全部作废；若有等待中的新文件，接力启动
            if self._pending_files:
                self._start_worker()
            return
        # 当前批次：如有等待中的新文件则接力，否则正常收尾
        if self._pending_files:
            self._start_worker()
            return
        self.progress.setValue(100)
        if self._stop_requested:
            self._stop_requested = False
            self.status.setText(tr("status_stopped", n=len(self._results)))
        else:
            self.status.setText(tr("status_done", n=len(self._results)))
        self.btn_stop.setEnabled(False)
        self.btn_open.setEnabled(True)
        self._update_buttons()

    def stop_analysis(self):
        self._stop_requested = True
        self._pending_files = []   # 停止 = 同时取消尚未开始的文件（立即生效）
        if self._worker is not None:
            self._worker.stop()
            self.status.setText(tr("status_stopping"))

    def clear_all(self):
        self._batch_id += 1        # 作废正在跑批次的全部信号（结果不会再冒出来）
        self._stop_requested = False
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
        self._stop_playback()
        self._results = []
        self.table.setRowCount(0)
        self._pending_files = []
        self.progress.setValue(0)
        self.status.setText(tr("status_cleared"))
        self._save_results()
        self._update_buttons()

    # ----------------------------------------------------------------- 播放
    def _setup_player(self):
        """初始化 QtMultimedia 播放器。"""
        if not _PLAYER_AVAILABLE:
            self.btn_play.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.btn_play.setToolTip(tr("status_player_unavailable"))
            self.btn_pause.setToolTip(tr("status_player_unavailable"))
            _log("_setup_player: QtMultimedia 不可用，播放功能已禁用")
            return
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(1.0)  # 试听保持原始响度，便于评测
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_playback_error)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        _log("_setup_player: 播放器初始化完成")

    def _selected_result(self):
        """返回当前选中行对应的 AnalysisResult（无选中返回 None）。"""
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        res = item.data(Qt.UserRole)
        return res if isinstance(res, AnalysisResult) else None

    def play_selected(self):
        """「播放」按钮：播放 / 继续播放当前选中的音频。"""
        self._start_or_resume()

    def pause_playback(self):
        """「暂停」按钮：暂停当前播放。"""
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()

    def toggle_playback(self):
        """空格键：播放 / 暂停切换。"""
        if self._player is None:
            self.status.setText(tr("status_player_unavailable"))
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._start_or_resume()

    def _start_or_resume(self):
        """播放或继续。

        规则（与常规播放器一致）：
          · 已在播放同一文件 → 不重复操作
          · 同一文件处于暂停/停止（含拖动定位后）→ 从当前位置继续
          · 已播放到结尾 → 从头重播
          · 换文件 → 装载新文件从头播放
        """
        if self._player is None:
            self.status.setText(tr("status_player_unavailable"))
            return
        res = self._selected_result()
        if res is None:
            self.status.setText(tr("status_need_select"))
            return
        if not os.path.isfile(res.path):
            self.status.setText(tr("status_file_missing") + res.path)
            return

        state = self._player.playbackState()
        same_file = (res.path == self._player_source_path)
        if same_file:
            if state == QMediaPlayer.PlayingState:
                return  # 已在播放
            if state == QMediaPlayer.StoppedState:
                # 播放到结尾后再次触发 → 从头重播（其余停止场景保持当前位置）
                dur = self._player.duration()
                pos = self._player.position()
                if dur > 0 and pos >= dur - 250:
                    self._player.setPosition(0)
            self._player.play()  # 暂停 / 停止 → 从当前位置继续
            return

        # 不同文件：装载并从头播放（重置进度显示，并带入该文件的削波位置标记）
        self._player_source_path = res.path
        self._reset_seek_ui()
        self.seek_slider.set_marks([cr.start * 1000.0 for cr in res.clip_ranges])
        self._player.setSource(QUrl.fromLocalFile(res.path))
        self._player.play()

    def _stop_playback(self):
        """停止播放并清空播放状态显示。"""
        if self._player is not None:
            self._player.stop()
        self._player_source_path = ""
        self.now_playing_label.setText("")
        self._reset_seek_ui()

    def _reset_seek_ui(self):
        """进度条与时间显示归零/复位。"""
        self._seeking = False
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setValue(0)
        self.seek_slider.setEnabled(False)
        self.seek_slider.set_marks([])
        self.time_label.setText("00:00 / 00:00")

    # ---- 播放进度条（拖动定位）----
    @staticmethod
    def _fmt_time(ms: int) -> str:
        """毫秒 → mm:ss（超过 1 小时显示 h:mm:ss）。"""
        if ms < 0:
            ms = 0
        total_s = int(ms // 1000)
        m, s = divmod(total_s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _update_time_label(self, position=None):
        """刷新「已播 / 总长」时间显示；position 为 None 时取播放器当前值。"""
        dur = self._player.duration() if self._player is not None else 0
        if position is None:
            position = self._player.position() if self._player is not None else 0
        self.time_label.setText(
            f"{self._fmt_time(position)} / {self._fmt_time(dur)}")

    @Slot(int)
    def _on_duration_changed(self, duration):
        self.seek_slider.setRange(0, max(0, int(duration)))
        self.seek_slider.setEnabled(duration > 0)
        self._update_time_label()

    @Slot(int)
    def _on_position_changed(self, pos):
        if self._seeking:
            return  # 拖动中：UI 由拖动控制，忽略播放器的位置回调
        self.seek_slider.setValue(int(pos))
        self._update_time_label(position=pos)

    @Slot()
    def _on_seek_started(self):
        """开始拖动进度条：记录拖动前的播放状态。"""
        self._seeking = True
        self._was_playing_before_seek = (
            self._player is not None
            and self._player.playbackState() == QMediaPlayer.PlayingState)

    @Slot(int)
    def _on_seek_value_changed(self, value):
        # 拖动中实时预览时间（松手前不改动播放位置）
        if self._seeking:
            self._update_time_label(position=value)

    @Slot(int)
    def _on_seek_finished(self, value):
        """松开进度条：跳转到目标位置。

        · 拖动前在播放 → 从新位置继续播放
        · 拖动前是暂停/停止 → 仅定位，保持暂停
        """
        self._seeking = False
        if self._player is None:
            return
        self._player.setPosition(int(value))
        if self._was_playing_before_seek:
            self._player.play()
        self._update_time_label(position=value)

    @Slot(object)
    def _on_playback_state_changed(self, state):
        name = os.path.basename(self._player_source_path) if self._player_source_path else ""
        display = name if len(name) <= 22 else name[:21] + "…"
        self.now_playing_label.setToolTip(name)
        if state == QMediaPlayer.PlayingState:
            self.now_playing_label.setText(f"▶ {display}")
        elif state == QMediaPlayer.PausedState:
            self.now_playing_label.setText(f"⏸ {display}")
        else:  # StoppedState（播放完毕 / 停止）
            self.now_playing_label.setText("")

    def _on_playback_error(self, error, error_string):
        if self._player is not None and error != QMediaPlayer.NoError:
            _log(f"_on_playback_error: {error} {error_string}")
            self.status.setText(tr("status_play_failed") + str(error_string))
            self.now_playing_label.setText("")

    # 空格键 = 播放 / 暂停（全局生效，文本输入控件除外）
    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space \
                and not event.isAutoRepeat():
            fw = QApplication.focusWidget()
            if fw is not None and self.isAncestorOf(fw):
                if isinstance(fw, (QLineEdit, QAbstractSpinBox)):
                    return False  # 搜索框等输入控件放行
                self.toggle_playback()
                return True
        return super().eventFilter(obj, event)

    # ----------------------------------------------------------------- 图表
    def open_charts_for_row(self, row, column=0):
        """双击表格行：打开该记录的频谱图表窗口（非模态，可多开）。"""
        item = self.table.item(row, 0)
        res = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(res, AnalysisResult):
            return
        if not os.path.isfile(res.path):
            self.status.setText(tr("status_file_missing") + res.path)
            return
        dlg = self._chart_dialogs.get(res.path)
        if dlg is not None:
            # 已有窗口：文件若已被覆盖/更新则重新生成，再激活（防显示旧图）
            dlg.refresh_if_stale()
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = ChartDialog(res, cache=self._chart_cache, parent=self)
        self._chart_dialogs[res.path] = dlg
        dlg.show()
        self._prune_chart_dialogs(keep=res.path)

    def _prune_chart_dialogs(self, keep: str = "", limit: int = 6):
        """图表窗口数量上限（长期使用防内存累积）：

        仅销毁"已由用户关闭且无生成任务在跑"的最旧窗口；
        当前打开/正在生成的窗口永不销毁。
        """
        if len(self._chart_dialogs) <= limit:
            return
        for path, dlg in list(self._chart_dialogs.items()):
            if path == keep or not getattr(dlg, "_user_closed", False):
                continue
            w = getattr(dlg, "_worker", None)
            if w is not None and w.isRunning():
                continue
            dlg.deleteLater()
            self._chart_dialogs.pop(path, None)
            if len(self._chart_dialogs) <= limit:
                break

    # ----------------------------------------------------------------- 表格
    def _render_row(self, res: AnalysisResult):
        row = self.table.rowCount()
        self.table.insertRow(row)

        def cell(text, color=None, align=None, tooltip=None):
            item = QTableWidgetItem(text)
            if color:
                item.setForeground(QColor(color))
            if align:
                item.setTextAlignment(align)
            if tooltip:
                item.setToolTip(tooltip)
            return item

        failed = bool(res.error)
        gray = "#888888"
        na = "—"
        na_tip = "分析失败，不可用" if failed else "无有效值（如纯静音）"

        def finite(v):
            try:
                return math.isfinite(float(v))
            except (TypeError, ValueError):
                return False

        # 文件名（⚠ 失败提示并入同一单元格；结果对象始终挂回第一列，
        # 保证播放 / 图表 / 导出等入口不受失败状态影响）
        name_text = res.filename
        name_color = None
        name_tip = res.path
        if failed:
            name_text = f"{res.filename} ⚠ {res.error}"
            name_color = "#d9534f"
            name_tip = res.error
        name_item = cell(name_text, color=name_color, tooltip=name_tip)
        name_item.setData(Qt.UserRole, res)
        self.table.setItem(row, 0, name_item)

        # 时长
        if res.duration and res.duration > 0:
            self.table.setItem(row, 1, cell(f"{res.duration:.1f}s",
                                            align=Qt.AlignRight | Qt.AlignVCenter))
        else:
            self.table.setItem(row, 1, cell(na, color=gray, tooltip=na_tip,
                                            align=Qt.AlignRight | Qt.AlignVCenter))
        # 声道/采样率
        if res.sample_rate and res.channels:
            sr = f"{res.sample_rate / 1000:.1f}k"
            self.table.setItem(row, 2, cell(f"{res.channels}ch · {sr}",
                                            align=Qt.AlignCenter))
        else:
            self.table.setItem(row, 2, cell(na, color=gray, tooltip=na_tip,
                                            align=Qt.AlignCenter))

        # LUFS 积分响度（<= -70 视为无有效信号：ffmpeg 对全静音的哨兵值）
        lv = res.integrated_lufs
        if (failed and lv == 0.0) or not finite(lv) or lv <= -70.0:
            # 失败未测 / 纯静音（-inf）
            self.table.setItem(row, 3, cell(na, color=gray, tooltip=na_tip,
                                            align=Qt.AlignRight | Qt.AlignVCenter))
        else:
            lufs_str = f"{lv:+.1f}"
            target = float(self.config.get(AppConfig.KEY_TARGET_LUFS))
            if abs(lv - target) > 2.0:
                lufs_str += f" (与 {target:+.0f} 偏差 {lv - target:+.1f})"
            self.table.setItem(row, 3, cell(lufs_str,
                                            align=Qt.AlignRight | Qt.AlignVCenter))

        # LRA（短音频参考性提示）
        la = res.loudness_range_lu
        if (failed and la == 0.0) or not finite(la):
            self.table.setItem(row, 4, cell(na, color=gray, tooltip=na_tip,
                                            align=Qt.AlignRight | Qt.AlignVCenter))
        else:
            lra_cell = cell(f"{la:.1f}", align=Qt.AlignRight | Qt.AlignVCenter)
            if res.duration and res.duration < 30:
                lra_cell.setToolTip("音频较短（< 30 秒），LRA 参考性有限")
            self.table.setItem(row, 4, lra_cell)

        # 真峰值
        tp = res.true_peak_dbtp
        if (failed and tp == 0.0) or not finite(tp):
            self.table.setItem(row, 5, cell(na, color=gray, tooltip=na_tip,
                                            align=Qt.AlignRight | Qt.AlignVCenter))
        else:
            tp_warn = float(self.config.get(AppConfig.KEY_TRUE_PEAK_WARN))
            tp_color = "#d9534f" if tp > 0 else ("#e6a23c" if tp > tp_warn else None)
            self.table.setItem(row, 5, cell(f"{tp:+.1f}", color=tp_color,
                                            align=Qt.AlignRight | Qt.AlignVCenter))

        # 频谱质心·幅度加权（行业标准口径）
        if res.centroid_magnitude_hz > 0:
            mag_cell = cell(f"{res.centroid_magnitude_hz:.0f}",
                            align=Qt.AlignRight | Qt.AlignVCenter)
        else:
            mag_cell = cell(na, color=gray, align=Qt.AlignRight | Qt.AlignVCenter,
                            tooltip=na_tip)
        self.table.setItem(row, 6, mag_cell)
        # 频谱质心·功率加权
        if res.centroid_power_hz > 0:
            pow_cell = cell(f"{res.centroid_power_hz:.0f}",
                            align=Qt.AlignRight | Qt.AlignVCenter)
        else:
            pow_cell = cell(na, color=gray, align=Qt.AlignRight | Qt.AlignVCenter,
                            tooltip=na_tip)
        self.table.setItem(row, 7, pow_cell)

        # 削波状态：失败/未测量显示占位，绝不显示绿色“正常”误导
        if res.clipped:
            clip_cell = cell(tr("clip_yes"), color="#d9534f", align=Qt.AlignCenter)
        elif failed:
            clip_cell = cell(na, color=gray, align=Qt.AlignCenter, tooltip=na_tip)
        elif not res.true_peak_ok:
            clip_cell = cell(tr("clip_maybe"), color="#e6a23c", align=Qt.AlignCenter)
        else:
            clip_cell = cell(tr("clip_ok"), color="#5cb85c", align=Qt.AlignCenter)
        self.table.setItem(row, 8, clip_cell)
        # 削波详情（显示精简版；完整内容进 tooltip，防列宽截断）
        if res.clipped:
            detail_cell = cell(clip_summary(res),
                               tooltip=clip_summary(res, max_ranges=999))
        elif failed:
            detail_cell = cell(na, color=gray, tooltip=na_tip)
        else:
            detail_cell = cell(clip_summary(res))
        self.table.setItem(row, 9, detail_cell)

        # BPM（速度，整数显示；原值进 tooltip）
        if res.bpm and res.bpm > 0:
            bpm_tip = f"检测原值 {res.bpm:.1f} BPM"
            if res.duration and res.duration < 30:
                bpm_tip += " · 音频较短，结果仅供参考"
            bpm_cell = cell(f"{int(res.bpm + 0.5)}",
                            align=Qt.AlignRight | Qt.AlignVCenter,
                            tooltip=bpm_tip)
        else:
            bpm_cell = cell(na, color=gray, align=Qt.AlignRight | Qt.AlignVCenter,
                            tooltip=("未检出或不可用" if not failed
                                     else na_tip))
        self.table.setItem(row, 10, bpm_cell)

    # ------------------------------------------------------------- 持久化
    def _results_file(self) -> str:
        return os.path.join(self._data_dir, "results.json")

    def _save_results(self):
        try:
            path = self._results_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = [r.to_dict() for r in self._results]
            # 原子写：先写临时文件再替换，避免中途中断导致 JSON 半写损坏
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            _log(f"_save_results 失败: {e!r}")

    def _load_results(self):
        path = self._results_file()
        try:
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._results = [AnalysisResult.from_dict(d) for d in data]
        except Exception as e:  # noqa: BLE001
            _log(f"_load_results 失败: {e!r}")
            # 保留现场：损坏文件改名备份（避免下次启动继续失败，数据可人工恢复）
            try:
                if os.path.exists(path):
                    os.replace(path, path + ".corrupt")
            except Exception:  # noqa: BLE001
                pass
            self._results = []

    def _render_all(self):
        self.table.setRowCount(0)
        for res in self._results:
            self._render_row(res)
        self._update_buttons()

    def _update_buttons(self):
        has = len(self._results) > 0
        self.btn_export_csv.setEnabled(has)
        self.btn_export_json.setEnabled(has)
        self.btn_delete.setEnabled(self._selection_mode and has)

    # ------------------------------------------------------------- 多选删除
    def toggle_selection_mode(self):
        self._selection_mode = self.btn_select.isChecked()
        if self._selection_mode:
            self.table.setSelectionMode(QAbstractItemView.MultiSelection)
            self.status.setText(tr("status_multi_on"))
        else:
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.clearSelection()
            self.status.setText(tr("status_multi_off"))
        self._update_buttons()

    def delete_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()},
                      reverse=True)
        if not rows:
            self.status.setText(tr("status_none_selected"))
            return
        for row in rows:
            if 0 <= row < len(self._results):
                del self._results[row]
        self._render_all()
        self._save_results()
        self.status.setText(tr("status_deleted", n=len(rows)))
        # 正在播放的记录被删除时，停止播放
        if self._player_source_path and not any(
                r.path == self._player_source_path for r in self._results):
            self._stop_playback()

    # ----------------------------------------------------------------- 导出
    def export_results(self, kind: str):
        if self.table.rowCount() == 0:
            return
        default_name = "audio_analysis." + ("csv" if kind == "csv" else "json")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果", os.path.join(os.path.expanduser("~"), default_name),
            "CSV 文件 (*.csv)" if kind == "csv" else "JSON 文件 (*.json)")
        if not path:
            return

        rows = self._collect_rows()
        try:
            if kind == "csv":
                self._write_csv(path, rows)
            else:
                self._write_json(path, rows)
            self.status.setText(tr("status_exported") + path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("export_failed"), str(e))

    def _collect_rows(self):
        return list(self._results)

    def _write_csv(self, path, results):
        header = _CSV_HEADERS.get(_LANG, _CSV_HEADERS["zh"])

        def num(v, spec):
            """CSV 数值：非有限值（-inf 等）与缺失值输出为空。"""
            try:
                if v is None or not math.isfinite(float(v)):
                    return ""
            except (TypeError, ValueError):
                return ""
            return format(v, spec)

        yes = "是" if _LANG == "zh" else "Yes"
        no = "否" if _LANG == "zh" else "No"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            for res in results:
                if res is None:
                    continue
                w.writerow([
                    res.filename, res.path, f"{res.duration:.3f}",
                    res.channels, res.sample_rate,
                    num(res.integrated_lufs, ".2f"),
                    num(res.loudness_range_lu, ".2f"),
                    num(res.true_peak_dbtp, ".2f"),
                    num(res.sample_peak_dbfs, ".2f"),
                    f"{res.centroid_magnitude_hz:.1f}",
                    f"{res.centroid_power_hz:.1f}",
                    yes if res.clipped else no,
                    res.clipped_samples, f"{res.clip_percent_display:.4f}",
                    clip_summary(res, max_ranges=100),
                    f"{res.bpm:.1f}" if res.bpm > 0 else "",
                    res.error,
                ])

    def _write_json(self, path, results):
        data = [r.to_dict() for r in results if r is not None]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ----------------------------------------------------------------- ffmpeg
    def _check_ffmpeg(self):
        ffmpeg, ffprobe = ensure_ffmpeg()
        if not ffmpeg or not ffprobe:
            self.status.setText("⚠️ 未检测到 ffmpeg，请运行：brew install ffmpeg")
            self.status.setStyleSheet("color: #d9534f;")


# =========================================================================
# 入口
# =========================================================================

def _install_excepthook():
    """把未捕获异常写入日志文件（--windowed 模式下无控制台，便于排查）。"""
    import time
    import traceback
    log_path = os.path.join(
        os.path.expanduser("~"), "Library", "Logs", "AudioAnalysis.log")

    def hook(exc_type, exc_value, exc_tb):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("=" * 60 + "\n")
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 未捕获异常\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


class DockReopenFilter(QObject):
    """监听 macOS 点击 Dock 图标重新打开窗口的事件。"""

    def __init__(self, window):
        super().__init__()
        self.window = window

    def eventFilter(self, obj, event):
        if event.type() == QEvent.ApplicationActivate:
            # 用户点击 Dock 图标：窗口已隐藏则重新显示
            if not self.window.isVisible():
                _log("Dock 重新打开：show 窗口")
                self.window.show()
                self.window.raise_()
                self.window.activateWindow()
        return False


def main():
    _log("main: 开始启动")
    app = QApplication(sys.argv)
    app.setApplicationName("音频分析")
    app.setOrganizationName("WorkBuddy")

    _install_excepthook()
    _log("main: QApplication 已创建")

    config = AppConfig()
    set_language(config.get(AppConfig.KEY_LANGUAGE))  # 应用持久化的语言设置
    theme = resolve_theme(config.get(AppConfig.KEY_THEME))
    _log(f"main: 初始主题配置={config.get(AppConfig.KEY_THEME)}，解析为 {theme}")
    apply_theme(app, theme)
    _log("main: 初始 apply_theme 完成")

    win = MainWindow(config)
    _log("main: MainWindow 已创建")

    # 关闭最后一个窗口时不退出（点红叉只隐藏窗口，Dock 图标保留）
    app.setQuitOnLastWindowClosed(False)

    # 点击 Dock 图标重新打开窗口
    dock_filter = DockReopenFilter(win)
    app.installEventFilter(dock_filter)

    # 空格键控制播放 / 暂停（应用级事件过滤器）
    app.installEventFilter(win)

    # Dock 右键「退出」/ Cmd+Q 时，允许窗口真正关闭
    def _on_about_to_quit():
        _log("main: aboutToQuit，设置 _really_quit=True")
        win._really_quit = True
    app.aboutToQuit.connect(_on_about_to_quit)

    win.show()
    _log("main: 进入事件循环")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()