"""Inbox batch-processing pipeline — scan inbox dir and ingest all files."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from oprim._logging import log

from oskill.knowledge.classify_inbox_file import ClassifyResult, classify_inbox_file
from oskill.knowledge.ingest_substrate import IngestResult, ingest_substrate


@dataclass
class ProcessInboxResult:
    processed: list[IngestResult] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)       # {"path": ..., "error": ...}
    needs_review: list[dict] = field(default_factory=list)  # {"path": ..., "candidates": [...]}


async def process_inbox(
    inbox_dir: Path,
    archive_after_process: bool = True,
) -> ProcessInboxResult:
    """Process all files in inbox_dir.

    Skips hidden files (name starts with '.') and directories.
    Low-confidence classifications go to needs_review without ingestion.
    Exceptions per file are caught and added to failed — processing continues.
    """
    result = ProcessInboxResult()
    archive_dir = inbox_dir / "_archive"

    for file_path in sorted(inbox_dir.glob("*")):
        if not file_path.is_file() or file_path.name.startswith("."):
            continue
        try:
            classify_result: ClassifyResult = classify_inbox_file(file_path)
            if classify_result.layer == "needs_review":
                result.needs_review.append({
                    "path": str(file_path),
                    "candidates": classify_result.candidates,
                })
                if archive_after_process:
                    _move_to_archive(file_path, archive_dir)
                continue

            ingest_result = await ingest_substrate(
                path=file_path,
                source={"type": "inbox_local", "filename": file_path.name},
                target_storage="local",
            )
            result.processed.append(ingest_result)

            if archive_after_process:
                _move_to_archive(file_path, archive_dir)

        except Exception as e:
            log.error("omodul.process_inbox.failed", path=str(file_path), error=str(e))
            result.failed.append({"path": str(file_path), "error": str(e)})

    log.info(
        "omodul.process_inbox.done",
        processed=len(result.processed),
        failed=len(result.failed),
        needs_review=len(result.needs_review),
    )
    return result


def _move_to_archive(file_path: Path, archive_dir: Path) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / file_path.name
    if dest.exists():
        dest = archive_dir / f"{file_path.stem}_{int(time.time() * 1000)}{file_path.suffix}"
    file_path.rename(dest)


if __name__ == "__main__":  # pragma: no cover
    import asyncio
    from oprim._config import cfg
    from oprim.bootstrap import bootstrap

    bootstrap()
    inbox = Path(cfg.get("STRATUM_HOME", str(Path.home() / ".stratum"))) / "inbox"
    r = asyncio.run(process_inbox(inbox))
    print(f"processed={len(r.processed)} failed={len(r.failed)} needs_review={len(r.needs_review)}")
