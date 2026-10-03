package com.kirana.idempotency;

import java.time.Duration;

/**
 * Where keys live. Postgres (the default, D69): recovery points commit with the work. Redis
 * (7d, for comparison): faster, but it can't share the database's transaction.
 */
public interface IdempotencyStore {

    /** What claim() found. */
    sealed interface Claim {
    }

    /** This attempt owns the key. recoveryPoint != null: an earlier attempt died after reaching it. */
    record Claimed(String recoveryPoint, Long resourceId) implements Claim {
    }

    /** An earlier attempt finished: replay its response. */
    record Completed(int status, String body, String location) implements Claim {
    }

    /** An earlier attempt is still running (and holds the lock). */
    record InProgress() implements Claim {
    }

    /** The key was used for a different request (endpoint or body). */
    record Mismatch(String endpoint) implements Claim {
    }

    Claim claim(long userId, String key, String endpoint, String requestHash, Duration lockFor);

    /** Called inside the business transaction: the work up to this point is done. */
    void reach(long userId, String key, String recoveryPoint, Long resourceId);

    void complete(long userId, String key, int status, String body, String location);

    /** The attempt failed. Nothing committed: forget the key. Something committed: unlock it for a resume. */
    void fail(long userId, String key);
}
