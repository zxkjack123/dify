import os
import json
import sys
import argparse
from automation.clients.dify_console_client import DifyConsoleClient
from automation.services.workflow_service import WorkflowService
from automation.infra.logger import logger

WORKFLOWS_DIR = "automation/tests/e2e/workflows"
CONFIG_FILE = os.path.join(WORKFLOWS_DIR, "test_config.json")


def run_e2e_tests(mock: bool = False):
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"Config file not found: {CONFIG_FILE}")
        sys.exit(1)

    with open(CONFIG_FILE, "r") as f:
        test_config = json.load(f)

    client = DifyConsoleClient()
    
    if mock:
        from unittest.mock import MagicMock
        client = MagicMock()
        
        def import_side_effect(mode, yaml_content, **kwargs):
            if "E2E Greeting" in yaml_content:
                return {"app_id": "app-greeting"}
            elif "E2E Math" in yaml_content:
                return {"app_id": "app-math"}
            return {"app_id": "app-unknown"}
            
        client.import_app.side_effect = import_side_effect
        
        def run_side_effect(app_id, inputs, **kwargs):
            mock_resp = MagicMock()
            if app_id == "app-greeting":
                mock_resp.iter_lines.return_value = [
                    b'data: {"event": "workflow_started", '
                    b'"workflow_run_id": "run-1"}',
                    b'data: {"event": "workflow_finished", "data": '
                    b'{"outputs": {"greeting": "Hello, Dify!"}}}'
                ]
            elif app_id == "app-math":
                mock_resp.iter_lines.return_value = [
                    b'data: {"event": "workflow_started", '
                    b'"workflow_run_id": "run-2"}',
                    b'data: {"event": "workflow_finished", "data": '
                    b'{"outputs": {"sum": 15, "product": 50}}}'
                ]
            else:
                mock_resp.iter_lines.return_value = []
            return mock_resp

        client.run_workflow.side_effect = run_side_effect

    service = WorkflowService(client)
    
    passed = 0
    failed = 0

    print(f"Running E2E tests from {WORKFLOWS_DIR}...")

    for filename, config in test_config.items():
        filepath = os.path.join(WORKFLOWS_DIR, filename)
        if not os.path.exists(filepath):
            logger.warning(f"Workflow file not found: {filename}")
            continue

        print(f"Testing {filename}...")
        
        try:
            with open(filepath, "r") as f:
                dsl_content = f.read()

            inputs = config.get("inputs", {})
            expected_outputs = config.get("expected_outputs", {})

            result = service.create_and_run(dsl_content, inputs)

            if result["status"] != "succeeded":
                print(
                    f"  FAILED: Status is {result['status']}, "
                    f"Error: {result['error']}"
                )
                failed += 1
                continue

            # Verify outputs
            actual_outputs = result["outputs"]
            # Simple subset check
            match = True
            for k, v in expected_outputs.items():
                if actual_outputs.get(k) != v:
                    print(
                        f"  FAILED: Output mismatch. Expected {k}={v}, "
                        f"got {actual_outputs.get(k)}"
                    )
                    match = False
                    break
            
            if match:
                print("  PASSED")
                passed += 1
            else:
                failed += 1

        except Exception as e:
            print(f"  ERROR: {str(e)}")
            failed += 1

    print(f"\nSummary: {passed} passed, {failed} failed.")
    
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mock", action="store_true", help="Run with mocked client"
    )
    args = parser.parse_args()
    
    run_e2e_tests(mock=args.mock)
