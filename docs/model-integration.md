# 共用模型与推理实现

## 本机模型与运行时

默认发现 `%LOCALAPPDATA%/SoulX Studio/runtime/current.json` 指向的版本，并只读使用其 `core/pretrained_models` 和 Python。权重没有复制到本项目，不调用 Studio 商业后端、不修改其安装文件或授权代码。

## 独立的推理代码

- 官方来源：https://github.com/Soul-AILab/SoulX-Singer
- 固定提交：`81aeb3ae772c70093c3de74dc23c92d983801ae4`
- 上游许可证：Apache-2.0；完整 LICENSE 保留于独立 checkout。
- 获取目录：本项目 `runtime/SoulX-Singer`，由 Git 忽略。
- 补充依赖：`requirements-worker.txt`，安装到 `runtime/worker-deps`，不写入共用 Python。

## 已实现的链路

1. 从本工具的音频副本裁剪当前句。
2. 优先使用共享 HubertFA ONNX 歌声对齐模型，为已校对原词定位时间；不可用或校验失败时尝试 Qwen3-ForcedAligner。少量零时长音节按邻近边界修复并记录提示，严重异常报错。
3. RMVPE 提取连续 F0。
4. ROSVOT 在对齐的字词时长下提取音高与连音音符；按原词起音与延音关系映射新词。
5. G2P 生成原词和新词发音，保留音符持续时间、音高与停顿。
6. SoulX-Singer 以当前句原声音色为参考，生成旋律或乐谱条件下的新唱句。
7. 检查生成长度、非有限数和静音异常，生成 PCM WAV；以 10ms 淡入淡出接回原唱轨，按需叠加伴奏。
8. 主进程校验输入指纹后发布音频，旧输入产物不会覆盖新编辑。

默认使用 32 步、CFG 3、FP16 演唱模型，mel 前端保留 FP32。对当前共用环境的 Transformers 4.57 接口，适配器仅在自己的子进程中桥接上游自定义 Llama 层，不修改上游或安装目录的文件。

## 输入与结果管理

每句 1–20 秒、新旧中文字符/英文词单位数一致。数字和不支持的文字返回明确错误。LRC 不作为逐字时长直接送给模型。

每个 GPU 任务使用独立目录保存输入快照、模型日志、参考干声、元数据和产物；阶段结束释放模型，任务结束退出子进程。服务一次只允许一个任务，可取消。非终态任务遇服务重启时标记为中断。历史生成文件保留，当前版本单独维护。

指纹规范化整数/浮点时间值，包含源素材 ID、原词、新词、边界与生成方式。改动或换素材将使对应结果过期；重命名工程不影响生成结果。

## 验证证据与范围

已使用官方 `example/audio/zh_prompt.mp3` 完成 10 秒中文改词生成，验证 24 kHz PCM 输出、有限幅度和非静音信号。也从页面启动任务，验证生成完成状态、改词轨、音频导出及混音文件。演唱质量需要人工试听，不将文件生成成功视为音色或吐字质量的证明。

首版不支持自动分离、自动识词、跨字数改词、粤语、SVC 或完整 MIDI 钢琴卷帘编辑。共享模型版本若更换，应重新测试并重新生成已有产物。


## 歌声对齐模型适配

真实快唱片段暴露出 Qwen 大量返回零时长字的问题。现在使用 Studio 已有的 `runtime/components/hfa/versions/1.0.0/model` 与对应 `python` 只读依赖，独立运行 HFA 声学对齐。权重约 415 MB，没有复制或重新下载。

解码器和发音词典转换代码来自独立的 [Xiantaidu/Vocal2Midi](https://github.com/Xiantaidu/Vocal2Midi) checkout，固定提交 `e46590b490fca790e6cd2dacbc0bb7779babe383`，Apache-2.0。完整 LICENSE 留在 checkout 内。HFA 模型来源：[wolfgitpr/HubertFA v0.0.7](https://github.com/wolfgitpr/HubertFA/releases/tag/v0.0.7)，模型格式要求 VERSION 5；共享模型自带词典保留原许可。本工具不导入 Studio 的后端适配器。

`backend/hfa.py` 是自写的模型桥接，支持中文拼音与英文词典混合映射并保留单词索引。CPU 使用 ONNX Runtime 的两条计算线程，Qwen 按需加载。缺词、数量不符、非有限边界、过多零时长等仍按原校验拒绝，不会任意填充整句时间。

用户失败片段中，旧方式在 13 字中产生过多无时长项；HFA 返回 13 个有效正时长边界。已验证该片段的真实生成，以及页面工程的演唱/混音 WAV（24 kHz）导出。对齐模型置信度仅为模型统计，不能等同于听感或精确度证明；仍需试听。


## 逐字时间缓存

对齐可作为独立任务执行，结果保存在工程的 alignments 中。校验原词/源音频/句子范围后可复用；手动时间会作为后续 note_transcription 的词时长输入。生成任务同样发布可复用的逐字时间，避免重新加载对齐模型。对齐与生成均校验输入版本，手动时间在任务期间被修改时优先保留。参考映射见 studio-reference.md。
