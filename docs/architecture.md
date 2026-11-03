# 技术架构草案

## 运行方式

首版采用本机 Python 服务与浏览器界面，先跑通音频处理，再决定桌面打包方案。服务只监听回环地址；前端与 API 尽量同源。真实服务需校验会话令牌、来源及受控文件 ID，不暴露任意路径读取接口。

浏览器界面 → 项目与任务 API → 串行 GPU 调度器 → 独立模型适配器。媒体文件通过项目资源 ID 访问。CPU 解码、文件处理与 GPU 推理分离，避免阻塞界面。

## 适配器边界

- Separator：源音频 → 人声、伴奏。
- Transcriber：人声 → 原词和置信度。
- Aligner：人声、校对原词 → 字词时间轴。
- MelodyAnalyzer：人声 → 音高曲线、音符和持续时间。
- Singer：参考人声、目标歌词、旋律条件 → 生成片段。
- Mixer：片段、人声保留策略、伴奏、音量 → 输出文件。

所有适配器记录模型标识、版本、参数与输入摘要。尚未验证的模型不视为可用功能。应用实现独立编写，不导入安装目录中的商业应用后端。

## 项目数据

Project：id、schema_version、名称、素材资源 ID、采样率、时长、片段列表、混音设置。
Segment：id、start_ms、end_ms、original_text、target_text、alignment_revision、melody_revision、edit_revision、active_take_id。
Take：id、segment_id、input_fingerprint、模型与参数、输出资源 ID、状态、创建时间。
Job：id、type、project_id、segment_ids、status、stage、progress、error_code、cancel_requested。

输入指纹包含素材摘要、歌词、时间边界、对齐/旋律版本、参考音频与模型参数。任务完成时校验指纹：编辑期间完成的旧任务仅存入历史，不覆盖最新编辑。

## 存储与恢复

使用项目元数据与资源目录；元数据原子写入，资源以新文件生成成功后再更新引用。持久化任务状态，进程重启后将未完成任务标记为中断，允许重试。首版每次只允许一个 GPU 重任务，分析与生成交替释放模型。模型权重存储于项目外的配置目录。

## API 草案

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /api/projects | 创建项目 |
| POST | /api/projects/{id}/assets | 上传素材 |
| POST | /api/projects/{id}/analysis | 开始分析 |
| GET | /api/projects/{id} | 获取项目 |
| PATCH | /api/projects/{id}/segments/{segmentId} | 按预期版本修改歌词或边界 |
| POST | /api/projects/{id}/renders | 指定片段与预期版本开始生成 |
| GET | /api/jobs/{id} | 查询进度 |
| POST | /api/jobs/{id}/cancel | 请求取消 |
| GET | /api/assets/{id} | 访问受控媒体，支持 Range |
| POST | /api/projects/{id}/exports | 创建导出任务 |

版本冲突返回 409；任务错误含可读描述与稳定错误码。进度必须来自真实处理阶段，无法量化时使用不定进度。

## 实现顺序

1. 单句真实推理验证，记录依赖许可、显存峰值、耗时、输入输出格式和听感结果。
2. 项目存储、任务状态机、过期产物与恢复逻辑。
3. 素材导入、歌词校对和生成适配器。
4. 接入编辑界面与真实音频播放。
5. 拼接、混音、导出与端到端验收。


## 当前实现（2026-11-03）

已实现本地 FastAPI 服务与 JSON 工程存储。项目元数据通过同目录临时文件加原子替换写入；音频上传流式限额后生成受控资源 ID。更新工程和附加资源需要匹配 revision，冲突返回 409。媒体通过 FileResponse 提供 Range 请求。

已实现 API：GET /api/session，GET/POST /api/projects，PUT /api/projects/{id}，POST /api/projects/{id}/assets/{kind}，POST /api/projects/{id}/lyrics，GET /api/projects/{id}/lyrics.lrc，GET /api/assets/{id}。

服务仅监听回环地址，校验 Host、Origin 与写操作会话令牌；未启用跨域许可。界面与 API 同源。模型任务、产物发布与版本校验已实现，实际路径见下方“当前模型实现”。

波形由浏览器解码原唱/伴奏计算，80 MB 以上跳过波形计算。上传最大 512 MB。歌词时长以音频元数据为准，导入 LRC 不触发模型推理。播放对齐采用同时间起点与漂移修正，不能补偿源素材自身偏移。

## 当前模型实现

已新增 runtime.py（共享模型发现）、singing.py（输入校验/指纹）、model_worker.py（独立推理适配）、jobs.py（GPU 任务与发布）。POST /api/projects/{id}/renders 启动任务，GET /api/jobs/{id} 查询真实处理阶段，POST /api/jobs/{id}/cancel 取消任务；GET /api/runtime 显示模型文件状态；GET /api/projects/{id}/exports/{rendered|mixed} 导出有效且完整的产物。任务及运行依赖留在本机，不提交权重。

任务一次只运行一个；编辑不阻塞，完成时对每句校验输入，避免覆盖新编辑。混合轨还校验工程级指纹，仍有修改句未生成时禁止完整音频导出。
