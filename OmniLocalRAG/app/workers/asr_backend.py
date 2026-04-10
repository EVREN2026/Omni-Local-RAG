"""faster-whisper ASR backend helpers without Qt dependencies."""

from typing import Iterable


def iter_faster_whisper_events(
    video_path: str,
    model_size: str,
    device: str,
    compute_type: str,
    language: str,
    beam_size: int,
    vad_filter: bool,
) -> Iterable[dict]:
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        video_path,
        beam_size=beam_size,
        language=language,
        vad_filter=vad_filter,
    )
    yield {"type": "meta", "duration": float(info.duration or 1.0)}

    for seg in segments:
        yield {
            "type": "segment",
            "segment": {
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": str(seg.text).strip(),
                "confidence": round(float(getattr(seg, "avg_logprob", 0.0)) + 1.0, 3),
            },
        }
