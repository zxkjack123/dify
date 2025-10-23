# This script is intended to be executed inside `flask shell` via input redirection.
# Example:
#   CUTOFF_MINUTES=15 BATCH_SIZE=100 flask shell < /tmp/retry_stale_docs_flask.py

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta

from extensions.ext_database import db
from models.account import Account
from models.dataset import Dataset, Document
from tasks.retry_document_indexing_task import retry_document_indexing_task

cutoff_minutes = int(os.environ.get("CUTOFF_MINUTES", "15"))
batch_size = int(os.environ.get("BATCH_SIZE", "100"))

cutoff = datetime.utcnow() - timedelta(minutes=cutoff_minutes)

inprog = (
    db.session.query(Document)
    .filter(
        Document.indexing_status.in_(["indexing", "parsing", "cleaning", "splitting"]),
        Document.is_paused.is_(False),
        Document.updated_at < cutoff,
    )
    .all()
)

if not inprog:
    print("No stale documents found.")
else:
    groups: dict[str, list[Document]] = defaultdict(list)
    for d in inprog:
        groups[str(d.dataset_id)].append(d)

    total = 0
    for ds_id, docs in groups.items():
        dataset = db.session.get(Dataset, ds_id)
        user_id: str | None = None
        for d in docs:
            if getattr(d, "created_by", None):
                user_id = str(d.created_by)
                break
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
            chunk = ids[i : i + batch_size]
            retry_document_indexing_task.delay(ds_id, chunk, user_id)
            print(f"Enqueued retry for dataset={ds_id} size={len(chunk)} user={user_id}")
            total += len(chunk)

    print(f"Total documents enqueued: {total}")
