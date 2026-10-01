package com.kirana.messaging;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/** For the Resilience lab: how many events wait in the outbox, and why the oldest is stuck. */
@Component
public class OutboxStats {

    public record Snapshot(long waiting, Long oldestWaitingSeconds, String lastError, long published) {
    }

    private final JdbcTemplate jdbc;

    public OutboxStats(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Snapshot snapshot() {
        return jdbc.queryForObject("""
                SELECT count(*) FILTER (WHERE published_at IS NULL) AS waiting,
                       extract(epoch FROM now() - min(created_at) FILTER (WHERE published_at IS NULL))::bigint AS oldest,
                       (SELECT last_error FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT 1) AS last_error,
                       count(*) FILTER (WHERE published_at IS NOT NULL) AS published
                FROM outbox
                """, (rs, i) -> new Snapshot(rs.getLong("waiting"), (Long) rs.getObject("oldest"),
                rs.getString("last_error"), rs.getLong("published")));
    }
}
