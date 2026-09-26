package com.kirana.repository;

import com.kirana.entity.Inventory;
import org.springframework.data.jpa.repository.JpaRepository;

/** Keyed by product ID (shared primary key). */
public interface InventoryRepository extends JpaRepository<Inventory, Long> {
}
