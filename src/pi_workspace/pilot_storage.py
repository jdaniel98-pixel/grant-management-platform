"""Private-pilot identity, volume paths and serialized atomic JSON storage."""
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import fcntl

ROOT = Path(__file__).resolve().parent
_LOCKS = {}
_GUARD = threading.Lock()
_DEPTH = threading.local()


def enabled():
    return os.environ.get('PILOT_MODE') == '1' or bool(os.environ.get('RAILWAY_ENVIRONMENT_ID'))


def users():
    records = json.loads(os.environ.get('PILOT_USERS_JSON', '{}'))
    if not isinstance(records, dict) or not records or any(
        not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name)
        or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
        for name, digest in records.items()
    ):
        raise ValueError('Configure PILOT_USERS_JSON with tester names and SHA-256 access-code hashes')
    root = os.environ.get('WORKSPACE_STORAGE_ROOT')
    if not root or not Path(root).is_absolute():
        raise ValueError('Configure an absolute WORKSPACE_STORAGE_ROOT on the Railway volume')
    if os.environ.get('RAILWAY_ENVIRONMENT_ID'):
        mount = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
        if not mount or Path(root).resolve() != Path(mount).resolve():
            raise ValueError('WORKSPACE_STORAGE_ROOT must match the attached Railway volume mount path')
    return records


def authenticate(name, code):
    records = users()
    digest = hashlib.sha256(code.encode()).hexdigest()
    expected = records.get(name, '0' * 64)
    if name in records and hmac.compare_digest(expected, digest):
        # Server-only session marker; invalidated if credentials are changed/revoked.
        return {'name': name, 'credential': expected}
    return None


def workspace_root():
    if not enabled():
        return ROOT
    import streamlit as st
    records = users()
    identity = st.session_state.get('_pilot_identity') or {}
    name = identity.get('name', '')
    if name not in records or not hmac.compare_digest(records[name], identity.get('credential', '')):
        raise PermissionError('Sign in to access your workspace')
    folder = hashlib.sha256(('pi-pilot:' + name).encode()).hexdigest()
    root = Path(os.environ['WORKSPACE_STORAGE_ROOT']).resolve()
    path = root / 'workspaces' / folder
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.resolve().parent != (root / 'workspaces').resolve():
        raise PermissionError('Invalid workspace path')
    return path


def path(filename):
    return workspace_root() / filename


@contextmanager
def transaction(root=None):
    """One reentrant lock per workspace, including independent worker processes."""
    root = Path(root) if root is not None else workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    key = str(root.resolve())
    with _GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        depths = getattr(_DEPTH, 'depths', None)
        if depths is None:
            depths = _DEPTH.depths = {}
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
        else:
            with (root / '.workspace.lock').open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                depths[key] = 1
                try:
                    yield
                finally:
                    depths.pop(key)
                    fcntl.flock(handle, fcntl.LOCK_UN)


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with transaction():
            return function(*args, **kwargs)
    return wrapped


def atomic_json(target, value):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(target)
        temporary = None
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def ideas_path():
    if not enabled():
        return ROOT / 'data' / 'funding_needs.json'
    target = path('funding_needs.json')
    with transaction():
        if not target.exists():
            atomic_json(target, json.loads((ROOT / 'data' / 'funding_needs.json').read_text()))
    return target
