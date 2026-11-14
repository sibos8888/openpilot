#!/usr/bin/env python3
import json
import os
from pathlib import Path
import pty
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
RECORDER = '''import json, os, sys
from pathlib import Path
with open(os.environ['OP_LOG'], 'a') as output:
  output.write(json.dumps({'command': Path(sys.argv[0]).name, 'cwd': os.getcwd(), 'args': sys.argv[1:]}) + '\\n')
'''


def executable(path, source):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(source)
  path.chmod(0o755)


class TestOpShell(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.base = Path(self.temporary.name).resolve()
    self.repo = self.base / 'checkout with spaces'
    self.repo.mkdir()
    self.outside = self.base / 'outside'
    self.version = self.base / 'VERSION'
    self.version.write_text('fixture')
    self.outside.mkdir()
    self.bin = self.base / 'bin'
    self.bin.mkdir()
    self.log = self.base / 'commands.jsonl'
    self.real_git = shutil.which('git')
    assert self.real_git is not None
    self.agnos = self.base / 'AGNOS'
    self.env = dict(os.environ)
    for name in ('DRY', 'NO_VERIFY', 'VERBOSE', 'GITHUB_PATH', 'BASH_ENV', 'OPENPILOT_ROOT', 'PYTHONPATH'):
      self.env.pop(name, None)
    self.env.update(HOME=str(self.base), SHELL='/bin/bash', OSTYPE='darwin', OP_LOG=str(self.log),
                    PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}')
    executable(self.bin / 'scons', f'#!{sys.executable}\n{RECORDER}')
    executable(self.bin / 'sudo', f'#!{sys.executable}\n{RECORDER}')
    executable(self.bin / 'git', f'''#!{sys.executable}
import sys
if sys.argv[1:] == ["config", "--file", ".gitmodules", "--get-regexp", "path"]: sys.exit(0)
raise SystemExit("unexpected git operation in offline fixture")
''')
    self.populate(self.repo)

  def populate(self, repo):
    (repo / 'tools').mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'tools/op.sh').read_text().replace('"/AGNOS"', json.dumps(str(self.agnos))).replace('/VERSION', str(self.version))
    executable(repo / 'tools/op.sh', source)
    (repo / 'launch_openpilot.sh').touch()
    (repo / 'launch_env.sh').write_text('AGNOS_VERSION=fixture\n')
    (repo / '.gitmodules').touch()
    chunker = repo / 'openpilot/common/file_chunker.py'
    chunker.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'openpilot/common/file_chunker.py', chunker)
    self.model = repo / 'openpilot/selfdrive/modeld/models/driving_supercombo.onnx'
    self.model.parent.mkdir(parents=True, exist_ok=True)
    self.model.write_bytes(b'complete model')
    executable(repo / 'openpilot/system/manager/build.py', f'#!{sys.executable}\n{RECORDER}')
    executable(repo / 'scripts/lint/lint.sh', f'#!{sys.executable}\n{RECORDER}')
    executable(repo / 'scripts/post-commit', (ROOT / 'scripts/post-commit').read_text())

  def invoke(self, *args, cwd=None, repo=None):
    return subprocess.run(['bash', str((repo or self.repo) / 'tools/op.sh'), *map(str, args)],
                          cwd=cwd or self.repo, env=self.env, capture_output=True, text=True, timeout=30)

  def records(self):
    return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

  def assert_success(self, result):
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_build_uses_target_root_for_external_calls_and_keeps_internal_subdirectory(self):
    subdirectory = self.repo / 'openpilot/selfdrive'
    for cwd, options, expected in ((self.repo, [], self.repo), (subdirectory, [], subdirectory),
                                   (self.outside, [], self.repo), (self.outside, ['--dir', self.repo], self.repo)):
      with self.subTest(cwd=cwd, options=options):
        self.assert_success(self.invoke(*options, 'build', '-j2', cwd=cwd))
        self.assertEqual(self.records()[-1], {'command': 'scons', 'cwd': str(expected), 'args': ['-u', '-j2']})

  def test_explicit_other_checkout_wins_and_invalid_directory_fails(self):
    other = self.base / 'other checkout'
    self.populate(other)
    self.assert_success(self.invoke('--dir', other, 'build', cwd=self.repo))
    self.assertEqual(self.records()[-1]['cwd'], str(other))
    count = len(self.records())
    self.assertNotEqual(self.invoke('--dir', self.outside, 'build').returncode, 0)
    self.assertEqual(len(self.records()), count)

  def test_no_verify_still_selects_repository_for_external_build(self):
    self.assert_success(self.invoke('--no-verify', 'build', cwd=self.outside))
    self.assertEqual(self.records()[-1]['cwd'], str(self.repo))

  def test_agnos_build_uses_absolute_entry_from_subdirectory_and_outside(self):
    self.agnos.touch()
    for cwd in (self.repo / 'openpilot/selfdrive', self.outside):
      with self.subTest(cwd=cwd):
        self.assert_success(self.invoke('build', '--flag', cwd=cwd))
        self.assertEqual(self.records()[-1]['command'], 'build.py')
        self.assertEqual(self.records()[-1]['args'], ['--flag'])

  def test_restart_reaches_existing_service_restart(self):
    self.agnos.touch()
    self.assert_success(self.invoke('restart'))
    self.assertEqual(self.records()[-1]['command'], 'sudo')
    self.assertEqual(self.records()[-1]['args'], ['systemctl', 'restart', 'comma'])

  def test_model_layouts_accept_complete_or_all_chunks_and_reject_missing_inputs(self):
    manifest = Path(f'{self.model}.chunkmanifest')
    first = Path(f'{self.model}.chunk01of02')
    second = Path(f'{self.model}.chunk02of02')
    manifest.write_text('broken')
    self.assert_success(self.invoke('check'))
    self.model.unlink()
    self.assertNotEqual(self.invoke('check').returncode, 0)
    manifest.write_text('2')
    first.write_bytes(b'first')
    self.assertNotEqual(self.invoke('check').returncode, 0)
    second.write_bytes(b'second')
    self.assert_success(self.invoke('check'))
    manifest.write_text('0')
    self.assertNotEqual(self.invoke('check').returncode, 0)
    manifest.unlink()
    self.assertNotEqual(self.invoke('check').returncode, 0)

  def git(self, *args, cwd=None):
    assert self.real_git is not None
    return subprocess.run([self.real_git, *map(str, args)], cwd=cwd or self.repo,
                          capture_output=True, text=True, check=True, timeout=30)

  def test_hook_installation_and_execution_in_checkout_and_linked_worktree(self):
    self.assertIsNotNone(self.real_git)
    (self.bin / 'git').unlink()
    self.git('init')
    self.git('config', 'user.name', 'Offline fixture')
    self.git('config', 'user.email', 'offline@example.invalid')
    self.git('add', '.')
    self.git('-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'fixture')
    hooks = Path(self.git('rev-parse', '--path-format=absolute', '--git-path', 'hooks').stdout.strip())
    executable(hooks / 'post-commit', f'#!{sys.executable}\n{RECORDER}')
    self.assert_success(self.invoke('post-commit'))
    self.assertEqual((hooks / 'post-commit').resolve(), self.repo / 'scripts/post-commit')
    linked = self.base / 'linked worktree'
    self.git('worktree', 'add', '-b', 'offline-linked', linked)
    for checkout in (self.repo, linked):
      with self.subTest(checkout=checkout):
        self.assert_success(self.invoke('post-commit', cwd=checkout, repo=checkout))
        actual_hooks = Path(self.git('rev-parse', '--path-format=absolute', '--git-path', 'hooks', cwd=checkout).stdout.strip())
        self.assertEqual((actual_hooks / 'post-commit').resolve(), checkout / 'scripts/post-commit')
        count = len(self.records())
        result = subprocess.run([str(actual_hooks / 'post-commit')], cwd=checkout, env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assert_success(result)
        effects = self.records()[count:]
        self.assertEqual([effect['command'] for effect in effects], ['post-commit', 'lint.sh'])
        self.assertTrue(all(effect['cwd'] == str(checkout) for effect in effects))
        self.assertEqual(effects[-1]['args'], ['--fast'])


class TestSetupShell(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.base = Path(self.temporary.name).resolve()
    self.bin = self.base / 'bin'
    self.bin.mkdir()
    self.log = self.base / 'install.jsonl'
    self.env = dict(os.environ)
    for name in ('OPENPILOT_ROOT', 'INTERACTIVE', 'SKIP_GIT_CLONE', 'BASH_ENV'):
      self.env.pop(name, None)
    self.env.update(PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', OP_LOG=str(self.log))
    self.op_source = f'#!{sys.executable}\n{RECORDER}'
    executable(self.bin / 'git', f'''#!{sys.executable}
{RECORDER}
if sys.argv[1] != "clone": raise SystemExit("unexpected git operation")
if os.environ.get("FAIL_CLONE"): raise SystemExit(1)
root = Path(sys.argv[-1])
(root / "tools").mkdir(parents=True, exist_ok=True)
(root / "launch_openpilot.sh").touch()
(root / "tools/op.sh").write_text({self.op_source!r})
(root / "tools/op.sh").chmod(0o755)
print("offline clone complete")
''')

  def invoke(self, interactive=False, answer=b'\n'):
    command = ['bash', str(ROOT / 'tools/setup.sh')]
    if interactive:
      master, slave = pty.openpty()
      try:
        attributes = termios.tcgetattr(slave)
        # Unread echo on this input-only PTY can block bash's tcsetattr.
        attributes[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        with subprocess.Popen(command, cwd=self.base, env=self.env, stdin=slave,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              start_new_session=True) as process:
          try:
            os.close(slave)
            slave = None
            os.write(master, answer)
            stdout, stderr = process.communicate(timeout=30)
          finally:
            os.close(master)
            master = None
            try:
              os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
              pass
            process.communicate()
          return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
      finally:
        if master is not None:
          os.close(master)
        if slave is not None:
          os.close(slave)
    return subprocess.run(command, cwd=self.base, env=self.env, input='', capture_output=True, text=True, timeout=30)

  def records(self):
    return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

  def test_unset_variables_clone_and_install_noninteractively(self):
    result = self.invoke()
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual([entry['args'][0] for entry in self.records()], ['clone', 'post-commit', 'setup'])

  def test_existing_checkout_with_spaces_skips_clone(self):
    destination = self.base / 'existing checkout'
    destination.mkdir()
    (destination / 'launch_openpilot.sh').touch()
    executable(destination / 'tools/op.sh', self.op_source)
    self.env['OPENPILOT_ROOT'] = str(destination)
    result = self.invoke()
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual([entry['args'][0] for entry in self.records()], ['post-commit', 'setup'])

  def test_empty_existing_directory_is_cloned(self):
    (self.base / 'openpilot').mkdir()
    result = self.invoke()
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual(self.records()[0]['args'][0], 'clone')

  def test_interactive_default_path(self):
    result = self.invoke(interactive=True)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual([entry['args'][0] for entry in self.records()], ['clone', 'post-commit', 'setup'])

  def test_interactive_existing_checkout_accepts_or_declines(self):
    destination = self.base / 'openpilot'
    destination.mkdir()
    (destination / 'launch_openpilot.sh').touch()
    executable(destination / 'tools/op.sh', self.op_source)
    self.assertNotEqual(self.invoke(interactive=True, answer=b'\nn\n').returncode, 0)
    self.assertEqual(self.records(), [])
    result = self.invoke(interactive=True, answer=b'\ny\n')
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual([entry['args'][0] for entry in self.records()], ['post-commit', 'setup'])

  def test_failed_clone_does_not_install(self):
    self.env['FAIL_CLONE'] = '1'
    self.assertNotEqual(self.invoke().returncode, 0)
    self.assertEqual([entry['args'][0] for entry in self.records()], ['clone'])

  def test_nonempty_invalid_destination_does_not_clone_or_install(self):
    destination = self.base / 'openpilot'
    destination.mkdir()
    (destination / 'user-file').touch()
    self.assertNotEqual(self.invoke().returncode, 0)
    self.assertEqual(self.records(), [])


class TestIdeEntrypoints(unittest.TestCase):
  def test_ui_and_replay_configs_reference_existing_sources(self):
    for filename in ('Build_BIG_UI.run.xml', 'Build_SMALL_UI.run.xml'):
      configuration = ET.parse(ROOT / '.run' / filename)
      option = configuration.find('.//option[@name="SCRIPT_NAME"]')
      assert option is not None
      script = option.attrib['value']
      self.assertTrue(Path(script.replace('$ProjectFileDir$', str(ROOT))).is_file(), script)
    configuration = json.loads((ROOT / '.vscode/launch.json').read_text())
    process = next(item for item in configuration['inputs'] if item['id'] == 'python_process')
    self.assertIn('openpilot/selfdrive/ui/ui.py', process['options'])
    replay = next(item for item in configuration['configurations'] if item['name'] == 'Replay drive')
    self.assertTrue(Path(replay['program'].replace('${workspaceFolder}', str(ROOT))).is_file())
    self.assertFalse(any(item['name'] == 'C++: openpilot Process' for item in configuration['configurations']))


if __name__ == '__main__':
  unittest.main()
