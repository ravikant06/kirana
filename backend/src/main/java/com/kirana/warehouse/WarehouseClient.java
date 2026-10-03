package com.kirana.warehouse;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.Map;

import com.kirana.config.FulfilmentProperties;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Asks the warehouse to ship an order. The Idempotency-Key is derived from our order id, so
 * asking twice for the same order (a retry after a timeout, a redelivered event, the naive path
 * and the consumer both trying) returns the same shipment: the warehouse de-duplicates.
 *
 * That makes this call safe to retry blindly, unlike the payment gateway's refund call (6d),
 * which needed "ask first, then act".
 */
@Component
public class WarehouseClient {

    public record Line(String sku, String name, int quantity) {
    }

    private final FulfilmentProperties props;
    private final HttpClient http;
    private final JsonMapper json;

    public WarehouseClient(FulfilmentProperties props, JsonMapper json) {
        this.props = props;
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(props.connectTimeout()).build();
    }

    public Shipment ship(long orderId, List<Line> lines) {
        String body = json.writeValueAsString(Map.of("order_id", Long.toString(orderId), "items", lines));
        HttpRequest request = HttpRequest.newBuilder(URI.create(props.warehouseUrl().replaceAll("/+$", "") + "/v1/shipments"))
                .timeout(props.readTimeout())
                .header("Content-Type", "application/json")
                .header("X-Api-Key", props.apiKey())
                .header("Idempotency-Key", "kirana-order-" + orderId)
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<String> response;
        try {
            response = http.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new WarehouseUnavailableException("warehouse did not answer: " + e.getClass().getSimpleName(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new WarehouseUnavailableException("warehouse call interrupted", e);
        }
        int status = response.statusCode();
        if (status >= 500 || status == 429) {
            throw new WarehouseUnavailableException("warehouse answered " + status, null);
        }
        if (status >= 400) {
            throw new WarehouseRejectedException("warehouse rejected order " + orderId + " (" + status + "): " + response.body());
        }
        JsonNode r = json.readTree(response.body());
        boolean replayed = response.headers().firstValue("Idempotent-Replayed").map("true"::equalsIgnoreCase).orElse(false);
        return new Shipment(r.get("id").asString(), r.get("status").asString(), replayed);
    }
}
