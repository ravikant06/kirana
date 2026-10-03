package com.kirana.service;

import java.time.Instant;
import java.util.List;

import com.kirana.entity.Order;
import com.kirana.entity.OrderStatus;
import com.kirana.repository.OrderRepository;
import com.kirana.warehouse.Shipment;
import com.kirana.warehouse.WarehouseClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Stage 6e: hands a paid order to the warehouse, once.
 *
 * "Once" comes from two places, so no inbox table is needed here (compare refunds, 6d):
 *   - the warehouse de-duplicates by Idempotency-Key (our order id): calling twice is harmless
 *   - recording the shipment id is a conditional UPDATE (WHERE shipment_id IS NULL)
 *
 * The warehouse call is made outside any transaction (it can take seconds). Warehouse errors are
 * thrown to the caller: the Kafka consumer retries them, the naive path just loses the order.
 */
@Service
public class FulfilmentService {

    private static final Logger log = LoggerFactory.getLogger(FulfilmentService.class);

    private final OrderRepository orders;
    private final WarehouseClient warehouse;
    private final TransactionTemplate tx;

    public FulfilmentService(OrderRepository orders, WarehouseClient warehouse, PlatformTransactionManager txManager) {
        this.orders = orders;
        this.warehouse = warehouse;
        // REQUIRES_NEW: the naive path runs in afterCommit(), where a plain REQUIRED transaction
        // would join the one that has just committed, and its UPDATE would never be saved.
        this.tx = new TransactionTemplate(txManager);
        this.tx.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    /** Returns the shipment id, or null if there was nothing to do (not paid, or already sent). */
    public String send(long orderId) {
        record Snapshot(OrderStatus status, String shipmentId, List<WarehouseClient.Line> lines) {
        }
        Snapshot o = tx.execute(s -> {
            Order order = orders.findWithItemsById(orderId).orElseThrow();
            return new Snapshot(order.getStatus(), order.getShipmentId(), order.getItems().stream()
                    .map(i -> new WarehouseClient.Line(Long.toString(i.getProduct().getId()), i.getProductName(), i.getQuantity()))
                    .toList());
        });
        if (o.status() != OrderStatus.PAID || o.shipmentId() != null) {
            return null;
        }
        Shipment shipment = warehouse.ship(orderId, o.lines());
        Integer recorded = tx.execute(s -> orders.markSentToWarehouse(orderId, shipment.shipmentId(), Instant.now()));
        log.info("Fulfilment: order {} sent to the warehouse as {}{}", orderId, shipment.shipmentId(),
                shipment.replayed() ? " (the warehouse already had it: idempotency key replayed)"
                        : recorded != null && recorded == 0 ? " (already recorded by another caller)" : "");
        return shipment.shipmentId();
    }
}
