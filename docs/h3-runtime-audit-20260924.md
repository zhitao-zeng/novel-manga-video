# H3运行与最终输入核对（2026-09-24）

## 已确认

- SGLang发行包为0.5.17；部署目录没有.git，不能报出准确源码提交号。已冻结实际运行路径的8个核心文件，两台服务逐文件一致。
- 服务选择Ref2VA，Base下载元数据revision为42ed227ee7df40d41602854ae760620d6eb651fe；没有改成FL2VA或混合权重。
- LoRA为minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors，1,383,677,808 bytes，alpha8、scale1、BF16。两台文件SHA256均与官方文件9bac880b1a5d7ac052171cf6cce769f0cceaaa42ffa51de4b8e41143a2bdd2d2一致。
- API的num_inference_steps决定sigma点数，去噪循环使用相邻点对：8→7次模型求值，9→8次。服务日志7/7与源码执行结果一致。夜班默认填9，但显式生产请求8覆盖默认值。
- 该Ref2V 8-step v1.0 768p的官方推荐shift为12/3；不要把FL2VA 768p的6/3套过来。
- 客户端参考图保持比例，最长边1280、JPEG质量88；服务端实际函数为目标面积match、不过度放大、nearest32，无参考内容crop。模块旧顶部注释还写2048短边，但函数已改为match，以实际执行函数为准。

## 输入层已确认的矛盾

- clip09第一镜只拍席勒，光线仍要求装甲反光；第二镜先将席勒置于画外，结束又写席勒在背景。该片段第二镜本应让托尼穿甲离开，不能用整段零装甲作验收标准。
- clip15第一镜的画外装甲反光没有被导演备注彻底移除。召甲后的正确数量是两名人物（其中托尼穿甲）加一套独立空甲。
- clip17/19有背影或画外、背影或侧面等未定构图，当前编译并未把镜头状态收敛成唯一决定。
- planning.flow先做retention语义审查，后做道具标注与presence在场增删，随后直接落盘；episode_plan还在此前构造。终审结果与最终各产物存在分叉风险。后续应将公共解析和检查对准最终场景，修正源数据，不再依赖单段备注覆盖。
- media.cache.request_matches未比较视频采样配置；只改底层步数并不能证明重跑时旧7NFE素材被替换。不能用缓存成片宣称验证了新配置。

## 已完成的小型对照

固定clip11、clip15，各用历史种子及+1009第二种子，分别请求8/9个sigma点。每对同一实例、同提示词、同素材、同对白和时长，唯一因素为求值次数；生成总预算8条，原片缓存不用来充当新基线。

不在批次中途改提示词，不以某一条成功提前宣称胜出。对照不证明新配置泛化，也不解决已知输入矛盾。生产代码和服务暂未切换，整集重拍未启动。

## 后续方案与条件

1. 共享流程：终审顺序、最终场景一致性、运行参数与缓存记录，需要独立回归；本次审查记录不等于已修复上线。
2. Context-IR：官方是托管接口/v2/h3_context_ir，项目未找到对应凭据；已询问配置位置。不能用本地Qwen翻译改名代替官方对照。
3. 原版Ref2VA：当前实例加载合并LoRA，不宜在共享服务上临时清掉LoRA冒充独立原版路径；需要独立资源/实例和匹配采样配置。
4. 人物/装甲A/B/C：先在相同输入和已确认运行条件下对照；C需要分别验收穿甲开面罩、闭面罩与无人空甲，不能混作一张图。
5. 显式关键帧：部署源码接受ref2va的reference+keyframe混合请求，但代码注明适用于hybrid checkpoints；尚未证明当前官方Ref2VA+Turbo权重可可靠使用。不把普通reference图片当首帧。
6. 声音跨切点：官方文档的scenetrans描述单次生成内部的声音连续性，不提供跨请求记忆。需独立实验。
7. 整集节奏/验收：先跑通固定回归，再重排叙事与整集验收；不加音乐掩盖静音比例，也不自动开启整集重拍。

## 一手来源

- Ref2V 8-step发布与12/3设置：https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/51
- 官方LoRA文件与校验值：https://huggingface.co/lightx2v/Minimax-h3-Turbo/blob/main/minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors
- 蒸馏调度与match参考图策略：https://github.com/ModelTC/Minimax-H3-Turbo
- 官方Context-IR示例：https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/scripts/readme/full-2k-ref2va-h3-context-ir.sh
- 官方完整参考提示词指南：https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md


完整现场证据目录：outputs/h3-runtime-contract-20260924-233455/。基线、请求、任务ID、sigma数值、核心源码和图片预处理尺寸均已冻结。


## 小型对照结论（2026-09-25）

8条完成，四对请求只改变sigma点数。两组语音门均4/4通过；穿甲单人镜头同一坏种子在7/8NFE都多出身体，另一种子都未见此错。召甲镜头有一个种子的多余装甲减少，但两种子的首镜画外装甲入镜问题都仍存在。两组各4条中都有3条被固定抽帧确认存在语义状态错误，不证明完整视频通过，也不能宣称仅改步数可稳定修复。

实际生产参数尚未更改。后续优先修共用采样记录、最终场景检查与导出顺序，再分别开展装甲表示等对照，不整集盲重拍。详见 outputs/h3-runtime-contract-20260924-233455/results.md 与 probe-metrics.json。
