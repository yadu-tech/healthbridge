"""Immutable raw snapshots: one directory per run, a SHA-256 checksum per file, one manifest.

Layout::

    data/raw/<run_id>/
        manifest.json
        <source>/<series>/page-0001.<ext>

A run directory is written once and never modified. ``verify_snapshot`` recomputes every
checksum, so any later change to a raw file (or any file added or lost) is detectable.
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from healthbridge import __version__
from healthbridge.ingest.sources import RawPage

MANIFEST_NAME = "manifest.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


class SnapshotWriter:
    def __init__(self, root: Path, scope: dict, run_id: str | None = None) -> None:
        self.run_id = run_id or new_run_id()
        self.run_dir = Path(root) / self.run_id
        if self.run_dir.exists():
            raise FileExistsError(f"snapshot {self.run_dir} already exists; snapshots are immutable")
        self.run_dir.mkdir(parents=True)
        self.scope = scope
        self.started_at = datetime.now(UTC).isoformat()
        self.files: list[dict] = []
        self.failures: list[dict] = []

    def write_pages(self, pages: list[RawPage]) -> None:
        for page in pages:
            relative = f"{page.source}/{page.series}/page-{page.page:04d}.{page.extension}"
            target = self.run_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(page.content)
            self.files.append({
                "source": page.source,
                "series": page.series,
                "concept": page.concept,
                "page": page.page,
                "path": relative,
                "url": page.url,
                "http_status": page.status,
                "content_type": page.content_type,
                "bytes": len(page.content),
                "sha256": sha256_bytes(page.content),
            })

    def add_failure(self, source: str, concept: str, error: Exception) -> None:
        self.failures.append(
            {"source": source, "concept": concept, "error": f"{type(error).__name__}: {error}"}
        )

    def finalize(self) -> Path:
        manifest = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "package_version": __version__,
            "python_version": platform.python_version(),
            "scope": self.scope,
            "files": self.files,
            "failures": self.failures,
        }
        path = self.run_dir / MANIFEST_NAME
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def verify_snapshot(run_dir: Path) -> list[str]:
    """Return a list of problems; an empty list means the snapshot is intact."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return [f"missing {MANIFEST_NAME}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    listed = set()
    for entry in manifest["files"]:
        listed.add(entry["path"])
        file_path = run_dir / entry["path"]
        if not file_path.exists():
            problems.append(f"missing file: {entry['path']}")
        elif sha256_file(file_path) != entry["sha256"]:
            problems.append(f"checksum mismatch: {entry['path']}")
    on_disk = {
        p.relative_to(run_dir).as_posix()
        for p in run_dir.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    }
    problems += [f"unlisted file: {name}" for name in sorted(on_disk - listed)]
    return problems
