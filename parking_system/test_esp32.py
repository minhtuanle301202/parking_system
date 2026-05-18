"""
test_esp32.py
Giả lập ESP32-CAM gửi ảnh lên server để test khi chưa có phần cứng.
Dùng: python test_esp32.py --image path/to/image.jpg
      python test_esp32.py --image path/to/image.jpg --loop  (gửi liên tục)
"""

import argparse
import time
import requests

SERVER_URL = "http://localhost:8000/detect"

def send_image(image_path: str):
    with open(image_path, "rb") as f:
        files = {"file": ("image.jpg", f, "image/jpeg")}
        try:
            res = requests.post(SERVER_URL, files=files, timeout=10)
            data = res.json()
            print(f"✓ Biển số: {data.get('plate_text') or '(không nhận ra)'}"
                  f"  |  conf: {data.get('confidence', 0):.1%}"
                  f"  |  {data.get('inference_ms')}ms")
        except Exception as e:
            print(f"✗ Lỗi: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Đường dẫn ảnh cần gửi")
    parser.add_argument("--loop", action="store_true", help="Gửi liên tục mỗi 2 giây")
    parser.add_argument("--interval", type=float, default=2.0, help="Khoảng cách giữa các lần gửi (giây)")
    args = parser.parse_args()

    if args.loop:
        print(f"Đang gửi ảnh liên tục mỗi {args.interval}s... (Ctrl+C để dừng)")
        while True:
            send_image(args.image)
            time.sleep(args.interval)
    else:
        send_image(args.image)