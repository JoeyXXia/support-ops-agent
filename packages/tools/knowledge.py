"""Authorized local document search for the registered read-only tool."""

from sqlalchemy.exc import SQLAlchemyError

from packages.knowledge.context import CANDIDATE_DEPTH, build_context
from packages.knowledge.embeddings import MockEmbedding
from packages.knowledge.retrieval import search_query
from packages.storage.database import engine_from_environment

from .schemas import DocEvidence, SearchDocsResult


def search_docs(args, tenant_id):
    engine = engine_from_environment()
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            rows = search_query(
                conn,
                args.question,
                MockEmbedding(),
                tenant_id=tenant_id,
                product_version=args.product_version,
                mode="bm25",
                k=CANDIDATE_DEPTH,
            )
    except SQLAlchemyError:
        raise ValueError("document_search_unavailable") from None
    finally:
        engine.dispose()
    context = build_context(args.question, rows)
    selected = {row["chunk_id"] for row in context["sources"]}
    return SearchDocsResult(
        sources=[
            DocEvidence(
                chunk_id=str(row["chunk_id"]),
                source=row["source"],
                body=row["body"],
                source_path=row["source_path"],
                version_number=row["version_number"],
                product_version=args.product_version,
                start_line=row["start_line"],
                end_line=row["end_line"],
            )
            for row in rows
            if row["chunk_id"] in selected
        ]
    )
