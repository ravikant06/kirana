package com.kirana.messaging;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.ConsumerGroupDescription;
import org.apache.kafka.clients.admin.OffsetSpec;
import org.apache.kafka.clients.admin.TopicDescription;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.common.TopicPartition;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.stereotype.Component;

/**
 * For the Resilience lab: is the broker reachable, which topics, and (Stage 6f) how far behind each
 * consumer group is, and how many dead letters wait to be re-driven.
 *
 * Lag = the partition's end offset minus the group's committed offset: records written that the
 * group has not finished yet. A group that never committed on a partition counts from its start.
 */
@Component
public class KafkaStatus {

    public record Topic(String name, int partitions) {
    }

    /** One consumer group: members = app instances listening now; lag = records not processed yet. */
    public record Group(String name, String topic, String state, int members, long lag) {
    }

    /** waiting = dead letters not re-driven yet. */
    public record DeadLetterTopic(String name, long waiting) {
    }

    public record Snapshot(boolean enabled, boolean reachable, String bootstrap, List<Topic> topics,
                           List<Group> groups, List<DeadLetterTopic> deadLetters, String error) {
    }

    /** Kirana's consumer groups and the topic each reads. */
    private static final Map<String, String> GROUPS = new java.util.LinkedHashMap<>(Map.of(
            PaymentEventsListener.GROUP, Topics.PAYMENTS));
    static {
        GROUPS.put(RefundListener.GROUP, Topics.ORDERS);
        GROUPS.put(FulfilmentListener.GROUP, Topics.ORDERS);
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
            return new Snapshot(false, false, bootstrap, List.of(), List.of(), List.of(), null);
        }
        Map<String, Object> config = new HashMap<>(admin.getConfigurationProperties());
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
            Map<String, Integer> partitionCount = new HashMap<>();
            described.values().forEach(d -> partitionCount.put(d.name(), d.partitions().size()));

            Map<String, ConsumerGroupDescription> groups = client.describeConsumerGroups(GROUPS.keySet())
                    .all().get(2, TimeUnit.SECONDS);
            List<Group> lags = new ArrayList<>();
            for (var g : GROUPS.entrySet()) {
                if (!partitionCount.containsKey(g.getValue())) {
                    continue;
                }
                ConsumerGroupDescription d = groups.get(g.getKey());
                lags.add(new Group(g.getKey(), g.getValue(), d == null ? "UNKNOWN" : String.valueOf(d.groupState()),
                        d == null ? 0 : d.members().size(), lag(client, g.getKey(), g.getValue(), partitionCount.get(g.getValue()))));
            }
            List<DeadLetterTopic> deadLetters = DeadLetters.TOPICS.stream()
                    .filter(partitionCount::containsKey)
                    .map(t -> new DeadLetterTopic(t, lag(client, DeadLetters.REDRIVE_GROUP, t, partitionCount.get(t))))
                    .toList();
            return new Snapshot(true, true, bootstrap, topics, lags, deadLetters, null);
        } catch (Exception e) {
            return new Snapshot(true, false, bootstrap, List.of(), List.of(), List.of(), e.getClass().getSimpleName());
        }
    }

    private static long lag(AdminClient client, String group, String topic, int partitions) {
        try {
            Map<TopicPartition, OffsetAndMetadata> committed = client.listConsumerGroupOffsets(group)
                    .partitionsToOffsetAndMetadata().get(2, TimeUnit.SECONDS);
            Map<TopicPartition, OffsetSpec> latest = new HashMap<>();
            Map<TopicPartition, OffsetSpec> earliest = new HashMap<>();
            for (int p = 0; p < partitions; p++) {
                latest.put(new TopicPartition(topic, p), OffsetSpec.latest());
                earliest.put(new TopicPartition(topic, p), OffsetSpec.earliest());
            }
            var end = client.listOffsets(latest).all().get(2, TimeUnit.SECONDS);
            var start = client.listOffsets(earliest).all().get(2, TimeUnit.SECONDS);
            long lag = 0;
            for (TopicPartition tp : latest.keySet()) {
                OffsetAndMetadata c = committed.get(tp);
                long from = c != null ? c.offset() : start.get(tp).offset();
                lag += Math.max(0, end.get(tp).offset() - from);
            }
            return lag;
        } catch (Exception e) {
            return -1;
        }
    }
}
