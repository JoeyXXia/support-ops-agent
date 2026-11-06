"""Only vector(1536) transport; no retrieval or index abstraction."""

import json

from sqlalchemy.types import UserDefinedType

from packages.knowledge.embeddings import validate_vectors


class Vector1536(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "VECTOR(1536)"

    def bind_processor(self, dialect):
        def process(value):
            validate_vectors([value], 1)
            return json.dumps(value, separators=(",", ":"))

        return process

    def result_processor(self, dialect, coltype):
        return lambda value: json.loads(value) if isinstance(value, str) else value
