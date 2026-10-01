package com.kirana.dto;

import java.util.List;
import java.util.Map;

import com.kirana.messaging.KafkaStatus;

/** GET /system/status: what the Resilience lab shows. */
public record SystemStatus(
        boolean resilienceEnabled,
        boolean chaosEnabled,
        List<Breaker> breakers,
        Slots checkout,
        Map<String, Object> paymentMockMode,
        Map<String, String> networkFaults,
        KafkaStatus.Snapshot kafka) {

    /** state: CLOSED (normal), OPEN (failing fast), HALF_OPEN (trying again). Rates are % of recent calls, -1 if too few. */
    public record Breaker(String name, String state, float failureRate, float slowCallRate, int recentCalls, long rejectedCalls) {
    }

    public record Slots(int available, int max) {
    }
}
