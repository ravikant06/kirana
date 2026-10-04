package com.kirana.messaging;

import com.kirana.warehouse.WarehouseRejectedException;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.common.TopicPartition;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.kafka.autoconfigure.ConcurrentKafkaListenerContainerFactoryConfigurer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.KafkaException;
import org.springframework.kafka.listener.CommonErrorHandler;
import org.springframework.kafka.listener.ConsumerRecordRecoverer;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.ExponentialBackOff;
import org.springframework.util.backoff.FixedBackOff;
import tools.jackson.core.JacksonException;

/**
 * Topics Kirana owns, created at startup by Spring's KafkaAdmin (the broker has automatic
 * topic creation switched off, so a typo in a topic name fails instead of silently creating
 * a new topic).
 *
 * Partitions are the unit of parallelism: at most one consumer in a group reads a partition,
 * so 3 partitions allow up to 3 consumers to share the work. Records with the same key always
 * go to the same partition, which is what keeps one order's events in order.
 * Replication factor 1 because there is one broker; production uses 3.
 */
@Configuration
@ConditionalOnProperty(name = "kirana.kafka.enabled", havingValue = "true")
public class KafkaConfig {

    private static final Logger log = LoggerFactory.getLogger(KafkaConfig.class);

    @Bean
    NewTopic ordersTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.ORDERS).partitions(partitions).replicas(1).build();
    }

    @Bean
    NewTopic paymentsTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.PAYMENTS).partitions(partitions).replicas(1).build();
    }

    @Bean
    NewTopic catalogTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.CATALOG).partitions(partitions).replicas(1).build();
    }

    /** Written by catalog.v1's consumer (kirana-ai), but created here, next to its topic. */
    @Bean
    NewTopic catalogDeadLetterTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.CATALOG_DLT).partitions(partitions).replicas(1).build();
    }

    @Bean
    NewTopic ordersDeadLetterTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.ORDERS_DLT).partitions(partitions).replicas(1).build();
    }

    @Bean
    NewTopic paymentsDeadLetterTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.PAYMENTS_DLT).partitions(partitions).replicas(1).build();
    }

    /**
     * Stage 6f: where a record goes when a consumer gives up on it: "<topic>-dlt", same partition.
     * Spring adds headers with the original topic/partition/offset, the consumer group and the
     * exception, so a person can see why, fix it, and re-drive it (DeadLetters).
     */
    @Bean
    DeadLetterPublishingRecoverer deadLetterRecoverer(KafkaTemplate<String, String> template) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(template,
                (record, e) -> new TopicPartition(record.topic() + Topics.DLT_SUFFIX, record.partition()));
        recoverer.setLogLevel(KafkaException.Level.ERROR); // "Dead-letter publication" problems are errors
        return recoverer;
    }

    /**
     * What a listener does when handling a record throws: try the same record again 3 more times,
     * 1 s apart (the partition waits meanwhile, which keeps order), then dead-letter it (6f; it
     * used to be logged and dropped). Records that can never succeed skip the retries: a body that
     * isn't valid JSON, or an event type this consumer doesn't know. Spring Boot wires this bean
     * into every @KafkaListener that has no factory of its own.
     */
    @Bean
    CommonErrorHandler kafkaErrorHandler(DeadLetterPublishingRecoverer deadLetters) {
        DefaultErrorHandler handler = new DefaultErrorHandler(logged(deadLetters), new FixedBackOff(1000L, 3));
        handler.addNotRetryableExceptions(JacksonException.class, UnknownEventTypeException.class);
        handler.setCommitRecovered(true); // the dead-lettered record's offset is committed: not redelivered on restart
        return handler;
    }

    /** Dead-letters a record and says so in the log (Spring's recoverer is silent when it works). */
    private static ConsumerRecordRecoverer logged(DeadLetterPublishingRecoverer deadLetters) {
        return (record, e) -> {
            Throwable cause = e.getCause() != null ? e.getCause() : e;
            log.error("Dead-lettered {} p{}@{} (key {}) to {}: {}", record.topic(), record.partition(), record.offset(),
                    record.key(), record.topic() + Topics.DLT_SUFFIX, cause.toString());
            deadLetters.accept(record, e);
        };
    }

    /**
     * Stage 6e: the fulfilment consumer's own container factory, with a different error policy.
     * Warehouse unavailable: retry the same record forever, 1 s doubling to 30 s apart (the call
     * is idempotent, and the outage affects every record alike; see FulfilmentListener). The
     * warehouse rejecting the request (4xx), or a malformed record: never retry, dead-letter it.
     */
    @Bean
    ConcurrentKafkaListenerContainerFactory<Object, Object> fulfilmentListenerFactory(
            ConcurrentKafkaListenerContainerFactoryConfigurer configurer, ConsumerFactory<Object, Object> consumerFactory,
            DeadLetterPublishingRecoverer deadLetters) {
        ConcurrentKafkaListenerContainerFactory<Object, Object> factory = new ConcurrentKafkaListenerContainerFactory<>();
        configurer.configure(factory, consumerFactory); // the same settings as every listener (manual ack, ...)
        ExponentialBackOff backOff = new ExponentialBackOff(1000L, 2.0);
        backOff.setMaxInterval(30_000L); // no max elapsed time: keep trying until the warehouse is back
        DefaultErrorHandler handler = new DefaultErrorHandler(logged(deadLetters), backOff);
        handler.addNotRetryableExceptions(WarehouseRejectedException.class, JacksonException.class);
        handler.setCommitRecovered(true);
        factory.setCommonErrorHandler(handler);
        return factory;
    }
}
