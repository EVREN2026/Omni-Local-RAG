import importlib
import unittest
from unittest.mock import patch


class _FakeServerManager:
    def __init__(self, running=False, start_ok=True, last_error="", base_url="http://127.0.0.1:8000"):
        self._running = running
        self._start_ok = start_ok
        self.last_error = last_error
        self.base_url = base_url
        self.ensure_started_calls = []

    @property
    def is_running(self):
        return self._running

    def ensure_started(self, model_path=None):
        self.ensure_started_calls.append(model_path)
        if self._start_ok:
            self._running = True
            return True
        return False

    def stop(self):
        self._running = False


class _FakeHttpClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def chat_stream(self, messages):
        yield "本地"
        yield "回答"

    def chat_once(self, messages):
        return "本地回答"


class LLMManagerLoadTest(unittest.TestCase):
    def setUp(self):
        from app.utils import config as cfg

        self.cfg = cfg
        self.old_cache = dict(cfg._cache)
        cfg._cache = {
            "llm": {
                "enabled": True,
                "model_path": "models/gemma-4-2b-it-q4_k_m.gguf",
                "n_gpu_layers": 0,
                "n_ctx": 4096,
            },
            "llama_server": {
                "model_path": "models/gemma-4-2b-it-q4_k_m.gguf",
                "host": "127.0.0.1",
                "port": 8000,
            },
        }
        self.llm_manager = importlib.import_module("app.models.llm_manager")
        self.old_instance = self.llm_manager.LLMManager._instance
        self.old_last_error = self.llm_manager.LLMManager._last_error
        self.old_server_ready = self.llm_manager.LLMManager._server_ready
        self.llm_manager.LLMManager._instance = None
        self.llm_manager.LLMManager._last_error = ""
        self.llm_manager.LLMManager._server_ready = False

    def tearDown(self):
        self.llm_manager.LLMManager._instance = self.old_instance
        self.llm_manager.LLMManager._last_error = self.old_last_error
        self.llm_manager.LLMManager._server_ready = self.old_server_ready
        self.cfg._cache = self.old_cache

    def test_load_success_starts_llama_server(self):
        fake_mgr = _FakeServerManager(running=False, start_ok=True)
        with patch("app.models.llama_server_manager.LlamaServerManager", return_value=fake_mgr):
            manager = self.llm_manager.LLMManager()
            self.assertTrue(manager.load())
        self.assertEqual(manager.last_error, "")
        self.assertEqual(len(fake_mgr.ensure_started_calls), 1)

    def test_load_failure_propagates_error(self):
        fake_mgr = _FakeServerManager(running=False, start_ok=False, last_error="server start failed")
        with patch("app.models.llama_server_manager.LlamaServerManager", return_value=fake_mgr):
            manager = self.llm_manager.LLMManager()
            self.assertFalse(manager.load())
            self.assertIn("server start failed", manager.last_error)

    def test_generate_stream_uses_http_client(self):
        fake_mgr = _FakeServerManager(running=True)
        with patch("app.models.llama_server_manager.LlamaServerManager", return_value=fake_mgr), patch(
            "app.models.llm_http_client.LLMHttpClient", _FakeHttpClient
        ):
            manager = self.llm_manager.LLMManager()
            text = "".join(manager.generate("prompt", stream=True))
        self.assertEqual(text, "本地回答")

    def test_generate_raises_when_server_not_running(self):
        fake_mgr = _FakeServerManager(running=False)
        with patch("app.models.llama_server_manager.LlamaServerManager", return_value=fake_mgr):
            manager = self.llm_manager.LLMManager()
            with self.assertRaises(RuntimeError):
                list(manager.generate("prompt", stream=True))


if __name__ == "__main__":
    unittest.main()