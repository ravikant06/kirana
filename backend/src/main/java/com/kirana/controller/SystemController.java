package com.kirana.controller;

import com.kirana.dto.SystemStatus;
import com.kirana.messaging.KafkaStatus;
import com.kirana.resilience.ChaosControls;
import com.kirana.resilience.Resilience;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * The Resilience lab's endpoints (dev tooling). Status is read-only; the controls change only
 * the local fault injectors (payment-mock, Toxiproxy) and reset breakers.
 */
@RestController
public class SystemController {

    private final Resilience resilience;
    private final ChaosControls chaos;
    private final KafkaStatus kafka;

    public SystemController(Resilience resilience, ChaosControls chaos, KafkaStatus kafka) {
        this.resilience = resilience;
        this.chaos = chaos;
        this.kafka = kafka;
    }

    @GetMapping("/system/status")
    public SystemStatus status() {
        var breakers = resilience.allBreakers().stream().map(SystemController::toBreaker).toList();
        var bulkhead = resilience.checkoutBulkhead();
        return new SystemStatus(resilience.enabled(), chaos.chaosEnabled(), breakers,
                new SystemStatus.Slots(bulkhead.getMetrics().getAvailableConcurrentCalls(),
                        bulkhead.getMetrics().getMaxAllowedConcurrentCalls()),
                chaos.paymentMode(), chaos.networkFaults(), kafka.snapshot());
    }

    @PostMapping("/system/breakers/{name}/reset")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void reset(@PathVariable String name) {
        resilience.reset(name);
    }

    public record PaymentFault(String mode, Integer delayMs, Double failureRate) {
    }

    @PostMapping("/system/chaos/payment")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void paymentFault(@RequestBody PaymentFault f) {
        chaos.setPaymentMode(f.mode(), f.delayMs(), f.failureRate());
    }

    public record NetworkFault(String fault, Integer latencyMs) {
    }

    @PostMapping("/system/chaos/network/{proxy}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void networkFault(@PathVariable String proxy, @RequestBody NetworkFault f) {
        chaos.setNetworkFault(proxy, f.fault(), f.latencyMs());
    }

    private static SystemStatus.Breaker toBreaker(CircuitBreaker b) {
        var m = b.getMetrics();
        return new SystemStatus.Breaker(b.getName(), b.getState().name(), m.getFailureRate(), m.getSlowCallRate(),
                m.getNumberOfBufferedCalls(), m.getNumberOfNotPermittedCalls());
    }
}
