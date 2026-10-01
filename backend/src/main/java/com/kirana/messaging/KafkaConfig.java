package com.kirana.messaging;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

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
}
