import importlib
import sys
import tempfile
import types
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


class FakeLlama:
    calls = []
    fail_gpu_once = False
    fail_always = False

    def __init__(self, model_path, n_gpu_layers=0, n_ctx=4096, verbose=False):
        self.__class__.calls.append(
            {
                "model_path": model_path,
                "n_gpu_layers": n_gpu_layers,
                "n_ctx": n_ctx,
                "verbose": verbose,
            }
        )
        if self.__class__.fail_always:
            raise OSError("exception: access violation reading 0x0000000000000000")
        if n_gpu_layers > 0 and self.__class__.fail_gpu_once:
            self.__class__.fail_gpu_once = False
            raise OSError("GPU backend failed")


class FakeTokenProcess:
    def __init__(self):
        self.stdin = StringIO()
        self.stdout = StringIO(
            '{"type":"token","text":"本地"}\n'
            '{"type":"token","text":"回答"}\n'
            '{"type":"done"}\n'
        )

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class FakeCrashProcess:
    def __init__(self):
        self.stdin = StringIO()
        self.stdout = StringIO("Windows fatal exception: access violation\n")

    def wait(self, timeout=None):
        return 3221225477

    def kill(self):
        pass


class LLMManagerLoadTest(unittest.TestCase):
    def setUp(self):
        from app.utils import config as cfg

        self.cfg = cfg
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"

        model_path = Path(self.temp_dir.name) / "models" / "gemma.gguf"
        model_path.parent.mkdir(parents=True)
        model_path.write_text("fake gguf", encoding="utf-8")
        cfg._cache = {
            "llm": {
                "model_path": "models/gemma.gguf",
                "subprocess": False,
                "n_gpu_layers": 999,
                "n_ctx": 4096,
            }
        }

        fake_llama_cpp = types.ModuleType("llama_cpp")
        fake_llama_cpp.Llama = FakeLlama
        self.old_llama_cpp = sys.modules.get("llama_cpp")
        sys.modules["llama_cpp"] = fake_llama_cpp

        self.llm_manager = importlib.import_module("app.models.llm_manager")
        self.old_instance = self.llm_manager.LLMManager._instance
        self.old_llm = self.llm_manager.LLMManager._llm
        self.old_last_error = self.llm_manager.LLMManager._last_error
        self.old_load_failed = self.llm_manager.LLMManager._load_failed
        self.old_subprocess_mode = self.llm_manager.LLMManager._subprocess_mode
        self.llm_manager.LLMManager._instance = None
        self.llm_manager.LLMManager._llm = None
        self.llm_manager.LLMManager._last_error = ""
        self.llm_manager.LLMManager._load_failed = False
        self.llm_manager.LLMManager._subprocess_mode = False
        FakeLlama.calls = []
        FakeLlama.fail_gpu_once = False
        FakeLlama.fail_always = False

    def tearDown(self):
        self.llm_manager.LLMManager._instance = self.old_instance
        self.llm_manager.LLMManager._llm = self.old_llm
        self.llm_manager.LLMManager._last_error = self.old_last_error
        self.llm_manager.LLMManager._load_failed = self.old_load_failed
        self.llm_manager.LLMManager._subprocess_mode = self.old_subprocess_mode
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        if self.old_llama_cpp is None:
            sys.modules.pop("llama_cpp", None)
        else:
            sys.modules["llama_cpp"] = self.old_llama_cpp
        self.temp_dir.cleanup()

    def test_gpu_llm_load_failure_retries_cpu(self):
        FakeLlama.fail_gpu_once = True
        manager = self.llm_manager.LLMManager()

        with patch.object(self.llm_manager.logger, "warning"):
            self.assertTrue(manager.load())

        self.assertEqual([call["n_gpu_layers"] for call in FakeLlama.calls], [999, 0])
        self.assertEqual(manager.last_error, "")

    def test_native_backend_access_violation_is_reported_as_environment_problem(self):
        FakeLlama.fail_always = True
        manager = self.llm_manager.LLMManager()

        with patch.object(self.llm_manager.logger, "warning"), patch.object(
            self.llm_manager.logger, "error"
        ):
            self.assertFalse(manager.load())

        self.assertIn("llama-cpp-python 原生后端初始化失败", manager.last_error)
        self.assertIn("不是 models/ 目录缺失", manager.last_error)

    def test_subprocess_llm_streams_tokens_without_importing_llama_in_parent(self):
        self.cfg._cache["llm"]["subprocess"] = True
        manager = self.llm_manager.LLMManager()

        self.assertTrue(manager.load())
        with patch.object(self.llm_manager.subprocess, "Popen", return_value=FakeTokenProcess()):
            text = "".join(manager.generate("prompt"))

        self.assertEqual(text, "本地回答")
        self.assertEqual(FakeLlama.calls, [])

    def test_subprocess_llm_crash_reports_error_without_parent_crash(self):
        self.cfg._cache["llm"]["subprocess"] = True
        manager = self.llm_manager.LLMManager()

        self.assertTrue(manager.load())
        with patch.object(self.llm_manager.subprocess, "Popen", return_value=FakeCrashProcess()):
            with self.assertRaises(self.llm_manager.LLMSubprocessError):
                list(manager.generate("prompt"))

        self.assertIn("LLM 子进程异常退出", manager.last_error)
        self.assertIn("主程序已保护不崩溃", manager.last_error)


if __name__ == "__main__":
    unittest.main()
