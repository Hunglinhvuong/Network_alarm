# network_alarm

## Cấu trúc thư mục

```
network_alarm/
├── config/
│   └── settings.py          # toàn bộ config (DB, nguồn alarm, ngưỡng RCA) — đọc từ ENV
├── db/
│   ├── connection.py        # kết nối PostgreSQL (psycopg2 thuần, không ORM)
│   └── device_repo.py       # tra cứu device_code -> device_id (có cache)
├── collectors/
│   ├── base.py               # interface BaseAlarmCollector + AlarmRecord
│   ├── csv_collector.py      # đọc alarm từ CSV (dùng để test)
│   ├── oracle_collector.py   # đọc alarm từ Oracle (nguồn thật, sau này)
│   ├── factory.py            # chọn collector theo config.ALARM_SOURCE
│   └── alarm_sync.py         # upsert alarm mới + auto-clear alarm hết
├── topology/
│   └── path_to_root.py       # get_path_to_root, get_nearest_station_ancestor, get_children, get_root_node
├── sample_data/
│   └── alarms.csv            # dữ liệu mẫu để test
├── main_collector.py          # entry point: vòng lặp poll (adaptive interval)
└── requirements.txt
```

```
network_alarm/                (tiếp)
├── topology/
│   ├── path_to_root.py        # get_path_to_root, get_nearest_station_ancestor, get_children, get_root_node
│   └── descendants.py         # get_descendant_stations(node_id) — duyệt xuống, phục vụ escalation
├── rca/
│   └── engine.py               # RCA 3 bước: sync node cha -> power_fail liên quan -> neighbor lân cận
├── escalation/
│   └── engine.py               # gộp cảnh báo lên node cấp cao nhất đang down toàn bộ, suppress con cháu
├── alerting/
│   ├── notifier.py             # gửi push alert qua Telegram HTTP API (dùng trong main_collector)
│   ├── messages.py             # soạn nội dung tin nhắn DOWN/RECOVERED từ escalation + RCA
│   └── bot/                    # bot tra cứu Telegram — TIẾN TRÌNH RIÊNG, chạy: python -m alerting.bot.app
│       ├── app.py
│       └── handlers/           # registry pattern — thêm lệnh mới chỉ cần thêm 1 file ở đây
│           ├── registry.py     # COMMAND_HANDLERS + decorator @command(name, description)
│           ├── common.py       # is_authorized() — whitelist TELEGRAM_ADMIN_IDS
│           ├── start.py        # /start /help
│           ├── status.py       # /status — tổng quan escalation đang active
│           ├── lookup.py       # /tra <site_code|device_code> — tra cứu trạng thái
│           └── path.py         # /path <site_code> — xem đường đi lên root (ví dụ mở rộng)
├── alarm_pipeline.py            # nối escalation + RCA + alerting, gọi mỗi chu kỳ poll
├── .env.example                 # copy thành .env rồi điền giá trị thật
```

Dashboard (map + tree view) chưa làm — sẽ thêm package `dashboard/` sau.

## Cấu hình .env

```bash
cp .env.example .env
# rồi điền PG_*, TELEGRAM_BOT_TOKEN (lấy từ @BotFather), TELEGRAM_ALERT_CHAT_IDS,
# TELEGRAM_ADMIN_IDS...
```
`config/settings.py` tự load `.env` bằng `python-dotenv`; biến đã export sẵn trong
shell/systemd luôn được ưu tiên hơn giá trị trong `.env`.

## Chạy thử với CSV (chưa có alarm thật)

Cần chạy **2 tiến trình song song**:

```bash
pip install -r requirements.txt

# Tiến trình 1: poll alarm + sync + RCA + escalation + push cảnh báo
python main_collector.py

# Tiến trình 2: bot tra cứu (tương tác, /status /tra /path...)
python -m alerting.bot.app
```

