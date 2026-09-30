package com.kirana.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * Stage 5 implementation: in-process Spring events, delivered only after the publishing
 * transaction commits (a rolled-back change announces nothing). Lost if the app crashes
 * between commit and delivery; the Stage 6 outbox closes that gap.
 */
@Component
public class SpringOrderEvents implements OrderEvents {

    private static final Logger log = LoggerFactory.getLogger(SpringOrderEvents.class);

    private final ApplicationEventPublisher publisher;

    public SpringOrderEvents(ApplicationEventPublisher publisher) {
        this.publisher = publisher;
    }

    @Override
    public void publish(OrderEvent event) {
        publisher.publishEvent(event);
    }

    /** The only listener for now. fallbackExecution: also deliver when published outside a transaction. */
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT, fallbackExecution = true)
    public void log(OrderEvent event) {
        if (event instanceof OrderEvent.PaymentAfterClose) {
            log.error("REFUND NEEDED: {}", event);
        } else {
            log.info("Order event: {}", event);
        }
    }
}
