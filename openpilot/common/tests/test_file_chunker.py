import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpilot.common.file_chunker import (
  ChunkStream, chunked_file_exists, get_chunk_name, get_existing_chunks, get_manifest_path, open_file_chunked,
)


class TestFileChunker(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.path = Path(directory.name) / "model.pkl"

  def write_chunks(self):
    manifest = Path(get_manifest_path(self.path))
    manifest.write_text("2")
    chunks = [Path(get_chunk_name(self.path, i, 2)) for i in range(2)]
    chunks[0].write_bytes(b"chunk-one")
    chunks[1].write_bytes(b"chunk-two")
    return [str(manifest), *(str(p) for p in chunks)]

  def test_missing_file(self):
    self.assertFalse(chunked_file_exists(self.path))
    with self.assertRaises(FileNotFoundError):
      open_file_chunked(self.path)

  def test_complete_file(self):
    self.path.write_bytes(b"complete")
    self.assertEqual(get_existing_chunks(self.path), [self.path])
    self.assertTrue(chunked_file_exists(self.path))
    with open_file_chunked(self.path) as stream:
      self.assertEqual(stream.read(), b"complete")

  def test_chunked_file(self):
    paths = self.write_chunks()
    self.assertEqual(get_existing_chunks(self.path), paths)
    self.assertTrue(chunked_file_exists(self.path))
    with open_file_chunked(self.path) as stream:
      self.assertEqual(stream.read(), b"chunk-onechunk-two")

  def test_complete_file_precedes_chunks(self):
    self.write_chunks()
    self.path.write_bytes(b"complete")
    self.assertEqual(get_existing_chunks(self.path), [self.path])
    self.assertTrue(chunked_file_exists(self.path))
    with open_file_chunked(self.path) as stream:
      self.assertEqual(stream.read(), b"complete")

  def test_complete_file_precedes_bad_manifest(self):
    self.path.write_bytes(b"complete")
    Path(get_manifest_path(self.path)).write_text("broken")
    self.assertEqual(get_existing_chunks(self.path), [self.path])
    self.assertTrue(chunked_file_exists(self.path))
    with open_file_chunked(self.path) as stream:
      self.assertEqual(stream.read(), b"complete")

  def test_missing_chunk_preserves_build_dependencies(self):
    paths = self.write_chunks()
    Path(paths[-1]).unlink()
    self.assertEqual(get_existing_chunks(self.path), paths)
    self.assertFalse(chunked_file_exists(self.path))
    with self.assertRaises(FileNotFoundError), open_file_chunked(self.path) as stream:
      stream.read()

  def test_bad_manifest(self):
    for content in (b"broken", b"0", b"-1", b"\xff"):
      with self.subTest(content=content):
        Path(get_manifest_path(self.path)).write_bytes(content)
        self.assertFalse(chunked_file_exists(self.path))
        with self.assertRaises(ValueError):
          get_existing_chunks(self.path)
        with self.assertRaises(ValueError):
          open_file_chunked(self.path)

  def test_unreadable_chunk(self):
    self.write_chunks()
    with mock.patch("openpilot.common.file_chunker.open", side_effect=PermissionError):
      self.assertFalse(chunked_file_exists(self.path))

  def test_partial_read_closes_current_file(self):
    self.path.write_bytes(b"x" * (io.DEFAULT_BUFFER_SIZE * 2))
    opened = []
    real_open = open

    def track_open(*args, **kwargs):
      stream = real_open(*args, **kwargs)
      opened.append(stream)
      return stream

    with mock.patch("openpilot.common.file_chunker.open", side_effect=track_open):
      stream = open_file_chunked(self.path)
      self.assertEqual(stream.read(1), b"x")
      self.assertEqual(len(opened), 1)
      self.assertFalse(opened[0].closed)
      stream.close()
      stream.close()
      self.assertTrue(opened[0].closed)

  def test_exception_closes_current_file(self):
    self.path.write_bytes(b"x" * (io.DEFAULT_BUFFER_SIZE * 2))
    opened = []
    real_open = open

    def track_open(*args, **kwargs):
      stream = real_open(*args, **kwargs)
      opened.append(stream)
      return stream

    with mock.patch("openpilot.common.file_chunker.open", side_effect=track_open):
      with self.assertRaises(RuntimeError), open_file_chunked(self.path) as stream:
        self.assertEqual(stream.read(1), b"x")
        raise RuntimeError("consumer failed")
      self.assertTrue(opened[0].closed)

  def test_closed_raw_stream_rejects_reads(self):
    self.path.write_bytes(b"data")
    stream = ChunkStream([self.path])
    stream.close()
    with self.assertRaises(ValueError):
      stream.readinto(bytearray(1))
