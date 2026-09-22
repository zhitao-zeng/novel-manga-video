"""Existing asset request matching, stale archives and single-image generation."""
from __future__ import annotations

from pathlib import Path
import json
import shutil
from ..providers.base import ImageResult
from ..providers.local_qwen_image import LOCAL_IMAGE_MODEL
from ..util import atomic_write_json
from .common import sha256_text, sha256_file

def _archive_stale(path: Path, old_hash: str) -> None:
    if not path.exists():
        return
    archived = path.with_name(f"{path.stem}.stale-{old_hash[:8]}{path.suffix}")
    if archived.exists():
        archived = path.with_name(f"{path.stem}.stale-{old_hash[:12]}{path.suffix}")
    shutil.move(path, archived)


def ensure_image(
    settings, provider,
    prompt: str,
    output: Path,
    *,
    reference: Path | None = None,
    additional_references: tuple[Path, ...] = (),
    aspect_ratio: str | None = None,
) -> ImageResult:
    identity = {
        "prompt_sha256": sha256_text(prompt),
        "reference_sha256": sha256_file(reference) if reference and reference.is_file() else None,
        "additional_reference_sha256s": [
            sha256_file(path) for path in additional_references
        ],
        "provider": settings.provider,
        "image_model": settings.image_model,
        "image_transport": (
            "phanrouter-gpt-image-2"
            if settings.provider == "command"
            and "".join(
                character
                for character in settings.image_model.casefold()
                if character.isalnum()
            )
            == "gptimage2"
            and (
                settings.phanrouter_image_api_key
                or settings.phanrouter_api_key
            )
            else settings.provider
        ),
        "image_command_sha256": (
            sha256_text(settings.image_command) if settings.image_command else None
        ),
        # Only present when cards are drawn locally, so turning the service off leaves every
        # existing card's request hash exactly as it was; turning it on redraws, which is right
        # - a card from a different model is a different card.
        **({"local_image_model": LOCAL_IMAGE_MODEL} if settings.local_image_base_url else {}),
    }
    identity_hash = sha256_text(json.dumps(identity, sort_keys=True))
    meta = output.with_suffix(output.suffix + ".request.json")
    if output.is_file() and meta.is_file():
        saved = json.loads(meta.read_text(encoding="utf-8"))
        if saved.get("request_sha256") == identity_hash:
            if "provider_reference" in saved:
                saved.pop("provider_reference", None)
                atomic_write_json(meta, saved)
            return ImageResult(path=output)
        if settings.reuse_existing_assets:
            atomic_write_json(
                meta,
                {
                    **identity,
                    "request_sha256": identity_hash,
                    "artifact_sha256": sha256_file(output),
                    "origin": "locked-existing-asset",
                    "previous_request_sha256": saved.get("request_sha256"),
                },
            )
            return ImageResult(path=output)
        _archive_stale(output, str(saved.get("request_sha256", "unknown")))
        _archive_stale(meta, str(saved.get("request_sha256", "unknown")))
        output.with_suffix(output.suffix + ".task.json").unlink(missing_ok=True)
    elif output.is_file() and settings.reuse_existing_assets:
        atomic_write_json(
            meta,
            {
                **identity,
                "request_sha256": identity_hash,
                "artifact_sha256": sha256_file(output),
                "origin": "locked-existing-asset",
                "previous_request_sha256": None,
            },
        )
        return ImageResult(path=output)
    options = {"aspect_ratio": aspect_ratio} if aspect_ratio else {}
    if additional_references:
        result = provider.create_image(
            prompt,
            output,
            reference=reference,
            additional_references=additional_references,
            **options,
        )
    else:
        # Keep simple provider test doubles and hosted backends compatible
        # when a task genuinely has only one reference.
        result = provider.create_image(prompt, output, reference=reference, **options)
    atomic_write_json(
        meta,
        {
            **identity,
            "request_sha256": identity_hash,
        },
    )
    return result


