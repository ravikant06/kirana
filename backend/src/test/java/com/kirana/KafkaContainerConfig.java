package com.kirana;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.testcontainers.kafka.KafkaContainer;

/** A real Kafka broker (same version as compose) for tests that set kirana.kafka.enabled=true. */
@TestConfiguration(proxyBeanMethods = false)
public class KafkaContainerConfig {

    @Bean
    @ServiceConnection
    KafkaContainer kafka() {
        return new KafkaContainer("apache/kafka:4.1.2");
    }
}
