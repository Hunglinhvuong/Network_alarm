BEGIN;

ALTER TABLE topo_link
    ADD COLUMN IF NOT EXISTS child_node_id BIGINT,
    ADD COLUMN IF NOT EXISTS parent_node_id BIGINT,
    ADD COLUMN IF NOT EXISTS trans_type VARCHAR(50);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'topo_link'
          AND column_name = 'child_site_id'
    ) THEN
        EXECUTE 'UPDATE topo_link SET child_node_id = child_site_id, parent_node_id = parent_site_id';
        EXECUTE 'ALTER TABLE topo_link DROP COLUMN child_site_id CASCADE';
        EXECUTE 'ALTER TABLE topo_link DROP COLUMN parent_site_id CASCADE';
    END IF;
END
$$;

ALTER TABLE topo_link
    ALTER COLUMN child_node_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'topo_link'::regclass
          AND conname = 'topo_link_child_node_id_key'
    ) THEN
        ALTER TABLE topo_link
            ADD CONSTRAINT topo_link_child_node_id_key UNIQUE (child_node_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'topo_link'::regclass
          AND conname = 'topo_link_child_node_id_fkey'
    ) THEN
        ALTER TABLE topo_link
            ADD CONSTRAINT topo_link_child_node_id_fkey
            FOREIGN KEY (child_node_id) REFERENCES node(node_id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'topo_link'::regclass
          AND conname = 'topo_link_parent_node_id_fkey'
    ) THEN
        ALTER TABLE topo_link
            ADD CONSTRAINT topo_link_parent_node_id_fkey
            FOREIGN KEY (parent_node_id) REFERENCES node(node_id) ON DELETE SET NULL;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_topo_link_parent_active
    ON topo_link(parent_node_id) WHERE is_active = true;

COMMIT;
