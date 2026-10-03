-- V8: Stage 6e, fulfilment. A paid order is handed to the warehouse once; the warehouse's
-- shipment id is recorded here (set once: UPDATE ... WHERE shipment_id IS NULL).
ALTER TABLE orders
    ADD COLUMN shipment_id           VARCHAR(100),
    ADD COLUMN sent_to_warehouse_at  TIMESTAMPTZ;
