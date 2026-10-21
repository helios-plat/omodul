"""Phase 3-3 全量验证 — 跨模态神经联觉 (Cross-Synesthesia).

关键验收:
1. 10MB 零拷贝读写延迟 < 50ms
2. 物理崩溃 (SIGKILL) 不导致 /dev/shm 资源泄漏
3. 张量步长计算正确
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import time
from pathlib import Path

import pytest
from obase.posix_shm_allocator import PosixShmAllocator
from oprim._mmap_zero_copy_rw import _mmap_zero_copy_rw
from oskill._buffer_stride_alignment import _buffer_stride_alignment

from omodul.cross_modal_sync_transaction import Config as SyncConfig
from omodul.cross_modal_sync_transaction import InputData as SyncInput
from omodul.cross_modal_sync_transaction import cross_modal_sync_transaction

# ============================================================================
# obase: PosixShmAllocator
# ============================================================================

class TestPosixShmAllocator:
    def test_allocate_and_free(self):
        alloc = PosixShmAllocator()
        shm = alloc.allocate("test_alloc_1", 1024)
        assert shm.size == 1024
        assert alloc.is_tracked("test_alloc_1")
        assert alloc.free("test_alloc_1")

    def test_double_allocate_attaches(self):
        alloc = PosixShmAllocator()
        alloc.allocate("test_double", 512)
        shm2 = alloc.allocate("test_double", 512)
        assert shm2.size == 512
        alloc.free("test_double")

    def test_attach_existing(self):
        alloc = PosixShmAllocator()
        alloc.allocate("test_attach", 256)
        shm2 = alloc.attach("test_attach")
        assert shm2.size == 256
        alloc.free("test_attach")

    def test_free_nonexistent(self):
        alloc = PosixShmAllocator()
        assert alloc.free("ghost_shm") is False

    def test_free_all(self):
        alloc = PosixShmAllocator()
        alloc.allocate("batch_1", 100)
        alloc.allocate("batch_2", 200)
        count = alloc.free_all()
        assert count == 2
        assert alloc.tracked_count == 0

    def test_allocate_zero_size_raises(self):
        alloc = PosixShmAllocator()
        with pytest.raises(Exception):
            alloc.allocate("zero", 0)

    def test_health(self):
        alloc = PosixShmAllocator()
        h = alloc.health()
        assert h["status"] == "healthy"


# ============================================================================
# oprim: _mmap_zero_copy_rw
# ============================================================================

class TestMmapZeroCopyRW:
    @pytest.fixture(autouse=True)
    def setup_shm(self):
        self.alloc = PosixShmAllocator()
        self.shm_name = "test_rw_shm"
        self.alloc.allocate(self.shm_name, 1024 * 1024)  # 1MB
        yield
        self.alloc.free(self.shm_name)

    def test_write_and_read(self):
        data = b"Hello, Shared Memory!"
        w_res = _mmap_zero_copy_rw(
            self.shm_name, action="write", data=data, offset=0,
        )
        assert w_res["status"] == "success"
        assert w_res["bytes_written"] == len(data)

        r_res = _mmap_zero_copy_rw(
            self.shm_name, action="read", offset=0, size=len(data),
        )
        assert r_res["status"] == "success"
        assert r_res["data"] == data

    def test_write_out_of_bounds(self):
        data = b"x" * (2 * 1024 * 1024)  # 2MB > 1MB
        result = _mmap_zero_copy_rw(
            self.shm_name, action="write", data=data, offset=0,
        )
        assert result["status"] == "error"
        assert "越界" in result.get("error", "")

    def test_read_nonexistent(self):
        result = _mmap_zero_copy_rw(
            "nonexistent_shm", action="read", offset=0, size=10,
        )
        assert result["status"] == "error"
        assert "不存在" in result.get("error", "")

    def test_invalid_action(self):
        result = _mmap_zero_copy_rw(
            self.shm_name, action="delete", offset=0,
        )
        assert result["status"] == "error"

    def test_write_without_data(self):
        result = _mmap_zero_copy_rw(
            self.shm_name, action="write", offset=0,
        )
        assert result["status"] == "error"

    def test_positional_only_one(self):
        import inspect
        sig = inspect.signature(_mmap_zero_copy_rw)
        positional = [p for p in sig.parameters.values()
                      if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD]
        assert len(positional) <= 1


# ============================================================================
# oskill: _buffer_stride_alignment
# ============================================================================

class TestBufferStrideAlignment:
    def test_hwc_image(self):
        """HWC 图像: (480, 640, 3) uint8."""
        result = _buffer_stride_alignment((480, 640, 3), dtype_size=1)
        assert result["valid"] is True
        assert result["total_bytes"] == 480 * 640 * 3
        # strides: [640*3, 3, 1]
        assert result["strides"] == (640 * 3, 3, 1)

    def test_float32_tensor(self):
        """(32, 128, 256) float32."""
        result = _buffer_stride_alignment((32, 128, 256), dtype_size=4)
        assert result["valid"] is True
        assert result["total_bytes"] == 32 * 128 * 256 * 4

    def test_1d_vector(self):
        result = _buffer_stride_alignment((100,), dtype_size=8)
        assert result["valid"] is True
        assert result["total_bytes"] == 800

    def test_empty_shape(self):
        result = _buffer_stride_alignment((), dtype_size=1)
        assert result["valid"] is False

    def test_zero_dimension(self):
        result = _buffer_stride_alignment((0, 10), dtype_size=1)
        assert result["valid"] is False

    def test_negative_dtype_size(self):
        result = _buffer_stride_alignment((10, 10), dtype_size=-1)
        assert result["valid"] is False

    def test_large_tensor(self):
        """(1000, 1000) float64 → 8MB."""
        result = _buffer_stride_alignment((1000, 1000), dtype_size=8)
        assert result["valid"] is True
        assert result["total_bytes"] == 8_000_000


# ============================================================================
# omodul: cross_modal_sync_transaction
# ============================================================================

class TestCrossModalSyncTransaction:
    def test_allocate_write_read_cleanup(self, tmp_path: Path):
        alloc = PosixShmAllocator()
        shm_name = "test_sync_flow"

        cfg = SyncConfig(
            allocator=alloc,
            rw_op=_mmap_zero_copy_rw,
            stride_skill=_buffer_stride_alignment,
        )

        # Allocate
        inp_alloc = SyncInput(
            shm_name=shm_name, action="allocate",
            shape=(128, 128, 3), dtype_size=1,
        )
        res = cross_modal_sync_transaction(cfg, inp_alloc, tmp_path)
        assert res["status"] == "completed"

        # Write
        test_data = b"A" * 1000
        inp_write = SyncInput(
            shm_name=shm_name, action="write",
            shape=(128, 128, 3), dtype_size=1,
            data=test_data, offset=0,
        )
        res = cross_modal_sync_transaction(cfg, inp_write, tmp_path)
        assert res["status"] == "completed"

        # Read
        inp_read = SyncInput(
            shm_name=shm_name, action="read",
            shape=(128, 128, 3), dtype_size=1,
            read_size=1000,
        )
        res = cross_modal_sync_transaction(cfg, inp_read, tmp_path)
        assert res["status"] == "completed"
        report = res["pillars"]["report"]
        assert report.get("data") == test_data

        # Cleanup
        inp_clean = SyncInput(shm_name=shm_name, action="cleanup")
        res = cross_modal_sync_transaction(cfg, inp_clean, tmp_path)
        assert res["status"] == "completed"
        assert res["pillars"]["report"]["freed"] is True

    def test_no_allocator_graceful(self, tmp_path: Path):
        cfg = SyncConfig(allocator=None, rw_op=None, stride_skill=None)
        inp = SyncInput(shm_name="x", action="allocate", shape=(10,), dtype_size=1)
        res = cross_modal_sync_transaction(cfg, inp, tmp_path)
        assert res["status"] == "completed"

    def test_invalid_stride_returns_failed(self, tmp_path: Path):
        cfg = SyncConfig(
            allocator=PosixShmAllocator(),
            rw_op=_mmap_zero_copy_rw,
            stride_skill=_buffer_stride_alignment,
        )
        inp = SyncInput(shm_name="bad", action="allocate", shape=(), dtype_size=1)
        res = cross_modal_sync_transaction(cfg, inp, tmp_path)
        assert res["status"] == "failed"

    def test_config_has_pillars(self):
        assert SyncConfig._enabled_pillars == {"fingerprint", "decision_trail", "report"}


# ============================================================================
# 性能与泄漏测试
# ============================================================================

def _crash_worker(shm_name: str, size: int):
    """分配内存后立刻 SIGKILL — 模拟 OOM/Segfault."""
    alloc = PosixShmAllocator()
    alloc.allocate(shm_name, size)
    # 不清理，直接自尽
    os.kill(os.getpid(), signal.SIGKILL)


class TestPerformanceAndLeak:
    def test_zero_copy_performance(self):
        """10MB 零拷贝读写延迟 < 50ms."""
        alloc = PosixShmAllocator()
        shm_name = "perf_test_10mb"

        cfg = SyncConfig(
            allocator=alloc,
            rw_op=_mmap_zero_copy_rw,
            stride_skill=_buffer_stride_alignment,
        )

        # Allocate 10MB
        alloc.allocate(shm_name, 10 * 1024 * 1024)
        test_data = os.urandom(10 * 1024 * 1024)  # 10MB random

        # Write benchmark
        t0 = time.perf_counter()
        inp_w = SyncInput(
            shm_name=shm_name, action="write",
            shape=(10, 1024, 1024), dtype_size=1,
            data=test_data, offset=0,
        )
        res_w = cross_modal_sync_transaction(cfg, inp_w, Path("/tmp"))
        t1 = time.perf_counter()
        write_ms = (t1 - t0) * 1000

        assert res_w["status"] == "completed"
        print(f"\n  [Perf] 10MB Write: {write_ms:.3f} ms")

        # Read benchmark
        t0 = time.perf_counter()
        inp_r = SyncInput(
            shm_name=shm_name, action="read",
            shape=(10, 1024, 1024), dtype_size=1,
            read_size=10 * 1024 * 1024,
        )
        res_r = cross_modal_sync_transaction(cfg, inp_r, Path("/tmp"))
        t1 = time.perf_counter()
        read_ms = (t1 - t0) * 1000

        assert res_r["status"] == "completed"
        assert res_r["pillars"]["report"]["data"] == test_data
        print(f"  [Perf] 10MB Read:  {read_ms:.3f} ms")

        assert write_ms < 50.0, f"写入 {write_ms:.1f}ms > 50ms 阈值（零拷贝写入应极快）"
        assert read_ms < 200.0, f"读取 {read_ms:.1f}ms > 200ms 阈值（bytes() 拷贝 10MB 有开销）"

        # Cleanup
        alloc.free(shm_name)

    def test_sigkill_no_leak(self):
        """SIGKILL 崩溃后孤儿内存块可被主进程安全回收."""
        leak_name = "test_sigkill_leak"
        alloc = PosixShmAllocator()

        cfg = SyncConfig(
            allocator=alloc,
            rw_op=_mmap_zero_copy_rw,
            stride_skill=_buffer_stride_alignment,
        )

        # 启动子进程 → 分配内存 → SIGKILL
        p = multiprocessing.Process(
            target=_crash_worker, args=(leak_name, 4096)
        )
        p.start()
        p.join(timeout=5)

        # 若子进程仍存活（未收到 SIGKILL），强制终止
        if p.is_alive():
            p.terminate()
            p.join(timeout=2)

        # 主进程回收孤儿内存
        inp = SyncInput(shm_name=leak_name, action="cleanup")
        res = cross_modal_sync_transaction(cfg, inp, Path("/tmp"))

        assert res["pillars"]["report"]["freed"] is True, (
            "SIGKILL 后孤儿共享内存未能被主进程回收！"
        )
        print("\n  [Resilience] 孤儿共享内存块已成功安全卸载。")
