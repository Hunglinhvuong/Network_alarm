-- 1. BẢNG NÚT TỔNG QUÁT (ABSTRACT NODE)
CREATE TABLE node (
    node_id     BIGSERIAL PRIMARY KEY,
    node_code   VARCHAR(20) NOT NULL UNIQUE,
    node_name   VARCHAR(100),
    node_type   VARCHAR(20) NOT NULL CHECK (node_type IN ('STATION', 'TRANS_NODE')),
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 2. BẢNG TRẠM PHÁT SÓNG (KẾ THỪA TỪ NODE)
CREATE TABLE station (
    site_id       BIGINT PRIMARY KEY REFERENCES node(node_id) ON DELETE CASCADE,
    site_code     VARCHAR(30) NOT NULL UNIQUE,
    site_name     VARCHAR(100) NOT NULL,
    lat           DOUBLE PRECISION NOT NULL,
    long          DOUBLE PRECISION NOT NULL,
    status        VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_station_geo ON station USING gist (ll_to_earth(lat, long));

-- 3. BẢNG NÚT TRUYỀN DẪN (KẾ THỪA TỪ NODE)
CREATE TABLE trans_node (
    node_id         BIGINT PRIMARY KEY REFERENCES node(node_id) ON DELETE CASCADE,
    equipment_type  VARCHAR(50)  -- vd: ODF, ring, splitter, repeater...
);

-- 4. BẢNG THIẾT BỊ
CREATE TABLE device (
    device_id     BIGSERIAL PRIMARY KEY,
    device_code   VARCHAR(30) NOT NULL UNIQUE,
    device_name   VARCHAR(100),
    site_id       BIGINT NOT NULL REFERENCES station(site_id) ON DELETE CASCADE,
    type          VARCHAR(5) NOT NULL CHECK (type IN ('3G', '4G', '5G')),
    num_cell      INTEGER CHECK (num_cell IS NULL OR num_cell >= 0),
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_device_site ON device(site_id);

-- Trigger kiểm tra tối đa 3 device/site
CREATE OR REPLACE FUNCTION check_max_device() RETURNS TRIGGER AS $$
BEGIN
    IF (SELECT COUNT(*) FROM device WHERE site_id = NEW.site_id) >= 3 THEN
        RAISE EXCEPTION 'Site % đã có tối đa 3 device', NEW.site_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_max_device
BEFORE INSERT ON device
FOR EACH ROW EXECUTE FUNCTION check_max_device();

-- 5. BẢNG LIÊN KẾT TỎA CÂY (TOPO LINK)
CREATE TABLE topo_link (
    link_id           BIGSERIAL PRIMARY KEY,
    child_node_id     BIGINT NOT NULL UNIQUE REFERENCES node(node_id) ON DELETE CASCADE,
    parent_node_id    BIGINT REFERENCES node(node_id) ON DELETE SET NULL,
    trans_type        VARCHAR(50),
    effective_from    TIMESTAMPTZ DEFAULT now(),
    is_active         BOOLEAN DEFAULT true
);
CREATE INDEX idx_topo_link_parent_active
    ON topo_link(parent_node_id) WHERE is_active = true;

-- 6. BẢNG CẠNH LÂN CẬN (NEIGHBOR EDGE)
CREATE TABLE neighbor_edge (
    edge_id       BIGSERIAL PRIMARY KEY,
    site_id_a     BIGINT NOT NULL REFERENCES station(site_id) ON DELETE CASCADE,
    site_id_b     BIGINT NOT NULL REFERENCES station(site_id) ON DELETE CASCADE,
    distance_km   DOUBLE PRECISION NOT NULL,
    UNIQUE (site_id_a, site_id_b)
);

-- 7. BẢNG CẢNH BÁO (ALARM EVENT)
CREATE TABLE alarm_event (
    alarm_id      BIGSERIAL PRIMARY KEY,
    device_id     BIGINT NOT NULL REFERENCES device(device_id),
    alarm_name    VARCHAR(20) NOT NULL CHECK (alarm_name IN ('loss_comm', 'power_fail')),
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ,
    status        VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cleared')),
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (device_id, alarm_name, start_time)
);
CREATE INDEX idx_alarm_active ON alarm_event(device_id, alarm_name) WHERE status = 'active';
CREATE INDEX idx_alarm_time ON alarm_event(start_time);