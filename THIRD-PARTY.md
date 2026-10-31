# Third-party models and libraries

Every piece allows commercial use. The models ship in `models/`, unchanged.

| What | Used for | Licence | Source |
|---|---|---|---|
| YuNet face detector (`face_detection_yunet_2023mar.onnx`) | finding faces | MIT | [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) |
| MediaPipe Face Landmarker (`face_landmarker.task`) | eyes open/closed and smile scores (blendshapes) | Apache-2.0 | [Google AI Edge](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) |
| MediaPipe Image Embedder, MobileNet V3 small (`mobilenet_v3_small.tflite`) | spotting look-alike shots | Apache-2.0 | [Google AI Edge](https://ai.google.dev/edge/mediapipe/solutions/vision/image_embedder) |
| SFace face recognizer (`face_recognition_sface_2021dec.onnx`) | "exclude a person": finding the same face again. Downloaded on first use (39 MB, SHA-256 checked), not shipped | Apache-2.0 | [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) |
| MediaPipe | runs the two models above | Apache-2.0 | [google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe) |
| OpenCV | image maths, runs YuNet | Apache-2.0 | [opencv/opencv](https://github.com/opencv/opencv) |
| rawpy / LibRaw | reading the preview inside RAW files | MIT / LGPL-2.1 or CDDL-1.0 | [letmaik/rawpy](https://github.com/letmaik/rawpy) |
| Pillow, pillow-heif | JPEG and HEIC | MIT-CMU, BSD-3 (libheif LGPL-3.0) | |
| ExifRead | capture time, camera | BSD-3 | |
| NumPy | maths | BSD-3 | |
| Starlette, Uvicorn | the local web page | BSD-3 | |
| truststore | HTTPS checked against the computer's own certificates | MIT | [sethmlarson/truststore](https://github.com/sethmlarson/truststore) |
| uv (installer only) | installs Python and the libraries | MIT / Apache-2.0 | [astral-sh/uv](https://github.com/astral-sh/uv) |

The test photos are generated: NASA's public-domain portrait of Eileen Collins (via scikit-image's sample data),
drawn shapes, and two CC0 RAW samples from [raw.pixls.us](https://raw.pixls.us). No client photos are in this repository.
