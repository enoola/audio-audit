# Local model assets

Runtime analysis never downloads a model. The optional Silero VAD artifact is
provisioned separately and verified before use.

The pinned artifact is:

- file: `silero_vad_16k_op15.onnx`
- upstream: `snakers4/silero-vad`
- source commit: `5cd7945676eb32225748052e2e6a0580e4686a08`
- SHA-256: `7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49`
- upstream license: MIT (retain and review the upstream license before redistribution)

Provision it on a connected build machine with:

```bash
uv run python scripts/provision_models.py
```

Then install the optional runtime with:

```bash
uv sync --python 3.12 --extra vad
```

If the artifact or `onnxruntime` is absent, `auto` uses the explicitly labelled
energy activity fallback. Use `--vad-backend silero` to fail instead of falling back.
