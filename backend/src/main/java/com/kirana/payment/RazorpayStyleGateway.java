package com.kirana.payment;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Map;

import com.kirana.config.PaymentProperties;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * A client for Razorpay's Orders API (https://razorpay.com/docs/api/orders/), used for both the
 * real gateway (test mode) and payment-mock, which imitates the same endpoints:
 *   POST /v1/orders                {amount, currency, receipt}  -> {id, amount, currency, status}
 *   GET  /v1/orders/{id}/payments                                -> {items: [{id, status}]}
 * Authentication is HTTP Basic with key id and key secret.
 *
 * Timeouts are explicit (kirana.payment.http): a gateway that does not answer must not hold a
 * request thread forever. Stage 5 wraps these calls in retries and a circuit breaker.
 */
public class RazorpayStyleGateway implements PaymentGateway {

    private final String id;
    private final String label;
    private final PaymentProperties.Gateway config;
    private final Duration readTimeout;
    private final HttpClient http;
    private final JsonMapper json;

    public RazorpayStyleGateway(String id, String label, PaymentProperties.Gateway config,
                                PaymentProperties.Http timeouts, JsonMapper json) {
        this.id = id;
        this.label = label;
        this.config = config;
        this.readTimeout = timeouts.readTimeout();
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(timeouts.connectTimeout()).build();
    }

    @Override
    public String id() {
        return id;
    }

    @Override
    public String label() {
        return label;
    }

    @Override
    public boolean available() {
        return config.configured();
    }

    @Override
    public GatewayOrder createOrder(long orderId, long amountPaise, String currency) {
        String body = json.writeValueAsString(Map.of(
                "amount", amountPaise,
                "currency", currency,
                "receipt", "kirana-order-" + orderId,
                "notes", Map.of("kirana_order_id", Long.toString(orderId))));
        JsonNode r = send(HttpRequest.newBuilder(uri("/v1/orders"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body)));
        return new GatewayOrder(r.get("id").asString(), r.get("amount").asLong(), r.get("currency").asString());
    }

    @Override
    public GatewayStatus fetchStatus(String gatewayOrderId) {
        JsonNode r;
        try {
            r = send(HttpRequest.newBuilder(uri("/v1/orders/" + gatewayOrderId + "/payments")).GET());
        } catch (GatewayRejectedException e) {
            if (e.status == 404) {
                return new GatewayStatus(GatewayStatus.State.UNKNOWN, null);
            }
            throw e;
        }
        for (JsonNode p : r.path("items")) {
            if ("captured".equals(p.path("status").asString())) {
                return new GatewayStatus(GatewayStatus.State.PAID, p.get("id").asString());
            }
        }
        return new GatewayStatus(GatewayStatus.State.PENDING, null);
    }

    @Override
    public boolean verifySignature(String gatewayOrderId, String paymentId, String signature) {
        if (gatewayOrderId == null || paymentId == null || signature == null) {
            return false;
        }
        byte[] expected = hmacSha256(gatewayOrderId + "|" + paymentId).getBytes(StandardCharsets.UTF_8);
        // Constant-time comparison: timing must not reveal how many leading characters matched.
        return MessageDigest.isEqual(expected, signature.getBytes(StandardCharsets.UTF_8));
    }

    @Override
    public PaymentSession session(long orderId, GatewayOrder order) {
        String checkoutUrl = config.checkoutUrl() == null || config.checkoutUrl().isBlank()
                ? null
                : config.checkoutUrl().replaceAll("/+$", "") + "/checkout/" + order.gatewayOrderId();
        return new PaymentSession(id, orderId, order.gatewayOrderId(), order.amountPaise(), order.currency(),
                config.keyId(), checkoutUrl);
    }

    private JsonNode send(HttpRequest.Builder request) {
        String basic = Base64.getEncoder().encodeToString(
                (config.keyId() + ":" + config.keySecret()).getBytes(StandardCharsets.UTF_8));
        HttpResponse<String> response;
        try {
            response = http.send(request.timeout(readTimeout).header("Authorization", "Basic " + basic).build(),
                    HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            // Connection refused, reset, or the read timeout (HttpTimeoutException is an IOException).
            throw new PaymentUnavailableException(label + " did not answer: " + e.getClass().getSimpleName(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new PaymentUnavailableException(label + " call interrupted", e);
        }
        int status = response.statusCode();
        if (status >= 500 || status == 429) {
            throw new PaymentUnavailableException(label + " answered " + status, null);
        }
        if (status >= 400) {
            throw new GatewayRejectedException(status, label + " rejected the request (" + status + "): " + response.body());
        }
        return json.readTree(response.body());
    }

    private URI uri(String path) {
        return URI.create(config.apiUrl().replaceAll("/+$", "") + path);
    }

    private String hmacSha256(String data) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(config.keySecret().getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return HexFormat.of().formatHex(mac.doFinal(data.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException | InvalidKeyException e) {
            throw new IllegalStateException("HmacSHA256 unavailable", e);
        }
    }

    /** 4xx from the gateway: our request was wrong (bad key, bad id). Not worth retrying. */
    public static class GatewayRejectedException extends RuntimeException {

        final int status;

        GatewayRejectedException(int status, String message) {
            super(message);
            this.status = status;
        }
    }
}
