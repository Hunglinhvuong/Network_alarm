# Timezone Migration — Asia/Ho_Chi_Minh

## Mục đích
Unify múi giờ của toàn bộ hệ thống từ UTC (mặc định của hệ điều hành/PostgreSQL) sang **Asia/Ho_Chi_Minh**.
Các timestamp hiển thị trong Telegram alerts, logs, và DB queries sẽ đều sử dụng múi giờ này.

## Thay đổi chi tiết

### 1. **config/settings.py**
- Thêm biến `APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Ho_Chi_Minh")`
- Giá trị mặc định: `Asia/Ho_Chi_Minh`
- Có thể override qua biến môi trường `APP_TIMEZONE`

### 2. **db/connection.py**
- Khi mở kết nối PostgreSQL, chạy ngay: `SET TIME ZONE 'Asia/Ho_Chi_Minh'`
- Điều này ép session DB luôn chuyển đổi TIMESTAMPTZ thành múi giờ cục bộ
- Log: "Đã cấu hình DB session timezone thành: Asia/Ho_Chi_Minh"

### 3. **alerting/messages.py**
- Thêm function `local_now()` → trả về `datetime.now(ZoneInfo(APP_TIMEZONE))`
- Thay tất cả `datetime.now().strftime(...)` → `local_now().strftime(...)`
- Alert messages sẽ kèm theo múi giờ: "2025-12-26 14:30:45 (Asia/Ho_Chi_Minh)"

### 4. **main_collector.py**
- Thay `vietnam_time_converter` → `app_time_converter`
- Logging sử dụng `datetime.now(ZoneInfo(APP_TIMEZONE))` thay vì UTC
- Startup log kèm: "(múi giờ: Asia/Ho_Chi_Minh)"

### 5. **alerting/bot/app.py**
- Thêm `app_time_converter` tương tự `main_collector.py`
- Logging hiển thị giờ cục bộ
- Startup log kèm: "(múi giờ: Asia/Ho_Chi_Minh)"

### 6. **.env.example**
- Thêm section: `APP_TIMEZONE=Asia/Ho_Chi_Minh`
- Comment: "Múi giờ cho toàn bộ hệ thống (DB session, logging, alert messages)"

## Cách verify sau khi deploy

1. **Check DB timezone:**
   ```sql
   SELECT now();
   -- Kết quả phải là giờ Việt Nam, không phải UTC
   ```

2. **Check log messages:**
   ```bash
   python main_collector.py
   # Xem dòng đầu tiên:
   # 2025-12-26 14:30:45 [INFO] main_collector: Alarm Collector khởi động, nguồn: CSVCollector (múi giờ: Asia/Ho_Chi_Minh)
   ```

3. **Check Telegram alerts:**
   - Trigger 1 alarm
   - Telegram message phải hiển thị giờ Việt Nam
   - Ví dụ: "Thời điểm phát hiện: 2025-12-26 14:30:45 (Asia/Ho_Chi_Minh)"

## Rollback (nếu cần)
Nếu muốn quay lại UTC:
1. Sửa `.env`: `APP_TIMEZONE=UTC`
2. Hoặc export: `export APP_TIMEZONE=UTC`
3. Restart cả `main_collector.py` và `alerting/bot/app.py`

## Lưu ý
- PostgreSQL lưu tất cả TIMESTAMPTZ dưới dạng UTC trong DB (không thay đổi)
- Chỉ client-side (Python app) chuyển đổi hiển thị theo múi giờ APP_TIMEZONE
- Khi query, PostgreSQL sẽ tự động chuyển đổi nếu session đã `SET TIME ZONE`
- Các timezone string khác có thể dùng (vd: `UTC`, `Asia/Bangkok`, `America/New_York`)
  - List đầy đủ: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
