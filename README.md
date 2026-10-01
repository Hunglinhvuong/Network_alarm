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
│   └── bot/                    # bot Telegram + collector nền, chạy cùng tiến trình
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

## Triển khai lên máy Linux dùng systemd

Đặt checkout ở đường dẫn ổn định và dùng chính user sở hữu thư mục dự án để cài.
Trên Ubuntu/Debian, cài Python và công cụ PostgreSQL client nếu máy chưa có:

```bash
sudo apt update
sudo apt install python3 python3-venv postgresql-client
```

Tạo `.env` từ mẫu, đặt quyền chỉ chủ sở hữu đọc, rồi điền `PG_*`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_IDS` và `TELEGRAM_ADMIN_IDS`. Đặt
`ALARM_SOURCE=oracle` cùng `ORACLE_*` nếu dùng Oracle. Installer từ chối password
PostgreSQL mặc định và whitelist admin rỗng.

Khởi tạo database mới một lần (đổi host/user/database cho đúng môi trường):

```bash
psql -h localhost -U postgres -d network_alarm -c "CREATE EXTENSION IF NOT EXISTS cube; CREATE EXTENSION IF NOT EXISTS earthdistance;"
psql -h localhost -U postgres -d network_alarm -f schema.sql
psql -h localhost -U postgres -d network_alarm -f db/telegram_outbox.sql
```

Với database đã có, backup trước; nếu `topo_link` còn dùng `child_site_id` /
`parent_site_id`, chạy `db/migrate_topology_nodes.sql`. Luôn áp dụng
`db/telegram_outbox.sql` để tạo/cập nhật outbox. Installer kiểm tra kết nối và
các bảng cần thiết, không tự chạy schema hoặc migration.

Cài và khởi động service:

```bash
test -f .env || cp .env.example .env
chmod 600 .env
# chỉnh .env trước khi tiếp tục
bash deploy/deploy.sh install
```

Nếu `.env` chưa có, script tạo từ `.env.example` rồi dừng để bạn điền cấu hình.
Script tạo virtualenv tại `.venv/` và sinh `network-alarm.service` ngay trong
thư mục dự án. Nó đăng ký unit bằng đường dẫn tuyệt đối; systemd tạo symlink để
tham chiếu file này, không copy unit sang nơi khác. Không di chuyển checkout sau
khi cài; nếu đổi đường dẫn, chạy lại `install`.

Quản lý service bằng `bash deploy/deploy.sh status`, `logs`, `restart` hoặc
`uninstall`. Gỡ service không xóa `.env`, virtualenv hay dữ liệu database. Chỉ
chạy một entry point: service này hoặc `python main_collector.py`, không chạy cả
hai cùng lúc.

Báo cáo tổng hợp mất liên lạc được gửi tới `TELEGRAM_ALERT_CHAT_IDS` cùng nơi với
cảnh báo chủ động. Cấu hình `PERIODIC_REPORT_START_TIME` theo `HH:MM` trong
`APP_TIMEZONE` và `PERIODIC_REPORT_INTERVAL_MINUTES` theo phút; mặc định là
`08:00` và `1440` (mỗi ngày). Lịch được giữ neo theo giờ bắt đầu kể cả khi tiến
trình khởi động lại.

Trước khi chạy collector, áp dụng migration outbox một lần vào database:

```bash
psql -h localhost -p 5432 -U postgres -d network_alarm -f db/telegram_outbox.sql
```

Thay thông tin kết nối trong lệnh theo các giá trị `PG_*` trong `.env`.

`send_alert()` ghi tin vào PostgreSQL outbox; worker gửi tuần tự và giữ tin qua
lần restart. HTTP 429 sẽ tạm dừng toàn bộ bot theo `retry_after`; lỗi mạng/5xx
dùng backoff tăng dần. Tin nhắn dài hơn 4000 ký tự hiển thị được tự chia trang.
Giới hạn mặc định là 1 giây/chat cá nhân, 3 giây/chat nhóm và 20 tin/giây toàn
bot; có thể chỉnh bằng `TELEGRAM_SEND_INTERVAL_SEC`,
`TELEGRAM_GROUP_SEND_INTERVAL_SEC`, `TELEGRAM_GLOBAL_SEND_INTERVAL_SEC`. Tin bị
lỗi vĩnh viễn được giữ trạng thái `failed` trong `telegram_outbox` để tra cứu.

## Dọn dữ liệu định kỳ

Chạy module bảo trì theo lịch hệ điều hành: hàng tuần cho Telegram outbox và
hàng tháng cho lịch sử alarm. Mặc định, tin Telegram `sent` được giữ 30 ngày,
`failed` giữ 90 ngày; alarm `cleared` giữ 180 ngày. Có thể đổi bằng
`TELEGRAM_OUTBOX_SENT_RETENTION_DAYS`,
`TELEGRAM_OUTBOX_FAILED_RETENTION_DAYS`, `ALARM_EVENT_RETENTION_DAYS`; mỗi lần
xóa tối đa 5.000 hàng rồi commit để hạn chế transaction/WAL lớn.

Trước khi khởi động phiên bản mới, áp dụng lại `db/telegram_outbox.sql` để thêm
cột `failed_at` vào database đang tồn tại; migration có thể chạy lặp an toàn.

Trước lần chạy đầu tiên, kiểm tra số lượng đủ điều kiện mà chưa xóa:

```bash
python -m db.cleanup --target all --dry-run
```

Ví dụ cron (sửa đường dẫn và interpreter theo môi trường triển khai):

```cron
30 3 * * 0 cd /opt/network-alarm && .venv/bin/python -m db.cleanup --target telegram-outbox
0 4 1 * * cd /opt/network-alarm && .venv/bin/python -m db.cleanup --target alarm-events
```

Module chỉ xóa outbox `sent`/`failed` đã hết hạn và alarm `cleared`; không xóa
tin `pending`/`sending`, alarm `active`, hay các bảng trạng thái nhỏ
`telegram_outbox_control` và `telegram_chat_limits`. PostgreSQL thường giữ lại
không gian đã xóa để tái sử dụng nội bộ, không nhất thiết giảm kích thước file
database trên SSD. Không chạy `VACUUM FULL` tự động trên ổ gần đầy.

## Chạy thử với CSV (chưa có alarm thật)

Cần chạy **một tiến trình** để vừa poll alarm, vừa nhận lệnh chat 1-1 và gửi
cảnh báo group. Không chạy thêm `main_collector.py` cùng lúc với lệnh dưới đây.

```bash
pip install -r requirements.txt

