package com.kirana.idempotency;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.List;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The default store (D69). claim, complete and fail run in their own short transactions
 * (REQUIRES_NEW: a claim must be visible to a concurrent duplicate at once). reach() joins the
 * caller's transaction: the recovery point and the work commit together, or neither does.
 */
@Component
@ConditionalOnProperty(name = "kirana.idempotency.store", havingValue = "postgres", matchIfMissing = true)
public class PostgresIdempotencyStore implements IdempotencyStore {

    private final JdbcTemplate jdbc;
    private final TransactionTemplate own;

    public PostgresIdempotencyStore(JdbcTemplate jdbc, PlatformTransactionManager txManager) {
        this.jdbc = jdbc;
        this.own = new TransactionTemplate(txManager);
        this.own.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    @Override
    public Claim claim(long userId, String key, String endpoint, String requestHash, Duration lockFor) {
        return own.execute(s -> {
            Timestamp now = Timestamp.from(Instant.now());
            Timestamp until = Timestamp.from(Instant.now().plus(lockFor));
            // 1. First attempt: the INSERT wins. A concurrent duplicate's INSERT does nothing.
            int inserted = jdbc.update("""
                    INSERT INTO idempotency_keys (user_id, idempotency_key, endpoint, request_hash, status, locked_until)
                    VALUES (?, ?, ?, ?, 'IN_PROGRESS', ?)
                    ON CONFLICT (user_id, idempotency_key) DO NOTHING
                    """, userId, key, endpoint, requestHash, until);
            if (inserted == 1) {
                return new Claimed(null, null);
            }
            // 2. The key exists: lock its row and decide.
            List<Claim> found = jdbc.query("""
                    SELECT endpoint, request_hash, status, recovery_point, resource_id, locked_until,
                           response_status, response_body, response_location
                    FROM idempotency_keys WHERE user_id = ? AND idempotency_key = ? FOR UPDATE
                    """, (rs, i) -> {
                if (!rs.getString("request_hash").equals(requestHash)) {
                    return new Mismatch(rs.getString("endpoint"));
                }
                if ("COMPLETED".equals(rs.getString("status"))) {
                    return new Completed(rs.getInt("response_status"), rs.getString("response_body"),
                            rs.getString("response_location"));
                }
                if (rs.getTimestamp("locked_until").after(now)) {
                    return new InProgress();
                }
                // The attempt that held it died (its lock ran out): take over, resuming where it got to.
                long resource = rs.getLong("resource_id");
                return new Claimed(rs.getString("recovery_point"), rs.wasNull() ? null : resource);
            }, userId, key);
            Claim claim = found.get(0);
            if (claim instanceof Claimed) {
                jdbc.update("UPDATE idempotency_keys SET locked_until = ? WHERE user_id = ? AND idempotency_key = ?",
                        until, userId, key);
            }
            return claim;
        });
    }

    @Override
    public void reach(long userId, String key, String recoveryPoint, Long resourceId) {
        // No transaction of its own: part of the caller's (the work's) transaction.
        jdbc.update("UPDATE idempotency_keys SET recovery_point = ?, resource_id = ? WHERE user_id = ? AND idempotency_key = ?",
                recoveryPoint, resourceId, userId, key);
    }

    @Override
    public void complete(long userId, String key, int status, String body, String location) {
        own.executeWithoutResult(s -> jdbc.update("""
                UPDATE idempotency_keys SET status = 'COMPLETED', response_status = ?, response_body = ?,
                    response_location = ?, completed_at = now()
                WHERE user_id = ? AND idempotency_key = ?
                """, status, body, location, userId, key));
    }

    @Override
    public void fail(long userId, String key) {
        own.executeWithoutResult(s -> {
            // Nothing committed (no recovery point): the key never "happened"; a retry starts fresh.
            int deleted = jdbc.update("""
                    DELETE FROM idempotency_keys
                    WHERE user_id = ? AND idempotency_key = ? AND status = 'IN_PROGRESS' AND recovery_point IS NULL
                    """, userId, key);
            if (deleted == 0) {
                // Some work committed: keep the key, but let the next attempt resume right away.
                jdbc.update("UPDATE idempotency_keys SET locked_until = now() WHERE user_id = ? AND idempotency_key = ?",
                        userId, key);
            }
        });
    }
}
