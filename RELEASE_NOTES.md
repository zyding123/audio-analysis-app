## 音频分析 v1.2.1 — 新增 Intel Mac 支持

本版新增 **Intel（x86_64）Mac** 原生构建，功能与 Apple Silicon / Windows 版完全一致。
同一套测量引擎与界面，三个平台分别原生打包。

### 新增

- **macOS Intel（x86_64）安装包**：`audio-analysis-v1.2.1-macos-x86_64.dmg`
- **macOS Apple Silicon 安装包**：`audio-analysis-v1.2.1-macos-arm64.dmg`
- **Windows x64 安装包**：`audio-analysis-v1.2.1-windows-x64-setup.exe`
- 三个安装包均附 **SHA-256 校验文件**。

> 两个 macOS 包不可互换：Intel Mac 装 x86_64 包，Apple Silicon（M 系列）装 arm64 包。
> 查看方式：左上角  →「关于本机」→ 芯片一栏显示「Intel」或「Apple M 系列」。

### 平台差异（有意为之）

- macOS：点红叉只隐藏窗口（Dock 图标保留），Dock 右键 / Cmd+Q 退出。
- Windows：点关闭按钮即退出程序（符合 Windows 习惯）。
- 数据目录：macOS `~/Library/Application Support/AudioBatchAnalyzer`，
  Windows `%APPDATA%\AudioBatchAnalyzer`；日志同理。
- 「跟随系统」主题：macOS 读系统外观偏好，Windows 读注册表 `AppsUseLightTheme`。

### 功能（三平台一致）

- 客观测量：LUFS（EBU R128）/ LRA / 真峰值 dBTP / 样本峰值 / 双口径频谱质心 / BPM / 疑似满幅削波。
- 批量分析：拖拽文件或文件夹；曲库管理；CSV / JSON 导出；中英双语界面；深浅色主题。
- 播放试听：进度条上以红色竖线标出削波位置，拖过去即可复听。
- 频谱图表（双击记录）：频谱图 + 平均频谱曲线，可保存 PNG。
- 立体声逐声道汇总（反相 / 中置成分不丢失）；源文件版本校验；长文件分块处理与中途取消。

### 安装

**macOS**：下载对应架构的 `.dmg`，拖入 Applications；未签名应用首次打开需在
「系统设置 → 隐私与安全性」中放行。

**Windows**：下载 `audio-analysis-v1.2.1-windows-x64-setup.exe`，双击安装（中英文向导）。
未做代码签名，首次运行如遇 SmartScreen 提示，选择「更多信息 → 仍要运行」。

### 说明

- Intel 版本由 GitHub Actions 在 `macos-15-intel`（x86_64）机器上原生构建，
  通过仓库内 32 项回归测试与包内自检（含内置 ffmpeg、图表生成、界面与导出）。
- 三个平台的构建产物均来自同一份源码与同一套测试，除平台适配层外无功能差异。
- 安装包未做代码签名 / Apple 公证，企业环境的 SmartScreen 提示属预期行为。
