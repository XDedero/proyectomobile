"""Regression coverage for local environments copied between Python versions."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from scripts import start_local


class LocalEnvironmentTests(unittest.TestCase):
    def test_working_environment_does_not_require_package_downloads(self):
        with patch.object(start_local, 'dependency_error', return_value=''), patch.object(
            start_local.subprocess, 'run',
        ) as run:
            python = start_local.app_python()
        self.assertEqual(python, Path(start_local.sys.executable))
        run.assert_not_called()

    def test_installed_but_incompatible_extensions_are_reinstalled(self):
        # A normal pip install reports "already satisfied" for the copied cp314
        # packages, although imports still fail under Python 3.13.
        with patch.object(start_local, 'dependency_error', side_effect=[
            'NumPy import failed', 'NumPy import failed', 'NumPy import failed', '',
        ]), patch.object(start_local, 'can_run', return_value=True), patch.object(
            start_local.subprocess, 'run',
        ) as run, redirect_stdout(io.StringIO()):
            python = start_local.app_python()
        self.assertEqual(python, start_local.ROOT / '.venv-local' / 'Scripts' / 'python.exe')
        installs = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(installs), 2)
        self.assertNotIn('--force-reinstall', installs[0])
        self.assertIn('--force-reinstall', installs[1])
        self.assertIn('--no-cache-dir', installs[1])

    def test_successful_first_install_does_not_reinstall(self):
        with patch.object(start_local, 'dependency_error', side_effect=[
            'ModuleNotFoundError', 'ModuleNotFoundError', '',
        ]), patch.object(start_local, 'can_run', return_value=True), patch.object(
            start_local.subprocess, 'run',
        ) as run, redirect_stdout(io.StringIO()):
            start_local.app_python()
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('--force-reinstall', run.call_args.args[0])

    def test_unrepaired_environment_reports_the_import_error(self):
        stderr = io.StringIO()
        with patch.object(start_local, 'dependency_error', return_value='ImportError: incompatible binary'), patch.object(
            start_local, 'can_run', return_value=True,
        ), patch.object(start_local.subprocess, 'run'), redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            with self.assertRaisesRegex(RuntimeError, 'después de reparar'):
                start_local.app_python()
        self.assertIn('ImportError: incompatible binary', stderr.getvalue())

    def test_pillow_compiled_extension_is_checked(self):
        with patch.object(start_local.subprocess, 'run', return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='ImportError: PIL._imaging',
        )) as run:
            error = start_local.dependency_error(Path('python.exe'))
        self.assertIn('from PIL import Image', run.call_args.args[0][2])
        self.assertEqual(error, 'ImportError: PIL._imaging')


if __name__ == '__main__':
    unittest.main()
