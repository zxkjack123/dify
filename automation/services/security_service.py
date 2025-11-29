import ast
import re
from typing import Dict, List, Set


class SecurityService:
    BLOCKED_IMPORTS: Set[str] = {
        "os", "subprocess", "sys", "shutil", "pickle", "importlib",
        "socket", "requests", "urllib", "http", "ftplib", "telnetlib"
    }
    BLOCKED_FUNCTIONS: Set[str] = {
        "eval", "exec", "compile", "open", "__import__", "input", "breakpoint"
    }
    BLOCKED_ATTRIBUTE_CALLS: Set[str] = {
        "os.system",
        "os.popen",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.run",
        "subprocess.check_output",
        "subprocess.check_call",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.patch",
        "socket.socket",
        "socket.create_connection",
        "urllib.request",
        "http.client",
    }
    NETWORK_MODULES: Set[str] = {
        "requests",
        "socket",
        "urllib",
        "http",
        "ftplib",
        "telnetlib",
    }
    REMEDIATION_HINT = (
        "Use workflow-approved integrations or environment variables "
        "instead of direct system/network calls."
    )
    SUSPICIOUS_REGEX = {
        re.compile(r"os\.system\s*\(", re.IGNORECASE):
            "os.system shell execution",
        re.compile(r"subprocess\.(popen|call|run)", re.IGNORECASE):
            "subprocess invocation",
        re.compile(r"requests\.(get|post|put|delete|patch)", re.IGNORECASE):
            "requests network call",
        re.compile(r"socket\.", re.IGNORECASE): "socket networking",
    }

    def scan_code(self, code: str) -> List[str]:
        """
        Scan Python code for dangerous imports and function calls using AST and
        fallback regex heuristics. Returns security violation messages.
        """
        errors: List[str] = []
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [f"Syntax error: {str(e)}"]

        alias_map = self._collect_aliases_and_check_imports(tree, errors)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = self._resolve_callable_name(node.func, alias_map)
                if not func_name:
                    continue

                root_name = func_name.split('.')[0]

                if root_name in self.BLOCKED_FUNCTIONS:
                    errors.append(
                        "Blocked function call: "
                        f"{root_name} ({self.REMEDIATION_HINT})"
                    )
                    continue

                if func_name in self.BLOCKED_ATTRIBUTE_CALLS:
                    errors.append(
                        "Blocked attribute call: "
                        f"{func_name} ({self.REMEDIATION_HINT})"
                    )
                    continue

                if root_name in self.BLOCKED_IMPORTS:
                    errors.append(
                        "Blocked call via module: "
                        f"{func_name} ({self.REMEDIATION_HINT})"
                    )
                    continue

                if root_name in self.NETWORK_MODULES:
                    errors.append(
                        "Network call blocked: "
                        f"{func_name} ({self.REMEDIATION_HINT})"
                    )

        errors.extend(self._regex_scan(code, errors))

        return errors

    def _collect_aliases_and_check_imports(
        self,
        tree: ast.AST,
        errors: List[str]
    ) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base_module = alias.name.split('.')[0]
                    alias_name = alias.asname or base_module
                    alias_map[alias_name] = base_module
                    if base_module in self.BLOCKED_IMPORTS:
                        errors.append(
                            "Blocked import: "
                            f"{alias.name} ({self.REMEDIATION_HINT})"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    base_module = node.module.split('.')[0]
                    for alias in node.names:
                        alias_name = alias.asname or alias.name
                        alias_map[alias_name] = base_module
                    if base_module in self.BLOCKED_IMPORTS:
                        errors.append(
                            "Blocked import from: "
                            f"{node.module} ({self.REMEDIATION_HINT})"
                        )
        return alias_map

    def _resolve_callable_name(
        self,
        func_node: ast.AST,
        alias_map: Dict[str, str]
    ) -> str | None:
        if isinstance(func_node, ast.Name):
            return alias_map.get(func_node.id, func_node.id)
        if isinstance(func_node, ast.Attribute):
            parts: List[str] = []
            current: ast.AST = func_node
            while isinstance(current, ast.Attribute):
                parts.insert(0, current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                base = alias_map.get(current.id, current.id)
                parts.insert(0, base)
                return '.'.join(parts)
        return None

    def _regex_scan(self, code: str, existing_errors: List[str]) -> List[str]:
        findings: List[str] = []
        seen = set(existing_errors)
        for pattern, description in self.SUSPICIOUS_REGEX.items():
            if pattern.search(code):
                message = (
                    f"Suspicious pattern detected ({description}). "
                    f"{self.REMEDIATION_HINT}"
                )
                if message not in seen:
                    findings.append(message)
                    seen.add(message)
        return findings
