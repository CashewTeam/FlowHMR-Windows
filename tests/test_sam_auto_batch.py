"""Cover changing resource budgets without stressing the user's hardware."""

from contextlib import nullcontext
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("torch"), "requires the inference runtime")
class SAMAutoBatchTest(unittest.TestCase):
    def setUp(self):
        import torch
        from flowhmr.utils import sam_batch
        self.sam = sam_batch
        self.mib = sam_batch.MIB
        with patch.object(sam_batch.sys, "platform", "linux"):
            self.planner = sam_batch.SAMBatchPlanner(torch.device("cuda:0"), 6 * self.mib)

    def test_batch_grows_from_observed_work_and_shrinks_for_either_resource(self):
        planner = self.planner
        memory = self.sam._Memory(ram_free=8000 * self.mib, ram_room=6000 * self.mib,
                                  vram_room=7000 * self.mib, process_ram=0, process_ram_peak=0)
        work = lambda n: 2 * n
        self.assertEqual(planner.choose(memory, 100, 0, work), 1)

        # A whole warm task has sustained several small batches. A 16 GiB
        # card with more headroom can use batches above the old five-frame cap.
        planner.last_good = 5
        planner.seen_hands = True
        planner.vram_per_crop = 350 * self.mib
        self.assertEqual(planner.choose(memory, 100, 0, work), 7)
        memory.vram_room = 1900 * self.mib
        self.assertEqual(planner.choose(memory, 100, 0, work), 2)
        memory.vram_room = 7000 * self.mib
        memory.ram_room = 75 * self.mib
        self.assertEqual(planner.choose(memory, 100, 0, work), 2)
        self.assertEqual(planner.choose(memory, 100, 30 * self.mib, work), 1)
        with self.assertRaises(self.sam.SAMMemoryError) as error:
            planner.choose(memory, 100, 70 * self.mib, work)
        self.assertEqual(error.exception.resource, "ram")

    def test_peak_from_body_only_limits_later_sparse_batches(self):
        planner = self.planner
        memory = self.sam._Memory(ram_free=8000 * self.mib, ram_room=6000 * self.mib,
                                  vram_room=2000 * self.mib, process_ram=0, process_ram_peak=0)
        with patch.object(self.sam.torch.cuda, "max_memory_allocated", return_value=700 * self.mib), \
                patch.object(self.sam.torch.cuda, "max_memory_reserved", return_value=720 * self.mib):
            planner.observe(memory, memory, 1, 0)
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: 0), 2)
        planner.last_good = 10
        planner.seen_hands = True
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: 2), 2)
        memory.vram_room = 899 * self.mib
        with self.assertRaises(self.sam.SAMMemoryError) as error:
            planner.choose(memory, 100, 0, lambda n: 0)
        self.assertEqual(error.exception.resource, "vram")

    def test_driver_budget_and_commit_are_independent_limits(self):
        planner = self.planner
        mib = self.mib
        planner.driver = SimpleNamespace(local=lambda: (9000 * mib, 7000 * mib))
        with patch.object(self.sam, "system_memory", return_value=(5000 * mib, 32000 * mib)), \
                patch.object(self.sam.torch.cuda, "mem_get_info", return_value=(10600 * mib, 16384 * mib)), \
                patch.object(self.sam.torch.cuda, "memory_allocated", return_value=5000 * mib), \
                patch.object(self.sam.torch.cuda, "memory_reserved", return_value=5200 * mib):
            memory = planner.snapshot()
        self.assertLess(memory.vram_room, 1000 * mib)
        self.assertLess(memory.ram_room, 4000 * mib)

    def test_squeezed_budget_is_rechecked_after_releasing_cache(self):
        planner = self.planner
        mib = self.mib
        squeezed = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                    vram_room=600 * mib, allocated=4000 * mib, reserved=5000 * mib)
        after_release = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                        vram_room=400 * mib, allocated=4000 * mib, reserved=4000 * mib)
        with patch.object(planner, "snapshot", side_effect=[squeezed, after_release]), \
                patch.object(planner, "release_cache") as release:
            self.assertIs(planner.before_batch(), after_release)
        release.assert_called_once()

    def test_allocation_failures_and_smaller_retry(self):
        planner = self.planner
        for message in ("HIPBLAS_STATUS_ALLOC_FAILED", "rocblas_status_memory_error", "miopenStatusAllocFailed"):
            with self.subTest(message=message):
                self.assertEqual(self.sam.memory_error_resource(RuntimeError(message)), "vram")
        self.assertEqual(self.sam.memory_error_resource(MemoryError()), "ram")
        self.assertEqual(self.sam.memory_error_resource(RuntimeError("DefaultCPUAllocator: can't allocate memory")), "ram")
        self.assertIsNone(self.sam.memory_error_resource(RuntimeError("MIOpen convolution shape mismatch")))
        with patch.object(self.sam.torch.cuda, "device", return_value=nullcontext()), \
                patch.object(self.sam.torch.cuda, "empty_cache"):
            planner.retry(5, "vram")
            self.assertEqual(planner.retry_size, 2)
            self.assertLess(planner.retry_limit, 5)
            planner.retry(1, "vram")
            self.assertEqual(planner.retry_limit, 1)
            self.assertTrue(planner.low_memory)
        self.assertEqual(planner.info["retries"], 2)

    def test_sparse_interpolation_across_blocks(self):
        import numpy as np
        import torch
        from flowhmr.utils.runtime_sam_features import fill_hand_slots

        torch.set_num_threads(min(torch.get_num_threads(), 4))
        total = 640
        indices = np.array([5, 31, 237, 411, 620])
        tokens = torch.full((total, 3072), torch.nan)
        tokens[:, :1024] = 7
        tokens[indices, 1024:] = torch.from_numpy(indices.astype(np.float32))[:, None]
        ordinary = fill_hand_slots(tokens, indices)
        result = fill_hand_slots(tokens, indices, in_place=True)
        self.assertIs(result, tokens)
        self.assertTrue(torch.equal(result, ordinary))
        expected = np.interp(np.arange(total), indices, indices)
        np.testing.assert_allclose(result[:, 1024].numpy(), expected, atol=1e-4)
        self.assertTrue(torch.all(result[:, :1024] == 7))
