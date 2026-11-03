# AI Lyrics Studio

参照 SoulX Studio 操作布局开发的本地 AI 唱歌改词工具。

## 当前可用

- 创建工程，元数据与导入音频保存在本机，刷新后恢复。
- 导入原唱/干声和伴奏，显示音频波形、同步播放、独立静音及句子试听。
- 导入 UTF-8 LRC，逐句改词、修改起止边界与生成方式，检查相邻句重叠。
- 导出修改后的 LRC 歌词。

唱歌生成、自动人声分离、ASR、逐字强对齐、真实音符槽位、SVC、MV 和混音导出尚未接入。生成入口显示未接入提示，不产生假音频或完成状态。

## 启动

已在 Windows、Python 3.12 环境验证。在仓库目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m backend.app
```

打开 http://127.0.0.1:18733 。端口被占用时可运行 `python -m backend.app --port 18734`。

服务只监听本机地址。默认数据保存在 `data/`；启动前设置 `AI_LYRICS_DATA` 可指定其他目录。关闭服务前先完成保存；备份整个数据目录可保留工程与音频。

## 使用顺序

1. 创建工程，导入本地原唱或干声音频，再按需导入伴奏。
2. 导入带时间戳的 LRC。时间来自 LRC，尚未自动校对；最后一句延伸至音频末尾。
3. 点击“改词”，编辑新歌词，数值或滑块调整边界，试听后确认保存。
4. 点击顶部“导出歌词”，下载当前版本的 LRC。

支持浏览器能解码的 WAV、MP3、FLAC、M4A、OGG、AAC、Opus、WebM，具体依浏览器编解码支持；单文件上限 512 MB。超过 80 MB 的音频不在浏览器计算波形，仍可尝试播放。原唱和伴奏应来自同一时间起点，应用不自动对齐偏移。

导入新的 LRC 会替换该工程的全部歌词编辑，界面会先确认。新音频短于已有歌词时禁止替换，建议另建工程。已替换的媒体仍保留在数据目录中，当前没有自动清理。

## 验证

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

覆盖工程持久化、旧版本冲突、边界错误不覆盖数据、LRC 异常时间戳、媒体上传与 Range 播放请求、失败上传清理、本机来源与会话校验。浏览器已验证导入合成音频、LRC、改词、刷新恢复、双轨播放进度及下载歌词；合成测试音不用于评价演唱效果。

## 目录

- `backend/`：本地 API、工程存储和 LRC 处理。
- `design/`：按 SoulX Studio 布局实现的界面，运行时由本机服务提供。
- `tests/`：API 与数据处理测试。
- `docs/`：设计与后续模型接入计划。

第三方组件与模型在引入前核对来源和许可证。仓库不含 SoulX Studio 商业应用源码、模型权重、用户歌曲或密钥。
