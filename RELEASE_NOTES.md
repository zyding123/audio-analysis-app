## 音频分析 v1.2.0 — 新增 Windows 版本

本版在 v1.1.0 的基础上完整支持 **Windows x64**：功能与 macOS 版一致（同一套测量引擎与界面），
并提供可直接安装的安装程序。

### 新增

- **Windows x64 安装包**（`audio-analysis-v1.2.0-windows-x64-setup.exe`）：内置 ffmpeg/ffprobe，
  安装后自带开始菜单与桌面快捷方式、标准卸载程序，目标机器无需预装任何依赖。
- **macOS（Apple Silicon）安装包**：与 Windows 版同版本发布。
- 全部安装包附带 **SHA-256 校验文件**。

### 平台差异（有意为之）

- macOS：点红叉只隐藏窗口（Dock 图标保留），Dock 右键 / Cmd+Q 退出。
- Windows：点关闭按钮即退出程序（符合 Windows 习惯）。
- 数据目录按平台规范：macOS `~/Library/Application Support/AudioBatchAnalyzer`，
  Windows `%APPDATA%\AudioBatchAnalyzer`；日志同理（Windows 在 `%LOCALAPPDATA%\AudioAnalysis\Logs`）。
- 「跟随系统」主题：macOS 读系统外观偏好，Windows 读注册表 `AppsUseLightTheme`。

### 沿用 v1.1 的关键修复

- 立体声逐声道汇总（反相 / 中置成分不丢失）；削波判定区分“疑似满幅平台”与听感失真。
- 失败指标不再导出为零（界面“—”、CSV 空值、JSON null）。
- 清空 / 停止 / 替换批次的状态机修复；退出时统一取消并等待后台线程。
- 源文件版本校验（纳秒时间戳 + 大小），防止旧测量值与新音频混用。
- 长文件：FFmpeg 解码到临时文件并映射读取，削波 / 频谱逐块计算，可中途取消。

### 安装

**Windows**：下载 `audio-analysis-v1.2.0-windows-x64-setup.exe`，双击安装（支持中英文安装向导）。
安装包未做代码签名，首次运行如遇 SmartScreen 提示，选择「更多信息 → 仍要运行」。

**macOS**：下载 `audio-analysis-v1.2.0-macos-arm64.dmg`，拖入 Applications；
未签名应用首次打开需在「系统设置 → 隐私与安全性」中放行。

### 说明

Windows 版由 GitHub Actions 在 windows-latest 上构建，并通过仓库内 32 项回归测试与包内自检；
界面表现已尽量与 macOS 保持一致，但仍建议在实际办公电脑上复核拖拽、播放与图表生成。
Windows 版未做代码签名，企业环境的 SmartScreen 提示属预期行为。
