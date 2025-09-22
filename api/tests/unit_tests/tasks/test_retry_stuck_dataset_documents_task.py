import sys
from types import SimpleNamespace


def _install_dummy_app_module():
    """Install a lightweight dummy `app` module so the Celery task decorator
    in the schedule module becomes a no-op during import.
    """

    class _DummyCelery:
        def task(self, queue=None):
            def _decorator(fn):
                return fn

            return _decorator

    dummy = SimpleNamespace(celery=_DummyCelery())
    sys.modules["app"] = dummy


def _make_scalar_result(items):
    class _Scalars:
        def __init__(self, _items):
            self._items = _items

        def all(self):
            return self._items

    class _Result:
        def __init__(self, _items):
            self._items = _items

        def scalars(self):
            return _Scalars(self._items)

    return _Result(items)


def test_retry_stuck_docs_categories_and_guard(monkeypatch, capsys):
    # Install a dummy `app` module to bypass real app/celery initialization
    _install_dummy_app_module()

    # Import the module under test after installing dummy app
    from schedule import retry_stuck_dataset_documents_task as sched_mod  # type: ignore

    # Build simple document-like objects
    dup = SimpleNamespace(id="dup", dataset_id="ds1", indexing_status="error")
    err = SimpleNamespace(id="e1", dataset_id="ds1", indexing_status="error")
    idx = SimpleNamespace(id="i1", dataset_id="ds1", indexing_status="indexing")

    # Mock db.session.execute to return in order: indexing docs, then error docs
    execute_calls = [
        _make_scalar_result([idx, dup]),  # stuck indexing docs
        _make_scalar_result([err, dup]),  # error docs
    ]

    fake_session = SimpleNamespace(
        execute=lambda stmt: execute_calls.pop(0),  # pop results in order
        get=lambda *args, **kwargs: None,
    )
    fake_db = SimpleNamespace(
        session=fake_session, add=lambda *a, **k: None, commit=lambda: None, rollback=lambda: None
    )
    monkeypatch.setattr(sched_mod, "db", fake_db)

    # Install a small in-memory store for redis guard behavior
    store: dict[str, str] = {}

    def _redis_get(key):
        val = store.get(key)
        if val is None:
            return None
        return str(val).encode()

    def _redis_setex(key, ttl, value):
        store[key] = value

    # Patch redis client used by the module
    monkeypatch.setattr(sched_mod.redis_client, "get", _redis_get)
    monkeypatch.setattr(sched_mod.redis_client, "setex", _redis_setex)

    # Capture the status writes but ignore their effects
    monkeypatch.setattr(sched_mod.redis_client, "set", lambda *a, **k: None)

    # Mock DocumentService.retry_document to only set the redis guard
    calls = []

    def _fake_retry(dataset_id: str, docs: list):
        for d in docs:
            calls.append((dataset_id, d.id))
            _redis_setex(f"document_{d.id}_is_retried", 600, 1)

    monkeypatch.setattr(sched_mod, "DocumentService", SimpleNamespace(retry_document=_fake_retry))

    # Configure flags
    monkeypatch.setattr(
        sched_mod,
        "dify_config",
        SimpleNamespace(
            ENABLE_RETRY_DATASET_DOCUMENTS_TASK=True,
            RETRY_DATASET_DOCUMENTS_THRESHOLD_MINUTES=20,
            RETRY_DATASET_DOCUMENTS_INTERVAL_MINUTES=5,
            RETRY_DATASET_DOCUMENTS_MAX_PER_RUN=100,
            ENABLE_OTEL=False,
        ),
    )

    # Execute
    sched_mod.retry_stuck_dataset_documents_task()

    # Verify the duplicate doc is only retried once due to guard
    dup_count = sum(1 for (_dataset, doc_id) in calls if doc_id == "dup")
    assert dup_count == 1

    # Verify total retries: err, dup (from error list), idx => 3 unique retries
    ids = [doc_id for (_dataset, doc_id) in calls]
    assert sorted(ids) == ["dup", "e1", "i1"]

    # Assert the printed summary contains the per-type counts
    out = capsys.readouterr().out
    assert "Retried 3 stuck documents" in out
    assert "error=2" in out
    assert "indexing=1" in out
