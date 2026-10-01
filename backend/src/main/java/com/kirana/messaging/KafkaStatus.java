package com.kirana.messaging;

import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.TopicDescription;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.stereotype.Component;

/** For the Resilience lab: is the broker reachable, and which topics with how many partitions. */
@Component
public class KafkaStatus {

    public record Topic(String name, int partitions) {
    }

    public record Snapshot(boolean enabled, boolean reachable, String bootstrap, List<Topic> topics, String error) {
    }

    private final boolean enabled;
    private final KafkaAdmin admin;

    public KafkaStatus(@Value("${kirana.kafka.enabled:false}") boolean enabled, KafkaAdmin admin) {
        this.enabled = enabled;
        this.admin = admin;
    }

    public Snapshot snapshot() {
        String bootstrap = String.valueOf(admin.getConfigurationProperties().get(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG));
        if (!enabled) {
            return new Snapshot(false, false, bootstrap, List.of(), null);
        }
        Map<String, Object> config = new java.util.HashMap<>(admin.getConfigurationProperties());
        config.put(AdminClientConfig.REQUEST_TIMEOUT_MS_CONFIG, 2000);
        config.put(AdminClientConfig.DEFAULT_API_TIMEOUT_MS_CONFIG, 2000);
        try (AdminClient client = AdminClient.create(config)) {
            var names = client.listTopics().names().get(2, TimeUnit.SECONDS);
            Map<String, TopicDescription> described = client.describeTopics(names).allTopicNames().get(2, TimeUnit.SECONDS);
            List<Topic> topics = described.values().stream()
                    .filter(d -> !d.name().startsWith("_"))
                    .map(d -> new Topic(d.name(), d.partitions().size()))
                    .sorted(java.util.Comparator.comparing(Topic::name))
                    .toList();
            return new Snapshot(true, true, bootstrap, topics, null);
        } catch (Exception e) {
            return new Snapshot(true, false, bootstrap, List.of(), e.getClass().getSimpleName());
        }
    }
}
