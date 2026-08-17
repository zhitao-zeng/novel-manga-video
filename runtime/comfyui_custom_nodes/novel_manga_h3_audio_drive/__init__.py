"""Minimal MiniMax H3 source-audio lock node used by novel-manga-video.

Derived from VRGDG_MiniMaxH3AudioDrive by Jean Thompson (2026) and modified
for a narrowly scoped, self-contained deployment on 2026-08-13. This file is
licensed under AGPL-3.0; see LICENSE in this directory.
"""

import torch
import torchaudio

import comfy.nested_tensor


def _nested_av_parts(av_latent):
    if not isinstance(av_latent, dict) or "samples" not in av_latent:
        raise ValueError("MiniMax H3 Audio Drive requires an AV LATENT input")
    samples = av_latent["samples"]
    if not getattr(samples, "is_nested", False):
        raise ValueError("MiniMax H3 Audio Drive expected a joint AV latent")
    parts = list(samples.unbind())
    if len(parts) < 2:
        raise ValueError("MiniMax H3 Audio Drive could not find the audio latent")
    return parts[0], parts[1]


def _fit_audio_latent(encoded_audio, template_audio):
    if encoded_audio.ndim != 4 or template_audio.ndim != 4:
        raise ValueError("MiniMax H3 audio latents must be four dimensional")
    if encoded_audio.shape[1:-1] != template_audio.shape[1:-1]:
        raise ValueError("Encoded source audio does not match H3 audio layout")
    target_batch = template_audio.shape[0]
    if encoded_audio.shape[0] == 1 and target_batch > 1:
        encoded_audio = encoded_audio.repeat(target_batch, 1, 1, 1)
    elif encoded_audio.shape[0] != target_batch:
        encoded_audio = encoded_audio[:target_batch]
        if encoded_audio.shape[0] != target_batch:
            raise ValueError("Source audio batch cannot match H3 latent batch")
    target_t = template_audio.shape[-1]
    current_t = encoded_audio.shape[-1]
    if current_t > target_t:
        encoded_audio = encoded_audio[..., :target_t]
    elif current_t < target_t:
        encoded_audio = torch.cat(
            (
                encoded_audio,
                encoded_audio.new_zeros(
                    (*encoded_audio.shape[:-1], target_t - current_t)
                ),
            ),
            dim=-1,
        )
    return encoded_audio.to(
        device=template_audio.device, dtype=template_audio.dtype
    )


class VRGDG_MiniMaxH3AudioDrive:
    """Lock source audio in H3's AV latent while only sampling video."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "av_latent": ("LATENT",),
                "source_audio": ("AUDIO",),
                "audio_vae": ("VAE",),
            }
        }

    RETURN_TYPES = ("LATENT", "AUDIO")
    RETURN_NAMES = ("audio_driven_av_latent", "original_audio")
    FUNCTION = "apply_audio_drive"
    CATEGORY = "Novel Manga/Video/Conditioning"

    def apply_audio_drive(self, av_latent, source_audio, audio_vae):
        if not isinstance(source_audio, dict):
            raise ValueError("MiniMax H3 Audio Drive requires AUDIO input")
        waveform = source_audio.get("waveform")
        sample_rate = source_audio.get("sample_rate")
        if waveform is None or sample_rate is None or waveform.ndim != 3:
            raise ValueError("Connected AUDIO has invalid waveform/sample-rate data")
        video_latent, template_audio = _nested_av_parts(av_latent)
        vae_sample_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
        if int(sample_rate) != vae_sample_rate:
            waveform = torchaudio.functional.resample(
                waveform, int(sample_rate), vae_sample_rate
            )
        encoded_audio = audio_vae.encode(waveform[:1].movedim(1, -1))
        encoded_audio = _fit_audio_latent(encoded_audio, template_audio)
        output = av_latent.copy()
        output["samples"] = comfy.nested_tensor.NestedTensor(
            (video_latent, encoded_audio)
        )
        output["noise_mask"] = comfy.nested_tensor.NestedTensor(
            (torch.ones_like(video_latent), torch.zeros_like(encoded_audio))
        )
        return output, source_audio


NODE_CLASS_MAPPINGS = {
    "VRGDG_MiniMaxH3AudioDrive": VRGDG_MiniMaxH3AudioDrive,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "VRGDG_MiniMaxH3AudioDrive": "Novel Manga MiniMax H3 Audio Drive",
}
