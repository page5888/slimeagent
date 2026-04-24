"""Voice pipeline — record/transcribe (STT) and synthesize/play (TTS).

First cut of the voice modality. Mirrors vision.py in shape: a
self-contained primitive module with provider fallback, called from
the Phase D action handlers. Approval gating happens in the action
layer so nothing here leaks a recording to the network or the
speakers without the user's explicit ok.

Why text-in / text-out instead of a native voice model
------------------------------------------------------
Cheap path, runs on Claude/Gemini/OpenAI with the same text protocol
we already use. Trades conversational latency for simplicity and
provider portability. A realtime / multimodal-audio path (GPT-4o
realtime, Gemini Live) can slot in later as a separate action type
without touching this module.

Public API
----------
    record_audio(duration_s, out_path=None) -> dict
        Capture audio from the default input device to a WAV file.

    transcribe(audio_path, language=None) -> dict
        Send a WAV to the first available STT provider. Returns
        {"ok", "text", "provider"} or {"ok": False, "error"}.

    record_and_transcribe(duration_s, language=None) -> dict
        Convenience: record then transcribe, always cleans up the
        temp file (transcripts can end up in the Context Bus; audio
        should not linger on disk).

    synthesize(text, out_path=None) -> dict
        Text → WAV/MP3 via provider; returns {"ok", "path", "provider"}.

    speak(text) -> dict
        Convenience: synthesize then play, cleanup temp file.

All provider calls are lazy-imported so the app still starts when
sounddevice / openai / google-genai aren't installed. The feature is
gracefully disabled — approvals just get a clear error back.
"""
from __future__ import annotations

import base64
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from sentinel import config

log = logging.getLogger("sentinel.voice")


# Recording format. 16 kHz mono 16-bit PCM is the sweet spot for every
# STT provider we target — Whisper downsamples to 16k internally
# anyway, and Gemini accepts it directly. Higher rates waste bytes.
SAMPLE_RATE = 16_000
CHANNELS = 1
MAX_RECORD_SECONDS = 60  # Hard cap to stop a runaway action.
MAX_TTS_CHARS = 1_000    # Text chunks longer than this get rejected
                         # at policy time, not here.


# ── Recording / playback (sounddevice) ────────────────────────────


def record_audio(duration_s: float, out_path: Optional[str] = None) -> dict:
    """Capture `duration_s` seconds from the default input device.

    Returns {"ok": True, "path": str, "duration": float} on success or
    {"ok": False, "error": str} if sounddevice/soundfile aren't
    available or recording fails.

    The recording is blocking — the caller thread is paused until the
    clip is done. That's fine for action handlers (they run on an
    approval-worker thread and the user already consented) but it
    means don't call this on the GUI thread directly.
    """
    try:
        import sounddevice as sd
        import soundfile as sf
        import numpy as np
    except ImportError as e:
        return {"ok": False,
                "error": f"audio deps not installed ({e}); "
                         f"run: pip install sounddevice soundfile numpy"}

    duration_s = float(duration_s)
    if duration_s <= 0 or duration_s > MAX_RECORD_SECONDS:
        return {"ok": False,
                "error": f"duration out of range (0, {MAX_RECORD_SECONDS}]"}

    path = out_path or str(Path(tempfile.gettempdir())
                           / f"slime_voice_{int(time.time()*1000)}.wav")
    try:
        frames = int(duration_s * SAMPLE_RATE)
        audio = sd.rec(frames, samplerate=SAMPLE_RATE,
                       channels=CHANNELS, dtype="int16")
        sd.wait()
        sf.write(path, audio, SAMPLE_RATE, subtype="PCM_16")
    except Exception as e:
        log.warning(f"record_audio failed: {e}")
        return {"ok": False, "error": f"record failed: {e}"}

    return {"ok": True, "path": path, "duration": duration_s}


def play_audio(path: str) -> dict:
    """Play a WAV/MP3 through the default output device. Blocks until
    playback finishes (same rationale as record_audio).

    MP3 playback requires soundfile's libsndfile to support it, which
    most distributions do. If not, caller should pass WAV from our
    synthesize() (we default to WAV).
    """
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as e:
        return {"ok": False, "error": f"audio deps not installed: {e}"}

    if not path or not os.path.exists(path):
        return {"ok": False, "error": f"audio file not found: {path}"}
    try:
        data, rate = sf.read(path, dtype="int16")
        sd.play(data, rate)
        sd.wait()
    except Exception as e:
        log.warning(f"play_audio failed: {e}")
        return {"ok": False, "error": f"playback failed: {e}"}
    return {"ok": True}


