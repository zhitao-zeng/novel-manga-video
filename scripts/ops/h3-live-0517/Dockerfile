FROM python:3.11.13-slim-bookworm

RUN --mount=type=bind,source=apt-debs,target=/tmp/h3-apt,readonly \
    dpkg -i /tmp/h3-apt/*.deb

COPY rootfs/ /
COPY start-h3.sh /usr/local/bin/start-h3.sh
COPY verify_runtime.py /opt/h3/app/verify_runtime.py
RUN chmod +x /usr/local/bin/start-h3.sh

ENV PATH=/opt/h3/python/bin:/usr/local/bin:/opt/cuda/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONPATH=/opt/h3/sglang-source:/opt/h3/deps:/opt/h3/app \
    LD_LIBRARY_PATH=/opt/h3/python/lib/python3.11/site-packages/nvidia/cu13/lib:/opt/h3/python/lib:/opt/cuda/lib64 \
    CUDA_HOME=/opt/cuda \
    CUDA_DEVICE_ORDER=PCI_BUS_ID \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 HF_ENDPOINT=https://hf-mirror.com FLASHINFER_DISABLE_VERSION_CHECK=1 \
    TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=8 \
    TRITON_CACHE_DIR=/cache/triton TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor \
    H3_MODEL_PATH=/models/MiniMax-H3 \
    H3_LORA=/models/lora/minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors \
    H3_PORT=30000 H3_OUTPUT=/outputs

LABEL org.opencontainers.image.title="MiniMax-H3 live SGLang 0.5.17 snapshot" \
      org.opencontainers.image.description="GPU052 live Ref2VA runtime with local SGLang/PEFT patches and request defaults; weights mounted separately" \
      org.opencontainers.image.version="0.5.17-live-20261021" \
      io.h3.python="3.11.15" io.h3.torch="2.11.0" io.h3.cuda-runtime="13.0" \
      io.h3.source-overlay="included-live-snapshot" io.h3.weights="external"

WORKDIR /opt/h3
EXPOSE 30000
ENTRYPOINT ["/usr/local/bin/start-h3.sh"]
