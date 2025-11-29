import logging
import time
import uuid
import requests
from typing import Dict, Any, Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
from automation.infra.config import Config
from automation.infra.logger import logger
from automation.infra.metrics import RequestMetricsRecorder
from automation.domain.exceptions import (
    AuthError,
    NetworkError,
    RateLimitError,
    ServerError
)


class DifyConsoleClient:
    def __init__(self):
        self.config = Config()
        self.base_url = self.config.DIFY_CONSOLE_URL.rstrip('/')
        self.email = self.config.DIFY_EMAIL
        self.password = self.config.DIFY_PASSWORD
        self.session = requests.Session()
        self.logged_in = False
        self.metrics = RequestMetricsRecorder()

    def login(self) -> None:
        url = f"{self.base_url}/console/api/login"
        payload = {
            "email": self.email,
            "password": self.password
        }
        logger.info(f"Attempting login for user: {self.email}")
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            token = data.get("data", {}).get("access_token")
            if token:
                self.session.headers.update({
                    "Authorization": f"Bearer {token}"
                })
                self.logged_in = True
                logger.info("Login successful")
            else:
                logger.error("Login failed: No access token received")
                raise AuthError("Login failed: No access token received")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("Login failed: Invalid credentials")
                raise AuthError("Invalid email or password")
            logger.error(f"Login failed: {str(e)}")
            raise NetworkError(f"Login failed: {str(e)}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Login network error: {str(e)}")
            raise NetworkError(f"Login network error: {str(e)}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((NetworkError, ServerError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        trace_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if not self.logged_in and endpoint != "/console/api/login":
            self.login()

        url = f"{self.base_url}{endpoint}"
        logger.debug(f"Request: {method} {url}")

        trace_id = trace_id or self._new_http_trace_id()
        start_time = time.perf_counter()
        status_code: Optional[int] = None
        error_label: Optional[str] = None

        try:
            response = self.session.request(method, url, **kwargs)
            status_code = response.status_code

            if status_code == 401 and self.logged_in:
                logger.warning("Session expired, retrying login...")
                self.logged_in = False
                self.login()
                response = self.session.request(method, url, **kwargs)
                status_code = response.status_code

            if status_code == 429:
                error_label = "RateLimitError"
                raise RateLimitError("Rate limit exceeded")

            if status_code is not None and 500 <= status_code < 600:
                error_label = "ServerError"
                raise ServerError(f"Server error: {status_code}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.ConnectionError as exc:
            error_label = "NetworkError"
            raise NetworkError(f"Connection error: {str(exc)}")
        except requests.exceptions.Timeout as exc:
            error_label = "NetworkError"
            raise NetworkError(f"Timeout error: {str(exc)}")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None:
                status_code = exc.response.status_code
            if status_code == 401:
                error_label = "AuthError"
                raise AuthError("Unauthorized")
            error_label = error_label or "HTTPError"
            raise
        except (RateLimitError, ServerError, NetworkError):
            raise
        except Exception as exc:
            error_label = exc.__class__.__name__
            logger.error(f"Unexpected error: {str(exc)}")
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self._record_http_metric(
                trace_id=trace_id,
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                duration_ms=duration_ms,
                error_type=error_label
            )

    def get_apps(self, trace_id: Optional[str] = None) -> Dict[str, Any]:
        return self._request(
            "GET",
            "/console/api/apps",
            trace_id=trace_id
        )

    def import_app(
        self,
        mode: str,
        yaml_content: str,
        app_id: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "mode": mode,
            "yaml_content": yaml_content
        }
        if app_id:
            payload["app_id"] = app_id
        
        return self._request(
            "POST",
            "/console/api/apps/imports",
            json=payload,
            trace_id=trace_id
        )

    def run_workflow(
        self,
        app_id: str,
        inputs: Dict[str, Any],
        files: Optional[list] = None,
        mode: str = "workflow",
        trace_id: Optional[str] = None
    ) -> Any:
        # This endpoint returns SSE stream.
        # For MVP, we will consume the stream and return the final result
        # or list of events.
        payload = {
            "inputs": inputs,
            "files": files or []
        }

        if mode == "advanced-chat":
            url = (
                f"{self.base_url}/console/api/apps/{app_id}"
                "/advanced-chat/workflows/draft/run"
            )
            # For advanced-chat, query is a top-level field
            if "query" in inputs:
                payload["query"] = inputs.pop("query")
            else:
                payload["query"] = ""
        else:
            url = (
                f"{self.base_url}/console/api/apps/{app_id}"
                "/workflows/draft/run"
            )
            
        if not self.logged_in:
            self.login()
            
        logger.info(f"Running workflow app_id={app_id} mode={mode}")
        
        trace_id = trace_id or self._new_http_trace_id()
        start_time = time.perf_counter()
        status_code: Optional[int] = None
        error_label: Optional[str] = None
        endpoint = self._normalize_endpoint(url)

        try:
            response = self.session.post(url, json=payload, stream=True)
            status_code = response.status_code

            if status_code == 401 and self.logged_in:
                logger.warning(
                    "Session expired during run, retrying login..."
                )
                self.logged_in = False
                self.login()
                response = self.session.post(url, json=payload, stream=True)
                status_code = response.status_code

            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None:
                status_code = exc.response.status_code
            if status_code == 401:
                error_label = "AuthError"
                raise AuthError("Unauthorized")
            error_label = error_label or "HTTPError"
            logger.error(f"Run workflow failed: {str(exc)}")
            raise
        except Exception as exc:
            error_label = exc.__class__.__name__
            logger.error(f"Run workflow unexpected error: {str(exc)}")
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self._record_http_metric(
                trace_id=trace_id,
                method="POST",
                endpoint=endpoint,
                status_code=status_code,
                duration_ms=duration_ms,
                error_type=error_label,
                metadata={"stream": True, "mode": mode}
            )

    def run_workflow_node(
        self,
        app_id: str,
        node_id: str,
        inputs: Dict[str, Any],
        files: Optional[list] = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run a single node in the draft workflow.
        """
        url = (
            f"{self.base_url}/console/api/apps/{app_id}"
            "/workflows/draft/nodes/{node_id}/run"
        ).format(node_id=node_id)

        payload = {
            "inputs": inputs,
            "files": files or []
        }

        return self._request(
            "POST",
            url,
            json=payload,
            trace_id=trace_id
        )

    def export_app(
        self,
        app_id: str,
        include_secret: bool = False,
        trace_id: Optional[str] = None
    ) -> str:
        params = {"include_secret": str(include_secret).lower()}
        response = self._request(
            "GET",
            f"/console/api/apps/{app_id}/export",
            params=params,
            trace_id=trace_id
        )
        return response.get("data", "")

    def _new_http_trace_id(self) -> str:
        return f"http_{uuid.uuid4().hex}"

    def _normalize_endpoint(self, url: str) -> str:
        if url.startswith(self.base_url):
            return url[len(self.base_url):]
        return url

    def _record_http_metric(
        self,
        *,
        trace_id: str,
        method: str,
        endpoint: str,
        status_code: Optional[int],
        duration_ms: float,
        error_type: Optional[str],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        success = error_type is None
        entry_metadata = dict(metadata or {})
        entry_metadata.setdefault("client", "console")

        try:
            self.metrics.record(
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                duration_ms=duration_ms,
                trace_id=trace_id,
                success=success,
                error_type=error_type,
                metadata=entry_metadata
            )
        except Exception as exc:
            logger.warning(
                "Failed to record metrics for %s %s: %s",
                method,
                endpoint,
                exc
            )

        logger.info(
            "HTTP %s %s status=%s duration=%.2fms trace=%s result=%s",
            method,
            endpoint,
            status_code if status_code is not None else "n/a",
            duration_ms,
            trace_id,
            "success" if success else (error_type or "error")
        )
