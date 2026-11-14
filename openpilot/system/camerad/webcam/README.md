# Webcam camera source on PC

## Setup openpilot
- Follow [this readme](/tools/README.md) to install and build the requirements

## Connect the hardware
- Connect the camera first
- The webcam source does not require a panda connection.

## Run the camera source

The current manager registers the native `camerad` process. It does not select this Python webcam source when `USE_WEBCAM=1` is set.

The standalone source entry point is available from the repository root:

```sh
python -m openpilot.system.camerad.webcam.camerad
```

This starts the webcam source only. It does not provide the manager integration needed for the former PC calibration and engagement workflow.

## Specify Cameras

Use the `ROAD_CAM` (default 0) and optional `DRIVER_CAM`, `WIDE_CAM` environment variables to specify which camera is which (ie. `ROAD_CAM=1` uses `/dev/video1`, on Ubuntu, for the road camera):
```
ROAD_CAM=1 python -m openpilot.system.camerad.webcam.camerad
```
