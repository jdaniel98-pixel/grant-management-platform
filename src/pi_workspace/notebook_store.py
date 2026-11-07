"""Local research notebooks, separate from structured ideas and opportunities."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
import pilot_storage

NOTEBOOKS_PATH = Path(__file__).parent / "research_notebooks.json"
_LOCK = RLock()


def load_notebooks(path=None):
    path = Path(path) if path is not None else (pilot_storage.path("research_notebooks.json") if pilot_storage.enabled() else NOTEBOOKS_PATH)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _update(operation, path=None):
    path = Path(path) if path is not None else (pilot_storage.path("research_notebooks.json") if pilot_storage.enabled() else NOTEBOOKS_PATH)
    with pilot_storage.transaction(path.parent), _LOCK:
        records = load_notebooks(path=path)
        result = operation(records)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
                temporary = Path(file.name)
                json.dump(records, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return result


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def entry_title(entry):
    """Deterministic fallback for legacy entries; no migration or AI needed."""
    if entry.get('title'):
        return entry['title']
    text = (Path(entry['content']['original_filename']).stem if entry['kind'] == 'attachment'
            else entry['content'])
    words = ' '.join(text.split()).split()
    return ' '.join(words[:8])[:80].rstrip(' .,;:') or 'Research entry'


def create_notebook(title, path=None):
    title = title.strip()
    if not title:
        raise ValueError("Give your notebook a name")
    now = timestamp()
    record = {"id": uuid4().hex, "title": title, "created_at": now, "updated_at": now,
              "entries": [], "pending_text": "", "pending_transcript": None,
              "review_draft": None, "saved_title": None}
    return _update(lambda records: (records.append(record), record)[1], path)


def get_notebook(notebook_id, path=None):
    return next(record for record in load_notebooks(path=path) if record["id"] == notebook_id)


def update_notebook(notebook_id, changes, path=None):
    allowed = {"title", "pending_text", "pending_transcript", "review_draft", "saved_title"}
    if not set(changes) <= allowed:
        raise ValueError("Unsupported notebook update")
    if "title" in changes and not changes["title"].strip():
        raise ValueError("Give your notebook a name")
    def apply(records):
        record = next(item for item in records if item["id"] == notebook_id)
        record.update(changes)
        if "title" in changes:
            record["title"] = record["title"].strip()
        record["updated_at"] = timestamp()
        return record
    return _update(apply, path)


def add_entry(notebook_id, kind, content, token, path=None):
    if kind not in {"text", "transcript", "attachment"}:
        raise ValueError("Unsupported entry type")
    if kind != "attachment" and not content.strip():
        raise ValueError("Add some text before saving")
    def apply(records):
        record = next(item for item in records if item["id"] == notebook_id)
        if any(entry["save_token"] == token for entry in record["entries"]):
            if kind == "transcript" and record.get("pending_transcript") and token == "transcript:" + record["pending_transcript"]["digest"]:
                record["pending_transcript"] = None
            return False
        now = timestamp()
        record["entries"].append({"id": uuid4().hex, "kind": kind, "created_at": now,
                                  "content": content, "save_token": token,
                                  "title": entry_title({'kind': kind, 'content': content})})
        record["updated_at"] = now
        if kind == "text":
            record["pending_text"] = ""
        elif kind == "transcript":
            record["pending_transcript"] = None
        return True
    return _update(apply, path)


def structuring_source(record):
    return "\n\n".join(entry["content"] for entry in record["entries"]
                        if entry["kind"] in {"text", "transcript"})


def set_entry_edit(notebook_id, entry_id, text=None, cancel=False, path=None, title=None):
    """Retain unfinished edits separately from the original saved entry."""
    def apply(records):
        record = next(item for item in records if item['id'] == notebook_id)
        entry = next(item for item in record['entries'] if item['id'] == entry_id)
        edits = record.setdefault('entry_edits', {})
        titles = record.setdefault('entry_title_edits', {})
        if cancel:
            edits.pop(entry_id, None)
            titles.pop(entry_id, None)
        else:
            edits[entry_id] = entry['content'] if text is None else text
            titles[entry_id] = entry_title(entry) if title is None else title
        return record
    return _update(apply, path)


def edit_entry(notebook_id, entry_id, text, path=None, title=None):
    if isinstance(text, str) and not text.strip():
        raise ValueError('Add some text before saving')
    if title is not None and not title.strip():
        raise ValueError('Give this entry a title')
    def apply(records):
        record = next(item for item in records if item['id'] == notebook_id)
        entry = next(item for item in record['entries'] if item['id'] == entry_id)
        now = timestamp()
        if entry['kind'] != 'attachment':
            entry['content'] = text
        entry.update({'title': entry_title(entry) if title is None else title.strip(), 'modified_at': now})
        record['updated_at'] = now
        record.setdefault('entry_edits', {}).pop(entry_id, None)
        record.setdefault('entry_title_edits', {}).pop(entry_id, None)
        return entry
    return _update(apply, path)


def delete_entry(notebook_id, entry_id, uploads_directory, path=None):
    """Remove only the selected entry; unlink only unreferenced, contained files."""
    path = Path(path) if path is not None else (pilot_storage.path("research_notebooks.json") if pilot_storage.enabled() else NOTEBOOKS_PATH)
    with pilot_storage.transaction(path.parent), _LOCK:
        removed = None
        def apply(records):
            nonlocal removed
            record = next(item for item in records if item['id'] == notebook_id)
            removed = next((item for item in record['entries'] if item['id'] == entry_id), None)
            if removed is None:
                return
            record['entries'] = [item for item in record['entries'] if item['id'] != entry_id]
            for key in ('entry_edits', 'entry_title_edits'):
                record.get(key, {}).pop(entry_id, None)
            record['updated_at'] = timestamp()
        _update(apply, path)
        if not removed or removed['kind'] != 'attachment':
            return
        root = Path(uploads_directory).resolve()
        directory = root / notebook_id
        target = (directory / removed['content']['stored_filename']).resolve()
        # Refuse traversal/symlink targets outside this notebook's upload folder.
        if target.parent != directory.resolve() or directory.resolve().parent != root:
            return
        for record in load_notebooks(path=path):
            for entry in record['entries']:
                if entry['kind'] == 'attachment' and (root / record['id'] / entry['content']['stored_filename']).resolve() == target:
                    return
        target.unlink(missing_ok=True)
        manifest = directory / 'metadata.json'
        if manifest.exists():
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
            metadata = [item for item in metadata if item['stored_filename'] != removed['content']['stored_filename']]
            pilot_storage.atomic_json(manifest, metadata)