# Bot polling và collector nền trong cùng tiến trình
python -m alerting.bot.app
```

Sửa `sample_data/alarms.csv` (thêm/xoá dòng) khi ứng dụng đang chạy để test:
alarm mới, auto-clear, đồng bộ nhiều trạm cùng node cha (RCA bước 1
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

Oracle collector giới hạn TCP connect mặc định 5 giây và mỗi call 10 giây; có
thể chỉnh bằng `ORACLE_CONNECT_TIMEOUT_SEC` và `ORACLE_CALL_TIMEOUT_MS`.

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
from escalation.engine import compute_escalation
from rca.engine import analyze_group, analyze_station

group = compute_escalation()[0]
result = analyze_group(group)        # RCA một lần cho cả group full-down
print(result.conclusion)          # sync_parent_node | power_fail | area_power_outage | isolated
print(result.to_text())           # text tiếng Việt, dùng trực tiếp trong alert

station_result = analyze_station(site_id=42)  # RCA theo station, gồm partial-down
```
RCAP xét theo thứ tự bước 1 -> 2 -> 3 và dừng ở bước đầu tiên có bằng chứng.
Full-down được gộp theo escalation group; partial-down được phân tích riêng theo
station và bỏ qua bước 1. Kết quả RCA tính on-demand, không lưu vào DB.

## Escalation

```python
from escalation.engine import compute_escalation

groups = compute_escalation()   # list[EscalationGroup], mỗi group = 1 cảnh báo cần gửi
```
Thuật toán: từ mỗi station đang down, đi lên root, dừng ở node cao nhất mà TOÀN
BỘ station con cháu của nó đều down (tận dụng tính đơn điệu của tập con cháu khi
đi lên cây để dừng sớm, không cần duyệt hết root mỗi lần).

## Alerting (Telegram)

