# Advanced CivicVision model slots

The stock `yolov8n.pt` model can detect person, bicycle, car, motorcycle, bus and truck. It does **not** contain reliable classes for helmet/no-helmet, pothole/road damage, or fallen-tree road obstruction.

The project now exposes configuration slots for three custom weights:

- `models/helmet.pt`
- `models/road_damage.pt`
- `models/road_obstruction.pt`

Keep model files out of Git (`*.pt` is already ignored). Put trained weights on the deployment machine and set the paths in `.env` if different.

Recommended class contracts:

- Helmet model: `helmet`, `no_helmet`
- Road damage model: `pothole`, `road_damage`, optionally `crack`
- Road obstruction model: `fallen_tree`, optionally `branch`, `road_obstruction`

A static fallen-tree/road-obstruction detector is a practical first stage. Detecting the *act of a tree falling* is a temporal event and should be built later using consecutive-frame motion/orientation logic rather than claiming it from one frame.

## Vehicle analytics already implemented

The current phase uses ByteTrack IDs and a configurable normalized horizontal counting line. A vehicle is persisted only once when its tracked center crosses the line. Counts are stored in SQLite at `data/cctv_analytics.sqlite3` and exposed through `/analytics/vehicle_counts`.
