import datetime
import importlib
import itertools
import time

import click
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

import app
from configs import dify_config
from extensions.ext_database import db
from extensions.ext_redis import redis_client
from models.dataset import Document
from services.dataset_service import DocumentService

_meter = None
_found_counter = None
_retried_counter = None
# Per-type metrics
_found_error_counter = None
_found_indexing_counter = None
_retried_error_counter = None
_retried_indexing_counter = None
_latency_hist = None


@app.celery.task(queue="dataset")
def retry_stuck_dataset_documents_task() -> None:
    """
    Periodic task to automatically recover dataset documents that appear
    to be stuck in non-ready states for longer than the configured
    threshold.

    A document is considered stuck if:
    - indexing_status in {waiting, parsing, cleaning, splitting, indexing}
    - and it has not progressed for RETRY_DATASET_DOCUMENTS_THRESHOLD_MINUTES

    Safety limits:
    - Max documents processed per run: RETRY_DATASET_DOCUMENTS_MAX_PER_RUN
        - Uses a short-lived Redis key per document to avoid duplicate
            retries within 10 minutes
    """
    if not dify_config.ENABLE_RETRY_DATASET_DOCUMENTS_TASK:
        return

    global _meter, _found_counter, _retried_counter, _latency_hist
    global _found_error_counter, _found_indexing_counter
    global _retried_error_counter, _retried_indexing_counter
    if dify_config.ENABLE_OTEL and _meter is None:
        try:
            metrics_mod = importlib.import_module("opentelemetry.metrics")
            get_meter = metrics_mod.get_meter
            _meter = get_meter(
                "dataset_retry",
                version=dify_config.project.version,
            )
            _found_counter = _meter.create_counter(
                "dify.datasets.stuck_documents.found",
                description="Number of stuck dataset documents found per scan",
                unit="{document}",
            )
            _retried_counter = _meter.create_counter(
                "dify.datasets.stuck_documents.retried",
                description=(
                    "Number of stuck dataset documents retried per scan"),
                unit="{document}",
            )
            # Per-type counters
            _found_error_counter = _meter.create_counter(
                "dify.datasets.stuck_documents.found_error",
                description=(
                    "Number of error-status dataset documents found per scan"),
                unit="{document}",
            )
            _found_indexing_counter = _meter.create_counter(
                "dify.datasets.stuck_documents.found_indexing",
                description=(
                    "Number of indexing-status dataset documents found per scan"),
                unit="{document}",
            )
            _retried_error_counter = _meter.create_counter(
                "dify.datasets.stuck_documents.retried_error",
                description=(
                    "Number of error-status dataset documents retried per scan"),
                unit="{document}",
            )
            _retried_indexing_counter = _meter.create_counter(
                "dify.datasets.stuck_documents.retried_indexing",
                description=(
                    "Number of indexing-status dataset documents retried per scan"),
                unit="{document}",
            )
            _latency_hist = _meter.create_histogram(
                "dify.datasets.stuck_documents.scan_latency_ms",
                description="Latency of stuck-docs scanning in milliseconds",
                unit="ms",
            )
        except Exception:
            # Metrics are best-effort only
            _meter = None

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    threshold_minutes = dify_config.RETRY_DATASET_DOCUMENTS_THRESHOLD_MINUTES
    stale_before = now - datetime.timedelta(minutes=int(threshold_minutes))

    max_per_run = int(dify_config.RETRY_DATASET_DOCUMENTS_MAX_PER_RUN)

    click.echo(
        click.style(
            "Scanning for stuck dataset documents...",
            fg="green",
        )
    )
    start_at = time.perf_counter()

    processed = 0
    processed_error = 0
    processed_indexing = 0
    found = 0
    found_error = 0
    found_indexing = 0
    try:
        # A document can be considered "recently updated" by the most
        # recent of its progress timestamps. Build a COALESCE-like notion
        # to check any progress field > stale_before. We'll select
        # candidates where none of the progress timestamps is recent.
        progress_recent_cond = or_(
            Document.processing_started_at > stale_before,
            Document.parsing_completed_at > stale_before,
            Document.cleaning_completed_at > stale_before,
            Document.splitting_completed_at > stale_before,
            Document.completed_at > stale_before,
            Document.stopped_at > stale_before,
            Document.updated_at > stale_before,
        )

        # type B: indexing-like statuses but stale ("stuck in indexing")
        candidates_stmt = (
            select(Document)
            .where(
                Document.archived.is_(False),
                Document.enabled.is_(True),
                (Document.is_paused.is_(False) | (Document.is_paused.is_(None))),
                Document.indexing_status.in_(
                    [
                        "waiting",
                        "parsing",
                        "cleaning",
                        "splitting",
                        "indexing",
                    ]
                ),
                ~progress_recent_cond,
            )
            .order_by(Document.updated_at.asc())
        )

        stuck_indexing_docs = db.session.execute(
            candidates_stmt).scalars().all()
        found_indexing = len(stuck_indexing_docs)

        # type A: error status docs (retryable)
        error_stmt = (
            select(Document)
            .where(
                Document.archived.is_(False),
                Document.enabled.is_(True),
                (Document.is_paused.is_(False) | (Document.is_paused.is_(None))),
                Document.indexing_status == "error",
            )
            .order_by(Document.updated_at.asc())
        )
        error_docs = db.session.execute(error_stmt).scalars().all()
        found_error = len(error_docs)

        found = found_indexing + found_error
        if not found:
            click.echo(click.style("No stuck documents found.", fg="cyan"))
            return

        # Process type A first (error), then type B (stuck indexing)
        for doc in itertools.chain(error_docs, stuck_indexing_docs):
            if processed >= max_per_run:
                break

            # Skip if a retry is already in-flight
            retry_cache_key = f"document_{doc.id}_is_retried"
            if redis_client.get(retry_cache_key) is not None:
                continue

            # Use the existing service method which sets status and
            # triggers async retry
            try:
                DocumentService.retry_document(doc.dataset_id, [doc])
                processed += 1
                if doc.indexing_status == "error":
                    processed_error += 1
                else:
                    processed_indexing += 1
            except Exception as e:
                # Mark document as error to avoid endless loop, but don't
                # fail the batch
                try:
                    fresh = db.session.get(Document, doc.id)
                    if fresh:
                        fresh.indexing_status = "error"
                        fresh.error = str(e)
                        fresh.stopped_at = now
                        db.session.add(fresh)
                        db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    # best-effort only

        click.echo(
            click.style(
                (
                    "Retried {processed} stuck documents (error={processed_error}, indexing={processed_indexing})."
                ).format(
                    processed=processed,
                    processed_error=processed_error,
                    processed_indexing=processed_indexing,
                ),
                fg="green",
            )
        )
    except SQLAlchemyError as ex:
        db.session.rollback()
        click.echo(
            click.style(
                f"retry_stuck_dataset_documents_task SQL error: {ex}",
                fg="red",
            )
        )
    finally:
        end_at = time.perf_counter()
        # Write last-run status for admin endpoint
        try:
            redis_client.set("retry_stuck_docs:last_run", str(time.time()))
            redis_client.set("retry_stuck_docs:last_count", str(processed))
            redis_client.set(
                "retry_stuck_docs:last_count_error",
                str(processed_error),
            )
            redis_client.set(
                "retry_stuck_docs:last_count_indexing",
                str(processed_indexing),
            )
        except Exception:
            # ignore redis failures
            pass

        # Emit metrics if enabled
        try:
            if _meter is not None:
                if _found_counter is not None:
                    _found_counter.add(found)
                if _retried_counter is not None:
                    _retried_counter.add(processed)
                # Per-type metric emits
                if _found_error_counter is not None:
                    _found_error_counter.add(found_error)
                if _found_indexing_counter is not None:
                    _found_indexing_counter.add(found_indexing)
                if _retried_error_counter is not None:
                    _retried_error_counter.add(processed_error)
                if _retried_indexing_counter is not None:
                    _retried_indexing_counter.add(processed_indexing)
                if _latency_hist is not None:
                    latency_ms = (end_at - start_at) * 1000.0
                    _latency_hist.record(latency_ms)
        except Exception:
            pass
        click.echo(
            click.style(
                f"Scan latency: {end_at - start_at:.3f}s",
                fg="green",
            )
        )
