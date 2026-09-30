package com.kirana.controller;

import java.util.List;

import com.kirana.config.PaymentProperties;
import com.kirana.dto.PaymentProviderResponse;
import com.kirana.payment.PaymentGateways;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PaymentController {

    private final PaymentGateways gateways;
    private final PaymentProperties props;

    public PaymentController(PaymentGateways gateways, PaymentProperties props) {
        this.gateways = gateways;
        this.props = props;
    }

    /** Which payment options the checkout can offer; Razorpay shows as unavailable without keys. */
    @GetMapping("/payments/providers")
    public List<PaymentProviderResponse> providers() {
        return gateways.all().stream()
                .map(g -> new PaymentProviderResponse(g.id(), g.label(), g.available(), g.id().equals(props.defaultProvider())))
                .toList();
    }
}
