import argparse
from importlib import metadata
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--cuda', action='store_true')
args = parser.parse_args()
import torch
import diffusers
import peft
import sglang
from request_defaults import fill_defaults
from sglang.multimodal_gen.runtime.pipelines_core.stages.model_specific_stages.minimax_h3.video_adapter import MiniMaxH3VideoModelAdapter

result = {'python': sys.version.split()[0], 'torch': torch.__version__,
          'cuda_runtime': torch.version.cuda,
          'sglang_distribution': metadata.version('sglang'),
          'sglang_source': sglang.__file__, 'peft_source': peft.__file__,
          'diffusers': diffusers.__version__, 'h3_adapter_import': True,
          'defaults': fill_defaults({}, {'short_edge': 544, 'num_inference_steps': 9})}
if args.cuda:
    assert torch.cuda.is_available()
    result['gpu'] = torch.cuda.get_device_name(0)
    a = torch.randn(64, 64, device='cuda')
    assert torch.isfinite(a @ a.T).all()
    from sglang.kernels.ops.attention.flash_attention_v3 import flash_attn_varlen_func
    q = torch.randn(32, 4, 64, device='cuda', dtype=torch.bfloat16)
    cu = torch.tensor([0, 32], device='cuda', dtype=torch.int32)
    output = flash_attn_varlen_func(q, q, q, cu_seqlens_q=cu, cu_seqlens_k=cu,
                                  max_seqlen_q=32, max_seqlen_k=32, causal=False)
    if isinstance(output, tuple):
        output = output[0]
    assert output.shape == q.shape and torch.isfinite(output).all()
    torch.cuda.synchronize()
    result.update(cuda_matmul=True, flash_attention_v3=True)
print(json.dumps(result, ensure_ascii=False, indent=2))
