package com.kirana.resilience;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.kirana.exception.InvalidFieldException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Drives the fault injectors for the Resilience lab (dev only):
 *  - payment-mock's /admin/mode: make the gateway API slow, down, flaky or hang.
 *  - Toxiproxy's API: add latency, hang or refuse connections between the backend and
 *    Redis, MinIO or payment-mock. Only effective with the chaos profile, which routes the
 *    backend's traffic through Toxiproxy.
 */
@Component
public class ChaosControls {

    public static final List<String> PROXIES = List.of("redis", "minio", "payment");
    private static final String TOXIC = "kirana";

    private final boolean chaosEnabled;
    private final String toxiproxy;
    private final String mockAdmin;
    private final JsonMapper json;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(1)).build();

    public ChaosControls(@Value("${kirana.chaos.enabled:false}") boolean chaosEnabled,
                         @Value("${kirana.chaos.toxiproxy-url}") String toxiproxy,
                         @Value("${kirana.chaos.payment-mock-admin-url}") String mockAdmin,
                         JsonMapper json) {
        this.chaosEnabled = chaosEnabled;
        this.toxiproxy = toxiproxy;
        this.mockAdmin = mockAdmin;
        this.json = json;
    }

    public boolean chaosEnabled() {
        return chaosEnabled;
    }

    /** payment-mock's current fault mode, or null if the mock is not reachable. */
    public Map<String, Object> paymentMode() {
        try {
            JsonNode n = send("GET", mockAdmin + "/admin/mode", null);
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("mode", n.path("mode").asString());
            m.put("delayMs", n.path("delay_ms").asInt());
            m.put("failureRate", n.path("failure_rate").asDouble());
            return m;
        } catch (IOException e) {
            return null;
        }
    }

    public void setPaymentMode(String mode, Integer delayMs, Double failureRate) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("mode", mode);
        if (delayMs != null) {
            body.put("delay_ms", delayMs);
        }
        if (failureRate != null) {
            body.put("failure_rate", failureRate);
        }
        try {
            send("POST", mockAdmin + "/admin/mode", json.writeValueAsString(body));
        } catch (IOException e) {
            throw new IllegalStateException("payment-mock is not reachable at " + mockAdmin, e);
        }
    }

    /** For each proxy: "normal", "latency 1000 ms", "hang" or "down"; empty when chaos is off. */
    public Map<String, String> networkFaults() {
        Map<String, String> result = new LinkedHashMap<>();
        if (!chaosEnabled) {
            return result;
        }
        for (String proxy : PROXIES) {
            try {
                JsonNode p = send("GET", toxiproxy + "/proxies/" + proxy, null);
                String state = "normal";
                if (!p.path("enabled").asBoolean(true)) {
                    state = "down";
                } else {
                    for (JsonNode t : p.path("toxics")) {
                        state = switch (t.path("type").asString()) {
                            case "latency" -> "latency " + t.path("attributes").path("latency").asInt() + " ms";
                            case "timeout" -> "hang";
                            default -> t.path("type").asString();
                        };
                    }
                }
                result.put(proxy, state);
            } catch (IOException e) {
                result.put(proxy, "toxiproxy unreachable");
            }
        }
        return result;
    }

    /** fault: normal | latency | hang | down. */
    public void setNetworkFault(String proxy, String fault, Integer latencyMs) {
        if (!chaosEnabled) {
            throw new InvalidFieldException("fault", "network faults need the backend started with the chaos profile");
        }
        if (!PROXIES.contains(proxy)) {
            throw new InvalidFieldException("proxy", "must be one of " + PROXIES);
        }
        try {
            // Start from a clean proxy: enabled, no toxics.
            send("POST", toxiproxy + "/proxies/" + proxy, "{\"enabled\": true}");
            for (JsonNode t : send("GET", toxiproxy + "/proxies/" + proxy + "/toxics", null)) {
                send("DELETE", toxiproxy + "/proxies/" + proxy + "/toxics/" + t.path("name").asString(), null);
            }
            switch (fault) {
                case "normal" -> { }
                case "latency" -> send("POST", toxiproxy + "/proxies/" + proxy + "/toxics", json.writeValueAsString(Map.of(
                        "name", TOXIC, "type", "latency", "stream", "downstream", "toxicity", 1.0,
                        "attributes", Map.of("latency", latencyMs == null ? 1000 : latencyMs, "jitter", 0))));
                // timeout with 0: data stops flowing and the connection hangs until the client gives up.
                case "hang" -> send("POST", toxiproxy + "/proxies/" + proxy + "/toxics", json.writeValueAsString(Map.of(
                        "name", TOXIC, "type", "timeout", "stream", "downstream", "toxicity", 1.0,
                        "attributes", Map.of("timeout", 0))));
                // A disabled proxy refuses new connections and closes existing ones.
                case "down" -> send("POST", toxiproxy + "/proxies/" + proxy, "{\"enabled\": false}");
                default -> throw new InvalidFieldException("fault", "must be normal, latency, hang or down");
            }
        } catch (IOException e) {
            throw new IllegalStateException("Toxiproxy is not reachable at " + toxiproxy, e);
        }
    }

    private JsonNode send(String method, String url, String body) throws IOException {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(2))
                .method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
        if (body != null) {
            b.header("Content-Type", "application/json");
        }
        try {
            HttpResponse<String> r = http.send(b.build(), HttpResponse.BodyHandlers.ofString());
            if (r.statusCode() >= 400) {
                throw new IOException(method + " " + url + " -> " + r.statusCode() + " " + r.body());
            }
            return r.body() == null || r.body().isBlank() ? json.createObjectNode() : json.readTree(r.body());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted", e);
        }
    }
}
