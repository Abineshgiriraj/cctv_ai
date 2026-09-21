# CivicVision model sources

The repository does not commit `.pt` weights. `setup_ai_models.py` downloads the
following third-party pretrained models only when the local files are missing and
verifies their SHA-256 before installation.

## Helmet detection

- Repository: `iam-tsr/yolov8n-helmet-detection`
- File: `best.pt`
- Local destination: `models/helmet.pt`
- Classes: helmet / no_helmet
- License stated by the model repository: MIT
- SHA-256: `c8eb324e365cf4faeab491d9cc301535ec745171b55e8b1acadea62be5101a9d`

## Road damage

- Repository: `vinothvikas1987/pothole-detection-yolov8`
- File: `best.pt`
- Local destination: `models/road_damage.pt`
- Classes: Longitudinal Crack, Transverse Crack, Alligator Crack, Pothole, Other
- License stated by the model repository: Apache-2.0
- SHA-256: `8b8a587c021bd1a497912d59e5b379f4876655c7296399ccd3ebee608b3694f1`

## Accident and road obstruction

No dedicated accident or road-obstruction weight is required by the current
implementation. Accident candidates are derived from YOLO/ByteTrack vehicle
trajectories, proximity and sudden-stop behavior. Persistent road obstructions
use a fixed-camera foreground model and can include large branches, fallen
trees, debris or other new blockages.

These detections are operational screening signals and should be reviewed by a
human before dispatch or enforcement decisions.
