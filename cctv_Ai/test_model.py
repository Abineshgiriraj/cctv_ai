from ultralytics import YOLO

model = YOLO("yolov8n.pt")

print("YOLO model loaded successfully")
print()
print("Available classes:")

for class_id, name in model.names.items():
    print(class_id, name)