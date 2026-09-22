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

---

## 9. 实际接入（2026-09-22 当天完成）

上面第 6、8 节是下载还没跑完时写的设计草稿，实际做法有三处不同，记在这里以本节为准。

### 做成了常驻服务，不是 `command` provider，也没用 vLLM-Omni

`CommandMediaProvider` 已经存在且实现完整（`NOVEL_IMAGE_COMMAND`，约定
`<命令> --prompt … --width … --height … --output <路径>`），但生产代码一处都没有实例化它——
`provider` 在五个出卡入口都是字面量 `"phanrouter"`，`Settings.from_env` 里也没有环境变量覆盖，
所以那条路走不到。而且它每次调用起一个进程：一张卡画 64s、加载 pipeline 也要 ~60s，
八十个角色的书会把一半时间花在加载上。

改成照 H3 的形状做常驻服务：

```
qwen-image-21/serve.py            模型常驻，stdlib http.server（单线程 = GPU 天然串行）
  GET  /health    -> {"ok":true,"model":…,"gpu":N,"drawn":N,"busy":false}
  POST /generate  <- {"prompt","width","height","seed"?,"steps"?,"references":[base64…]}
                  -> image/png
```

没引入 fastapi/uvicorn：出卡入口（`phase_cards.py` / `cards.py`）本来就是串行的，
单线程 HTTP 正好把单卡串起来，不必再加锁。

### 开关是一个字段，不是第二个子类

`Settings.local_image_base_url`（`NOVEL_LOCAL_QWEN_IMAGE_URL`），由
`media/adapters.py` 的 `FramedPhanRouter` 在构造时读取：

```python
self.local_image = (LocalQwenImageProvider(settings, settings.local_image_base_url)
                    if settings.local_image_base_url else None)

def create_image(self, prompt, output, reference=None, additional_references=(), *, aspect_ratio=None):
    target = self.local_image or super()
    return target.create_image(...)
```

五个出卡入口一行没改——它们本来就构造 `FramedPhanRouter`，`FramedLocalH3` 继承后也一起拿到。
用字段而不是第二个子类，是因为「图本地/远程」和「视频本地/远程」是两个独立开关，
两个开关做成四个类不如就是两个开关。视频、参考图上传、公开 URL 这些完全没碰。

### 缓存身份：新键只在开启时出现

`ensure_image` 用 `identity` 字典的 sha256 判断卡是否过期。无条件加一个键会让**所有已付费的卡**
在下次运行时被判为过期并 `_archive_stale` 掉，所以：

```python
**({"local_image_model": LOCAL_IMAGE_MODEL} if settings.local_image_base_url else {}),
```

实测（HEAD 的 src 与改后的 src 各跑一次）：

```
关闭  request_sha256 = 6fdc0196…   与改动前逐字一致
开启  request_sha256 = 287771af…   不同 —— 换了模型就该重画
```

开启后会重画该书已有的卡。想保留旧卡只补缺的，用 `reuse_existing_assets`。

### 尺寸

`image_dimensions` 给的是 1080x1920 / 1920x1080，而 pipeline 要求长宽都是 32 的倍数
（否则静默改尺寸）。各自向上取整会把 9:16 歪成 0.7% 再缩回来，等于一次各向异性挤压，脸上看得出来。
服务改为在 32 的倍数里挑长宽比最接近、面积最接近的一组来画，画完缩到请求的确切尺寸：

```
1080x1920 -> 画 1152x2048 (比例误差 0.000%) -> 缩到 1080x1920
1920x1080 -> 画 2048x1152 (比例误差 0.000%) -> 缩到 1920x1080
```

客户端收到 PNG 后按输出后缀转成 JPEG（quality 95、不做色度二次采样——平涂硬边最容易被 JPEG 振铃），
并在尺寸对不上时再缩一次：服务正常情况下已经缩好，但卡的画幅是后面每个镜头的构图基准，
调用方要到的就该是它要的那个。

### 验证

- `pytest tests/` 全绿（1274 项），其中两项是本次新增：路由与落盘格式、缓存身份的有无
- 端到端：起服务 → 真实 `Settings` + 真实 `FramedPhanRouter` + 真实 `ensure_image`
  → 画《在美漫当心灵导师的日子》托尼·斯塔克一张，64s，落盘 1080x1920 JPEG 0.65 MB，
  `request.json` 记到 `local_image_model=qwen-image-2.1`，二次调用 0.00s 命中缓存。
  写在 `tmp/`，没有碰 `outputs/` 下任何在产的卡。

### 画风包按模型分开措辞

同一段文字两个模型读出来不一样。最典型的是「选角定妆照」——gpt-image 认识这个行话并照着画，
Qwen 不认识，画出来是张海报（戏剧侧光、手插兜、墙角），当参考图没法用。所以要把画面写开。
但反过来，对 gpt-image 写开比直接用行话更差。

做法是画风包里开一个按模型分的小节，不是拆成两个会各自漂移的包：

```json
{
  "name": "美漫",
  "render_direction": "轮廓和衣褶由清晰的黑色线条勾出…",   // 两个模型通用：这描述的是画风本身
  "qwen": {
    "card_brief": "这是一张供动画制作使用的服装参考图。…"   // 只有本地 Qwen 出卡时才叠加
  }
}
```

`AssetStyle.for_genre(..., backend=image_backend(settings))`：`settings.local_image_base_url`
有值就取 `qwen` 小节覆盖顶层，没有就原样。三个装配点（`cards.py` / `phase_cards.py` /
`rendering/flow.py`）各加一个参数。

已迁移 `live` / `weimei` / `meiman` 三个包的 `card_brief`。实测（与加这套机制之前的
`1282149` 对比，77 本书 29582 条提示词）：

```
gpt-image 路径   0 处差异        ← 之前把 card_brief 放顶层造成的 4 条漂移已消除
Qwen 路径        4 条改写        ← 正好是没有冻结 style.json、直读 configs/styles 的那 4 本样本书
```

`3d-guoman-qwen.json` 暂未合并：它整包就是 Qwen 变体，`card_brief` 放顶层不会误伤
gpt-image，但现在和 `3d-guoman.json` 有重复的措辞，折进去会更省事。

### 还没做

- PE 两个模型仍未接，理由见第 7 节，未变。
- `meiman-daoshi` 的 `outputs/meiman-daoshi/style.json` 还是建书时冻结的初版，
  不含新的 `card_brief`；要让它吃到第八版措辞需要显式刷新（按设计，改 `configs/styles/`
  不会回头改已建的书）。
