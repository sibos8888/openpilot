import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/test_runner.py"


class TestRunnerCollection(unittest.TestCase):
  def run_runner(self, sources, target_suffix="", dotted=False, keyword=""):
    with tempfile.TemporaryDirectory(prefix="runner_fixture_", dir=ROOT / "tools") as directory:
      paths = []
      for index, source in enumerate(sources):
        path = Path(directory) / f"test_case_{index}.py"
        path.write_text(textwrap.dedent(source))
        paths.append(path)
      targets = [".".join(path.relative_to(ROOT).with_suffix("").parts) if dotted else str(path) for path in paths]
      targets[0] += target_suffix
      keyword_args = ["-k", keyword] if keyword else []
      return subprocess.run(
        [sys.executable, str(RUNNER), *targets, "-j", "1", "-v", *keyword_args],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, text=True, capture_output=True, check=False,
      )

  def test_unittest_module_runs(self):
    result = self.run_runner(["""
      import unittest
      class TestSupported(unittest.TestCase):
        def test_runs(self):
          self.assertEqual(2 + 2, 4)
    """])
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("TestSupported.test_runs", result.stdout)
    self.assertIn("1 passed", result.stdout)

  def test_pytest_function_is_rejected_without_executing(self):
    source = """
      def test_function():
        raise RuntimeError('FUNCTION_WAS_EXECUTED')
    """
    for dotted, suffix in ((False, ""), (True, ""), (False, "::test_function")):
      with self.subTest(dotted=dotted, suffix=suffix):
        result = self.run_runner([source], suffix, dotted)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unsupported pytest", result.stdout)
        self.assertIn("test_function", result.stdout)
        self.assertIn("python -m pytest", result.stdout)
        self.assertNotIn("FUNCTION_WAS_EXECUTED", result.stdout + result.stderr)

  def test_mixed_module_cannot_hide_pytest_function(self):
    result = self.run_runner(["""
      import unittest
      class TestSupported(unittest.TestCase):
        def test_runs(self):
          pass
      def test_omitted():
        pass
    """])
    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("unsupported pytest", result.stdout)
    self.assertIn("test_omitted", result.stdout)

  def test_mixed_module_respects_supported_selection(self):
    source = """
      import unittest
      class TestSupported(unittest.TestCase):
        def test_runs(self):
          pass
      def test_omitted():
        raise RuntimeError('FUNCTION_WAS_EXECUTED')
      class TestSupportedPytest:
        def test_omitted(self):
          raise RuntimeError('PYTEST_METHOD_WAS_EXECUTED')
    """
    for dotted, suffix, keyword in (
      (False, "::TestSupported", ""),
      (False, "::TestSupported::test_runs", ""),
      (True, ".TestSupported", ""),
      (True, ".TestSupported.test_runs", ""),
      (False, "", "TEST_RUNS"),
    ):
      with self.subTest(dotted=dotted, suffix=suffix, keyword=keyword):
        result = self.run_runner([source], suffix, dotted, keyword)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TestSupported.test_runs", result.stdout)
        self.assertIn("1 passed", result.stdout)
        self.assertNotIn("FUNCTION_WAS_EXECUTED", result.stdout + result.stderr)
        self.assertNotIn("PYTEST_METHOD_WAS_EXECUTED", result.stdout + result.stderr)

  def test_keyword_matching_pytest_is_rejected_without_executing(self):
    result = self.run_runner(["""
      import unittest
      class TestSupported(unittest.TestCase):
        def test_runs(self):
          pass
      def test_omitted():
        raise RuntimeError('FUNCTION_WAS_EXECUTED')
      class TestPytest:
        def test_omitted(self):
          raise RuntimeError('PYTEST_METHOD_WAS_EXECUTED')
    """], keyword="TEST_OMITTED")
    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("unsupported pytest", result.stdout)
    self.assertIn("test_case_0.test_omitted", result.stdout)
    self.assertIn("TestPytest.test_omitted", result.stdout)
    self.assertNotIn("FUNCTION_WAS_EXECUTED", result.stdout + result.stderr)
    self.assertNotIn("PYTEST_METHOD_WAS_EXECUTED", result.stdout + result.stderr)

  def test_pytest_class_is_rejected_in_mixed_module(self):
    result = self.run_runner(["""
      import unittest
      class TestSupported(unittest.TestCase):
        def test_runs(self):
          pass
      class TestPytest:
        def test_omitted(self):
          pass
    """])
    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("TestPytest.test_omitted", result.stdout)

  def test_supported_module_cannot_hide_unsupported_module(self):
    result = self.run_runner(["""
      import unittest
      class TestSupported(unittest.TestCase):
        def test_runs(self):
          pass
    """, """
      class TestPytest:
        def test_omitted(self):
          pass
    """])
    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("TestSupported.test_runs", result.stdout)
    self.assertIn("TestPytest.test_omitted", result.stdout)

  def test_pytest_non_tests_do_not_block_unittest(self):
    result = self.run_runner(["""
      import unittest
      def test_helper():
        pass
      test_helper.__test__ = False
      class TestHelper:
        __test__ = False
        def test_helper(self):
          pass
      class TestSupported(unittest.TestCase):
        def test_runs(self):
          pass
    """])
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("1 passed", result.stdout)
