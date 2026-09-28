package com.kirana.repository;

import java.time.Instant;

import com.kirana.entity.Inventory;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;

/**
 * Keyed by product ID (shared primary key).
 *
 * Stock changes are atomic conditional updates (Stage 3, R1/R2): the check and the change
 * happen in one statement, against whatever the row holds at that moment. A concurrent
 * writer waits for the row lock, then Postgres re-checks the WHERE on the newest committed
 * row. Return value is rows changed: 1 = applied, 0 = the condition failed.
 * They bypass the persistence context, so an Inventory loaded earlier in the same
 * transaction is stale afterwards; callers read it again if they need it.
 */
public interface InventoryRepository extends JpaRepository<Inventory, Long> {

    @Modifying
    @Query("""
            update Inventory i set i.quantity = i.quantity - :quantity, i.updatedAt = :now
            where i.productId = :productId and i.quantity >= :quantity
            """)
    int decrementIfAvailable(Long productId, int quantity, Instant now);

    @Modifying
    @Query("""
            update Inventory i set i.quantity = i.quantity + :delta, i.updatedAt = :now
            where i.productId = :productId and i.quantity + :delta >= 0
            """)
    int adjustIfValid(Long productId, int delta, Instant now);
}