- `alerting/notifier.py`: `send_alert(text)` — xếp tin vào PostgreSQL outbox;
  `alerting/outbox.py` gửi tuần tự và retry độc lập với vòng poll.
- `alerting/bot/app.py`: polling `getUpdates` và khởi động collector nền qua
  lifecycle hooks; chạy duy nhất bằng `python -m alerting.bot.app` để nhận cả
  lệnh tra cứu lẫn cảnh báo push. `main_collector.py` là entry point standalone
  thay thế, không chạy đồng thời với bot app.
- Trong bot app, collector được giám sát và tự thử lại sau lỗi với backoff từ
  5 đến 60 giây; lỗi xử lý từng Telegram update được ghi log mà không dừng polling.
- Mở rộng thêm lệnh tra cứu: tạo file mới trong `alerting/bot/handlers/`, viết
  hàm `async def`, gắn `@command("ten_lenh", "mô tả")`, rồi thêm 1 dòng import
  trong `alerting/bot/handlers/__init__.py`. Không cần sửa `app.py`.
- Quyền dùng lệnh: whitelist theo Telegram `user_id` trong `TELEGRAM_ADMIN_IDS`
  (`.env`), để trống = cho phép tất cả (chỉ nên dùng khi test).

## Bối cảnh và quyết định thiết kế

- Ứng dụng Python dùng PostgreSQL, hướng đến máy Dell Wyse 5010 (RAM 4 GB,
  SSD 16 GB). Giữ mức dùng RAM thấp, không dùng ORM nặng; database cần bật
  autovacuum vì bảng `alarm_event` nhận dữ liệu liên tục.
- Mô hình ứng dụng dùng `node` làm supertype cho `STATION` và `TRANS_NODE`.
  `station` và `trans_node` kế thừa khóa `node_id`; `device` thuộc về một
  station; `topo_link` mô tả quan hệ cha-con; `neighbor_edge` lưu trạm lân cận;
  `alarm_event` lưu `loss_comm` và `power_fail`.
- Dùng khóa số nội bộ (`node_id`, `device_id`) làm PK/FK; giữ `node_code`,
  `site_code`, `device_code` làm mã nghiệp vụ để map dữ liệu ngoài. Mỗi node có
  tối đa một cha; kiểm tra cycle và một root phải được thực hiện ở tầng ứng dụng.
- Thiết kế dữ liệu địa lý dùng `cube` và `earthdistance` thay vì PostGIS.

`schema.sql` và code topology dùng `child_node_id`/`parent_node_id` cùng
`trans_type`, cho phép liên kết cả `STATION` lẫn `TRANS_NODE`. Với database đã
tạo theo schema cũ (`child_site_id`/`parent_site_id`), backup trước rồi chạy
migration một lần:

```bash
psql -h localhost -p 5432 -U postgres -d network_alarm -f db/migrate_topology_nodes.sql
```

Migration giữ lại các liên kết cũ (site ID và node ID dùng chung khóa), thêm
`trans_type` và index cho truy vấn topo. Các script trong `inputdata/` hiện vẫn
có cấu hình kết nối DB riêng, chưa đọc cấu hình tập trung từ `.env`.

## Nạp dữ liệu nền

Các CSV đầu vào nằm trong `inputdata/`:

- `station.csv`: `site_code`, `site_name`, `lat`, `long`, `status`.
- `device.csv`: `device_code`, `device_name`, `site_code`, `type`.
- `trans_node.csv`: `node_code`, `node_name`, `equipment_type`.
- `topo.csv`: `child_node_code`, `parent_node_code`, `trans_type`.

Chạy các script từ thư mục `inputdata/` vì đường dẫn CSV hiện tính theo thư mục
làm việc:

```bash
cd inputdata
python import_initial_data.py
python validate_data.py
python neighbor_edge.py
```

`import_initial_data.py` upsert station, node, device và topology; kiểm tra cây
trước khi ghi topology nhưng hiện không prune các bản ghi đã bị xóa khỏi CSV.
`validate_data.py` là bước riêng để kiểm tra toàn vẹn sau import.
`neighbor_edge.py` tạo cạnh Delaunay rồi lọc theo khoảng cách Haversine tối đa
7 km; cần ít nhất 4 station active. Hãy kiểm tra schema và cấu hình DB của các
script trước khi chạy trên môi trường thật.

