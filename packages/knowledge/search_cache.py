"""Small, process-local cache for scoped retrieval evidence."""

import hashlib
import threading
import time
from collections import OrderedDict

from .chunking import INPUT_VERSION, POLICY
from .context import CANDIDATE_DEPTH

_TTL_SECONDS = 60
_MAX_ENTRIES = 128
_rows = OrderedDict()
_lock = threading.Lock()


def cache_key(database, schema, tenant, version, provider, revision, question):
    return (
        database,
        schema,
        tenant,
        version,
        provider.provider,
        provider.model,
        POLICY,
        INPUT_VERSION,
        "bm25",
        CANDIDATE_DEPTH,
        revision,
        hashlib.sha256(question.encode()).digest(),
    )


def get(key):
    now = time.monotonic()
    with _lock:
        item = _rows.pop(key, None)
        if item is not None and item[0] > now:
            _rows[key] = item
            return item[1]
    return None


def put(key, result):
    with _lock:
        _rows[key] = (time.monotonic() + _TTL_SECONDS, result)
        _rows.move_to_end(key)
        while len(_rows) > _MAX_ENTRIES:
            _rows.popitem(last=False)
