# Qwen-Image-2.1 部署与接入调研（2026-09-22）

下载中，尚未跑通任何推理。本文是动手前的事实梳理和方案，不含实测结论。

## 1. 模型

| | |
|---|---|
| 仓库 | `Qwen/Qwen-Image-2.1`，revision `790c92633540` |
| 本地 | `/mnt/disk3/Qwen-Image-2.1`（28 文件 / 33.1 GB） |
| 配套 | `Qwen-Image-2.1-PE-T2I`、`PE-I2I`，各 15 文件 / 18.8 GB，同在 disk3 |
| 许可 | **Qwen RESEARCH LICENSE**（2026-09-20 发布） |

`model_index.json`：

```
_class_name      QwenImage21Pipeline
_diffusers_version 0.37.0.dev0
text_encoder     Qwen3VLForConditionalGeneration
transformer      QwenImage21Transformer2DModel
vae              AutoencoderKLQwenImage21
scheduler        FlowMatchEulerDiscreteScheduler
processor        Qwen3VLProcessor
```

视觉生成组件 7B，32 层 Single-Stream DiT。四个官方卖点里两个和本项目直接相关：

- **原生 RGBA 透明**：角色卡可直接出透明背景
- **最多 10 张参考图**：现在 `create_image` 只传 1 张（`base64File`）或多张（`base64Files`），10 张可同时锁脸、锁服装、锁风格

## 2. 许可证

用户已决定本轮不因许可证停下，此处只做记录。

```
"Non-Commercial" shall mean for research or evaluation purposes only.
You are granted a ... license ... FOR NON-COMMERCIAL PURPOSES ONLY.
You shall not use the Materials for any commercial purpose without obtaining
a separate commercial license from us. ... model-business@notice.qwencloud.com
```

与旧版不同：`Qwen/Qwen-Image`（2508）是 Apache-2.0，可商用；2.1 改成了研究许可。
本项目批量出片并上传 ModelScope 公开数据集，不属于 "research or evaluation only"。
若要对外发布 2.1 产出的内容，需先取得单独商用授权。

## 3. 依赖缺口

| 依赖 | ComfyUI venv 现有 | 2.1 要求 | 状态 |
|---|---|---|---|
| torch | 2.10.0+cu128 | ≥2.4.0 | 满足 |
| transformers | 5.14.1 | ≥5.17 | 差 3 个小版本；但 `Qwen3VLForConditionalGeneration` 和 `Qwen3VLProcessor` 在 5.14.1 已存在 |
| diffusers | 0.39.0 | 0.37.0.dev0 的三个新类 | **发行版里没有** |

实测 `diffusers 0.39.0`：`QwenImage21Pipeline` / `QwenImage21Transformer2DModel` /
`AutoencoderKLQwenImage21` 三个类**一个都没有**，只有旧的 `QwenImagePipeline`、`QwenImageEditPipeline`。
走 diffusers 就得装 git 主分支，版本会漂；走 vLLM-Omni 或 SGLang 可绕开。

`novel-manga-video/.venv` 里没有 torch —— 它是纯编排层，图片全走 PhanRouter HTTP API。
所以接入不是 import，是加一个 provider。

## 4. 显存：本机七张卡全部有主

| 占用者 | 卡数 | 每卡 | 说明 |
|---|---:|---:|---|
| `sgl_diffusion::scheduler_TP0/1_U0/U1` | 4 | 60.7 GB | **H3 夜班**（`h3-night-shift`，今日 01:35 启动），SGLang 扩散后端，TP2×Ulysses2 |
| `VLLM::EngineCore` | 3 | ~70.8 GB | **Qwen3.8-27B-Project** 文本服务，容器内 |

每卡余量 11–21 GB。2.1 的 BF16 完整加载 27–30 GB，**单卡余量装不下**。

H3 夜班那四张**不要碰**：该项目规则是"排除 zengzhitao 的实例及其占用的整张卡"，
且有早间恢复流程，强行停会打断在跑的 H3 任务。

可选路径：等夜班白天让卡 / `enable_model_cpu_offload()` / FP8 量化（vLLM-Omni 支持，约 15 GB） / 上远程机。

## 5. 部署方式

| 方式 | 命令 | 备注 |
|---|---|---|
| **vLLM-Omni** | `vllm serve Qwen/Qwen-Image-2.1 --omni --port 8091` | 给出 OpenAI 兼容的 `/v1/images/generations`；支持 TP/Ulysses/Ring/CFG 并行、分布式 VAE 解码、FP8 |
| SGLang | `sglang generate` | 同类并行能力 + 组件 offload；夜班已在用这套 |
| Diffusers | 无内置 server | 需自己包服务层 |

本机 `vllm` 未装在任何可见 venv（三个跑着的在容器里）。8091 端口当前被一个返回 HTML 404 的服务占着，与 vLLM 无关。

推荐参数：`num_inference_steps=40`，原生 2K（2048×2048），7 种画幅最大 **2752×1536**
（1.79:1，与 16:9 的 1.778 基本一致，三本书都是 16:9）。

## 6. 接入方案

照 `providers/local_h3.py` 的形状新增 provider：

```
现有   LocalH3MediaProvider(PhanRouterMediaProvider) + h3_pool.py 管实例
新增   LocalQwenImageProvider(PhanRouterMediaProvider) + 同样的池子
       覆盖 payload 构造，指向 vLLM-Omni 的 /v1/images/generations
```

接口形状接近：现有 `phanrouter_images.create_image()` 打 `POST /v3/images/generations`，
payload `{model, prompt, aspectRatio, resolution, base64File}`；vLLM-Omni 是 OpenAI 兼容形状。
主要工作是字段映射和多参考图（最多 10 张）的传递。

**与画风包的衔接**：建议给 `configs/styles/*.json` 加 `image_model` 字段，
不同画风走不同出图模型。这本来就是画风包该管的维度，且新字段对老包缺省即维持现状。

## 7. PE 两个模型

`PE-T2I`（`run_vllm.py --task t2i`）和 `PE-I2I`（`--task edit`）是 Qwen3.5-VL 微调版，
把短提示词扩写成详细描述，并给出推荐画幅。

**先别挂进链路。** 本项目刚把画风包的措辞做到分层不重复
（见 `style-packages-plan-20260920.md` 与 commit `86c110d`），PE 会把提示词整体重写，
与手写的画风声明大概率冲突。应先单独测"同一张卡，PE 改写前后"的差异，再决定。

## 8. 下一步

1. 等下载完成并通过逐文件字节校验
2. 找一张可用的卡或确定 offload/量化方案——这是当前最硬的技术阻塞
3. 用 diffusers git 主分支或 vLLM-Omni 跑通一张图，确认端到端可行
4. 再谈 provider 和画风包接线

尚未做：任何推理、任何性能测试、vLLM-Omni 的安装与版本确认。
