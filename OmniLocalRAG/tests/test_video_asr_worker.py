import importlib
import sys
import tempfile
import types
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


class DummySignal:
    def __init__(self, *args, **kwargs):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)

    def clear(self):
        self.emitted.clear()


class DummyQThread:
    def __init__(self, *args, **kwargs):
        pass


class FakeSegment:
    def __init__(self, start, end, text, avg_logprob):
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob


class FakeInfo:
    duration = 10.0


class FakeWhisperModel:
    calls = []

    def __init__(self, model_size, device="cpu", compute_type="int8"):
        self.__class__.calls.append(
            {
                "model_size": model_size,
                "device": device,
                "compute_type": compute_type,
            }
        )

    def transcribe(self, path, beam_size=5, language="zh", vad_filter=True):
        self.__class__.calls.append(
            {
                "path": path,
                "beam_size": beam_size,
                "language": language,
                "vad_filter": vad_filter,
            }
        )
        return [
            FakeSegment(0.0, 2.0, " 第一段 ", -0.2),
            FakeSegment(2.0, 5.0, "第二段", -0.1),
        ], FakeInfo()


class FakeCrashProcess:
    def __init__(self):
        self.stdout = StringIO(
            "Windows fatal exception: access violation\n"
            "  File \"faster_whisper/transcribe.py\", line 145 in __init__\n"
        )
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        return 3221225477

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def install_import_stubs():
    pyqt5 = types.ModuleType("PyQt5")
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.QThread = DummyQThread
    qtcore.pyqtSignal = lambda *args, **kwargs: DummySignal()
    pyqt5.QtCore = qtcore
    sys.modules["PyQt5"] = pyqt5
    sys.modules["PyQt5.QtCore"] = qtcore

    faster_whisper = types.ModuleType("faster_whisper")
    faster_whisper.WhisperModel = FakeWhisperModel
    sys.modules["faster_whisper"] = faster_whisper


class VideoASRWorkerTest(unittest.TestCase):
    def setUp(self):
        install_import_stubs()
        sys.modules.pop("app.workers.asr_worker", None)
        self.asr_worker = importlib.import_module("app.workers.asr_worker")

        from app.utils import config as cfg

        self.cfg = cfg
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {"asr": {"subprocess": False}}

        self.sqlite_store = importlib.import_module("app.models.sqlite_store")
        self.old_db_path = self.sqlite_store._DB_PATH
        self.old_instance = self.sqlite_store.SQLiteStore._instance
        self.sqlite_store._DB_PATH = cfg.abs_path("data/omni.db")
        self.sqlite_store.SQLiteStore._instance = None
        self.sqlite_store.SQLiteStore().init()

        for signal_name in ("segment_ready", "progress", "finished", "error_occurred"):
            getattr(self.asr_worker.ASRWorker, signal_name).clear()
        FakeWhisperModel.calls = []

    def tearDown(self):
        self.sqlite_store._DB_PATH = self.old_db_path
        self.sqlite_store.SQLiteStore._instance = self.old_instance
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        sys.modules.pop("faster_whisper", None)
        self.temp_dir.cleanup()

    def test_asr_worker_stores_transcripts_and_emits_segments(self):
        video_path = Path(self.temp_dir.name) / "demo.mp4"
        video_path.write_bytes(b"fake video")
        worker = self.asr_worker.ASRWorker(str(video_path), model_size="tiny")

        worker.run()

        transcripts = self.sqlite_store.SQLiteStore().get_transcripts("demo.mp4")
        self.assertEqual([row["text"] for row in transcripts], ["第一段", "第二段"])
        self.assertEqual(len(self.asr_worker.ASRWorker.segment_ready.emitted), 2)
        self.assertEqual(self.asr_worker.ASRWorker.finished.emitted[-1], (True,))
        self.assertEqual(FakeWhisperModel.calls[0]["device"], "cpu")
        self.assertEqual(FakeWhisperModel.calls[0]["compute_type"], "int8")

    def test_asr_subprocess_crash_emits_error_without_crashing_worker(self):
        self.cfg._cache = {"asr": {"subprocess": True}}
        video_path = Path(self.temp_dir.name) / "crash.mp4"
        video_path.write_bytes(b"fake video")
        worker = self.asr_worker.ASRWorker(str(video_path), model_size="tiny")

        with patch.object(self.asr_worker.subprocess, "Popen", return_value=FakeCrashProcess()):
            with patch.object(self.asr_worker.logger, "warning"):
                worker.run()

        transcripts = self.sqlite_store.SQLiteStore().get_transcripts("crash.mp4")
        self.assertEqual(transcripts, [])
        self.assertEqual(self.asr_worker.ASRWorker.finished.emitted[-1], (False,))
        error = self.asr_worker.ASRWorker.error_occurred.emitted[-1][0]
        self.assertIn("视频 ASR 子进程异常退出", error)
        self.assertIn("主程序已保护不崩溃", error)


if __name__ == "__main__":
    unittest.main()
