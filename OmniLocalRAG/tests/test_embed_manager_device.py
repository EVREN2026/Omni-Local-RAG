import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


class FakeCuda:
    available = True
    capability = (12, 0)
    arch_list = ["sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]
    device_name = "NVIDIA GeForce RTX 5070"
    empty_cache_calls = 0

    @classmethod
    def is_available(cls):
        return cls.available

    @classmethod
    def get_device_capability(cls, index=0):
        return cls.capability

    @classmethod
    def get_arch_list(cls):
        return cls.arch_list

    @classmethod
    def get_device_name(cls, index=0):
        return cls.device_name

    @classmethod
    def empty_cache(cls):
        cls.empty_cache_calls += 1

    @classmethod
    def reset(cls):
        cls.available = True
        cls.capability = (12, 0)
        cls.arch_list = ["sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]
        cls.device_name = "NVIDIA GeForce RTX 5070"
        cls.empty_cache_calls = 0


class FakeSentenceTransformer:
    loaded_devices = []
    loaded_kwargs = []          # captures extra kwargs passed at construction
    fail_cuda_load_once = False
    fail_cuda_encode_once = False

    def __init__(self, model_path, device="cpu", **kwargs):
        if device.startswith("cuda") and self.__class__.fail_cuda_load_once:
            self.__class__.fail_cuda_load_once = False
            raise RuntimeError("CUDA error: no kernel image is available for execution on the device")
        self.model_path = model_path
        self.device = device
        self.__class__.loaded_devices.append(device)
        self.__class__.loaded_kwargs.append(kwargs)

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        if self.device.startswith("cuda") and self.__class__.fail_cuda_encode_once:
            self.__class__.fail_cuda_encode_once = False
            raise RuntimeError("CUDA error: no kernel image is available for execution on the device")
        return FakeVectors([[1.0, 0.0] for _ in texts])


class FakeVectors:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class LegacySentenceTransformer:
    loaded_devices = []

    def __init__(self, model_path, device="cpu"):
        self.model_path = model_path
        self.device = device
        self.__class__.loaded_devices.append(device)

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        return FakeVectors([[1.0, 0.0] for _ in texts])


class EmbedManagerDeviceTest(unittest.TestCase):
    def setUp(self):
        from app.utils import config as cfg

        self.cfg = cfg
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {
            "embed": {
                "model_path": "models/bge-m3",
                "device": "cuda",
            }
        }

        fake_torch = types.ModuleType("torch")
        fake_torch.cuda = FakeCuda
        self.old_torch = sys.modules.get("torch")
        sys.modules["torch"] = fake_torch

        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = FakeSentenceTransformer
        self.old_sentence_transformers = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = fake_sentence_transformers

        self.embed_manager = importlib.import_module("app.models.embed_manager")
        self.old_instance = self.embed_manager.EmbedManager._instance
        self.old_model = self.embed_manager.EmbedManager._model
        self.old_device = self.embed_manager.EmbedManager._device
        self.old_last_error = self.embed_manager.EmbedManager._last_error
        self.embed_manager.EmbedManager._instance = None
        self.embed_manager.EmbedManager._model = None
        self.embed_manager.EmbedManager._device = "cpu"
        self.embed_manager.EmbedManager._last_error = ""
        FakeCuda.reset()
        FakeSentenceTransformer.loaded_devices = []
        FakeSentenceTransformer.loaded_kwargs = []
        FakeSentenceTransformer.fail_cuda_load_once = False
        FakeSentenceTransformer.fail_cuda_encode_once = False

    def tearDown(self):
        self.embed_manager.EmbedManager._instance = self.old_instance
        self.embed_manager.EmbedManager._model = self.old_model
        self.embed_manager.EmbedManager._device = self.old_device
        self.embed_manager.EmbedManager._last_error = self.old_last_error
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        self._restore_module("torch", self.old_torch)
        self._restore_module("sentence_transformers", self.old_sentence_transformers)
        self.temp_dir.cleanup()

    def test_unsupported_cuda_architecture_falls_back_to_cpu(self):
        manager = self.embed_manager.EmbedManager()

        self.assertTrue(manager.load())

        self.assertEqual(FakeSentenceTransformer.loaded_devices, ["cpu"])
        self.assertEqual(manager.encode(["hello"]), [[1.0, 0.0]])
        # local_files_only must be set to prevent any HuggingFace Hub download
        self.assertTrue(
            all(kw.get("local_files_only") for kw in FakeSentenceTransformer.loaded_kwargs),
            "EmbedManager must pass local_files_only=True to prevent network downloads",
        )

    def test_cuda_runtime_load_error_falls_back_to_cpu(self):
        FakeCuda.arch_list = ["sm_120"]
        FakeSentenceTransformer.fail_cuda_load_once = True
        manager = self.embed_manager.EmbedManager()

        self.assertTrue(manager.load())

        self.assertEqual(FakeSentenceTransformer.loaded_devices, ["cpu"])
        self.assertEqual(manager.encode(["hello"]), [[1.0, 0.0]])

    def test_cuda_runtime_encode_error_reloads_on_cpu_and_retries(self):
        FakeCuda.arch_list = ["sm_120"]
        FakeSentenceTransformer.fail_cuda_encode_once = True
        manager = self.embed_manager.EmbedManager()

        self.assertTrue(manager.load())
        vectors = manager.encode(["hello"])

        self.assertEqual(vectors, [[1.0, 0.0]])
        self.assertEqual(FakeSentenceTransformer.loaded_devices, ["cuda", "cpu"])
        self.assertEqual(FakeCuda.empty_cache_calls, 1)

    def test_encode_lazy_loads_embedding_model(self):
        FakeCuda.arch_list = ["sm_120"]
        manager = self.embed_manager.EmbedManager()

        vectors = manager.encode(["hello"])

        self.assertEqual(vectors, [[1.0, 0.0]])
        self.assertEqual(FakeSentenceTransformer.loaded_devices, ["cuda"])

    def test_legacy_sentence_transformer_without_local_files_only_still_loads(self):
        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = LegacySentenceTransformer
        old = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = fake_sentence_transformers
        LegacySentenceTransformer.loaded_devices = []
        self.embed_manager.EmbedManager._instance = None
        self.embed_manager.EmbedManager._model = None
        try:
            manager = self.embed_manager.EmbedManager()
            self.assertTrue(manager.load())
            self.assertEqual(manager.encode(["hello"]), [[1.0, 0.0]])
            self.assertEqual(LegacySentenceTransformer.loaded_devices, ["cpu"])
        finally:
            self._restore_module("sentence_transformers", old)

    @staticmethod
    def _restore_module(name, old_module):
        if old_module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old_module


if __name__ == "__main__":
    unittest.main()
