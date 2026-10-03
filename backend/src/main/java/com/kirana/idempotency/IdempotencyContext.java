package com.kirana.idempotency;

import java.util.Optional;

/**
 * The key of the request being handled on this thread, for services that mark recovery points.
 * Empty when there is none (jobs, Kafka consumers, tests calling services directly): reach() is
 * then a no-op and past() is empty, so services behave exactly as before.
 *
 * Recovery points used (names are part of the stored state; don't rename them):
 *   cart_updated   CartService.add       the increment committed
 *   order_created  OrderService.place    the order and its stock reservation committed (resource = order id)
 *   order_closed   CheckoutSaga.cancel   the cancellation committed (resource = order id)
 */
public final class IdempotencyContext {

    public static final String CART_UPDATED = "cart_updated";
    public static final String ORDER_CREATED = "order_created";
    public static final String ORDER_CLOSED = "order_closed";

    record Active(long userId, String key, String recoveryPoint, Long resourceId, IdempotencyStore store) {
    }

    private static final ThreadLocal<Active> CURRENT = new ThreadLocal<>();

    private IdempotencyContext() {
    }

    static void bind(Active active) {
        CURRENT.set(active);
    }

    static void clear() {
        CURRENT.remove();
    }

    /**
     * If an earlier attempt of this request already reached this point (and then died before
     * answering), the resource it created; the service skips that work and resumes after it.
     */
    public static Optional<Long> past(String recoveryPoint) {
        Active a = CURRENT.get();
        if (a == null || !recoveryPoint.equals(a.recoveryPoint())) {
            return Optional.empty();
        }
        return Optional.of(a.resourceId() == null ? 0L : a.resourceId());
    }

    /** Call INSIDE the transaction that did the work, so the mark commits (or rolls back) with it. */
    public static void reach(String recoveryPoint, Long resourceId) {
        Active a = CURRENT.get();
        if (a != null) {
            a.store().reach(a.userId(), a.key(), recoveryPoint, resourceId);
        }
    }
}