Sửa `sample_data/alarms.csv` (thêm/xoá dòng) trong lúc `main_collector.py` đang
chạy để test: alarm mới, auto-clear, đồng bộ nhiều trạm cùng node cha (RCA bước 1
+ escalation gộp cảnh báo), power_fail liên quan (RCA bước 2), trạm lân cận cùng
mất liên lạc (RCA bước 3).

Sửa `sample_data/alarms.csv` (thêm/xoá dòng) trong lúc chương trình đang chạy để
test cả 2 nhánh: alarm mới xuất hiện, và alarm auto-clear khi biến mất khỏi CSV.

## Chuyển sang nguồn Oracle thật

Chỉ cần đổi ENV, không sửa code:

```bash
export ALARM_SOURCE=oracle
export ORACLE_HOST=... ORACLE_PORT=1521 ORACLE_SERVICE=... ORACLE_USER=... ORACLE_PASSWORD=...
export ORACLE_ALARM_TABLE=ALARM_ACTIVE
```

⚠️ `collectors/oracle_collector.py` hiện đang **giả định** cấu trúc bảng
(`DEVICE_CODE`, `ALARM_NAME`, `START_TIME`, `END_TIME`). Cần xác nhận lại schema
thật với đội quản lý hệ thống nguồn rồi chỉnh `_build_query()` / `_map_row()` /
`ALARM_NAME_MAP` cho khớp.

## Path-to-root / nearest station ancestor

```python
from topology.path_to_root import get_path_to_root, get_nearest_station_ancestor

path = get_path_to_root(node_id=42)          # list từ node -> root
station = get_nearest_station_ancestor(42)    # STATION gần nhất (hoặc chính nó)
```

Dùng Recursive CTE trực tiếp trên PostgreSQL, không load toàn bộ cây vào Python
-> nhẹ RAM, phù hợp Wyse 5010.

## RCA Engine

```python
from rca.engine import run_rca

result = run_rca(alarm_id=123)   # chỉ nhận alarm loss_comm
print(result.conclusion)          # sync_parent_node | power_fail | area_outage | isolated_device
print(result.to_text())           # text tiếng Việt, dùng trực tiếp trong alert
```
Chạy đủ cả 3 bước (không tắt sớm) để có đầy đủ evidence, kết luận ưu tiên theo
đúng thứ tự bước 1 -> 2 -> 3. Chưa lưu kết quả vào DB (chưa có bảng riêng cho RCA
trong schema hiện tại) — tính lại theo yêu cầu (on-demand), kết quả chỉ dùng để
soạn nội dung cảnh báo.

## Escalation

```python
from escalation.engine import compute_escalation

groups = compute_escalation()   # list[EscalationGroup], mỗi group = 1 cảnh báo cần gửi
```
Thuật toán: từ mỗi station đang down, đi lên root, dừng ở node cao nhất mà TOÀN
BỘ station con cháu của nó đều down (tận dụng tính đơn điệu của tập con cháu khi
đi lên cây để dừng sớm, không cần duyệt hết root mỗi lần).

## Alerting (Telegram)

- `alerting/notifier.py`: `send_alert(text)` — push 1 chiều qua HTTP `sendMessage`,
  dùng trong `main_collector.py` (không polling, không xung đột với bot tra cứu).
- `alerting/bot/app.py`: bot tương tác, polling `getUpdates`, chạy tiến trình
  **riêng**: `python -m alerting.bot.app`.
- Mở rộng thêm lệnh tra cứu: tạo file mới trong `alerting/bot/handlers/`, viết
  hàm `async def`, gắn `@command("ten_lenh", "mô tả")`, rồi thêm 1 dòng import
  trong `alerting/bot/handlers/__init__.py`. Không cần sửa `app.py`.
- Quyền dùng lệnh: whitelist theo Telegram `user_id` trong `TELEGRAM_ADMIN_IDS`
  (`.env`), để trống = cho phép tất cả (chỉ nên dùng khi test).
