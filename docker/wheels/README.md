# Local CUDA wheels

Place the FlashAttention wheel named below in this directory before building
the offline image:

`flash_attn-2.8.3.post1-cp312-cp312-linux_x86_64.whl`

The Dockerfile verifies its SHA256 before installation.  Wheels are local
build artifacts and are intentionally excluded from Git.
