"""ASR worker for video transcription."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.sqlite_store import SQLiteStore
from app.utils import config as cfg
from app.utils.logger import logger
from app.workers.asr_backend import iter_faster_whisper_events


class ASRSubprocessError(RuntimeError):
    pass


class ASRWorker(QThread):
    segment_ready = pyqtSignal(dict)      # {"start", "end", "text", "confidence"}
    progress = pyqtSignal(float)          # 0.0–1.0 estimated progress
    finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, video_path: str, model_size: Optional[str] = None) -> None:
        super().__init__()
        self.video_path = video_path
        self.model_size = model_size or str(cfg.get("asr.model_size", "base"))
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        path = Path(self.video_path)
        if not path.exists():
            self.error_occurred.emit(f"视频文件不存在: {self.video_path}")
            self.finished.emit(False)
            return

        try:
            db = SQLiteStore()
            duration = 1.0
            seen_segments = 0

            for event in self._iter_asr_events(path):
                if self._cancelled:
                    break
                if event.get("type") == "meta":
                    duration = float(event.get("duration") or 1.0)
                    continue
                if event.get("type") != "segment":
                    continue

                row = event["segment"]
                db.insert_transcript(
                    video_file=str(path.name),
                    start_sec=row["start"],
                    end_sec=row["end"],
                    text=row["text"],
                    confidence=row["confidence"],
                )
                seen_segments += 1
                self.segment_ready.emit(row)
                self.progress.emit(min(row["end"] / duration, 1.0))

            if self._cancelled:
                self.finished.emit(False)
                return
            if seen_segments == 0:
                self.progress.emit(1.0)
            self.finished.emit(not self._cancelled)

        except ImportError:
            self.error_occurred.emit("faster-whisper 未安装，请执行: pip install faster-whisper")
            self.finished.emit(False)
        except ASRSubprocessError as e:
            logger.warning(str(e))
            self.error_occurred.emit(str(e))
            self.finished.emit(False)
        except Exception as e:
            logger.error(f"ASRWorker error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))
            self.finished.emit(False)

    def _iter_asr_events(self, path: Path) -> Iterable[dict]:
        if cfg.get("asr.subprocess", True):
            yield from self._iter_asr_events_subprocess(path)
        else:
            yield from iter_faster_whisper_events(
                video_path=str(path),
                model_size=self.model_size,
                device=cfg.get("asr.device", "cpu"),
                compute_type=cfg.get("asr.compute_type", "int8"),
                language=cfg.get("asr.language", "zh"),
                beam_size=int(cfg.get("asr.beam_size", 5)),
                vad_filter=bool(cfg.get("asr.vad_filter", True)),
            )

    def _iter_asr_events_subprocess(self, path: Path) -> Iterable[dict]:
        cmd = [
            sys.executable,
            "-m",
            "app.workers.asr_subprocess",
            "--video",
            str(path),
            "--model-size",
            self.model_size,
            "--device",
            str(cfg.get("asr.device", "cpu")),
            "--compute-type",
            str(cfg.get("asr.compute_type", "int8")),
            "--language",
            str(cfg.get("asr.language", "zh")),
            "--beam-size",
            str(int(cfg.get("asr.beam_size", 5))),
        ]
        if bool(cfg.get("asr.vad_filter", True)):
            cmd.append("--vad-filter")

        proc = subprocess.Popen(
            cmd,
            cwd=str(cfg._BASE),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        diagnostics: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._cancelled:
                    proc.terminate()
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics.append(line)
                    diagnostics = diagnostics[-20:]
                    continue
                if event.get("type") == "error":
                    raise ASRSubprocessError(str(event.get("message") or "ASR 子进程失败"))
                yield event
        finally:
            try:
                return_code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                return_code = proc.wait(timeout=5)

        if self._cancelled:
            return
        if return_code != 0:
            detail = "\n".join(diagnostics[-8:]).strip()
            message = (
                f"视频 ASR 子进程异常退出(exit={return_code})。"
                "这通常是 faster-whisper/CTranslate2/DLL 后端与当前 Windows 环境不兼容；"
                "主程序已保护不崩溃。"
            )
            if detail:
                message += f"\n{detail}"
            raise ASRSubprocessError(message)
