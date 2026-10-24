"""Transient Apple ticket-service errors must retry, with failures still fatal."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/retry-command.sh'

class RetryTests(unittest.TestCase):
    def run_command(self, failures):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'sleep').write_text('#!/bin/sh\nexit 0\n')
            (root / 'sleep').chmod(0o755)
            counter = root / 'attempts'
            command = [sys.executable, '-c', 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); sys.exit(68 if n<=int(sys.argv[2]) else 0)', str(counter), str(failures)]
            command = ['bash', str(SCRIPT), *command]
            result = subprocess.run(command, env=dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH']), capture_output=True, text=True)
            return result.returncode, int(counter.read_text())

    def test_transient_ticket_failure_eventually_succeeds(self):
        self.assertEqual((0,3),self.run_command(2))

    def test_persistent_failure_stops_after_three_attempts_with_original_status(self):
        self.assertEqual((68,3),self.run_command(10))

    def test_success_does_not_repeat_operation(self):
        self.assertEqual((0,1),self.run_command(0))