# ── STT providers ─────────────────────────────────────────────────


def _rate_limit_hint(error_str: str) -> bool:
    """Transient / rate-limit detection so fallback loops advance
    instead of bubbling up retryable errors."""
    markers = ("429", "503", "rate", "quota", "exhausted",
               "overloaded", "unavailable")
    e = error_str.lower()
    return any(m in e for m in markers)


def _stt_openai(provider: dict, audio_path: str,
                language: Optional[str]) -> Optional[str]:
    """OpenAI whisper-1 (or any openai-compat endpoint that exposes
    /audio/transcriptions). Non-standard models get a single try; we
    don't iterate provider['models'] because STT is served by the
    endpoint's own whisper model, not the chat models configured.
    """
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai SDK missing; skipping OpenAI STT")
        return None

    client = OpenAI(
        api_key=provider["api_key"],
        base_url=provider.get("base_url", "https://api.openai.com/v1"),
    )
    try:
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=language,   # None = auto-detect
                response_format="text",
            )
        # SDK returns a plain string for response_format=text
        text = (resp if isinstance(resp, str)
                else getattr(resp, "text", "")) or ""
        return text.strip() or None
    except Exception as e:
        if _rate_limit_hint(str(e)):
            log.info("OpenAI STT rate-limited")
        else:
            log.warning(f"OpenAI STT error: {e}")
        return None


def _stt_gemini(provider: dict, audio_path: str,
                language: Optional[str]) -> Optional[str]:
    """Gemini multimodal STT via google-genai.

    Gemini treats audio like any other Part; we ask it plainly to
    transcribe. Language hint goes into the prompt — Gemini doesn't
    expose a language flag.
    """
    try:
        import google.genai as genai
        from google.genai import types
    except ImportError:
        log.warning("google-genai missing; skipping Gemini STT")
        return None

    client = genai.Client(api_key=provider["api_key"])
    try:
        data = Path(audio_path).read_bytes()
    except OSError as e:
        log.error(f"read audio failed: {e}")
        return None
    audio_part = types.Part.from_bytes(data=data, mime_type="audio/wav")

    prompt = "Transcribe this audio verbatim. Only output the transcript."
    if language:
        prompt = f"Transcribe this audio in {language}, verbatim. Only output the transcript."

    for model in provider.get("models", []):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[audio_part, prompt],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=1024,
                ),
            )
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as e:
            if _rate_limit_hint(str(e)):
                log.info(f"Gemini/{model} STT rate-limited, next model")
                continue
            log.warning(f"Gemini/{model} STT error: {e}")
            continue
    return None


def transcribe(audio_path: str, language: Optional[str] = None) -> dict:
    """STT with multi-provider fallback.

    Tries providers in LLM_PROVIDERS order:
      - openai / openai_compat → whisper-1 endpoint
      - gemini → multimodal transcription
      - anthropic → skipped (no audio input in current Claude API)
    """
    if not audio_path or not os.path.exists(audio_path):
        return {"ok": False, "error": "audio file missing"}

    providers = getattr(config, "LLM_PROVIDERS", None) or []
    attempted: list[str] = []

    for provider in providers:
        if not provider.get("enabled") or not provider.get("api_key"):
            continue
        ptype = provider.get("type")
        name = provider.get("name", ptype or "?")
        attempted.append(name)

        text: Optional[str] = None
        if ptype in ("openai", "openai_compat"):
            text = _stt_openai(provider, audio_path, language)
        elif ptype == "gemini":
            text = _stt_gemini(provider, audio_path, language)
        else:
            continue

        if text:
            return {"ok": True, "text": text, "provider": name}

    return {"ok": False,
            "error": f"no STT provider succeeded; attempted: {attempted}"}


