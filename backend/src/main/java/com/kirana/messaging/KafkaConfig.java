package com.kirana.messaging;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.listener.CommonErrorHandler;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

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

    @Bean
    NewTopic ordersTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.ORDERS).partitions(partitions).replicas(1).build();
    }

    @Bean
    NewTopic paymentsTopic(@Value("${kirana.kafka.topics.partitions:3}") int partitions) {
        return TopicBuilder.name(Topics.PAYMENTS).partitions(partitions).replicas(1).build();
    }

    /**
     * What a listener does when handling a record throws (Stage 6c): try the same record again
     * 3 more times, 1 s apart (the partition waits meanwhile, which keeps order), then log it and
     * move on. Skipping loses the event for this consumer; 6f adds a dead-letter topic instead.
     * Spring Boot wires this bean into every @KafkaListener.
     */
    @Bean
    CommonErrorHandler kafkaErrorHandler() {
        return new DefaultErrorHandler(new FixedBackOff(1000L, 3));
    }
}
