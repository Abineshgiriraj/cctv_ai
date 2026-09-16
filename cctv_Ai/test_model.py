from ultralytics import YOLO

model = YOLO("models/helmet.pt")

results = model.predict(
    "test_rider.jpg",
    conf=0.20,
    imgsz=960,
    save=True,
    verbose=True
)

for result in results:
    if result.boxes is None or len(result.boxes) == 0:
        print("NO HELMET DETECTION FOUND")
        continue

    for box in result.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        print(model.names[cls_id], round(conf * 100, 2), "%")