def record_and_transcribe(duration_s: float,
                          language: Optional[str] = None) -> dict:
    """Record a clip then transcribe it, cleaning up the temp file.

    Captured audio is considered sensitive — we delete the file after
    transcription whether the STT call succeeds or not. The resulting
    transcript is the only artifact that should persist (via the
    Context Bus / audit log).
    """
    rec = record_audio(duration_s)
    if not rec.get("ok"):
        return rec
    audio_path = rec["path"]
    try:
        result = transcribe(audio_path, language)
    finally:
        try:
            Path(audio_path).unlink(missing_ok=True)
        except OSError as e:
            log.warning(f"audio cleanup failed: {e}")
    if result.get("ok"):
        result["duration"] = rec["duration"]
    return result


# ── TTS providers ─────────────────────────────────────────────────


def _tts_openai(provider: dict, text: str, out_path: str) -> Optional[str]:
    """OpenAI /audio/speech (tts-1). Writes MP3 by default — callers
    that want WAV can set the extension on out_path and we'll honor it.
    """
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai SDK missing; skipping OpenAI TTS")
        return None

    client = OpenAI(
        api_key=provider["api_key"],
        base_url=provider.get("base_url", "https://api.openai.com/v1"),
    )
    # Pick a format based on the output extension so callers can
    # route into sounddevice (which prefers WAV) without transcoding.
    ext = Path(out_path).suffix.lower().lstrip(".")
    fmt = ext if ext in ("mp3", "wav", "opus", "flac", "aac") else "wav"
    # Default voice is "alloy" — neutral, works for en/zh.
    try:
        resp = client.audio.speech.create(
            model="tts-1",
            voice=provider.get("tts_voice", "alloy"),
            input=text,
            response_format=fmt,
        )
        # SDK exposes .stream_to_file for convenience.
        resp.stream_to_file(out_path)
        return out_path
    except Exception as e:
        if _rate_limit_hint(str(e)):
            log.info("OpenAI TTS rate-limited")
        else:
            log.warning(f"OpenAI TTS error: {e}")
        return None


def _tts_local(text: str, out_path: str) -> Optional[str]:
    """Local TTS via pyttsx3 — uses the OS speech engine (SAPI on
    Windows, NSSpeechSynthesizer on macOS, espeak on Linux). No
    network, no API key. Quality is noticeably lower than tts-1 but
    it's a reliable last resort.

    pyttsx3 only writes to file on some drivers; we fall back to
    speaking directly if save_to_file isn't supported. Returns the
    path on success, None on failure.
    """
    try:
        import pyttsx3
    except ImportError:
        return None
    try:
        engine = pyttsx3.init()
        engine.save_to_file(text, out_path)
        engine.runAndWait()
    except Exception as e:
        log.warning(f"local TTS failed: {e}")
        return None
    return out_path if os.path.exists(out_path) else None


def synthesize(text: str, out_path: Optional[str] = None) -> dict:
    """Text → audio file via provider fallback.

    Preference order:
      1. Any enabled OpenAI / openai_compat provider (tts-1 quality).
      2. Local pyttsx3 (offline fallback).

    WAV output is the default because our player prefers it; callers
    that want mp3 can pass an explicit out_path ending in .mp3.
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    if len(text) > MAX_TTS_CHARS:
        return {"ok": False,
                "error": f"text too long ({len(text)} > {MAX_TTS_CHARS})"}

    path = out_path or str(Path(tempfile.gettempdir())
                           / f"slime_tts_{int(time.time()*1000)}.wav")

    # Try network providers first.
    providers = getattr(config, "LLM_PROVIDERS", None) or []
    attempted: list[str] = []
    for provider in providers:
        if not provider.get("enabled") or not provider.get("api_key"):
            continue
        if provider.get("type") not in ("openai", "openai_compat"):
            continue
        attempted.append(provider.get("name", "openai"))
        if _tts_openai(provider, text, path):
            return {"ok": True, "path": path,
                    "provider": provider.get("name", "openai")}

    # Fall back to OS TTS.
    if _tts_local(text, path):
        return {"ok": True, "path": path, "provider": "local-pyttsx3"}

    return {"ok": False,
            "error": f"no TTS backend succeeded; attempted: {attempted or ['local']}"}


def speak(text: str) -> dict:
    """Synthesize then play; cleans up the temp file after playback.

    Returns the synth result dict (with `provider`) plus `played: bool`.
    """
    syn = synthesize(text)
    if not syn.get("ok"):
        return syn
    path = syn["path"]
    try:
        played = play_audio(path)
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    syn["played"] = bool(played.get("ok"))
    if not syn["played"]:
        syn["play_error"] = played.get("error")
    return syn
