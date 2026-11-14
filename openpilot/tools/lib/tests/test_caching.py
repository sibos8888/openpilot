import http.server
import os
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.parameterized import parameterized
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.test.helpers import http_server_context
from openpilot.common.hardware.hw import Paths
from openpilot.tools.lib.url_file import URLFile, prune_cache
import openpilot.tools.lib.url_file as url_file_module


class CachingTestRequestHandler(http.server.BaseHTTPRequestHandler):
  FILE_EXISTS = True

  def do_GET(self):
    if self.FILE_EXISTS:
      self.send_response(206 if "Range" in self.headers else 200, '1234')
    else:
      self.send_response(404)
    self.end_headers()

  def do_HEAD(self):
    if self.FILE_EXISTS:
      self.send_response(200)
      self.send_header("Content-Length", "4")
    else:
      self.send_response(404)
    self.end_headers()


def host():
  with http_server_context(handler=CachingTestRequestHandler) as (host, port):
    yield f"http://{host}:{port}"

class TestFileDownload(OpenpilotTestCase):

  def compare_loads(self, url, start=0, length=None):
    """Compares range between cached and non cached version"""
    file_cached = URLFile(url, cache=True)
    file_downloaded = URLFile(url, cache=False)

    file_cached.seek(start)
    file_downloaded.seek(start)

    assert file_cached.get_length() == file_downloaded.get_length()
    assert length + start if length is not None else 0 <= file_downloaded.get_length()

    response_cached = file_cached.read(ll=length)
    response_downloaded = file_downloaded.read(ll=length)

    assert response_cached == response_downloaded

    # Now test with cache in place
    file_cached = URLFile(url, cache=True)
    file_cached.seek(start)
    response_cached = file_cached.read(ll=length)

    assert file_cached.get_length() == file_downloaded.get_length()
    assert response_cached == response_downloaded

  def test_small_file(self):
    # Make sure we don't force cache
    os.environ.pop("DISABLE_FILEREADER_CACHE", None)
    small_file_url = "https://raw.githubusercontent.com/commaai/openpilot/master/docs/SAFETY.md"
    #  If you want large file to be larger than a chunk
    #  large_file_url = "https://commadataci.blob.core.windows.net/openpilotci/0375fdf7b1ce594d/2019-06-13--08-32-25/3/fcamera.hevc"

    #  Load full small file
    self.compare_loads(small_file_url)

    file_small = URLFile(small_file_url)
    length = file_small.get_length()

    self.compare_loads(small_file_url, length - 100, 100)
    self.compare_loads(small_file_url, 50, 100)

    #  Load small file 100 bytes at a time
    for i in range(length // 100):
      self.compare_loads(small_file_url, 100 * i, 100)

  def test_large_file(self):
    large_file_url = "https://commadataci.blob.core.windows.net/openpilotci/0375fdf7b1ce594d/2019-06-13--08-32-25/3/qlog.bz2"
    #  Load the end 100 bytes of both files
    file_large = URLFile(large_file_url)
    length = file_large.get_length()

    self.compare_loads(large_file_url, length - 100, 100)
    self.compare_loads(large_file_url)

  @parameterized.expand([True, False], names=("cache_enabled",))
  def test_recover_from_missing_file(self, host, cache_enabled):
    if cache_enabled:
      os.environ.pop("DISABLE_FILEREADER_CACHE", None)
    else:
      os.environ["DISABLE_FILEREADER_CACHE"] = "1"

    file_url = f"{host}/test.png"

    CachingTestRequestHandler.FILE_EXISTS = False
    length = URLFile(file_url).get_length()
    assert length == -1

    CachingTestRequestHandler.FILE_EXISTS = True
    length = URLFile(file_url).get_length()
    assert length == 4


class TestCache(OpenpilotTestCase):
  def test_prune_cache(self, monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
      monkeypatch.setattr(Paths, 'download_cache_root', staticmethod(lambda: tmpdir + "/"))

      # setup test files and manifest
      manifest_lines = []
      for i in range(3):
        fname = f"hash_{i}"
        with open(tmpdir + "/" + fname, "wb") as f:
          f.truncate(1000)
        manifest_lines.append(f"{fname} {1000 + i}")
      with open(tmpdir + "/manifest.txt", "w") as f:
        f.write('\n'.join(manifest_lines))

      # under limit, shouldn't prune
      assert len(os.listdir(tmpdir)) == 4
      prune_cache()
      assert len(os.listdir(tmpdir)) == 4

      # set a tiny cache limit to force eviction (1.5 chunks worth)
      monkeypatch.setattr(url_file_module, 'CACHE_SIZE', url_file_module.CHUNK_SIZE + url_file_module.CHUNK_SIZE // 2)

      # prune_cache should evict oldest files to get under limit
      prune_cache()
      remaining = os.listdir(tmpdir)
      # should have evicted at least one file + manifest
      assert len(remaining) < 4
      # newest file should remain
      assert manifest_lines[2].split()[0] in remaining


class BinaryRangeRequestHandler(http.server.BaseHTTPRequestHandler):
  DATA = bytes(range(64)) + b"A\rB\nC\r\n--"

  def log_message(self, format, *args):
    pass

  def do_HEAD(self):
    self.send_response(200)
    self.send_header("Content-Length", str(len(self.DATA)))
    self.end_headers()

  def do_GET(self):
    ranges = [tuple(int(value) for value in item.split("-")) for item in self.headers["Range"].removeprefix("bytes=").split(",")]
    if self.path == "/ignore-ranges":
      status, body, content_type = 200, self.DATA, "application/octet-stream"
    elif len(ranges) == 1:
      start, end = ranges[0]
      status, body, content_type = 206, self.DATA[start:end + 1], "application/octet-stream"
      self.send_response(status)
      self.send_header("Content-Range", f"bytes {start}-{min(end, len(self.DATA) - 1)}/{len(self.DATA)}")
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)
      return
    else:
      boundary = b"BinaryRange-Aa"
      status, content_type = 206, 'multipart/byteranges; boundary="BinaryRange-Aa"'
      body = b"".join(
        b"--" + boundary + b"\r\nContent-Type: application/octet-stream\r\n"
        + f"Content-Range: bytes {start}-{end}/{len(self.DATA)}\r\n\r\n".encode()
        + self.DATA[start:end + 1] + b"\r\n"
        for start, end in ranges
      ) + b"--" + boundary + b"--\r\n"
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


class TestHTTPRangeIntegrity(unittest.TestCase):
  def setUp(self):
    host, port = self.enterContext(http_server_context(handler=BinaryRangeRequestHandler))
    self.base_url = f"http://{host}:{port}"

  def test_full_response_is_sliced_for_each_range(self):
    reader = URLFile(self.base_url + "/ignore-ranges", cache=False)
    self.assertEqual(reader.get_multi_range([(3, 9), (20, 31)]), [bytes(range(3, 9)), bytes(range(20, 31))])

  def test_uncached_seek_and_read_with_full_response(self):
    reader = URLFile(self.base_url + "/ignore-ranges", cache=False)
    reader.seek(9)
    self.assertEqual(reader.read(7), bytes(range(9, 16)))
    self.assertEqual(reader.tell(), 16)
    self.assertEqual(reader.read(), BinaryRangeRequestHandler.DATA[16:])
    self.assertEqual(reader.tell(), len(BinaryRangeRequestHandler.DATA))

  def test_full_response_is_cached_in_correct_chunks(self):
    with tempfile.TemporaryDirectory() as directory, \
         patch.object(Paths, "download_cache_root", return_value=directory + "/"), \
         patch.object(url_file_module, "CHUNK_SIZE", 16):
      reader = URLFile(self.base_url + "/ignore-ranges", cache=True)
      reader.seek(13)
      self.assertEqual(reader.read(29), bytes(range(13, 42)))
      self.assertEqual(reader.tell(), 42)
      cached = URLFile(self.base_url + "/ignore-ranges", cache=True)
      cached.seek(13)
      with patch.object(cached, "_request", side_effect=AssertionError("cached read contacted HTTP server")):
        self.assertEqual(cached.read(29), bytes(range(13, 42)))
      self.assertEqual(cached.tell(), 42)

  def test_partial_response_keeps_range_semantics(self):
    reader = URLFile(self.base_url + "/ranges", cache=False)
    reader.seek(5)
    self.assertEqual(reader.read(8), bytes(range(5, 13)))
    self.assertEqual(reader.tell(), 13)

  def test_multipart_preserves_binary_endings(self):
    reader = URLFile(self.base_url + "/ranges", cache=False)
    self.assertEqual(reader.get_multi_range([(64, 66), (66, 68), (68, 71), (71, 73)]), [b"A\r", b"B\n", b"C\r\n", b"--"])
