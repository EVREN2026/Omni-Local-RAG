"""Command-line ASR runner used to isolate native faster-whisper crashes."""

import argparse
import faulthandler
import json
import sys

from app.workers.asr_backend import iter_faster_whisper_events


def _emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    # Keep native crash output inside the child process small. The parent reports
    # the non-zero exit code and keeps the Qt application alive.
    try:
        faulthandler.disable()
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--model-size", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--vad-filter", action="store_true")
    args = parser.parse_args(argv)

    try:
        for event in iter_faster_whisper_events(
            video_path=args.video,
            model_size=args.model_size,
            device=args.device,
            compute_type=args.compute_type,
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=args.vad_filter,
        ):
            _emit(event)
        _emit({"type": "done"})
        return 0
    except ImportError:
        _emit({"type": "error", "message": "faster-whisper 未安装，请执行: pip install faster-whisper"})
        return 2
    except Exception as exc:
        _emit({"type": "error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
