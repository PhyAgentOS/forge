"""Regression matrix for Pillow 13 using Forge's public image API."""

import io

import numpy as np
import pyarrow as pa
import pytest
from forge_msgs import CompressedImage, Image
from PIL import Image as PILImage


@pytest.mark.parametrize("fmt", ["jpeg", "png", "webp"])
@pytest.mark.parametrize("shape", ["rgb", "gray", "gray_channel"])
@pytest.mark.parametrize("strided", [False, True])
def test_codec_shape_dtype_channel_order(fmt, shape, strided):
    rgb = np.empty((48, 64, 3), dtype=np.uint8)
    rgb[:] = [220, 90, 30]
    frame = rgb if shape == "rgb" else np.full((48, 64), 123, dtype=np.uint8)
    if shape == "gray_channel":
        frame = frame[..., None]
    if strided:
        frame = frame[::2, ::2]
    compressed = CompressedImage.from_numpy(frame, format=fmt, quality=95)
    batch = compressed.to_arrow()
    buf = io.BytesIO()
    with pa.ipc.new_stream(buf, batch.schema) as writer:
        writer.write_batch(batch)
    decoded = CompressedImage.from_arrow(buf.getvalue()).to_numpy()
    expected = frame if frame.ndim == 3 else frame[..., None]
    if fmt == "webp" and expected.shape[-1] == 1:
        expected = np.repeat(expected, 3, axis=-1)
    assert decoded.shape == expected.shape
    assert decoded.dtype == np.uint8
    tolerance = 0 if fmt == "png" else 5
    assert np.abs(decoded.astype(int) - expected.astype(int)).max() <= tolerance
    external = np.array(PILImage.open(io.BytesIO(compressed.data)))
    if external.ndim == 2:
        external = external[..., None]
    # WebP decodes gray input as RGB; Forge's stable output contract follows mode.
    np.testing.assert_array_equal(decoded, external)


@pytest.mark.parametrize(
    "encoding,dtype", [("16UC1", "uint16"), ("32SC1", "int32"), ("32FC1", "float32")]
)
def test_depth_remains_exact_and_independent_of_pillow(encoding, dtype):
    frame = np.array([[0, 123, 65535], [1, 1000, 5000]], dtype=dtype)
    msg = Image.from_numpy(frame, encoding=encoding)
    np.testing.assert_array_equal(Image.from_arrow(msg.to_arrow()).to_numpy(), frame)
    with pytest.raises(ValueError, match="uint8"):
        CompressedImage.from_numpy(frame, format="png")


def test_bgr_input_is_not_a_pillow_mode_conversion():
    bgr = np.array([[[30, 90, 220]]], dtype=np.uint8)
    msg = Image.from_numpy(bgr, encoding="bgr8")
    np.testing.assert_array_equal(Image.from_arrow(msg.to_arrow()).to_numpy(), bgr)


@pytest.mark.parametrize("fmt", ["jpeg", "png", "webp"])
def test_invalid_bitstream_fails_explicitly(fmt):
    with pytest.raises(OSError):
        CompressedImage(format=fmt, data=b"invalid-image-data").to_numpy()
