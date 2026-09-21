"""Filesystem project store. One directory per project, no network calls."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from techhand_print_fab.spec import ModelSpec

_PROJECT_ID = re.compile(r"^[a-f0-9]{12}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class StoreError(ValueError):
    """A project or part path was rejected."""


def default_data_dir() -> Path:
    override = os.environ.get("FAB_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "techhand-print-fab"


def slugify(name: str) -> str:
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:64].strip("-")
    if not text or not _SLUG.match(text):
        raise StoreError("name must contain a letter or digit")
    return text


def spec_to_dict(spec: ModelSpec) -> dict[str, Any]:
    return asdict(spec)


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create_project(self, name: str, description: str) -> dict[str, Any]:
        slug_name = name.strip()
        if not slug_name or len(slug_name) > 120:
            raise StoreError("project name must be 1 to 120 characters")
        if not description:
            description = ""
        if len(description) > 4000:
            raise StoreError("description is too long")
        project_id = uuid.uuid4().hex[:12]
        while (self.root / "projects" / project_id).exists():
            project_id = uuid.uuid4().hex[:12]
        now = utc_now()
        record = {
            "id": project_id,
            "name": slug_name,
            "description": description,
            "units": "mm",
            "created_at": now,
            "updated_at": now,
        }
        project_dir = self.project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=False)
        _write_json(project_dir / "project.json", record)
        return record

    def project_dir(self, project_id: str) -> Path:
        if not _PROJECT_ID.match(project_id):
            raise StoreError("unknown project id")
        return self.root / "projects" / project_id

    def read_project(self, project_id: str) -> dict[str, Any] | None:
        path = self.project_dir(project_id) / "project.json"
        if not path.is_file():
            return None
        return _read_json(path)

    def part_dir(self, project_id: str, slug: str) -> Path:
        if not _SLUG.match(slug):
            raise StoreError("unknown part name")
        return self.project_dir(project_id) / "parts" / slug

    def save_part(
        self,
        project_id: str,
        slug: str,
        record: dict[str, Any],
        text_files: dict[str, str],
    ) -> Path:
        if self.read_project(project_id) is None:
            raise StoreError("unknown project id")
        directory = self.part_dir(project_id, slug)
        directory.mkdir(parents=True, exist_ok=True)
        for stale in ("model.stl", "model.3mf", "_openscad.stl"):
            leftover = directory / stale
            if leftover.exists():
                leftover.unlink()
        for filename, contents in text_files.items():
            if "/" in filename or filename.startswith("."):
                raise StoreError("refusing to write an unsafe filename")
            _write_text(directory / filename, contents)
        _write_json(directory / "part.json", record)
        return directory

    def read_part(self, project_id: str, slug: str) -> dict[str, Any] | None:
        path = self.part_dir(project_id, slug) / "part.json"
        if not path.is_file():
            return None
        return _read_json(path)

    def list_parts(self, project_id: str) -> list[dict[str, Any]]:
        if self.read_project(project_id) is None:
            raise StoreError("unknown project id")
        root = self.project_dir(project_id) / "parts"
        if not root.is_dir():
            return []
        parts: list[dict[str, Any]] = []
        for child in sorted(path for path in root.iterdir() if path.is_dir()):
            record_path = child / "part.json"
            if record_path.is_file():
                parts.append(_read_json(record_path))
        return parts


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(default_data_dir())
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise StoreError(f"{path.name} is not a JSON object")
    return loaded
