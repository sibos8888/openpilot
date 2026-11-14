#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from typing import Any
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
  spec = importlib.util.spec_from_file_location(name, ROOT / 'release' / 'ci' / f'{name}.py')
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class TestModelUpload(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.model = self.root / 'model'
    self.model.mkdir()
    (self.model / 'metadata.json').write_text(json.dumps({
      'bundles': [{'models': [{'artifact': {'file_name': 'model.pkl', 'download_uri': {}}}]}],
    }))
    self.onnx = self.root / 'model.onnx'
    self.onnx.write_bytes(b'model')
    self.old = self.root / 'default_models.json'
    self.old.write_text(json.dumps({'tinygrad_ref': 'old', 'bundles': [{'display_name': 'keep-me'}]}))
    self.api = Mock()
    self.download = Mock(return_value=str(self.old))
    hub = types.ModuleType('huggingface_hub')
    hub.__dict__.update(HfApi=Mock(return_value=self.api), hf_hub_download=self.download)
    errors = types.ModuleType('huggingface_hub.errors')
    self.missing_error = type('RemoteEntryNotFoundError', (Exception,), {})
    errors.__dict__['RemoteEntryNotFoundError'] = self.missing_error
    modules = patch.dict(sys.modules, {'huggingface_hub': hub, 'huggingface_hub.errors': errors})
    modules.start()
    self.addCleanup(modules.stop)
    self.module = load_script('upload_default_model')
    self.argv = ['upload_default_model.py', '--hf-repo', 'offline/models', '--hf-defaults-path', 'defaults',
                 '--artifact-name', 'model', '--model-dir', str(self.model), '--onnx-path', str(self.onnx),
                 '--onnx-ref', '1234567890', '--model-name', 'Test model', '--tinygrad-ref', 'new', '--run-number', '7']
    self.uploaded = []
    self.api.upload_file.side_effect = lambda **kw: self.uploaded.append(json.loads(Path(kw['path_or_fileobj']).read_text()))

  def invoke(self):
    with patch.object(sys, 'argv', self.argv), contextlib.redirect_stdout(io.StringIO()):
      self.module.main()

  def test_failed_download_never_uploads(self):
    for error in (ConnectionError('offline'), PermissionError('denied'), FileNotFoundError('cache miss')):
      with self.subTest(error=type(error).__name__):
        self.download.side_effect = error
        with self.assertRaises(type(error)):
          self.invoke()
        self.api.upload_folder.assert_not_called()
        self.api.upload_file.assert_not_called()

  def test_corrupt_catalog_never_uploads(self):
    self.old.write_text('{broken')
    with self.assertRaises(json.JSONDecodeError):
      self.invoke()
    self.api.upload_folder.assert_not_called()
    self.api.upload_file.assert_not_called()

  def test_remote_missing_catalog_initializes(self):
    self.download.side_effect = self.missing_error('file is absent')
    self.invoke()
    self.assertEqual(len(self.uploaded[0]['bundles']), 1)
    self.assertEqual(self.uploaded[0]['tinygrad_ref'], 'new')
    self.api.upload_folder.assert_called_once()

  def test_existing_entries_survive_and_matching_model_is_replaced(self):
    self.invoke()
    first = self.uploaded[-1]
    self.assertEqual(first['bundles'][0]['display_name'], 'keep-me')
    self.assertEqual(len(first['bundles']), 2)
    self.old.write_text(json.dumps(first))
    self.invoke()
    self.assertEqual(len(self.uploaded[-1]['bundles']), 2)
    self.assertEqual(self.uploaded[-1]['bundles'][0]['display_name'], 'keep-me')


class TestSquashPrs(unittest.TestCase):
  def setUp(self):
    self.module = load_script('squash_and_merge_prs')
    self.pr: dict[str, Any] = {'number': 7, 'headRefName': 'feature', 'createdAt': '2026-01-01T00:00:00Z',
               'commits': {'nodes': [{'commit': {'statusCheckRollup': {'state': 'SUCCESS'}}}]}}
    self.data = {'data': {'search': {'nodes': [self.pr]}}}

  def run_cli(self, run):
    with tempfile.TemporaryDirectory() as temporary:
      script = Path(temporary) / 'squash'
      script.write_text('offline executable')
      script.chmod(0o755)
      argv = ['squash_and_merge_prs.py', '--pr-data', json.dumps(self.data), '--squash-script-path', str(script)]
      with patch.object(sys, 'argv', argv), patch.object(subprocess, 'run', side_effect=run), \
           contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        with self.assertRaises(SystemExit) as exited:
          runpy.run_path(str(ROOT / 'release/ci/squash_and_merge_prs.py'), run_name='__main__')
        return exited.exception.code

  def test_mergeability_allows_only_explicit_mergeable(self):
    for status, expected in [('MERGEABLE', True), ('CONFLICTING', False), ('UNKNOWN', False), (None, False)]:
      with self.subTest(status=status), patch.object(subprocess, 'run', return_value=Mock(stdout=json.dumps({'mergeable': status}))):
        self.assertEqual(self.module.validate_pr(self.pr)[0], expected)

  def test_gh_failures_and_bad_json_reject_validation(self):
    for result in (subprocess.CalledProcessError(1, ['gh']), Mock(stdout='{broken')):
      with self.subTest(result=result):
        with patch.object(subprocess, 'run', side_effect=result if isinstance(result, Exception) else None,
                          return_value=result):
          with self.assertRaises((subprocess.CalledProcessError, json.JSONDecodeError)):
            self.module.validate_pr(self.pr)

  def test_failed_checks_command_cannot_approve_pr(self):
    self.pr['commits']['nodes'][0]['commit']['statusCheckRollup'] = {}
    with patch.object(subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['gh'])):
      with self.assertRaises(subprocess.CalledProcessError):
        self.module.validate_pr(self.pr)

  def test_conflicting_and_unknown_prs_never_fetch_or_squash(self):
    for status in ('CONFLICTING', 'UNKNOWN'):
      commands = []

      def run(command, commands=commands, status=status, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'mergeable': status}), stderr='')

      with self.subTest(status=status):
        self.assertEqual(self.run_cli(run), 0)
        self.assertFalse(any(command[:2] == ['git', 'fetch'] for command in commands))
        self.assertTrue(all(command[0] in ('git', 'gh') for command in commands))

  def test_gh_query_failure_exits_nonzero_without_fetch(self):
    commands = []

    def run(command, **_kwargs):
      commands.append(command)
      if command[:3] == ['gh', 'pr', 'view']:
        raise subprocess.CalledProcessError(1, command)
      return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    self.assertNotEqual(self.run_cli(run), 0)
    self.assertFalse(any(command[:2] == ['git', 'fetch'] for command in commands))

  def test_git_initialization_failure_exits_nonzero(self):
    def run(command, **_kwargs):
      if command[:3] == ['git', 'branch', 'master-dev-test']:
        raise subprocess.CalledProcessError(1, command)
      return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    self.assertNotEqual(self.run_cli(run), 0)

  def test_squash_failure_exits_nonzero(self):
    def run(command, **_kwargs):
      if command[0] not in ('git', 'gh'):
        return subprocess.CompletedProcess(command, 1, stdout='squash failed', stderr='')
      return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'mergeable': 'MERGEABLE'}), stderr='')

    self.assertNotEqual(self.run_cli(run), 0)

  def test_success_and_empty_batch_exit_zero(self):
    def run(command, **_kwargs):
      return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'mergeable': 'MERGEABLE'}), stderr='')

    self.assertEqual(self.run_cli(run), 0)
    self.data['data']['search']['nodes'] = []
    self.assertEqual(self.run_cli(run), 0)


if __name__ == '__main__':
  unittest.main()
