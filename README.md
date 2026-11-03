# AI Lyrics Studio

参照 SoulX Studio 操作布局开发的本地 AI 唱歌改词工具。模型权重共用本机 SoulX Studio 安装目录，不重复下载或复制。

## 当前可用

- 工程与素材保存到本机，刷新后恢复。
- 导入原唱干声、伴奏和 SRT/LRC，显示真实波形并同步试听。
- 逐句编辑歌词、时间边界与旋律/MIDI 条件；独立提取逐字时间、校正原词和手动调整字词边界。
- 使用本地 HubertFA 歌声对齐（Qwen 备用）、RMVPE、ROSVOT、SoulX-Singer 生成真实演唱。
- 单句或批量生成，显示处理阶段，支持取消，隔离运行 GPU 进程。
- 原唱/改词切换试听，导出改词演唱 WAV、带伴奏混音 WAV、SRT 字幕和 LRC。
- 检查生成结果的输入版本，旧任务不会覆盖后来修改的歌词。

波形支持整句与逐字边界拖拽、文字块平移、缩放、视窗平移、撤销/重做和数值微调。暂未接入自动人声分离、ASR、SVC、MV、非等长歌词适配与历史版本选择界面。

## 启动

已在 Windows、Python 3.12、RTX 5060 Ti 16 GB 环境验证。在仓库目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts/setup_runtime.py
.venv\Scripts\python.exe -m backend.app
```

打开 http://127.0.0.1:18733 。端口被占用时可运行 `python -m backend.app --port 18734`。

`setup_runtime.py` 只获取固定版本的官方推理代码和少量兼容依赖，保存于本项目的 `runtime/`。它不会下载模型或向 SoulX Studio 文件夹安装软件。它会读取已有的 Windows 系统代理用于下载，不改变系统代理设置。

默认从 `%LOCALAPPDATA%\SoulX Studio\runtime\current.json` 发现运行时，共用其 Python、模型与预处理权重。应用不调用商业 Studio 后端，也不修改其授权或安装文件。

可在启动前配置：

| 环境变量 | 用途 |
| --- | --- |
| `SOULX_STUDIO_HOME` | SoulX Studio 安装目录 |
| `SOULX_PYTHON` | 共用的 GPU Python 解释器路径 |
| `SOULX_MODELS_ROOT` | 共用的 pretrained_models 目录 |
| `SOULX_SOURCE_ROOT` | 独立的上游推理代码目录 |
| `SOULX_HFA_MODEL` | 可选 HubertFA 共享模型目录，默认 Studio 的 hfa 组件 model 目录 |
| `SOULX_HFA_PYTHON` | HFA 组件的只读 Python 补充依赖目录 |
| `SOULX_HFA_SOURCE` | 独立的 Vocal2Midi 对齐解码器代码目录 |
| `AI_LYRICS_DATA` | 本工具数据目录，默认 `data/` |

## 使用顺序

1. 创建工程，导入清晰干声；如需混音，先导入从同一时间起点开始的伴奏。
2. 点击“导入 SRT / LRC”并检查原词。SRT 直接保留每条字幕的毫秒起止时间与句间空隙；多行文字合为一句。模型会在生成前逐字对齐。
3. 点击“改词”，修改歌词与边界，选择旋律或 MIDI，然后“生成当前句”。
4. 也可先保存多句，再点击底部“生成 N 句”。后台一次运行一个 GPU 任务。
5. 完成后切换原唱/改词试听，再导出演唱、混音或字幕/歌词。顶部格式菜单默认 SRT，也可切换 LRC。其他未修改片段保留原唱。

### 首版生成范围

- 工程最长 30 分钟，单句 1–20 秒，每次最多 100 句。
- 支持中文汉字、英文单词和常用标点；数字请写成文字。
- 新旧歌词须保持相同发音单位数：一个汉字或一个英文单词算一个单位；停顿沿用原句，不按纯字符数判断英文。
- 已提取的逐字时间会在主界面用于实际定位与高亮；缺少时间时才按单位数估算。可以在“歌词时间轴”先提取，不必生成音频。
- 少量对齐后时长为零的快速音节会按邻近边界估算，并提示试听校对；大量异常会直接报错。
- 输出为 24 kHz、单声道 PCM WAV。混音按现有时间起点叠加并在必要时整体缩小峰值，不自动修复素材偏移。
- 生成过程按阶段释放模型，任务结束后子进程退出以释放显存。显存不足时需释放其他应用占用或缩短片段。
- 导入或替换伴奏后，需要重新生成一次以产生对应的混音文件。

音频上限 512 MB，超过 80 MB 时跳过浏览器波形计算；支持格式取决于浏览器解码能力，推荐 WAV、MP3。新音频短于现有歌词时禁止替换。再次导入 SRT/LRC 会替换现有歌词编辑，界面会先确认。

`data/` 保存工程、音频、历史产物、任务日志与元数据；备份整个目录可保留工程。当前没有自动清理历史文件。

## 验证

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

29 项测试覆盖工程恢复、版本冲突、非法边界、LRC、Range 媒体请求、会话校验、生成输入指纹、短音节修复、旧结果拒绝发布、取消与任务恢复、SRT 起止时间和格式往返、错误输入不覆盖、已知无演唱测试音的生成拦截。另已使用官方示例片段验证真实 GPU 生成和页面上的任务、试听及导出流程。

自动化测试不衡量歌词清晰度、音色相似度和听感质量，实际素材仍需试听。

## 目录与依赖

- `backend/`：本地 API、工程存储、GPU 任务管理与自写模型适配器。
- `design/`：按 SoulX Studio 布局实现的界面。
- `scripts/setup_runtime.py`：准备独立的推理代码与补充依赖。
- `tests/`：无需加载 GPU 模型的测试。
- `docs/`：设计、模型接入与依赖说明。

上游使用 [Soul-AILab/SoulX-Singer](https://github.com/Soul-AILab/SoulX-Singer)，固定版本见 [模型接入](docs/model-integration.md)。权重、上游 checkout、运行依赖、用户素材与生成音频均不提交到仓库。


## 字幕与示例说明

SRT/LRC 导入支持 UTF-8、带 BOM 的 UTF-16 和常见 GBK/GB18030 编码，单文件不超过 2 MB。SRT 的多行文字会合并、常见字幕样式会去除；时间倒置、重叠或超出音频时返回具体错误，保留原有编辑。SRT 导入本身不受单句 1–20 秒限制，但实际生成仍受该限制。详见 [字幕使用说明](docs/subtitles.md)。

旧的“功能验收 · 合成测试音”只有合成声音，没有演唱，不能用于改词生成。界面会明确提示并禁用生成，后端也会在加载模型前按音频内容拦截，改文件名不会绕过，也不会仅因真实歌曲同名而误判。要测试模型请使用真实演唱工程；要处理自己的歌曲，请在自己的工程导入对应原唱和 SRT。


### 快唱对齐

优先使用本机 Studio HFA 组件中的 HubertFA ONNX 歌声对齐模型，独立解码器代码固定为 Vocal2Midi 的 `e46590b490fca790e6cd2dacbc0bb7779babe383`。组件不存在或结果未通过校验时，尝试原有 Qwen 对齐。两种方式都检查每个字/词及时间边界，不用均匀分配时间掩盖失败。模型状态中显示歌声对齐组件是否就绪。

实际失败的 3.7 秒、13 字唱句已用共享 HFA 权重复测，通过完整演唱与混音导出。源歌词或边界在任务期间被修改时仍不会被旧结果覆盖。每个任务保留原始对齐诊断文件，便于复核。


## 时间轴优化参考

以本地 SoulX Studio 的实际时间轴、原词校正与生成缓存逻辑为主要参考，并研究 JIZURA 的时间轴拖动、缩放和平移交互，详见 [优化参考与对应改进](docs/studio-reference.md)。顶部“歌词时间轴”可独立提取每个字/英文词的时间、校正原词、拖动波形边界、试听并手动保存；这些时间会参与下一次真实生成。整句“改词”窗口也可直接拖动边界。波形显示按窗口宽度绘制密集细线，放大后使用约 5ms 的底层包络细节。
