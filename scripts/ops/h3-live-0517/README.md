# H3 线上 0.5.17 环境快照

已发布镜像：

```text
harbor-contest.4pd.io/zengzhitao/minimax-h3-sglang:0.5.17-ref2va-cu130-live-20261021
```

不可变 manifest digest：`sha256:1b79447b50a3fe067bf585a771c6ac3331647a8b37be21cf635bee433f9cd29b`。Linux/amd64，10 层，压缩层共 9,631,401,771 字节。详细回执在 `release-20261021.json`。

这是 GPU052 线上环境的本地源码覆盖层快照：Python 3.11.15、Torch 2.11.0+cu130、SGLang 0.5.17 发行包、实际 `sglang-source`、PEFT 覆盖层和请求默认值补丁。CUDA runtime 为 13.0，编译器为 13.2。源码不能视为未经修改的官方 v0.5.17；它与另行发布的官方 0.5.19 镜像分别管理。

## 启动

宿主机需有兼容的 NVIDIA 驱动、NVIDIA Container Toolkit 和支持 OCI/zstd 镜像的 Docker。已在 GPU052 Docker 29 的 containerd 镜像存储验证。

```bash
docker run --rm --gpus '"device=0,1,2,3"' --ipc=host \
  -p 30000:30000 \
  -v /path/to/MiniMax-H3:/models/MiniMax-H3:ro \
  -v /path/to/lora-directory:/models/lora:ro \
  -v /path/to/h3-outputs:/outputs \
  -v /path/to/h3-cache:/cache \
  harbor-contest.4pd.io/zengzhitao/minimax-h3-sglang:0.5.17-ref2va-cu130-live-20261021
```

基础模型挂载原始 MiniMax-H3 根目录；LoRA 目录内需要 `minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors`。两者均未进入镜像或 Git。

默认 H100 四卡 TP2 / Ulysses2、FA 后端、短边 544、16:9、5 秒、`num_inference_steps=9`、`flow_shift=12`、`audio_flow_shift=3`。9 个 sigma 点对应八次模型求值。请求显式值仍可覆盖默认值。容器内部 master/scheduler 端口为 30100/30200。

## 检查

```bash
docker run --rm --network none -e NVIDIA_VISIBLE_DEVICES=void \
  --entrypoint python IMAGE /opt/h3/app/verify_runtime.py
docker run --rm --network none --gpus '"device=5"' \
  --entrypoint python IMAGE /opt/h3/app/verify_runtime.py --cuda
```

第二条命令须选择实际空闲的 H100。已验证容器依赖、源码路径、请求默认值、Python.h、G++/NVCC、FFmpeg/FFprobe、H100 CUDA 矩阵计算、FA3，以及只读挂载原权重/LoRA 后的入口 `--help`。本次没有执行完整四卡权重加载或视频生成，不以这些基础检查声称新成片通过。

## 从实际环境构建

`snapshot_runtime.py` 必须由待导出的 H3 环境解释器运行。它读取已安装版本、复制所选包的实际目录和顶层扩展，保留 Conda 清单未列出的已安装二进制文件；不安装或升级源环境。`package-versions.json` 记录本次发布版本，清单中未安装的声明依赖保持原样。

在项目外的独立构建目录执行：

```bash
export H3_RECIPE_DIR=/path/to/novel-manga-video/scripts/ops/h3-live-0517
export H3_BUILD_DIR=/path/to/h3-image-build
export H3_EXPORT_PYTHON=/path/to/live-h3-python/bin/python
mkdir -p "$H3_BUILD_DIR"
cp "$H3_RECIPE_DIR"/{Dockerfile,.dockerignore,start-h3.sh,verify_runtime.py,fetch_debs.py,apt-downloads.json} "$H3_BUILD_DIR/"
H3_IMAGE_BUILD_CONTEXT="$H3_BUILD_DIR" \
  H3_SOURCE_PROJECT=/path/to/live-h3-project \
  H3_SOURCE_DEPLOY=/path/to/live-h3-project/deploy/current \
  "$H3_EXPORT_PYTHON" "$H3_RECIPE_DIR/snapshot_runtime.py"
cd "$H3_BUILD_DIR"
python3 fetch_debs.py
docker build --pull=false --network=none \
  --output=type=image,oci-mediatypes=true,compression=zstd,compression-level=1 \
  -t h3-live-snapshot:local .
```

部署目录须含当前 `entry.py` 和 `request_defaults.py`；项目目录须含 `sglang-source`、`deps/peft`。构建机还需已有 `python:3.11.13-slim-bookworm` 基础镜像。48 个冻结 Debian 包共 70,135,928 字节，构建时只读挂载安装；安装包和模型文件都不写入 Git。

已有完整构建上下文在 GPU052：`/mnt/disk0/zzt/minimax-h3-r2v-544p/deploy/docker-0517-live-20261021`。当前镜像含冻结源码；再次导出已变化的现场环境会得到新的快照，不能冒充原发布版本。精确复用使用上面的 immutable digest。

下载器使用本次命令的直接网络连接，不继承会话代理。上传使用操作者已有 Docker 凭据，不把登录信息复制到源码、构建上下文或镜像。本目录只归档代码和小型版本清单。
