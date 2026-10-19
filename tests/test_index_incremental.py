"""WAVE G gates (omodul): index_codebase incremental == full rebuild."""

from __future__ import annotations

import pytest

from omodul.index_codebase import Config, InputData, index_codebase


class FakeStore:
    def __init__(self):
        self.vectors: dict[str, str] = {}

    async def scanner(self, *, root, extensions):
        import pathlib

        return [str(p) for p in sorted(pathlib.Path(root).rglob("*.py"))]

    async def embedder(self, *, text):
        return [float(len(text))]

    async def writer(self, *, path, embedding, content, delete=False):
        if delete:
            self.vectors.pop(path, None)
        else:
            self.vectors[path] = content


def _write(root, name, content):
    path = root / name
    path.write_text(content)
    return str(path)


@pytest.mark.asyncio()
async def test_incremental_add_modify_delete_rename(tmp_path):
    store = FakeStore()
    fa = _write(tmp_path, "a.py", "x = 1\n")
    _write(tmp_path, "b.py", "y = 2\n")
    full = await index_codebase(
        Config(),
        InputData(
            root_path=str(tmp_path),
            scanner=store.scanner,
            embedder=store.embedder,
            vector_writer=store.writer,
        ),
        tmp_path,
    )
    assert full["status"] == "completed"
    assert full["indexed"] == 2
    assert full["source_revision"] and full["index_revision"]

    # modify a, delete b, add c
    _write(tmp_path, "a.py", "x = 100\n")
    (tmp_path / "b.py").unlink()
    fc = _write(tmp_path, "c.py", "z = 3\n")
    inc = await index_codebase(
        Config(incremental=True),
        InputData(
            root_path=str(tmp_path),
            embedder=store.embedder,
            vector_writer=store.writer,
            changed_files=[
                {"kind": "modified", "path": fa},
                {"kind": "deleted", "path": str(tmp_path / "b.py")},
                {"kind": "added", "path": fc},
            ],
            index_revision=full["index_revision"],
        ),
        tmp_path,
    )
    assert inc["indexed"] == 2 and inc["deleted"] == 1
    assert inc["last_applied_change"] == f"added:{fc}"

    # 等价性:另起全量 rebuild,store 内容一致
    store2 = FakeStore()
    await index_codebase(
        Config(),
        InputData(
            root_path=str(tmp_path),
            scanner=store2.scanner,
            embedder=store2.embedder,
            vector_writer=store2.writer,
        ),
        tmp_path,
    )
    assert store.vectors == store2.vectors


@pytest.mark.asyncio()
async def test_incremental_rename(tmp_path):
    store = FakeStore()
    fa = _write(tmp_path, "old.py", "x = 1\n")
    await index_codebase(
        Config(),
        InputData(
            root_path=str(tmp_path),
            scanner=store.scanner,
            embedder=store.embedder,
            vector_writer=store.writer,
        ),
        tmp_path,
    )
    fb = tmp_path / "new.py"
    (tmp_path / "old.py").rename(fb)
    inc = await index_codebase(
        Config(incremental=True),
        InputData(
            root_path=str(tmp_path),
            embedder=store.embedder,
            vector_writer=store.writer,
            changed_files=[{"kind": "renamed", "path": str(fb), "old_path": fa}],
        ),
        tmp_path,
    )
    assert inc["indexed"] == 1 and inc["deleted"] == 1
    assert list(store.vectors) == [str(fb)]


@pytest.mark.asyncio()
async def test_no_new_codegraph_engine(tmp_path):
    import pathlib

    names = [p.stem for p in pathlib.Path("omodul").glob("*.py")]
    for forbidden in ("code_graph_engine", "repository_graph_engine", "codegraph_engine"):
        assert forbidden not in names
