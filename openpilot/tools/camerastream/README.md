# Camera stream

`compressed_vipc.py` decodes remote encoded camera messages and republishes them over VisionIPC.

## Requirements

The device and PC must use the same openpilot commit. The device must publish `narrowRoadEncodeData`, `cabinEncodeData`, or `wideRoadEncodeData` through the messaging bridge.

This checkout does not build or register `encoderd`. Starting `camerad` and the messaging bridge alone does not provide the encoded messages that this client requires. The commands below describe the available tools. They do not establish an end-to-end streaming setup for this checkout.

## Device tools

Run these commands from separate terminals on a device with an existing encoded-message producer.

Start the messaging bridge:

```sh
cd /data/openpilot
./openpilot/cereal/messaging/bridge
```

If the camera process is not already running, start it:

```sh
cd /data/openpilot/openpilot/system/camerad
./camerad
```

The camera encoder sources are under `/data/openpilot/openpilot/system/loggerd`. There is no supported `encoderd` launch command in this checkout.

## PC tools

Run the following commands from the repository root.

Decode the remote stream:

```sh
python openpilot/tools/camerastream/compressed_vipc.py <ip>
```

Display the local VisionIPC stream in a separate terminal:

```sh
python openpilot/selfdrive/ui/watch3.py
```

## Client arguments

```text
usage: compressed_vipc.py [-h] [--cams CAMS] [--server SERVER] [--silent] addr

Decode video streams and broadcast on VisionIPC

positional arguments:
  addr             Address of comma three

options:
  -h, --help       show this help message and exit
  --cams CAMS      Cameras to decode
  --server SERVER  choose vipc server name
  --silent         Suppress debug output
```

For camera 0 on a device that already publishes the required messages, run:

```sh
python openpilot/tools/camerastream/compressed_vipc.py comma-ffffffff --cams 0
```
