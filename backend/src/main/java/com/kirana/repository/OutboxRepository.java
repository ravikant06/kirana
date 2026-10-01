package com.kirana.repository;

import com.kirana.entity.OutboxMessage;
import org.springframework.data.jpa.repository.JpaRepository;

/** Only inserts go through JPA; the relay reads and updates with plain SQL (OutboxRelay). */
public interface OutboxRepository extends JpaRepository<OutboxMessage, Long> {
}
