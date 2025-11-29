from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import time


class NodeExecutionResult(BaseModel):
    node_id: str
    node_type: str
    title: str
    status: str  # running, succeeded, failed
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_metadata: Optional[Dict[str, Any]] = None
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    duration: Optional[float] = None

    def finish(
        self,
        status: str,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ):
        self.status = status
        self.outputs = outputs
        self.error = error
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time


class WorkflowExecutionResult(BaseModel):
    run_id: str = ""
    app_id: str
    status: str = "pending"  # pending, running, succeeded, failed, stopped
    inputs: Dict[str, Any]
    outputs: Dict[str, Any] = {}
    error: Optional[str] = None
    node_executions: List[NodeExecutionResult] = []
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    duration: Optional[float] = None
    total_tokens: int = 0

    def finish(
        self,
        status: str,
        outputs: Dict[str, Any] = {},
        error: Optional[str] = None
    ):
        self.status = status
        self.outputs = outputs
        self.error = error
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time
