"""Regression tests for Windows GPU selection and shared memory planning."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import importlib.util
import sys
import unittest
from unittest.mock import Mock, patch

from flowhmr import runtime_environments as env, runtime_rocm as rocm, runtime_xpu as xpu
from flowhmr.model_downloads import DownloadError
from flowhmr.utils.runtime_capabilities import SAMMemoryError, SharedGPUMemoryError


def adapter(pci, name="AMD Radeon(TM) Graphics"):
    return rocm._amd_adapter(name, rf"PCI\VEN_1002&DEV_{pci}&SUBSYS_00000000")


class WindowsRocmTest(unittest.TestCase):
    def test_shared_names_use_device_id(self):
        # Even with the same marketing name, these adapters need different kernels.
        self.assertEqual(adapter("13C0")["capability"], "gfx1036")
        self.assertEqual(adapter("164E")["capability"], "gfx1036")
        self.assertEqual(adapter("15BF")["capability"], "gfx1103")
        self.assertEqual(adapter("1900", "AMD Radeon 780M")["capability"], "gfx1103")
        self.assertEqual(adapter("1114", "AMD Radeon 840M")["capability"], "gfx1152")
        self.assertEqual(adapter("1902", "AMD Radeon 840M")["capability"], "gfx1153")
        self.assertFalse(adapter("7550", "AMD Radeon RX 9070 XT")["integrated"])
        for pci, arch in (("7590", "gfx1200"), ("744C", "gfx1100"), ("747E", "gfx1101"), ("7480", "gfx1102")):
            self.assertEqual(adapter(pci)["capability"], arch)
            self.assertFalse(adapter(pci)["integrated"])
        self.assertIsNone(adapter("1638"))  # Vega, not RDNA

    def test_rdna1_packages_and_780m_match_the_detected_card(self):
        cards = (("731F", "gfx1010"), ("7362", "gfx1011"), ("7340", "gfx1012"), ("1900", "gfx1103"))
        with (TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(env, "SUPPORTED", True),
              patch.object(env, "_system_runtime", return_value=None), patch.object(env, "nvidia_adapters", return_value=()),
              patch.object(xpu, "windows_adapters", return_value=()), patch.object(rocm, "windows_adapters") as detect):
            manager = env.RuntimeEnvironments(tmp)
            for pci, arch in (*cards, ("1638", None)):
                with self.subTest(pci=pci):
                    device = adapter(pci)
                    detect.return_value = (device,) if device else ()
                    options = {item["id"]: item for item in manager.details()["options"]}
                    self.assertIn("780M", options["rocm714-gfx1103"]["models"])
                    self.assertIn("RX 5700", options["rocm714-gfx1010"]["models"])
                    enabled = {name for name, item in options.items() if name.startswith("rocm714-") and item["compatible"]}
                    self.assertEqual(enabled, {f"rocm714-{arch}"} if arch else set())
                    if arch in rocm.RDNA1_ARCHES:
                        self.assertEqual(options[f"rocm714-{arch}"]["detail_code"], "runtime_rocm_rdna1")
                        self.assertFalse(options[f"rocm714-{arch}"]["recommended"])
                    if arch != "gfx1103":
                        with self.assertRaises(DownloadError) as failure:
                            manager.start("rocm714-gfx1103")
                        self.assertEqual(failure.exception.params, {"arch": "gfx1103"})

    def test_rdna1_attention_follows_selected_device(self):
        torch = Mock()
        torch.version.hip = "7.2"
        torch.cuda.get_device_properties.side_effect = [SimpleNamespace(gcnArchName="gfx1010"),
                                                       SimpleNamespace(gcnArchName="gfx1010"),
                                                       SimpleNamespace(gcnArchName="gfx1103")]
        with patch.object(rocm.sys, "platform", "win32"):
            rocm.configure_windows_hip(torch, device=1, attention=True)
            torch.cuda.get_device_properties.assert_called_with(1)
            torch.backends.cuda.enable_flash_sdp.assert_called_with(False)
            torch.backends.cuda.enable_mem_efficient_sdp.assert_called_with(False)
            torch.backends.cuda.enable_math_sdp.assert_called_with(True)
            self.assertFalse(rocm.supports_bf16(torch, 1))
            torch.cuda.is_bf16_supported.assert_not_called()
            rocm.configure_windows_hip(torch, device=0, attention=True)
            torch.cuda.get_device_properties.assert_called_with(0)
            torch.backends.cuda.enable_flash_sdp.assert_called_with(True)
            torch.backends.cuda.enable_mem_efficient_sdp.assert_called_with(True)

    def test_exact_hip_device_even_when_shared_memory_looks_bigger(self):
        cards = [SimpleNamespace(gcnArchName="gfx1036", total_memory=80, is_integrated=False),
                 SimpleNamespace(gcnArchName="gfx1201:sramecc-:xnack-", total_memory=16, is_integrated=False)]
        torch = SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: len(cards),
                                                     get_device_properties=cards.__getitem__))
        self.assertEqual(rocm.gpu_ordinal(torch, "gfx1036"), 0)
        self.assertEqual(rocm.gpu_ordinal(torch, "gfx1201"), 1)
        self.assertEqual(rocm.gpu_ordinal(torch, None), 1)  # system ROCm prefers the discrete card
        with self.assertRaises(RuntimeError):
            rocm.gpu_ordinal(torch, "gfx1036", ordinal=1)
        with self.assertRaises(RuntimeError):
            rocm.gpu_ordinal(torch, "gfx1103")

    def test_amd_only_recommends_the_matching_slot(self):
        integrated = adapter("13C0")
        nvidia = {"name": "NVIDIA GeForce RTX 5090 D", "driver": "600.00", "capability": "12.0"}
        with (TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(env, "SUPPORTED", True),
              patch.object(env, "_system_runtime", return_value=None),
              patch.object(rocm, "windows_adapters", return_value=(integrated,)),
              patch.object(env, "nvidia_adapters", return_value=()) as nvidia_inventory):
            manager = env.RuntimeEnvironments(tmp)
            amd = manager.details()
            self.assertEqual([item["id"] for item in amd["options"] if item.get("recommended")], ["rocm714-gfx1036"])
            self.assertFalse(next(item for item in amd["options"] if item["id"] == "cu128")["compatible"])
            nvidia_inventory.return_value = (nvidia,)
            hybrid = manager.details()
            self.assertEqual(hybrid["gpus"], [nvidia, integrated])
            self.assertEqual([item["id"] for item in hybrid["options"] if item.get("recommended")], ["cu128"])
            with self.assertRaises(DownloadError) as failure:
                manager.start("rocm714-gfx1151")
            self.assertEqual(failure.exception.params, {"arch": "gfx1151"})

    def test_detected_780m_and_7800xt_get_separate_downloads(self):
        apu = adapter("1900", "AMD Radeon 780M")
        discrete = adapter("747E", "AMD Radeon RX 7800 XT")
        with (TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(env, "SUPPORTED", True),
              patch.object(env, "_system_runtime", return_value=None),
              patch.object(rocm, "windows_adapters", return_value=(apu, discrete)),
              patch.object(env, "nvidia_adapters", return_value=())):
            options = {item["id"]: item for item in env.RuntimeEnvironments(tmp).details()["options"]}
            self.assertTrue(options["rocm714-gfx1103"]["compatible"])
            self.assertEqual(options["rocm714-gfx1103"]["group"], "igpu")
            self.assertTrue(options["rocm714-gfx1101"]["recommended"])
            self.assertTrue(options["cpu"]["compatible"])
            self.assertFalse(options["cu121"]["compatible"])


class NvidiaSelectionTest(unittest.TestCase):
    def test_recommendation_and_legacy_visibility(self):
        card = lambda name, capability: dict(name=name, driver="616.92", capability=capability)
        blackwell = card("RTX 5090", "12.0")
        cases = [(blackwell, "cu128", False), (card("RTX 4090", "8.9"), "cu121", True),
                 (card("RTX 3090", "8.6"), "cu121", True), (card("RTX 2060", "7.5"), "cu121", True),
                 (card("GTX 1080", "6.1"), None, True)]
        with (TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(env, "SUPPORTED", True),
              patch.object(env, "_system_runtime", return_value=None),
              patch.object(rocm, "windows_adapters", return_value=()),
              patch.object(env, "nvidia_adapters") as detect):
            manager = env.RuntimeEnvironments(tmp)
            for gpu, suggested, compatible in cases:
                with self.subTest(gpu=gpu["name"]):
                    detect.return_value = (gpu,)
                    options = {item["id"]: item for item in manager.details()["options"]}
                    self.assertEqual([name for name, option in options.items() if option.get("recommended")],
                                     [suggested] if suggested else [])
                    self.assertEqual(options["cu121"]["compatible"], compatible)
                    self.assertNotIn("cu126", options)
                    self.assertNotIn("cu129", options)
            with patch.object(manager, "installed", side_effect=lambda name: Path(tmp) if name == "cu126" else None):
                self.assertIn("cu126", {item["id"] for item in manager.details()["options"]})

    def test_mixed_nvidia_uses_an_eligible_ordinal(self):
        torch = SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 2,
                                                     get_device_capability=[(12, 0), (8, 9)].__getitem__))
        self.assertEqual(env.cuda_ordinal(torch, "cu121"), 1)
        self.assertEqual(env.cuda_ordinal(torch, "cu128"), 0)
        with self.assertRaises(RuntimeError):
            env.cuda_ordinal(torch, "cu121", 0)


class SharedMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from flowhmr.utils import sam_batch

        cls.sam = sam_batch

    def planner(self, shared):
        with (patch.object(self.sam, "uses_shared_memory", return_value=shared),
              patch.object(self.sam.sys, "platform", "linux")):
            planner = self.sam.SAMBatchPlanner(SimpleNamespace(type="cuda"), 0)
        planner.last_good = 2
        planner.seen_hands = True
        planner.ram_per_frame = 64 * self.sam.MIB
        return planner

    def test_gpu_and_cpu_share_one_budget(self):
        mib = self.sam.MIB
        memory = self.sam._Memory(0, 820 * mib, 0, 0, vram_room=4096 * mib)
        estimate_hands = lambda n: 2 * n
        self.assertEqual(self.planner(False).choose(memory, 10, 0, estimate_hands), 3)
        planner = self.planner(True)
        self.assertEqual(planner.choose(memory, 10, 0, estimate_hands), 1)
        memory.reserved = 512 * mib  # Only count new allocations against available RAM.
        self.assertEqual(planner.choose(memory, 10, 0, estimate_hands), 2)

    def test_ram_error_instead_of_using_apparent_free_vram(self):
        mib = self.sam.MIB
        memory = self.sam._Memory(0, 256 * mib, 0, 0, vram_room=4096 * mib)
        with self.assertRaises(SAMMemoryError) as failure:
            self.planner(True).choose(memory, 1, 0, lambda n: 2 * n)
        self.assertEqual(failure.exception.resource, "ram")
        with patch.object(self.sam, "system_memory", return_value=(7 * 1024 * mib, 16 * 1024 * mib)):
            with self.assertRaises(SharedGPUMemoryError) as guard:
                self.sam.require_shared_ram(6 * 1024 * mib)
            self.assertEqual(guard.exception.params, {"available": "5.0", "needed": "6.0"})

    def test_camera_uses_system_headroom_on_apu_only(self):
        from flowhmr.utils import runtime_vggt_camera as camera

        gib = 1024 ** 3
        with (patch.object(camera.torch.cuda, "mem_get_info", return_value=(30 * gib, 32 * gib)),
              patch.object(camera, "uses_shared_memory", return_value=True) as shared,
              patch.object(camera, "shared_ram_room", return_value=4 * gib)):
            self.assertEqual(camera.camera_cuda_budget("cuda:0"), 4 * gib)
            shared.return_value = False
            self.assertGreater(camera.camera_cuda_budget("cuda:0"), 28 * gib)

    def test_cpu_camera_preflight_scales_with_sample_count(self):
        from flowhmr.utils import runtime_vggt_camera as camera
        from flowhmr.utils.runtime_capabilities import InferenceRAMError
        import torch

        self.assertGreater(camera._cpu_camera_workspace(32, 320), camera._cpu_camera_workspace(16, 256))
        with (TemporaryDirectory(dir=Path.cwd()) as tmp,
              patch.object(camera.osp, "getsize", return_value=4 * 1024 ** 3),
              patch.object(camera, "require_inference_ram", side_effect=InferenceRAMError(0, 1024)),
              patch.object(camera.multiprocessing, "get_context") as spawn):
            with self.assertRaises(InferenceRAMError):
                camera.extract_vggt_camera_isolated("unread.mp4", output_npz=str(Path(tmp) / "camera.npz"),
                    frame_range=(0, 200), expected_len=201, device=torch.device("cpu"),
                    frame_interval=1, cancel_check=lambda: None, on_progress=lambda _: None)
            spawn.assert_not_called()

    def test_amd_igpu_camera_does_not_spawn_a_gpu_worker(self):
        from flowhmr.utils import runtime_vggt_camera as camera
        from flowhmr.utils.runtime_capabilities import SharedCameraSamplingError, SharedGPUMemoryError

        with (TemporaryDirectory(dir=Path.cwd()) as tmp,
              patch.object(camera, "uses_shared_memory", return_value=True),
              patch.object(camera, "camera_cuda_budget", side_effect=AssertionError("Do not query GPU budget")),
              patch.object(camera.osp, "getsize", return_value=4 * 1024 ** 3),
              patch.object(camera, "require_shared_ram", side_effect=SharedGPUMemoryError(0, 1024)) as preflight,
              patch.object(camera.multiprocessing, "get_context") as spawn):
            options = dict(output_npz=str(Path(tmp) / "camera.npz"), frame_range=(10, 33), expected_len=24,
                           device="cuda:0", cancel_check=lambda: None, on_progress=lambda _: None)
            with self.assertRaises(SharedCameraSamplingError):
                camera.extract_vggt_camera_isolated("unread.mp4", frame_interval=1, **options)
            preflight.assert_not_called()
            with self.assertRaises(SharedGPUMemoryError):
                camera.extract_vggt_camera_isolated("unread.mp4", frame_interval=0, **options)
            self.assertGreater(preflight.call_count, 0)
            spawn.assert_not_called()

    def test_xpu_memory_telemetry_fallback_respects_host_headroom(self):
        import torch

        mib = self.sam.MIB
        with (patch.object(xpu, "is_integrated", return_value=True) as shared,
              patch.object(torch.xpu, "mem_get_info", side_effect=RuntimeError("Free memory is unavailable")),
              patch.object(torch.xpu, "memory_allocated", return_value=0),
              patch.object(torch.xpu, "memory_reserved", return_value=0),
              patch.object(self.sam, "system_memory", return_value=(10 * 1024 * mib, 32 * 1024 * mib)) as ram):
            planner = self.sam.SAMBatchPlanner(torch.device("xpu:0"), 0, host_processing=True)
            planner.last_good = 2
            planner.seen_hands = True
            memory = planner.snapshot()
            self.assertTrue(memory.vram_unreported)
            self.assertEqual(planner.choose(memory, 10, 0, lambda n: 2 * n), 3)
            ram.return_value = (1024 * mib, 32 * 1024 * mib)
            with self.assertRaises(SAMMemoryError):
                planner.choose(planner.snapshot(), 10, 0, lambda n: 2 * n)
            ram.return_value = (10 * 1024 * mib, 32 * 1024 * mib)
            shared.return_value = False
            discrete = self.sam.SAMBatchPlanner(torch.device("xpu:0"), 0, host_processing=True)
            discrete.last_good = 2
            discrete.seen_hands = True
            self.assertEqual(discrete.choose(discrete.snapshot(), 10, 0, lambda n: 2 * n), 1)


class IntelSelectionTest(unittest.TestCase):
    def test_arc_and_core_ultra_use_exact_pci_ids(self):
        arc = xpu._adapter("Intel Arc B580", r"PCI\VEN_8086&DEV_E20B\GPU")
        core_ultra = xpu._adapter("Intel Graphics", r"PCI\VEN_8086&DEV_64A0\GPU")
        self.assertFalse(arc["integrated"])
        self.assertTrue(core_ultra["integrated"])
        self.assertIsNone(xpu._adapter("Intel Iris Xe", r"PCI\VEN_8086&DEV_9A49\GPU"))
        self.assertIsNone(xpu._adapter("Intel Arc B580", r"PCI\VEN_1002&DEV_E20B\GPU"))
        torch = SimpleNamespace(version=SimpleNamespace(xpu="2025"),
            xpu=SimpleNamespace(device_count=lambda: 3, get_device_properties=[
                SimpleNamespace(device_id=0x9A49, total_memory=20),
                SimpleNamespace(device_id=0x64A0, total_memory=12),
                SimpleNamespace(device_id=0xE20B, total_memory=8),
            ].__getitem__))
        self.assertEqual(xpu.xpu_ordinal(torch), 2)
        self.assertEqual(xpu.xpu_ordinal(torch, 1), 1)
        with self.assertRaises(RuntimeError):
            xpu.xpu_ordinal(torch, 0)

    def test_runtime_only_offers_activation_for_eligible_intel(self):
        with (TemporaryDirectory(dir=Path.cwd()) as tmp, patch.object(env, "SUPPORTED", True),
              patch.object(env, "_system_runtime", return_value=None),
              patch.object(rocm, "windows_adapters", return_value=()),
              patch.object(env, "nvidia_adapters", return_value=()),
              patch.object(xpu, "windows_adapters", return_value=()) as detect):
            manager = env.RuntimeEnvironments(tmp)
            with self.assertRaises(DownloadError) as failure:
                manager.start(xpu.PROFILE)
            self.assertEqual(failure.exception.code, "runtime_xpu_no_device")
            detect.return_value = (xpu._adapter("Intel Arc A770", r"PCI\VEN_8086&DEV_56A0"),)
            options = {item["id"]: item for item in manager.details()["options"]}
            self.assertTrue(options["xpu"]["recommended"] and options["xpu"]["compatible"])
            self.assertFalse(options["cu121"]["compatible"])
            with patch.object(manager, "active", "xpu"):
                self.assertEqual(manager.default_device(), "xpu")


class SamModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "tools/web/server.py"
        spec = importlib.util.spec_from_file_location("sam_mode_test_web", source)
        cls.web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.web)
        cls.web.ARGS = SimpleNamespace(device="cuda", ckpt=None, model_cfg=None)

    def test_mode_defaults_and_rejects_unsupported_runtime(self):
        self.assertEqual(self.web.parse_params({})["sam_backend"], "gpu")
        with patch.object(self.web.RUNTIME, "active", "cu128"):
            with self.assertRaises(self.web.UserMessage) as rejected:
                self.web.parse_params({"sam_backend": "hybrid"})
            self.assertEqual(rejected.exception.code, "sam_hybrid_unavailable")
        with patch.object(rocm, "sam_hybrid_candidate", return_value=True):
            self.assertEqual(self.web.parse_params({"sam_backend": "hybrid"})["sam_backend"], "hybrid")
        with self.assertRaises(self.web.UserMessage) as invalid:
            self.web.parse_params({"sam_backend": "cpu"})
        self.assertEqual(invalid.exception.code, "param_sam_backend")

    def test_capability_does_not_require_a_live_gpu(self):
        self.assertFalse(rocm.sam_hybrid_candidate("cu128"))
        self.assertFalse(rocm.sam_hybrid_candidate(rocm.PROFILE))
        if sys.platform != "win32":
            self.assertFalse(rocm.sam_hybrid_candidate("rocm714-gfx1036"))


if __name__ == "__main__":
    unittest.main()
