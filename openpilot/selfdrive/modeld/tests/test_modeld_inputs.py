from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.common.file_chunker import get_chunk_name, get_manifest_path, open_file_chunked
from openpilot.selfdrive.modeld import modeld


@pytest.mark.parametrize(('complete', 'manifest', 'chunks', 'expected'), [
  (True, None, False, b'complete'),
  (False, '2', True, b'chunked'),
  (True, '2', False, b'complete'),
  (True, 'invalid', False, b'complete'),
  (True, '2', True, b'complete'),
  (False, '2', False, b'builtin'),
  (False, 'invalid', False, b'builtin'),
  (False, None, False, b'builtin'),
])
def test_model_selection_loads_available_artifact(tmp_path, monkeypatch, complete, manifest, chunks, expected):
  from pathlib import Path

  override = tmp_path / 'override.pkl'
  builtin = tmp_path / 'builtin.pkl'
  builtin.write_bytes(b'builtin')
  if complete:
    override.write_bytes(b'complete')
  if manifest is not None:
    Path(get_manifest_path(override)).write_text(manifest)
  if chunks:
    Path(get_chunk_name(override, 0, 2)).write_bytes(b'chunk')
    Path(get_chunk_name(override, 1, 2)).write_bytes(b'ed')
  monkeypatch.setenv('COMBINED_MODEL_PKL', str(override))
  monkeypatch.setattr(modeld, 'modeld_pkl_path', lambda: builtin)

  with open_file_chunked(modeld._find_driving_pkl()) as artifact:
    assert artifact.read() == expected


def test_model_selection_reports_missing_builtin(tmp_path, monkeypatch):
  monkeypatch.delenv('COMBINED_MODEL_PKL', raising=False)
  monkeypatch.setattr(modeld, 'modeld_pkl_path', lambda: tmp_path / 'missing.pkl')
  with pytest.raises(modeld.ModelUnavailable):
    modeld._find_driving_pkl()


@pytest.mark.parametrize(('learning', 'expected_delay'), [(False, 0.42), (True, 0.18)])
@pytest.mark.parametrize('lateral_control_params', [False, True])
def test_main_passes_selected_delay_to_model(monkeypatch, learning, expected_delay, lateral_control_params):
  class FrameCaptured(Exception):
    pass

  captured = {}

  def run(bufs, transforms, inputs):
    captured.update(inputs)
    raise FrameCaptured

  model = SimpleNamespace(vision_input_names=['img', 'big_img'],
                          npy={'lateral_control_params': None} if lateral_control_params else {}, run=run)
  params = SimpleNamespace(get_bool=lambda key: learning if key == 'LagdToggle' else False,
                           get=lambda key, **kwargs: '0.42' if key == 'LagdValueCache' else None)

  class Camera:
    width, height, buffer_len = 1344, 760, 1
    frame_id, timestamp_sof, timestamp_eof = 1, 1_000_000_000, 1_020_000_000

    @staticmethod
    def available_streams(*args, **kwargs):
      return [modeld.VisionStreamType.VISION_STREAM_WIDE_ROAD]

    def __init__(self, *args):
      pass

    def connect(self, *args):
      return True

    def recv(self):
      return object()

  class Messages:
    updated = {'extrinsicsCalibration': False}

    def __init__(self, *args):
      pass

    def update(self, *args):
      pass

    def __getitem__(self, key):
      return {
        'narrowRoadCameraState': SimpleNamespace(frameId=1),
        'carState': SimpleNamespace(vEgo=12.),
        'lateralDelay': SimpleNamespace(lateralDelay=0.18),
      }[key]

  monkeypatch.setattr(modeld, 'Params', lambda: params)
  monkeypatch.setattr(modeld, 'config_realtime_process', lambda *args: None)
  monkeypatch.setattr(modeld, 'VisionIpcClient', Camera)
  monkeypatch.setattr(modeld, 'ModelState', lambda *args: model)
  monkeypatch.setattr(modeld, 'set_offroad_alert', lambda *args, **kwargs: None)
  monkeypatch.setattr(modeld, 'PubMaster', lambda *args: None)
  monkeypatch.setattr(modeld, 'SubMaster', Messages)
  monkeypatch.setattr(modeld, 'BigReplyLatch', lambda *args, **kwargs: None)
  monkeypatch.setattr(modeld, 'get_demo_car_params', lambda: SimpleNamespace(longitudinalActuatorDelay=0.25, brand='toyota'))
  monkeypatch.setattr(modeld, 'DesireHelper', lambda: SimpleNamespace(desire=0))
  monkeypatch.setattr(modeld, 'RoadEdgeLaneChangeController', lambda: None)

  with pytest.raises(FrameCaptured):
    modeld.main(demo=True)

  np.testing.assert_allclose(captured['action_t'], [expected_delay + 0.075, 0.625], rtol=1e-6)
  if lateral_control_params:
    np.testing.assert_allclose(captured['lateral_control_params'], [12., expected_delay], rtol=1e-6)
  else:
    assert 'lateral_control_params' not in captured
