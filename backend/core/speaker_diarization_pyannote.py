"""The optional pyannote.audio speaker-diarization backend.

Chosen with ``FLUENTFLOW_SPEAKER_BACKEND=pyannote``. More accurate on hard
meetings than the default sherpa-onnx backend, several times slower, and it
needs PyTorch (about 900 MB more, ``requirements-speaker.txt``). Its model files
come from ``scripts/fetch_pyannote_models.py`` or, failing that, from Hugging
Face with a token. `backend.core.speaker_diarization` decides which backend
runs; this module only knows pyannote.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.core.local_config import get_sensitive_setting
from backend.core.runtime_paths import default_pyannote_model_dir
from backend.core.speaker_turns import SpeakerTurn


_TORCH_LOAD_LOCK = threading.Lock()


def local_model_config() -> Path | None:
    """The locally staged pipeline config, when a usable one is present.

    Preferred over the Hugging Face repo id: on 2026-09-03 this machine reached
    the Hugging Face API fine but got zero bytes from its model CDN, and
    `from_pretrained` neither failed nor finished. Local files remove the
    download from the request path entirely.

    "Usable" means every checkpoint the config points at is really on disk. The
    config alone is not enough to answer that, and treating it as enough is
    worse than having no local models: this directory takes priority over the
    token route, so a half-populated one would report the feature available and
    then fail every run, on a machine where the token would have worked.
    """
    config = default_pyannote_model_dir() / "config.yaml"
    if not config.is_file():
        return None
    return config if _referenced_checkpoints_exist(config) else None


def _referenced_checkpoints_exist(config: Path) -> bool:
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        params = ((loaded.get("pipeline") or {}).get("params") or {})
    except Exception:
        # Unreadable or unparseable: fall back to the token route rather than
        # claim a local pipeline that cannot be checked.
        return False
    for value in (params.get("segmentation"), params.get("embedding")):
        text = str(value or "").strip()
        # A bare repo id ("pyannote/segmentation-3.0") is fetched at run time and
        # is not ours to verify; only a path is.
        if not text or not (text.startswith("/") or text.startswith("~")):
            continue
        if not Path(text).expanduser().is_file():
            return False
    return True


def pyannote_status() -> dict[str, Any]:
    try:
        has_pyannote = importlib.util.find_spec("pyannote.audio") is not None
    except ModuleNotFoundError:
        has_pyannote = False
    has_token = bool(get_sensitive_setting("pyannote_auth_token"))
    has_local_models = local_model_config() is not None
    return {
        # Local model files need no token: nothing is fetched at run time.
        "available": has_pyannote and (has_local_models or has_token),
        "backend": "pyannote",
        "dependency_installed": has_pyannote,
        "auth_configured": has_token,
        "models_local": has_local_models,
    }


def diarize_with_pyannote(audio_path: str | Path) -> list[SpeakerTurn]:
    status = pyannote_status()
    if not status["available"]:
        raise RuntimeError(
            "Speaker diarization needs pyannote.audio plus either local model files "
            "(scripts/fetch_pyannote_models.py) or PYANNOTE_AUTH_TOKEN"
        )

    from pyannote.audio import Pipeline  # type: ignore

    local_config = local_model_config()
    if local_config is not None:
        pipeline = _load_local_pyannote_pipeline(Pipeline, local_config)
    else:
        token = get_sensitive_setting("pyannote_auth_token")
        if not token:
            raise RuntimeError("Speaker diarization requires pyannote.audio and PYANNOTE_AUTH_TOKEN")
        pipeline = _load_pyannote_pipeline(Pipeline, token)
    diarization = pipeline(str(audio_path))
    turns: list[SpeakerTurn] = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append(SpeakerTurn(start=float(turn.start), end=float(turn.end), speaker=str(speaker)))
    return turns


def _load_local_pyannote_pipeline(pipeline_cls: Any, config_path: Path) -> Any:
    with _torch_full_load_compat():
        return pipeline_cls.from_pretrained(str(config_path))


@contextmanager
def _torch_full_load_compat() -> Iterator[None]:
    """Let Lightning read pyannote checkpoints on torch >= 2.6.

    Torch 2.6 flipped `torch.load` to `weights_only=True`, and these
    checkpoints carry pickled objects, so loading them raises
    `UnpicklingError`. Torch's own escape hatch is this environment variable;
    it is set only around the load, and only for checkpoints that came from
    pyannote's own published models.
    """
    key = "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"
    # The variable is process-wide, so two overlapping loads would have the
    # first one to finish undo the relaxation the second still needs, failing a
    # run that succeeds when it is the only one. Serialising the loads also
    # keeps the window in which any other thread's `torch.load` would silently
    # lose its unpickling check down to one load at a time.
    with _TORCH_LOAD_LOCK:
        previous = os.environ.get(key)
        os.environ[key] = "1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def _load_pyannote_pipeline(pipeline_cls: Any, token: str) -> Any:
    model = "pyannote/speaker-diarization-3.1"
    try:
        return pipeline_cls.from_pretrained(model, token=token)
    except TypeError as exc:
        if "token" not in str(exc):
            raise
        with _hf_hub_download_auth_compat():
            return pipeline_cls.from_pretrained(model, use_auth_token=token)


@contextmanager
def _hf_hub_download_auth_compat() -> Iterator[None]:
    try:
        import huggingface_hub  # type: ignore
    except Exception:
        yield
        return

    original = getattr(huggingface_hub, "hf_hub_download", None)
    if original is None:
        yield
        return

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if "use_auth_token" in kwargs and "token" not in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        else:
            kwargs.pop("use_auth_token", None)
        return original(*args, **kwargs)

    patched: list[tuple[Any, Any]] = []
    for module in list(sys.modules.values()):
        if getattr(module, "hf_hub_download", None) is original:
            patched.append((module, original))
            setattr(module, "hf_hub_download", wrapper)
    try:
        yield
    finally:
        for module, previous in patched:
            try:
                setattr(module, "hf_hub_download", previous)
            except Exception:
                pass
