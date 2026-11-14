from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

from openpilot.sunnypilot.tools import pull_footage


class TestPullFootageRoute(unittest.TestCase):
  def test_route_and_segment_selectors_reach_download(self):
    route_id = "5beb9b58bd12b691/0000010a--a51155e496"
    pipe_id = route_id.replace("/", "|")
    cases = (
      (route_id, route_id, None, [0, 1, 2]),
      (pipe_id, pipe_id, None, [0, 1, 2]),
      (route_id + "/0:2", route_id, "0:2", [0, 1]),
      (pipe_id + "/1", pipe_id, "1", [1]),
      (route_id + "/:2", route_id, ":2", [0, 1]),
    )
    for source, expected_route, expected_range, expected_segments in cases:
      with self.subTest(source=source), redirect_stdout(io.StringIO()):
        segments = [{"num": index, "src": f"http://unused/{index}", "cam": "fcamera.hevc"} for index in range(3)]
        with patch.object(pull_footage.sys, "argv", ["pull_footage.py", source]), \
             patch.object(pull_footage, "get_segments", return_value=segments) as get_segments, \
             patch.object(pull_footage, "download", side_effect=lambda segment, directory: f"{directory}/{segment['num']}_fcamera.hevc") as download, \
             patch.object(pull_footage, "mux") as mux, \
             patch.object(pull_footage.os, "makedirs"), \
             patch.object(pull_footage.shutil, "rmtree"):
          pull_footage.main()
        get_segments.assert_called_once_with(source, expected_route, "fcamera.hevc", expected_range)
        self.assertEqual([call.args[0]["num"] for call in download.call_args_list], expected_segments)
        self.assertEqual(len(mux.call_args.args[0]), len(expected_segments))
