from datetime import datetime
from types import SimpleNamespace

from core.workflow.enums import NodeType, WorkflowNodeExecutionStatus
from core.workflow.graph_engine.domain.graph_execution import GraphExecution
from core.workflow.graph_engine.error_handler import ErrorHandler
from core.workflow.graph_events import NodeRunFailedEvent
from core.workflow.node_events import NodeRunResult
from core.workflow.nodes.base.entities import RetryConfig


class _GraphStub:
    def __init__(self):
        self.nodes: dict[str, SimpleNamespace] = {}


def _build_event(
    node_id: str,
    error: str,
    node_type: NodeType = NodeType.LLM,
) -> NodeRunFailedEvent:
    return NodeRunFailedEvent(
        id="execution-id",
        node_id=node_id,
        node_type=node_type,
        error=error,
        node_run_result=NodeRunResult(
            status=WorkflowNodeExecutionStatus.FAILED,
            error=error,
            error_type="TimeoutError" if "timeout" in error.lower() else "",
        ),
        start_at=datetime.utcnow(),
    )


def _build_node(
    *,
    retry_interval_ms: int,
    node_type: NodeType = NodeType.LLM,
    retry_enabled: bool = True,
) -> SimpleNamespace:
    retry_config = RetryConfig(
        max_retries=5,
        retry_interval=retry_interval_ms,
        retry_enabled=retry_enabled,
    )
    return SimpleNamespace(
        title="llm",
        retry=retry_config.retry_enabled,
        retry_config=retry_config,
        node_type=node_type,
        error_strategy=None,
    )


def test_llm_timeout_retry_uses_exponential_backoff():
    graph = _GraphStub()
    handler = ErrorHandler(graph, GraphExecution(workflow_id="wf"))
    node_id = "node-1"
    node = _build_node(retry_interval_ms=1000)
    graph.nodes[node_id] = node

    delay = handler._calculate_retry_delay_seconds(
        node,
        retry_count=2,
        event=_build_event(node_id, "Request timeout"),
    )

    assert delay == 4.0  # base 1s * 2^2


def test_llm_timeout_retry_respects_maximum_backoff():
    graph = _GraphStub()
    handler = ErrorHandler(graph, GraphExecution(workflow_id="wf"))
    node_id = "node-2"
    node = _build_node(retry_interval_ms=10000)
    graph.nodes[node_id] = node

    delay = handler._calculate_retry_delay_seconds(
        node,
        retry_count=3,
        event=_build_event(node_id, "超时"),
    )

    assert delay == ErrorHandler._MAX_BACKOFF_SECONDS


def test_non_timeout_or_non_llm_return_base_interval():
    graph = _GraphStub()
    handler = ErrorHandler(graph, GraphExecution(workflow_id="wf"))

    # Non-timeout error for LLM
    node = _build_node(retry_interval_ms=2000)
    delay_llm = handler._calculate_retry_delay_seconds(
        node,
        retry_count=1,
        event=_build_event("node", "Unknown failure"),
    )

    # Timeout error but non-LLM node
    non_llm_node = _build_node(retry_interval_ms=2000, node_type=NodeType.TOOL)
    delay_non_llm = handler._calculate_retry_delay_seconds(
        non_llm_node,
        retry_count=1,
        event=_build_event("node", "Request timeout", node_type=NodeType.TOOL),
    )

    assert delay_llm == 2.0
    assert delay_non_llm == 2.0
