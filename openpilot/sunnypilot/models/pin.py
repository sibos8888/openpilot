"""Read, write, and compare tinygrad revision sidecars for model pkls.

These helpers are not part of the production model-loading path.
modeld selects an existing COMBINED_MODEL_PKL override or the built-in pkl
and loads it without checking a .tinygrad_pin sidecar. A pin mismatch does
not trigger a fallback to the built-in model.
"""

from pathlib import Path

from openpilot.sunnypilot.models.tinygrad_ref import get_tinygrad_ref

PIN_SUFFIX = ".tinygrad_pin"


def device_tinygrad_ref() -> str | None:
  """The tinygrad revision this tree runs on. None when it can't be resolved."""
  try:
    return get_tinygrad_ref() or None
  except Exception:
    return None


def read_pkl_pin(pkl_path: str | Path) -> str | None:
  """Pin recorded by the producer for this pkl. None when absent/unreadable."""
  try:
    with open(str(pkl_path) + PIN_SUFFIX) as f:
      return f.read().strip() or None
  except OSError:
    return None


def pkl_pin_compatible(pkl_path: str | Path) -> bool | None:
  """True/False when a sidecar pin exists, None when unknown (no sidecar)."""
  pkl_pin = read_pkl_pin(pkl_path)
  device_ref = device_tinygrad_ref()
  if pkl_pin is None or device_ref is None:
    return None
  return pkl_pin == device_ref


def write_pkl_pin(pkl_path: str | Path, tinygrad_ref: str) -> None:
  with open(str(pkl_path) + PIN_SUFFIX, "w") as f:
    f.write(tinygrad_ref)
