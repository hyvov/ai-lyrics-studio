# FunnySinger 0.2.3 安装包与模型分发

## 安装结果

交付 `dist/FunnySinger-0.2.3-setup.exe`，Windows 10/11 x64 当前用户安装。
安装包包含独立 Python、FFmpeg、前后端、推理代码、HubertFA 对齐组件及较小的依赖。大型 CUDA 运算库和大模型在客户端选择下载；不要求安装 SoulX Studio、系统 Python、Git 或 CUDA Toolkit。
AI 功能仍需要可用的 NVIDIA 显卡驱动。基础工程管理、音频播放、字幕导入、歌词和时间轴编辑、字幕导出不依赖大模型。

## 大组件来源

| 功能 | 固定来源 | 安装方式 |
| --- | --- | --- |
| 共享 GPU 运算 | PyTorch 2.8.0+cu128、torchaudio 2.8.0+cu128 官方 Windows CPython 3.12 wheels | 自动下载约 3.2 GiB，SHA-256 校验，独立目录解压，CUDA 实际运算自检后切换 |
| 演唱与 MIDI | Soul-AILab/SoulX-Singer、SoulX-Singer-Preprocess | 包含 RMVPE、ROSVOT、RWBD 权重和配置 |
| 备用歌词对齐 | Qwen/Qwen3-ForcedAligner-0.6B | 选择演唱功能时一并安装，包含词典和配置 |
| SVC | Soul-AILab/SoulX-Singer 的 model-svc.pt、openai/whisper-base、共享 RMVPE | 包含 Whisper 预处理配置；转换程序随安装包提供 |
| 人声分离 | KimberleyJSN/melbandroformer、Sucial/Dereverb-Echo_Mel_Band_Roformer | 两个权重下载；pymss 程序及匹配 YAML 随包提供 |
| 自动识词 | FireRedTeam/FireRedASR2-AED，提交 2304afed56eacfee6256dee5937ed22ffa0b64ec | 下载官方原始 checkpoint，推理时使用 FP16；不冒用本地转换权重的哈希 |

完整 URL、提交、大小及哈希在 `backend/model_catalog.json` 和 `backend/gpu_setup.py`。
下载支持暂停续传。发现损坏时先将旧文件备份为 `.invalid-*`，验证新文件后再替换。安装组件期间不能启动模型推理任务。

## 数据位置

- 程序：`%LOCALAPPDATA%/Programs/CiShengStudio`。
- 工程和素材：`%LOCALAPPDATA%/CiShengStudio/data`。
- 模型：该用户目录的 `models` 和 `components`。
- GPU 库与下载缓存：`gpu-packages` 和 `downloads`。
- 日志：`data/logs/server.log`；推理日志在对应 `data/jobs` 中。

更新和卸载不删除以上用户目录。开发版工程没有被移动或打进安装包。内部验收可用 `CISHENG_USER_HOME` 指向隔离测试目录。

## 构建记录与来源

`scripts/build_release.py` 从本机已验证的独立运行时快照构建；程序不再读取 SoulX Studio 安装目录。
随包的 `files.sha256.json` 记录每个实际交付文件的大小及哈希，`third-party/python-packages.json` 记录 Python 包版本。
保留 Python 与各依赖原有的许可证和元数据。SoulX-Singer 来源为 Soul-AILab（Apache-2.0），HubertFA / Vocal2Midi 的来源和许可证保留在组件目录；pymss 为 MIT。
当前 SVC 本地加载适配器及基础运行时来自此前用户授权导入的本机优化快照，各文件哈希随包保留。本构建面向该用户的独立使用，没有发布到公共下载站。
`fireredasr2s` 元数据中的旧构建路径不是运行依赖；实际源码已打包。此构建仍依赖已保存快照，不能将其描述为只靠 PyPI 锁文件即可从零重建的发行流程。

安装器：Inno Setup 6.4.3，官方 installer SHA-256 `f3c42116542c4cc57263c5ba6c4feabfc49fe771f2f98a79d2f7628b8762723b`。
WebView2 bootstrapper 来自微软，构建时已验证 Authenticode 签名；仅在注册表未检测到 Runtime 时运行。
客户端由 PyInstaller 6.22.3、pywebview 6.2.1 构建。当前成品没有代码签名证书。
模型及第三方组件的许可证独立适用；Sucial 去混响模型包含 CC-BY-NC-SA-4.0 条件。

## 验收范围

在本机 RTX 5060 Ti 上，以新程序目录、新用户目录和不含系统 Python 的 PATH 实测：

- setup 静默安装成功，客户端独立启动并避开原客户端端口。
- 不安装大模型时创建工程、导入 WAV、通过随包 FFmpeg 提取波形、导入 SRT、改词、导出 SRT/LRC 成功。
- 官方 CUDA wheels 实际下载、SHA-256 校验、解压和 GPU 张量运算成功。
- 所选歌声/SVC/分离模型清单在独立副本校验通过；缺失小配置实际从官方地址下载。
- MIDI 真实改词生成成功；补齐此前缺少的 RWBD 配置和权重。
- SVC 与两阶段人声分离实际推理成功。
- FireRedASR2 官方 checkpoint 实际下载和校验后识词成功；修复 SentencePiece 的 Windows 中文路径读取问题。
- Python 测试运行 111 项（110 通过、1 项因系统 FFmpeg 缺失跳过；安装包 FFmpeg 已另做实际波形测试），三个 JavaScript 回归脚本通过。

验收针对当前 Windows / NVIDIA 环境，不代表已经验证所有显卡和其他电脑，也不对所有歌词的发音质量作保证。MV 仍是此前约定暂缓的功能。
