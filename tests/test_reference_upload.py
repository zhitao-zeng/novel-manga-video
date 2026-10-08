"""Large generated PNG references fit the image API without altering the stored card."""
import base64
import io
import random

from PIL import Image

from novel_manga.providers.phanrouter_images import reference_base64


def test_large_png_under_jpeg_name_has_a_small_valid_upload_and_original_stays_intact(tmp_path):
    path = tmp_path / 'turnaround.jpeg'
    pixels = random.Random(2026).randbytes(1500 * 2000 * 3)
    Image.frombytes('RGB', (2000, 1500), pixels).save(path, format='PNG')
    original = path.read_bytes()
    assert len(original) > 2 * 1024 * 1024
    encoded = reference_base64(path)
    # The former raw-base64 body exceeded the gateway limit in a real request.
    assert len(encoded) < 2 * 1024 * 1024
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.format == 'JPEG'
        assert image.size == (1280, 960)
    assert path.read_bytes() == original


def test_small_reference_keeps_its_original_representation(tmp_path):
    path = tmp_path / 'small.png'
    Image.new('RGB', (32, 32), 'red').save(path)
    assert base64.b64decode(reference_base64(path)) == path.read_bytes()
