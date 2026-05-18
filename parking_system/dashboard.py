"""
dashboard.py
Tkinter dashboard kết nối WebSocket tới server,
hiển thị ảnh nhận diện + biển số + lịch sử realtime.
Có popup nhập thủ công khi nhận diện thất bại sau 3 lần retry.

Chạy: python dashboard.py
"""

import tkinter as tk
from tkinter import messagebox
import threading
import base64
import json
import io
import requests
from datetime import datetime

try:
    from PIL import Image, ImageTk
except ImportError:
    raise SystemExit("Cài Pillow: pip install Pillow")

try:
    import websocket
except ImportError:
    raise SystemExit("Cài websocket-client: pip install websocket-client")

# ─── Config ───────────────────────────────────────────────────────────────────
WS_URL     = "ws://localhost:8000/ws"
SERVER_URL = "http://localhost:8000"

BG_DARK    = "#0D1117"
BG_CARD    = "#161B22"
BG_HOVER   = "#21262D"
BG_POPUP   = "#1C2128"
ACCENT     = "#58A6FF"
GREEN      = "#3FB950"
WARN       = "#F78166"
YELLOW     = "#E3B341"
TEXT_PRI   = "#E6EDF3"
TEXT_SEC   = "#8B949E"
BORDER     = "#30363D"


