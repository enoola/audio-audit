from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

MODEL_NAME = "silero_vad_16k_op15.onnx"
MODEL_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"
MODEL_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    "5cd7945676eb32225748052e2e6a0580e4686a08/"
    "src/silero_vad/data/silero_vad_16k_op15.onnx"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    destination = Path(__file__).resolve().parents[1] / "models" / MODEL_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and sha256(destination) == MODEL_SHA256:
        print(f"Already provisioned: {destination}")
        return 0

    fd, temporary_name = tempfile.mkstemp(prefix=f".{MODEL_NAME}.", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        print(f"Downloading pinned Silero VAD artifact from {MODEL_URL}")
        with (
            urllib.request.urlopen(MODEL_URL, timeout=60) as response,
            temporary.open("wb") as handle,
        ):
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        actual = sha256(temporary)
        if actual != MODEL_SHA256:
            raise RuntimeError(
                f"SHA-256 mismatch for downloaded model: expected {MODEL_SHA256}, got {actual}"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Provisioned and verified: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
