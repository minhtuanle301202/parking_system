"""
server.py
FastAPI nhận ảnh từ ESP32-CAM → nhận diện biển số → broadcast qua WebSocket
Dashboard Tkinter kết nối WebSocket để hiển thị realtime

Chạy: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import cv2
import numpy as np
import base64
import json
import time
from datetime import datetime
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from ultralytics import YOLO
from ton_ocr.pipeline import TonOCRPipeline
from database import init_db, close_db, create_session, get_session_by_plate, close_session
import cloudinary
import cloudinary.uploader

# ─── Cloudinary config ────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name = "dajwhxulp",
    api_key    = "571988185872689",
    api_secret = "sKAI8LSCHtfeXDkPRTkeH1CMowg",
    secure     = True
)

# ─── Model ────────────────────────────────────────────────────────────────────
detector: YOLO = None
ocr: TonOCRPipeline = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global detector, ocr
    print("[Server] Đang load model...")
    detector = YOLO("weights/plate/plate_yolov9s_640_2025.pt")
    ocr = TonOCRPipeline()
    print("[Server] Model sẵn sàng!")
    await init_db()
    yield
    await close_db()
    print("[Server] Shutting down...")

app = FastAPI(title="Parking LP Server", lifespan=lifespan)

# ─── WebSocket manager ────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        print(f"[WS] Dashboard kết nối. Tổng: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        print(f"[WS] Dashboard ngắt kết nối. Tổng: {len(self.active)}")

    async def broadcast(self, data: dict):
        """Gửi kết quả nhận diện tới tất cả dashboard đang kết nối"""
        msg = json.dumps(data, ensure_ascii=False)
        for ws in self.active.copy():
            try:
                await ws.send_text(msg)
            except Exception:
                self.active.remove(ws)

manager = ConnectionManager()

# ─── Helper ───────────────────────────────────────────────────────────────────
def preprocess(img: np.ndarray) -> np.ndarray:
    img = cv2.bilateralFilter(img, d=5, sigmaColor=75, sigmaSpace=75)
    img = cv2.convertScaleAbs(img, alpha=1.2, beta=15)
    return img

def detect_plate(img: np.ndarray):
    """
    Chạy YOLO + TonOCR trên ảnh.
    Trả về (annotated_img, plate_text, confidence, inference_ms)
    """
    img = preprocess(img)
    t0 = time.time()

    yolo_res = detector(img, conf=0.25, imgsz=640)[0]
    annotated = img.copy()
    best_text, best_conf = "", 0.0

    for box in yolo_res.boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        det_conf = float(box.conf[0])

        # Crop biển số
        pad = 4
        h, w = img.shape[:2]
        crop = img[max(0,y1-pad):min(h,y2+pad), max(0,x1-pad):min(w,x2+pad)]

        # Resize nếu quá nhỏ
        ch, cw = crop.shape[:2]
        if ch < 100:
            scale = 100 / ch
            crop = cv2.resize(crop, (int(cw*scale), 100), interpolation=cv2.INTER_CUBIC)

        # OCR
        plate_text, ocr_conf = "", 0.0
        try:
            results = ocr.predict(crop)
            if results:
                texts = [r.text for r in results if r.text.strip()]
                confs  = [r.score for r in results if r.text.strip()]
                if texts:
                    plate_text = "-".join(texts).upper() if len(texts) == 2 else " ".join(texts).upper()
                    ocr_conf = sum(confs) / len(confs)
        except Exception as e:
            print(f"[OCR] lỗi: {e}")

        # Vẽ bounding box
        color = (63, 185, 80)
        cv2.rectangle(annotated, (x1,y1), (x2,y2), color, 2)
        tag = f"{plate_text or 'plate'} {det_conf:.0%}"
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(annotated, (x1, y1-th-10), (x1+tw+8, y1), color, -1)
        cv2.putText(annotated, tag, (x1+4, y1-5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,0), 1)

        if det_conf > best_conf:
            best_text = plate_text
            best_conf = det_conf

    inf_ms = round((time.time() - t0) * 1000, 1)
    return annotated, best_text, best_conf, inf_ms

def img_to_base64(img: np.ndarray) -> str:
    """Chuyển ảnh BGR → base64 JPEG để gửi qua WebSocket"""
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode("utf-8")

async def upload_to_cloudinary(img: np.ndarray, plate_text: str, event: str) -> str:
    """
    Upload ảnh lên Cloudinary.
    event: 'entry' hoặc 'exit'
    Trả về URL ảnh hoặc "" nếu lỗi.
    """
    try:
        import io
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        plate_clean = plate_text.replace(" ", "_").replace("-", "_") if plate_text else "unknown"
        public_id = f"parking/{event}/{ts}_{plate_clean}"

        # Encode ảnh sang JPEG bytes
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        img_bytes = buf.tobytes()

        # Upload lên Cloudinary
        result = cloudinary.uploader.upload(
            img_bytes,
            public_id   = public_id,
            folder      = f"parking/{event}",
            resource_type = "image",
        )
        url = result.get("secure_url", "")
        print(f"[Cloudinary] Upload OK: {url}")
        return url
    except Exception as e:
        print(f"[Cloudinary] Lỗi upload: {e}")
        return ""


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": detector is not None}

MAX_RETRY = 3
retry_counts = {}

@app.post("/detect")
async def detect(request_data: Request):
    """
    Nhận ảnh từ ESP32-CAM — hỗ trợ cả 2 cách gửi:
    - Raw bytes (Content-Type: image/jpeg)  ← ESP32-CAM dùng cách này
    - Multipart form-data (field: file)     ← test_esp32.py dùng cách này
    """
    content_type = request_data.headers.get("content-type", "")

    if "multipart" in content_type:
        form = await request_data.form()
        file = form.get("file")
        image_bytes = await file.read()
    else:
        # Raw bytes từ ESP32-CAM
        image_bytes = await request_data.body()

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        return JSONResponse(status_code=400, content={"error": "Không decode được ảnh"})

    annotated, plate_text, conf, inf_ms = detect_plate(img)

    # Chuẩn bị dữ liệu broadcast tới dashboard
    payload = {
        "timestamp":   datetime.now().strftime("%H:%M:%S"),
        "plate_text":  plate_text,
        "event": "entry",
        "confidence":  round(conf, 4),
        "inference_ms": inf_ms,
        "image_b64":   img_to_base64(annotated),  # ảnh đã vẽ bounding box
    }
    client_ip = request_data.client.host

    if not plate_text:

        retry_counts[client_ip] = retry_counts.get(client_ip, 0) + 1

        current_retry = retry_counts[client_ip]

        print(f"[Retry] {client_ip} fail lần {current_retry}")

        # Hết số lần retry → bật nhập tay
        if current_retry >= MAX_RETRY:

            retry_counts[client_ip] = 0

            payload["action"] = "manual"

            # broadcast để dashboard mở popup
            await manager.broadcast(payload)

            print(f"[Manual] Yêu cầu nhập tay cho {client_ip}")

            return JSONResponse(content={
                "success": False,
                "action": "manual",
                "message": "Không nhận diện được biển số"
            })

        # Vẫn còn retry
        return JSONResponse(content={
            "success": False,
            "action": "retry",
            "attempt": current_retry,
            "message": f"Retry lần {current_retry}"
        })

    # ─── Detect OK → reset counter ───────────────────────────
    retry_counts[client_ip] = 0
    # Upload ảnh lên Cloudinary và lưu DB
    image_url = ""
    if plate_text:
        try:
            import asyncio
            image_url = await asyncio.get_event_loop().run_in_executor(
                None, lambda: cloudinary.uploader.upload(
                    cv2.imencode(".jpg", annotated)[1].tobytes(),
                    folder      = "parking/entry",
                    public_id   = f"parking/entry/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{plate_text.replace('-','_')}",
                    resource_type = "image",
                ).get("secure_url", "")
            )
            existing = await get_session_by_plate(plate_text)
            if not existing:
                await create_session(plate_text, image_url)
        except Exception as e:
            print(f"[DB/Cloudinary] Lỗi: {e}")

    payload["image_url"] = image_url

    await manager.broadcast(payload)

    return JSONResponse(content={
        "success":      bool(plate_text),
        "plate_text":   plate_text,
        "confidence":   round(conf, 4),
        "inference_ms": inf_ms,
    })

@app.post("/detect-exit")
async def detect_exit(request_data: Request):
    """
    ESP32-CAM cổng RA gửi ảnh lên đây.
    Server nhận diện biển số → tìm session → tính phí → broadcast kết quả.
    """
    content_type = request_data.headers.get("content-type", "")

    if "multipart" in content_type:
        form = await request_data.form()
        file = form.get("file")
        image_bytes = await file.read()
    else:
        image_bytes = await request_data.body()

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        return JSONResponse(status_code=400, content={"error": "Không decode được ảnh"})

    annotated, plate_text, conf, inf_ms = detect_plate(img)

    payload = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "plate_text": plate_text,
        "confidence": round(conf, 4),
        "inference_ms": inf_ms,
        "image_b64": img_to_base64(annotated),
        "event": "exit",
        "fee": 0,
        "fee_display": "",
        "duration_minutes": 0,
        "entry_time": "",
        "image_url": "",
    }

    result = {
        "success":      bool(plate_text),
        "plate_text":   plate_text,
        "confidence":   round(conf, 4),
        "inference_ms": inf_ms,
        "event":        "exit",
    }
    client_ip = request_data.client.host
    key = f"exit_{client_ip}"

    if not plate_text:
        retry_counts[key] = retry_counts.get(key, 0) + 1
        current_retry = retry_counts[key]
        print(f"[Retry-Exit] {client_ip} fail lần {current_retry}")

        if current_retry >= MAX_RETRY:
            retry_counts[key] = 0
            payload["action"] = "manual"
            payload["event"] = "exit"
            await manager.broadcast(payload)
            return JSONResponse(content={
                "success": False,
                "action": "manual",
                "event": "exit",
                "message": "Không nhận diện được biển số"
            })

        return JSONResponse(content={
            "success": False,
            "action": "retry",
            "event": "exit",
            "attempt": current_retry,
        })

    # Nhận diện OK → reset counter
    retry_counts[key] = 0
    if plate_text:
        try:
            import asyncio
            image_url = await asyncio.get_event_loop().run_in_executor(
                None, lambda: cloudinary.uploader.upload(
                    cv2.imencode(".jpg", annotated)[1].tobytes(),
                    folder      = "parking/exit",
                    public_id   = f"parking/exit/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{plate_text.replace('-','_')}",
                    resource_type = "image",
                ).get("secure_url", "")
            )
            payload["image_url"] = image_url

            # Tìm session đang gửi xe theo biển số
            session = await get_session_by_plate(plate_text)
            if session:
                # Đóng session và tính phí
                billing = await close_session(session["id"])
                if billing:
                    result.update({
                        "session_id":       billing["session_id"],
                        "entry_time":       billing["entry_time"],
                        "exit_time":        billing["exit_time"],
                        "duration_minutes": billing["duration_minutes"],
                        "blocks":           billing["blocks"],
                        "fee":              billing["fee"],
                        "fee_display":      f"{billing['fee']:,.0f}đ",
                    })
                    print(f"[Exit] {plate_text} | {billing['duration_minutes']} phút | {billing['fee']:,.0f}đ")
            else:
                print(f"[Exit] Không tìm thấy session cho biển số: {plate_text}")
                result["warning"] = "Không tìm thấy xe trong bãi"
        except Exception as e:
            print(f"[DB] Lỗi close session: {e}")
            result["error"] = str(e)

    # Broadcast kết quả ra/tính tiền lên dashboard
    payload = {
        "timestamp":    datetime.now().strftime("%H:%M:%S"),
        "plate_text":   plate_text,
        "confidence":   round(conf, 4),
        "inference_ms": inf_ms,
        "image_b64":    img_to_base64(annotated),
        "event":        "exit",
        "fee":          result.get("fee", 0),
        "fee_display":  result.get("fee_display", ""),
        "duration_minutes": result.get("duration_minutes", 0),
        "entry_time":   result.get("entry_time", ""),
    }
    await manager.broadcast(payload)

    return JSONResponse(content=result)


@app.post("/manual-entry")
async def manual_entry(request: Request):
    """
    Bảo vệ nhập biển số thủ công khi nhận diện thất bại.
    Body: { plate_text, event, session_id }
    """
    body = await request.json()
    plate_text = body.get("plate_text", "").strip().upper()
    event = body.get("event", "entry")
    session_id = body.get("session_id")

    if not plate_text:
        return JSONResponse(status_code=400, content={"error": "Thiếu biển số"})

    result = {"success": True, "plate_text": plate_text, "event": event, "manual": True}

    try:
        if event == "entry":
            existing = await get_session_by_plate(plate_text)
            if not existing:
                sid = await create_session(plate_text)
                result["session_id"] = sid
        elif event == "exit":
            session = await get_session_by_plate(plate_text) if not session_id else {"id": session_id}
            if session:
                billing = await close_session(session["id"])
                if billing:
                    result.update({
                        "fee": billing["fee"],
                        "fee_display": f"{billing['fee']:,.0f}d",
                        "duration_minutes": billing["duration_minutes"],
                    })
    except Exception as e:
        print(f"[DB] Lỗi manual entry: {e}")

    # Broadcast lên dashboard
    ts = datetime.now().strftime("%H:%M:%S")
    payload = {
        "timestamp": ts,
        "plate_text": plate_text,
        "event": event,
        "manual": True,
        "confidence": 1.0,
        "fee_display": result.get("fee_display", ""),
    }
    await manager.broadcast(payload)

    print(f"[Manual] {event.upper()}: {plate_text}")
    return JSONResponse(content=result)

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Dashboard Tkinter kết nối vào đây để nhận kết quả realtime"""
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # giữ kết nối
    except WebSocketDisconnect:
        manager.disconnect(ws)