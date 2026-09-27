package com.kirana.diagnostics;

/**
 * Per-request tally of SQL statements, held in a ThreadLocal. Works because one request
 * is handled start to finish on one thread (no @Async, no reactive code here).
 * Statements outside a request (Flyway, startup, health checks) are not counted.
 */
public final class QueryMetrics {

    public record Snapshot(int statements, double dbMillis) {
    }

    private static final class Tally {
        int statements;
        long nanos;
    }

    private static final ThreadLocal<Tally> CURRENT = new ThreadLocal<>();

    private QueryMetrics() {
    }

    static void start() {
        CURRENT.set(new Tally());
    }

    static Snapshot stop() {
        Tally t = CURRENT.get();
        CURRENT.remove();
        return t == null ? new Snapshot(0, 0) : new Snapshot(t.statements, t.nanos / 1_000_000.0);
    }

    static void record(long nanos) {
        Tally t = CURRENT.get();
        if (t != null) {
            t.statements++;
            t.nanos += nanos;
        }
    }
}
