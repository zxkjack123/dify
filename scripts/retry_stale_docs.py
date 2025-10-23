"""
Bulk retry stale in-progress documents older than cutoff minutes.

Usage inside API container (example):
    set CUTOFF_MINUTES and BATCH_SIZE env vars, set PYTHONPATH=/app/api,
    then run: python /tmp/retry_stale_docs.py

This script must run with Flask app context so it can access Celery and models.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import os

from app_factory import create_app
from extensions.ext_database import db
from models.account import Account
from models.dataset import Dataset, Document
from tasks.retry_document_indexing_task import retry_document_indexing_task


def bulk_retry(cutoff_minutes: int = 15, batch_size: int = 100) -> int:
    """Find stale in-progress docs and enqueue retry tasks by dataset.

    Returns number of documents enqueued.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=cutoff_minutes)

    inprog: list[Document] = (
        db.session.query(Document)
        .filter(
            Document.indexing_status.in_(
                ["indexing", "parsing", "cleaning", "splitting"]
            ),
            Document.is_paused.is_(False),
            Document.updated_at < cutoff,
        )
        .all()
    )

    if not inprog:
        print("No stale documents found.")
        return 0

    groups: dict[str, list[Document]] = defaultdict(list)
    for d in inprog:
        groups[str(d.dataset_id)].append(d)

    total = 0
    for ds_id, docs in groups.items():
        dataset = db.session.get(Dataset, ds_id)
        user_id: str | None = None
        # Prefer the document creator
        for d in docs:
            if getattr(d, "created_by", None):
                user_id = str(d.created_by)
                break
        # Fallback to dataset creator or any tenant account
        if not user_id:
            if dataset and getattr(dataset, "created_by", None):
                user_id = str(dataset.created_by)
            elif dataset:
                acc = (
                    db.session.query(Account)
                    .filter_by(tenant_id=dataset.tenant_id)
                    .order_by(Account.created_at.asc())
                    .first()
                )
                user_id = str(acc.id) if acc else None

        if not user_id:
            print(f"Skip dataset {ds_id}: no user_id found")
            continue

        ids = [str(d.id) for d in docs]
        for i in range(0, len(ids), batch_size):
            chunk = ids[i:i + batch_size]
            retry_document_indexing_task.delay(ds_id, chunk, user_id)
            print(
                "Enqueued retry: ds="
                + ds_id
                + " size="
                + str(len(chunk))
                + " user="
                + user_id
            )
            total += len(chunk)

    print(f"Total documents enqueued: {total}")
    return total


def main() -> None:
    cutoff = int(os.environ.get("CUTOFF_MINUTES", "15"))
    batch = int(os.environ.get("BATCH_SIZE", "100"))
    app = create_app()
    with app.app_context():
        bulk_retry(cutoff, batch)


if __name__ == "__main__":
    main()