# ─── Popup nhập thủ công ──────────────────────────────────────────────────────
class ManualEntryPopup(tk.Toplevel):
    def __init__(self, master, image_b64="", event="entry", session_id=None):
        super().__init__(master)
        self.master       = master
        self.image_b64    = image_b64
        self.event        = event
        self.session_id   = session_id
        self.result_plate = None

        self.title("Nhap bien so thu cong")
        self.configure(bg=BG_POPUP)
        self.geometry("480x520")
        self.resizable(False, False)
        self.grab_set()
        self.focus_set()

        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width()  - 480) // 2
        y = master.winfo_y() + (master.winfo_height() - 520) // 2
        self.geometry(f"+{x}+{y}")

        self._build_ui()

    def _build_ui(self):
        # Header cảnh báo
        hdr = tk.Frame(self, bg=WARN, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Khong nhan dien duoc bien so!",
                 font=("Segoe UI", 11, "bold"),
                 fg="#0D1117", bg=WARN).pack(expand=True)

        # Ảnh xe
        img_frame = tk.Frame(self, bg=BG_POPUP, height=180)
        img_frame.pack(fill="x", padx=16, pady=(12,0))
        img_frame.pack_propagate(False)

        self._img_canvas = tk.Canvas(img_frame, bg=BG_HOVER,
                                      highlightthickness=0, height=180)
        self._img_canvas.pack(fill="both", expand=True)

        if self.image_b64:
            self._show_image()
        else:
            self._img_canvas.create_text(224, 90, text="Khong co anh",
                                          font=("Segoe UI", 11), fill=TEXT_SEC)

        tk.Label(self, text="Bao ve vui long nhap bien so xe:",
                 font=("Segoe UI", 9), fg=TEXT_SEC, bg=BG_POPUP).pack(
                     anchor="w", padx=16, pady=(12,4))

        # Ô nhập biển số
        entry_frame = tk.Frame(self, bg=BG_HOVER)
        entry_frame.pack(fill="x", padx=16)

        self._plate_var = tk.StringVar()
        self._plate_entry = tk.Entry(
            entry_frame,
            textvariable=self._plate_var,
            font=("Consolas", 22, "bold"),
            bg=BG_HOVER, fg=GREEN,
            relief="flat", bd=8,
            insertbackground=GREEN,
            justify="center",
        )
        self._plate_entry.pack(fill="x")
        self._plate_entry.focus_set()
        self._plate_entry.bind("<Return>", lambda e: self._submit())

        tk.Label(self, text="Vi du: 30A-12345  hoac  51F-123.45",
                 font=("Segoe UI", 8), fg=TEXT_SEC, bg=BG_POPUP).pack(pady=(4,0))

        # Loại sự kiện
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=12)
        tk.Label(self, text="Loai su kien:",
                 font=("Segoe UI", 9), fg=TEXT_SEC, bg=BG_POPUP).pack(anchor="w", padx=16)

        self._event_var = tk.StringVar(value=self.event)
        event_frame = tk.Frame(self, bg=BG_POPUP)
        event_frame.pack(fill="x", padx=16, pady=(4,0))

        tk.Radiobutton(event_frame, text="Xe VAO",
                       variable=self._event_var, value="entry",
                       font=("Segoe UI", 10), fg=TEXT_PRI, bg=BG_POPUP,
                       selectcolor=BG_HOVER, activebackground=BG_POPUP,
                       activeforeground=TEXT_PRI).pack(side="left", padx=(0,20))

        tk.Radiobutton(event_frame, text="Xe RA",
                       variable=self._event_var, value="exit",
                       font=("Segoe UI", 10), fg=TEXT_PRI, bg=BG_POPUP,
                       selectcolor=BG_HOVER, activebackground=BG_POPUP,
                       activeforeground=TEXT_PRI).pack(side="left")

        # Buttons
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=12)
        btn_frame = tk.Frame(self, bg=BG_POPUP)
        btn_frame.pack(fill="x", padx=16, pady=(0,16))

        tk.Button(btn_frame, text="Huy",
                  command=self._cancel,
                  bg=BG_HOVER, fg=TEXT_SEC, relief="flat",
                  font=("Segoe UI", 10), padx=20, pady=8,
                  cursor="hand2").pack(side="left")

        tk.Button(btn_frame, text="Xac nhan",
                  command=self._submit,
                  bg=GREEN, fg="#0D1117", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=20, pady=8,
                  cursor="hand2").pack(side="right")

    def _show_image(self):
        try:
            img_bytes = base64.b64decode(self.image_b64)
            pil_img   = Image.open(io.BytesIO(img_bytes))
            scale     = min(448 / pil_img.width, 176 / pil_img.height)
            nw = int(pil_img.width  * scale)
            nh = int(pil_img.height * scale)
            pil_img = pil_img.resize((nw, nh), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(pil_img)
            self._img_canvas.create_image(224, 90, anchor="center", image=self._photo)
        except Exception as e:
            print(f"[Popup] loi hien thi anh: {e}")

    def _submit(self):
        plate = self._plate_var.get().strip().upper()
        if not plate:
            messagebox.showwarning("Thieu thong tin", "Vui long nhap bien so xe!", parent=self)
            return

        event = self._event_var.get()
        try:
            res = requests.post(f"{SERVER_URL}/manual-entry", json={
                "plate_text": plate,
                "event":      event,
                "session_id": self.session_id,
            }, timeout=5)

            if res.status_code == 200:
                self.result_plate = plate
                self.destroy()
            else:
                messagebox.showerror("Loi", f"Server tra ve loi: {res.text}", parent=self)
        except Exception as e:
            messagebox.showerror("Loi ket noi", str(e), parent=self)

    def _cancel(self):
        self.result_plate = None
        self.destroy()


# ─── App chính ────────────────────────────────────────────────────────────────
class ParkingDashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Parking System - Dashboard")
        self.configure(bg=BG_DARK)
        self.geometry("1100x680")
        self.minsize(900, 580)

        self._photo        = None
        self._history      = []
        self._ws           = None
        self._connected    = False
        self._last_img_b64 = ""

        self._build_ui()
        threading.Thread(target=self._connect_ws, daemon=True).start()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG_CARD, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  Parking System - Realtime Monitor",
                 font=("Segoe UI", 13, "bold"), fg=TEXT_PRI, bg=BG_CARD).pack(side="left", padx=16)

        self._conn_dot = tk.Label(hdr, text="●", fg=WARN, bg=BG_CARD, font=("Segoe UI", 14))
        self._conn_dot.pack(side="right", padx=(0,8))
        self._conn_lbl = tk.Label(hdr, text="Dang ket noi...", fg=TEXT_SEC,
                                  bg=BG_CARD, font=("Segoe UI", 9))
        self._conn_lbl.pack(side="right")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        body = tk.Frame(self, bg=BG_DARK)
        body.pack(fill="both", expand=True)

        self._build_sidebar(body)
        self._build_main(body)

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=BG_CARD, width=280)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        tk.Frame(sb, bg=BORDER, height=1).pack(fill="x")

        tk.Label(sb, text="BIEN SO XE", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_SEC, bg=BG_CARD).pack(anchor="w", padx=16, pady=(16,4))

        plate_box = tk.Frame(sb, bg=BG_HOVER)
        plate_box.pack(fill="x", padx=16)
        self._plate_lbl = tk.Label(plate_box, text="—",
                                   font=("Consolas", 24, "bold"),
                                   fg=GREEN, bg=BG_HOVER, pady=14)
        self._plate_lbl.pack()

        self._event_lbl = tk.Label(sb, text="", font=("Segoe UI", 9, "bold"),
                                   fg=TEXT_SEC, bg=BG_CARD)
        self._event_lbl.pack(pady=(4,0))

        tk.Frame(sb, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)
        stats = tk.Frame(sb, bg=BG_CARD)
        stats.pack(fill="x", padx=16)

        self._conf_lbl  = self._stat_row(stats, "Confidence:", "—")
        self._inf_lbl   = self._stat_row(stats, "Inference:", "—")
        self._time_lbl  = self._stat_row(stats, "Thoi gian:", "—")
        self._fee_lbl   = self._stat_row(stats, "Phi:", "—")
        self._count_lbl = self._stat_row(stats, "Tong xe:", "0")

        tk.Frame(sb, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)
        tk.Button(sb, text="Nhap thu cong",
                  command=self._open_manual_entry,
                  bg=YELLOW, fg="#0D1117", relief="flat",
                  font=("Segoe UI", 9, "bold"), padx=12, pady=6,
                  cursor="hand2").pack(padx=16, fill="x")

        tk.Frame(sb, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        hist_hdr = tk.Frame(sb, bg=BG_CARD)
        hist_hdr.pack(fill="x", padx=16)
        tk.Label(hist_hdr, text="LICH SU", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_SEC, bg=BG_CARD).pack(side="left")
        tk.Button(hist_hdr, text="Xoa", font=("Segoe UI", 7),
                  fg=TEXT_SEC, bg=BG_CARD, relief="flat",
                  command=self._clear_history, cursor="hand2").pack(side="right")

        hist_frame = tk.Frame(sb, bg=BG_DARK)
        hist_frame.pack(fill="both", expand=True, padx=16, pady=(4,16))

        self._history_lb = tk.Listbox(
            hist_frame, font=("Consolas", 8), bg=BG_DARK, fg=TEXT_PRI,
            relief="flat", bd=0, selectbackground=BG_HOVER,
            selectforeground=ACCENT, activestyle="none"
        )
        scroll = tk.Scrollbar(hist_frame, command=self._history_lb.yview, bg=BG_DARK)
        self._history_lb.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self._history_lb.pack(fill="both", expand=True)

    def _stat_row(self, parent, label, value):
        f = tk.Frame(parent, bg=BG_CARD)
        f.pack(fill="x", pady=2)
        tk.Label(f, text=label, font=("Segoe UI", 9), fg=TEXT_SEC,
                 bg=BG_CARD, width=12, anchor="w").pack(side="left")
        lbl = tk.Label(f, text=value, font=("Consolas", 10), fg=TEXT_PRI, bg=BG_CARD)
        lbl.pack(side="left")
        return lbl

    def _build_main(self, parent):
        main = tk.Frame(parent, bg=BG_DARK)
        main.pack(side="left", fill="both", expand=True)

        status = tk.Frame(main, bg=BG_DARK)
        status.pack(fill="x", padx=16, pady=10)
        tk.Label(status, text="Dang cho anh tu ESP32-CAM...",
                 font=("Segoe UI", 9), fg=TEXT_SEC, bg=BG_DARK).pack(side="left")

        canvas_frame = tk.Frame(main, bg=BG_CARD)
        canvas_frame.pack(fill="both", expand=True, padx=16, pady=(0,16))

        self._canvas = tk.Canvas(canvas_frame, bg=BG_CARD, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda e: self._redraw_placeholder())
        self._placeholder_shown = True
        self._redraw_placeholder()

    def _redraw_placeholder(self):
        if not self._placeholder_shown:
            return
        self._canvas.delete("all")
        w = self._canvas.winfo_width() or 600
        h = self._canvas.winfo_height() or 400
        self._canvas.create_text(w//2, h//2-20, text="[CAM]",
                                  font=("Segoe UI", 52), fill=BORDER)
        self._canvas.create_text(w//2, h//2+44,
                                  text="Cho anh tu ESP32-CAM...",
                                  font=("Segoe UI", 11), fill=TEXT_SEC)

    def _connect_ws(self):
        def on_open(ws):
            self._connected = True
            self.after(0, self._set_connected, True)

        def on_message(ws, message):
            try:
                data = json.loads(message)
                self.after(0, self._on_result, data)
            except Exception as e:
                print(f"[WS] parse loi: {e}")

        def on_error(ws, error):
            print(f"[WS] loi: {error}")

        def on_close(ws, code, msg):
            self._connected = False
            self.after(0, self._set_connected, False)
            import time; time.sleep(3)
            self._connect_ws()

        self._ws = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws.run_forever()

    def _set_connected(self, connected):
        if connected:
            self._conn_dot.config(fg=GREEN)
            self._conn_lbl.config(text="Da ket noi server")
        else:
            self._conn_dot.config(fg=WARN)
            self._conn_lbl.config(text="Mat ket noi - dang thu lai...")

    def _on_result(self, data):
        plate   = data.get("plate_text", "")
        conf    = data.get("confidence", 0.0)
        inf_ms  = data.get("inference_ms", 0)
        ts      = data.get("timestamp", "")
        img_b64 = data.get("image_b64", "")
        event   = data.get("event", "entry")
        fee     = data.get("fee_display", "")
        action  = data.get("action", "")

        if img_b64:
            self._last_img_b64 = img_b64

        # Server yêu cầu nhập thủ công
        if action == "manual":
            self._open_manual_entry(
                image_b64  = img_b64,
                event      = event,
                session_id = data.get("session_id"),
            )
            return

        self._plate_lbl.config(
            text=plate if plate else "Khong ro",
            fg=GREEN if plate else WARN
        )
        self._event_lbl.config(
            text="XE VAO" if event == "entry" else "XE RA",
            fg=GREEN if event == "entry" else YELLOW
        )
        self._conf_lbl.config(text=f"{conf:.1%}")
        self._inf_lbl.config(text=f"{inf_ms} ms")
        self._time_lbl.config(text=ts)
        self._fee_lbl.config(text=fee if fee else "—")

        icon  = "->" if event == "entry" else "<-"
        entry = f"{icon} {ts}  {plate or '???':<12}  {conf:.0%}"
        self._history.append(entry)
        self._history_lb.insert(0, entry)
        self._count_lbl.config(text=str(len(self._history)))

        if img_b64:
            self._show_image(img_b64)

    def _show_image(self, img_b64):
        try:
            img_bytes = base64.b64decode(img_b64)
            pil_img   = Image.open(io.BytesIO(img_bytes))

            cw = self._canvas.winfo_width()
            ch = self._canvas.winfo_height()
            if cw < 10 or ch < 10:
                return

            scale   = min(cw / pil_img.width, ch / pil_img.height)
            nw      = int(pil_img.width  * scale)
            nh      = int(pil_img.height * scale)
            pil_img = pil_img.resize((nw, nh), Image.LANCZOS)

            self._photo = ImageTk.PhotoImage(pil_img)
            self._canvas.delete("all")
            self._canvas.create_image(cw//2, ch//2, anchor="center", image=self._photo)
            self._placeholder_shown = False
        except Exception as e:
            print(f"[UI] loi hien thi anh: {e}")

    def _open_manual_entry(self, image_b64="", event="entry", session_id=None):
        if not image_b64:
            image_b64 = self._last_img_b64

        popup = ManualEntryPopup(self,
                                  image_b64  = image_b64,
                                  event      = event,
                                  session_id = session_id)
        self.wait_window(popup)

        if popup.result_plate:
            ts    = datetime.now().strftime("%H:%M:%S")
            icon  = "E->" if event == "entry" else "E<-"
            entry = f"{icon} {ts}  {popup.result_plate:<12}  manual"
            self._history_lb.insert(0, entry)
            self._plate_lbl.config(text=popup.result_plate, fg=YELLOW)
            self._event_lbl.config(text="NHAP THU CONG", fg=YELLOW)

    def _clear_history(self):
        self._history.clear()
        self._history_lb.delete(0, "end")
        self._count_lbl.config(text="0")


if __name__ == "__main__":
    app = ParkingDashboard()
    app.mainloop()