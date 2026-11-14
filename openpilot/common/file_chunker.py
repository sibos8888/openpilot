#!/usr/bin/env python3
import io
import sys
import math
import os
from pathlib import Path

CHUNK_SIZE = 45 * 1024 * 1024  # 45MB, under GitHub's 50MB limit

def get_chunk_name(name, idx, num_chunks):
  return f"{name}.chunk{idx+1:02d}of{num_chunks:02d}"

def get_manifest_path(name):
  return f"{name}.chunkmanifest"

def _chunk_paths(path, num_chunks):
  return [get_manifest_path(path)] + [get_chunk_name(path, i, num_chunks) for i in range(num_chunks)]

def get_chunk_targets(path, file_size):
  num_chunks = math.ceil(file_size / CHUNK_SIZE)
  return _chunk_paths(path, num_chunks)

def chunk_file(path, targets):
  manifest_path, *chunk_paths = targets
  actual_num_chunks = max(1, math.ceil(os.path.getsize(path) / CHUNK_SIZE))
  assert len(chunk_paths) >= actual_num_chunks, f"Allowed {len(chunk_paths)} chunks but needs at least {actual_num_chunks}, for path {path}"
  with open(path, 'rb') as f:
    for chunk_path in chunk_paths:
      with open(chunk_path, 'wb') as out:
        out.write(f.read(CHUNK_SIZE))
  Path(manifest_path).write_text(str(len(chunk_paths)))
  os.remove(path)

def get_existing_chunks(path):
  """Return build dependencies, including the manifest and all declared chunks."""
  if os.path.isfile(path):
    return [path]
  if os.path.isfile(manifest := get_manifest_path(path)):
    num_chunks = int(Path(manifest).read_text().strip())
    if num_chunks < 1:
      raise ValueError(f"Invalid chunk count in {manifest}: {num_chunks}")
    return _chunk_paths(path, num_chunks)
  raise FileNotFoundError(path)

def chunked_file_exists(path) -> bool:
  """Return whether the complete file or every declared chunk is readable."""
  try:
    paths = get_existing_chunks(path)
    for p in paths:
      if not os.path.isfile(p):
        return False
      with open(p, 'rb'):
        pass
  except (OSError, ValueError):
    return False
  return True

class ChunkStream(io.RawIOBase):
  def __init__(self, paths):
    self._paths = iter(paths)
    self._f = None

  def readable(self):
    return True

  def close(self):
    try:
      if self._f is not None:
        self._f.close()
        self._f = None
    finally:
      super().close()

  def readinto(self, b):
    self._checkClosed()
    n = 0
    view = memoryview(b)
    while n < len(b):
      if self._f is None:
        p = next(self._paths, None)
        if p is None:
          break
        self._f = open(p, 'rb')
      count = self._f.readinto(view[n:])
      if not count:
        self._f.close()
        self._f = None
        continue
      n += count
    return n

def open_file_chunked(path):
  paths = get_existing_chunks(path)
  if paths[0] == get_manifest_path(path):
    paths = paths[1:]
  return io.BufferedReader(ChunkStream(paths))


if __name__ == "__main__":
  path = sys.argv[1]
  chunk_paths = get_chunk_targets(path, os.path.getsize(path))
  chunk_file(path, chunk_paths)
