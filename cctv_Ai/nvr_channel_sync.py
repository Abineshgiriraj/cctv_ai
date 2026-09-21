import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CACHE_FILE = BASE_DIR / "data" / "nvr_channel_titles.json"


def _parse_recorders():
    raw = os.getenv("RECORDER_HTTP_PORTS", "")
    items = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            ip, port = part.rsplit(":", 1)
            try:
                port = int(port)
            except ValueError:
                continue
        else:
            ip, port = part, 80
        ip = ip.strip()
        if ip:
            items.append((ip, port))
    return items


def _credentials():
    return (
        os.getenv("RECORDER_CAMERA_USERNAME") or os.getenv("CAMERA_USERNAME", "admin"),
        os.getenv("RECORDER_CAMERA_PASSWORD") or os.getenv("CAMERA_PASSWORD", ""),
    )


def _digest_get(url, username, password, timeout=6):
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, url, username, password)
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager))
    request = urllib.request.Request(url, headers={"User-Agent": "CivicVision/1.0"})
    with opener.open(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_channel_titles(ip, port, username, password):
    url = f"http://{ip}:{port}/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"
    text = _digest_get(url, username, password)
    titles = {}
    for line in text.splitlines():
        match = re.match(r"table\.ChannelTitle\[(\d+)\]\.Name=(.*)", line.strip())
        if not match:
            continue
        index = int(match.group(1))
        title = match.group(2).replace("|", " ").strip()
        if title:
            titles[index + 1] = title
    return titles


def main():
    recorders = _parse_recorders()
    if not recorders:
        print("RECORDER_HTTP_PORTS is empty; skipping NVR title sync.")
        return 0

    username, password = _credentials()
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if CACHE_FILE.exists():
        try:
            existing = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    output = dict(existing)
    successes = 0
    for ip, port in recorders:
        print(f"Reading channel titles from {ip}:{port} ...")
        try:
            titles = fetch_channel_titles(ip, port, username, password)
        except urllib.error.HTTPError as exc:
            print(f"  HTTP {exc.code}: {exc.reason}")
            continue
        except Exception as exc:
            print(f"  Failed: {exc}")
            continue

        if not titles:
            print("  No ChannelTitle entries returned.")
            continue

        successes += 1
        for channel, title in titles.items():
            key = f"{ip}_ch{channel}"
            output[key] = {
                "camera_key": key,
                "camera_ip": ip,
                "channel_no": channel,
                "camera_name": title,
                "area_name": title,
                "source": "nvr_channel_title",
                "http_port": port,
            }
        print(f"  Found {len(titles)} channel title(s).")

    CACHE_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved {len(output)} cached title(s) to {CACHE_FILE}")
    return 0 if successes else 1


if __name__ == "__main__":
    raise SystemExit(main())
