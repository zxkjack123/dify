import json
import time
from typing import Dict, Any, Generator, Optional
from automation.clients.dify_console_client import DifyConsoleClient
from automation.domain.execution import (
    WorkflowExecutionResult,
    NodeExecutionResult
)
from automation.infra.logger import logger
from automation.services.trace_service import TraceService


class WorkflowRunner:
    def __init__(
        self,
        client: DifyConsoleClient,
        trace_service: Optional[TraceService] = None
    ):
        self.client = client
        self.trace_service = trace_service or TraceService()

    def run(
        self,
        app_id: str,
        inputs: Dict[str, Any],
        mode: str = "workflow",
        timeout: int = 300,
        trace_id: Optional[str] = None
    ) -> WorkflowExecutionResult:
        """
        Run a workflow and return the execution result.
        Handles SSE parsing and basic event tracking.
        """
        trace_id = trace_id or self.trace_service.new_trace_id()
        execution = WorkflowExecutionResult(app_id=app_id, inputs=inputs)
        execution.status = "running"

        start_time = time.time()
        self._record_trace(
            trace_id,
            "workflow_run",
            "start",
            {"app_id": app_id, "mode": mode}
        )

        try:
            response = self.client.run_workflow(
                app_id=app_id,
                inputs=inputs,
                mode=mode,
                trace_id=trace_id
            )

            # Process SSE stream
            for event_data in self._parse_sse(response):
                self._handle_event(execution, event_data)

                # Check timeout
                if time.time() - start_time > timeout:
                    raise TimeoutError(
                        f"Workflow execution timed out after {timeout}s"
                    )

            if execution.status == "running":
                # If stream ended but no finished event, mark as unknown
                # or success if we have outputs
                if execution.outputs:
                    execution.finish("succeeded", execution.outputs)
                else:
                    execution.finish(
                        "failed", error="Stream ended unexpectedly"
                    )

        except Exception as e:
            logger.error(f"Workflow execution failed: {str(e)}")
            execution.finish("failed", error=str(e))
            self._record_trace(
                trace_id,
                "workflow_run",
                "error",
                {"error": str(e)}
            )

        self._log_audit(execution, trace_id)
        self._record_trace(
            trace_id,
            "workflow_run",
            "finish",
            {
                "status": execution.status,
                "run_id": execution.run_id,
                "duration": execution.duration,
                "total_tokens": execution.total_tokens
            }
        )
        return execution

    def _parse_sse(self, response) -> Generator[Dict[str, Any], None, None]:
        """
        Yields parsed JSON data from SSE stream.
        """
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data: "):
                    data_str = decoded_line[6:]
                    try:
                        yield json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning(
                            f"Failed to decode SSE data: {data_str}"
                        )

    def _handle_event(
        self, execution: WorkflowExecutionResult, data: Dict[str, Any]
    ):
        event = data.get("event")

        if event == "workflow_started":
            execution.run_id = data.get("workflow_run_id", "")

        elif event == "node_started":
            node_data = data.get("data", {})
            node_id = node_data.get("node_id")
            if node_id:
                node_exec = NodeExecutionResult(
                    node_id=node_id,
                    node_type=node_data.get("node_type", "unknown"),
                    title=node_data.get("title", "Unknown Node"),
                    status="running",
                    inputs=node_data.get("inputs")
                )
                execution.node_executions.append(node_exec)

        elif event == "node_finished":
            node_data = data.get("data", {})
            node_id = node_data.get("node_id")
            # Find the running node
            for node in reversed(execution.node_executions):
                if node.node_id == node_id and node.status == "running":
                    node.finish(
                        status=node_data.get("status", "succeeded"),
                        outputs=node_data.get("outputs"),
                        error=node_data.get("error")
                    )
                    break

        elif event == "workflow_finished":
            data_content = data.get("data", {})
            execution.finish(
                status="succeeded",
                outputs=data_content.get("outputs", {})
            )
            execution.total_tokens = data_content.get("total_tokens", 0)

        elif event == "error":
            execution.finish("failed", error=data.get("message"))

    def _log_audit(
        self,
        execution: WorkflowExecutionResult,
        trace_id: str
    ):
        """
        Write execution summary to runs_history.jsonl
        """
        log_entry = {
            "run_id": execution.run_id,
            "app_id": execution.app_id,
            "status": execution.status,
            "start_time": execution.start_time,
            "duration": execution.duration,
            "error": execution.error,
            "total_tokens": execution.total_tokens,
            "node_count": len(execution.node_executions),
            "trace_id": trace_id
        }

        try:
            with open("automation/logs/runs_history.jsonl", "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit log: {str(e)}")

    def _record_trace(
        self,
        trace_id: str,
        action: str,
        stage: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        try:
            self.trace_service.record(trace_id, action, stage, metadata)
        except Exception as err:
            logger.error(f"Failed to record trace {trace_id}: {err}")

    def run_node(
        self,
        app_id: str,
        node_id: str,
        inputs: Dict[str, Any],
        files: Optional[list] = None
    ) -> NodeExecutionResult:
        """
        Run a single node in the draft workflow.
        """
        try:
            response = self.client.run_workflow_node(
                app_id=app_id,
                node_id=node_id,
                inputs=inputs,
                files=files
            )
            
            # Map response to NodeExecutionResult
            # Response structure depends on workflow_run_node_execution_fields
            # Typically: { "id": "...", "status": "...", "outputs": ... }
            
            return NodeExecutionResult(
                node_id=node_id,
                node_type=response.get("node_type", "unknown"),
                title=response.get("title", "Unknown Node"),
                status=response.get("status", "unknown"),
                inputs=response.get("inputs"),
                outputs=response.get("outputs"),
                error=response.get("error"),
                execution_metadata=response.get("execution_metadata")
            )
            
        except Exception as e:
            logger.error(f"Node execution failed: {str(e)}")
            return NodeExecutionResult(
                node_id=node_id,
                node_type="unknown",
                title="Unknown Node",
                status="failed",
                error=str(e)
            )
