"""Source ranges and identity evidence shared by repair and source rechecks."""
from __future__ import annotations

def source_passage(segments: dict, segment_ids: list) -> str:
    """Keep adjacent prose so a quote's speaker is not lost at segment borders."""
    ids = list(segments)
    selected = {i for i,key in enumerate(ids) if key in {str(s) for s in segment_ids}}
    window = {j for i in selected for j in range(max(0,i-1),min(len(ids),i+2))}
    return '\n'.join(segments[ids[i]] for i in sorted(window))


