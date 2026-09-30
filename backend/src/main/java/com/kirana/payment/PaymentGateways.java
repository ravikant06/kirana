package com.kirana.payment;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.kirana.config.PaymentProperties;
import com.kirana.exception.InvalidFieldException;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

/** The configured gateways, by id. Razorpay is listed but unavailable until its keys are set. */
@Component
public class PaymentGateways {

    private final Map<String, PaymentGateway> byId = new LinkedHashMap<>();
    private final String defaultId;

    public PaymentGateways(PaymentProperties props, JsonMapper json) {
        register(new RazorpayStyleGateway("mock", "Kirana test gateway", props.mock(), props.http(), json));
        register(new RazorpayStyleGateway("razorpay", "Razorpay (test mode)", props.razorpay(), props.http(), json));
        this.defaultId = props.defaultProvider();
    }

    private void register(PaymentGateway g) {
        byId.put(g.id(), g);
    }

    public List<PaymentGateway> all() {
        return List.copyOf(byId.values());
    }

    /** The gateway to use for a new checkout: the one asked for, else the default. */
    public PaymentGateway forCheckout(String requested) {
        String id = requested == null || requested.isBlank() ? defaultId : requested;
        PaymentGateway g = byId.get(id);
        if (g == null || !g.available()) {
            throw new InvalidFieldException("paymentProvider", "'%s' is not an available payment option".formatted(id));
        }
        return g;
    }

    /** The gateway an existing order was created with. */
    public PaymentGateway of(String id) {
        PaymentGateway g = byId.get(id);
        if (g == null) {
            throw new IllegalStateException("Unknown payment provider on order: " + id);
        }
        return g;
    }
}
