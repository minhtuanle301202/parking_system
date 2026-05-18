"""
database.py
Kết nối PostgreSQL và các hàm thao tác dữ liệu bãi đỗ xe.
"""

import asyncpg
from datetime import datetime
from math import ceil

# ─── Cấu hình ─────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "database": "parking_db",
    "user":     "postgres",       # ← đổi nếu khác
    "password": "minhtuan",         # ← đổi thành password của Tuan
}

# Giá tiền
PRICE_PER_BLOCK = 5000   # 5.000đ / block
BLOCK_MINUTES   = 30     # 1 block = 30 phút

# ─── Pool kết nối ─────────────────────────────────────────────────────────────
pool: asyncpg.Pool = None

async def init_db():
    """Khởi tạo connection pool — gọi 1 lần khi server start"""
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG, min_size=2, max_size=10)
    print("[DB] Kết nối PostgreSQL thành công!")

async def close_db():
    """Đóng pool khi server tắt"""
    global pool
    if pool:
        await pool.close()
        print("[DB] Đã đóng kết nối PostgreSQL")

# ─── Các hàm thao tác ─────────────────────────────────────────────────────────

async def create_session(plate_text: str, image_url: str = None) -> int:
    """
    Tạo phiên gửi xe mới khi xe vào.
    image_url: URL ảnh trên Cloudinary
    Trả về session_id.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO parking_sessions (plate_text, image_path, entry_time, status)
            VALUES ($1, $2, NOW(), 'parking')
            RETURNING id
        """, plate_text, image_url)
        session_id = row["id"]
        print(f"[DB] Xe vào: {plate_text} | session_id={session_id}")
        return session_id


async def close_session(session_id: int) -> dict:
    """
    Đóng phiên gửi xe khi xe ra — tính phí theo block giờ.

    Ví dụ với BLOCK_MINUTES=30, PRICE_PER_BLOCK=5000:
      - Gửi 20 phút  → 1 block  → 5.000đ
      - Gửi 45 phút  → 2 blocks → 10.000đ
      - Gửi 90 phút  → 3 blocks → 15.000đ
      - Gửi 3 giờ    → 6 blocks → 30.000đ
    """
    async with pool.acquire() as conn:
        session = await conn.fetchrow("""
            SELECT id, plate_text, entry_time
            FROM parking_sessions
            WHERE id = $1 AND status = 'parking'
        """, session_id)

        if not session:
            return None

        # ── Tính thời gian ────────────────────────────────────────────────────
        exit_time        = datetime.now()
        total_seconds    = (exit_time - session["entry_time"]).total_seconds()
        duration_minutes = total_seconds / 60
        duration_hours   = duration_minutes / 60

        # ── Tính phí theo block ───────────────────────────────────────────────
        # Làm tròn lên: 1 phút cũng tính 1 block
        blocks = max(1, ceil(duration_minutes / BLOCK_MINUTES))
        fee    = blocks * PRICE_PER_BLOCK

        # ── Lưu vào DB ────────────────────────────────────────────────────────
        await conn.execute("""
            UPDATE parking_sessions
            SET exit_time      = $1,
                duration_hours = $2,
                fee            = $3,
                status         = 'completed'
            WHERE id = $4
        """, exit_time, round(duration_hours, 4), fee, session_id)

        print(
            f"[DB] Xe ra: {session['plate_text']} | "
            f"{duration_minutes:.0f} phút | "
            f"{blocks} block x {PRICE_PER_BLOCK:,}đ = {fee:,.0f}đ"
        )

        return {
            "session_id":       session_id,
            "plate_text":       session["plate_text"],
            "entry_time":       session["entry_time"].strftime("%H:%M:%S %d/%m/%Y"),
            "exit_time":        exit_time.strftime("%H:%M:%S %d/%m/%Y"),
            "duration_minutes": round(duration_minutes),
            "duration_hours":   round(duration_hours, 2),
            "block_minutes":    BLOCK_MINUTES,
            "price_per_block":  PRICE_PER_BLOCK,
            "blocks":           blocks,
            "fee":              fee,
        }


async def get_active_sessions() -> list:
    """Lấy danh sách xe đang gửi (status = 'parking')"""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, plate_text, entry_time, image_path
            FROM parking_sessions
            WHERE status = 'parking'
            ORDER BY entry_time DESC
        """)
        return [dict(r) for r in rows]


async def get_session_by_plate(plate_text: str) -> dict:
    """Tìm phiên đang gửi theo biển số"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, plate_text, entry_time, image_path
            FROM parking_sessions
            WHERE plate_text = $1 AND status = 'parking'
            ORDER BY entry_time DESC
            LIMIT 1
        """, plate_text)
        return dict(row) if row else None


async def get_history(limit: int = 50) -> list:
    """Lấy lịch sử gửi xe gần nhất"""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, plate_text, entry_time, exit_time,
                   duration_hours, fee, status
            FROM parking_sessions
            ORDER BY entry_time DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]


async def get_revenue_today() -> dict:
    """Thống kê doanh thu hôm nay"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                COUNT(*)                    AS total_sessions,
                COALESCE(SUM(fee), 0)       AS total_revenue,
                COALESCE(AVG(fee), 0)       AS avg_fee
            FROM parking_sessions
            WHERE DATE(entry_time) = CURRENT_DATE
              AND status = 'completed'
        """)
        return dict(row)