import hashlib
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"

MODELS = {
    "helmet.pt": {
        "url": "https://huggingface.co/iam-tsr/yolov8n-helmet-detection/resolve/main/best.pt",
        "sha256": "c8eb324e365cf4faeab491d9cc301535ec745171b55e8b1acadea62be5101a9d",
        "description": "YOLOv8n helmet / no_helmet detector",
    },
    "road_damage.pt": {
        "url": "https://huggingface.co/vinothvikas1987/pothole-detection-yolov8/resolve/main/best.pt",
        "sha256": "8b8a587c021bd1a497912d59e5b379f4876655c7296399ccd3ebee608b3694f1",
        "description": "YOLOv8s road distress detector (cracks, potholes and other damage)",
    },
}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, destination):
    tmp = destination.with_suffix(destination.suffix + ".download")
    request = urllib.request.Request(url, headers={"User-Agent": "CivicVision/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, open(tmp, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            received += len(chunk)
            if total:
                print(f"  {received * 100 / total:5.1f}% ({received / 1024 / 1024:.1f} MB)", end="\r")
    print()
    tmp.replace(destination)


def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0

    print("CivicVision AI model setup")
    print("Model weights are third-party pretrained weights and are not committed to Git.")
    for filename, spec in MODELS.items():
        destination = MODELS_DIR / filename
        expected = spec["sha256"].lower()

        if destination.exists():
            actual = sha256_file(destination)
            if actual == expected:
                print(f"[OK] {filename} already installed.")
                continue
            print(f"[WARN] {filename} exists but checksum differs; keeping it unchanged.")
            print(f"       Existing SHA256: {actual}")
            print("       If this is your own trained model, keep it. Otherwise rename/delete it and rerun.")
            continue

        print(f"[DOWNLOAD] {filename}: {spec['description']}")
        try:
            download(spec["url"], destination)
        except Exception as exc:
            failures += 1
            print(f"[ERROR] Unable to download {filename}: {exc}")
            continue

        actual = sha256_file(destination)
        if actual != expected:
            failures += 1
            print(f"[ERROR] SHA256 mismatch for {filename}.")
            print(f"        Expected: {expected}")
            print(f"        Actual:   {actual}")
            destination.unlink(missing_ok=True)
            continue

        print(f"[OK] Installed {destination}")

    if failures:
        print(f"Completed with {failures} failure(s).")
        return 1

    print("Required helmet and road-damage models are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
