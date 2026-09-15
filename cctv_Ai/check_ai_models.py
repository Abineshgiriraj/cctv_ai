import os
from ultralytics import YOLO

MODELS = {
    "helmet": os.path.join("models", "helmet.pt"),
    "license_plate": os.path.join("models", "license_plate.pt"),
    "road_damage": os.path.join("models", "road_damage.pt"),
    "road_obstruction": os.path.join("models", "road_obstruction.pt"),
}

print("CivicVision advanced AI model check")
print("Working directory:", os.getcwd())
print()

for name, path in MODELS.items():
    exists = os.path.isfile(path)
    print(f"{name:18} {'FOUND' if exists else 'MISSING'}  {path}")
    if not exists:
        continue
    try:
        model = YOLO(path)
        print("  classes:", getattr(model, "names", {}))
    except Exception as exc:
        print("  ERROR loading model:", exc)

print()
print("Required for full advanced detection:")
print("  helmet.pt          -> helmet / no-helmet classes")
print("  license_plate.pt   -> license plate detector")
print("  road_damage.pt     -> pothole / road damage classes")
print("  road_obstruction.pt-> optional fallen-tree / obstruction classes")
