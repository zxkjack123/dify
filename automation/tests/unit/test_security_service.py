import unittest
from automation.services.security_service import SecurityService


class TestSecurityService(unittest.TestCase):
    def setUp(self):
        self.service = SecurityService()

    def test_safe_code(self):
        code = """
def main(x):
    return x + 1
"""
        errors = self.service.scan_code(code)
        self.assertEqual(errors, [])

    def test_blocked_import(self):
        code = "import os"
        errors = self.service.scan_code(code)
        self.assertTrue(
            any(message.startswith("Blocked import: os") for message in errors)
        )

    def test_blocked_import_from(self):
        code = "from subprocess import run"
        errors = self.service.scan_code(code)
        self.assertTrue(
            any(
                message.startswith("Blocked import from: subprocess")
                for message in errors
            )
        )

    def test_blocked_function(self):
        code = "eval('1 + 1')"
        errors = self.service.scan_code(code)
        self.assertTrue(
            any(
                message.startswith("Blocked function call: eval")
                for message in errors
            )
        )

    def test_syntax_error(self):
        code = "def broken("
        errors = self.service.scan_code(code)
        self.assertTrue(any("Syntax error" in e for e in errors))

    def test_nested_usage(self):
        code = """
def main():
    import sys
    exec('print("bad")')
"""
        errors = self.service.scan_code(code)
        self.assertTrue(
            any(
                message.startswith("Blocked import: sys") for message in errors
            )
        )
        self.assertTrue(
            any(
                message.startswith("Blocked function call: exec")
                for message in errors
            )
        )

    def test_blocks_os_system_call(self):
        code = """
import os

def run():
    os.system("ls")
"""
        errors = self.service.scan_code(code)
        self.assertTrue(
            any("os.system" in message for message in errors),
            msg=f"Expected os.system violation, got: {errors}"
        )

    def test_blocks_subprocess_alias(self):
        code = """
import subprocess as sp

def deploy():
    sp.Popen(["ls"])
"""
        errors = self.service.scan_code(code)
        self.assertTrue(
            any("subprocess.Popen" in message for message in errors),
            msg=f"Expected subprocess violation, got: {errors}"
        )

    def test_regex_detects_embedded_pattern(self):
        code = """
def build_payload():
    template = "requests.post('https://example.com', data=data)"
    return template
"""
        errors = self.service.scan_code(code)
        self.assertTrue(
            any("requests network call" in message for message in errors),
            msg=f"Expected regex violation, got: {errors}"
        )


if __name__ == '__main__':
    unittest.main()