## Quy tắc RCA chi tiết

Đánh giá trạng thái theo station:

- **Full-down:** mọi device trong station đều có `loss_comm` active.
- **Partial-down:** chỉ một phần device có `loss_comm` active; cảnh báo kèm loại
  device bị ảnh hưởng và không đưa station này vào escalation tree.
- Nếu bất kỳ device nào có `power_fail` active, station được đánh dấu có ảnh
  hưởng mất điện.

RCAP lần lượt kiểm tra:

1. Với full-down, tìm mất liên lạc đồng bộ trong cùng nhánh/node cha trong cửa sổ
   `NODE_SYNC_WINDOW_MINUTES` (mặc định 10 phút). Với group nhiều station, kết
   luận đồng bộ chỉ áp dụng khi mọi station đều full-down và thời điểm mất liên
   lạc của toàn bộ device nằm trong cùng cửa sổ.
2. Tìm `power_fail` xảy ra trong `POWER_FAIL_LOOKBACK_HOURS` giờ gần nhất (mặc
   định 12 giờ), trước `loss_comm` và cách thời điểm đó không quá
   `POWER_FAIL_CORRELATION_HOURS` (mặc định 6 giờ).
3. Kiểm tra các trạm có cạnh trong `neighbor_edge` (bán kính tạo cạnh mặc định
   7 km) có `power_fail` active hay không; kết quả nêu số trạm ảnh hưởng trên
   tổng số trạm lân cận.

Partial-down bỏ qua bước 1, chỉ xét bước 2 rồi bước 3. Mỗi luồng dừng khi tìm
được bằng chứng đầu tiên; nếu không có bằng chứng, kết luận là sự cố cục bộ/chưa
xác định. Với group full-down, RCA được chạy một lần cho cả group escalation.

## Múi giờ ứng dụng

Mặc định toàn hệ thống dùng `Asia/Ho_Chi_Minh`, có thể đổi bằng `APP_TIMEZONE`.
`config/settings.py` đọc biến môi trường (bao gồm `.env`); môi trường đã export
được ưu tiên hơn giá trị trong `.env`.

- `db/connection.py` đặt timezone cho PostgreSQL session khi mở kết nối. Dữ liệu
  `TIMESTAMPTZ` vẫn được PostgreSQL lưu theo UTC; timezone session ảnh hưởng cách
  chuyển đổi khi đọc/ghi.
- Collector và `alerting/bot/app.py` cấu hình timestamp của logging theo
  timezone ứng dụng.
- `alerting/messages.py` dùng `local_now()` để timestamp trong alert hiển thị
  timezone và tên múi giờ.

Kiểm tra sau khi triển khai:

1. Trong DB, chạy `SELECT now();` và xác nhận kết quả theo timezone session.
2. Khởi động `python -m alerting.bot.app`; log khởi động sẽ nêu timezone cấu hình.
3. Phát sinh một alarm thử và xác nhận Telegram hiển thị giờ cùng tên timezone.

Để quay lại UTC, đặt `APP_TIMEZONE=UTC` trong `.env` hoặc môi trường rồi khởi
động lại ứng dụng.

## Hạng mục còn lại và lưu ý vận hành

- Xác nhận schema thật của Oracle trước khi dùng `ALARM_SOURCE=oracle`; adapter
  hiện giả định các cột `DEVICE_CODE`, `ALARM_NAME`, `START_TIME`, `END_TIME`.
- Dashboard map/tree chưa được triển khai.
- `alarm_event` có thể tăng nhanh trên SSD nhỏ; cần thiết kế partition theo
  tháng hoặc job lưu trữ/xóa dữ liệu cũ trước khi vận hành dài hạn.
- Cân nhắc ghi log các trường hợp topology cần người vận hành xử lý, như nhiều
  root hoặc cycle. Cửa sổ đồng bộ node hiện cấu hình qua biến môi trường, chưa
  có bảng cấu hình trong DB.
- Xác nhận danh sách `trans_type` thực tế (hiện thiết kế đề cập fiber,
  microwave, satellite) trước khi chuẩn hóa dữ liệu production.
