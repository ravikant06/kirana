# payment-mock

A local stand-in for Razorpay's test mode (Stage 5). It speaks the same Orders API, hosts a
checkout page, signs successful payments the same way (HMAC-SHA256 of `order_id|payment_id`),
and can be made slow, down, flaky or hanging for failure experiments.

    cd infra && docker compose up -d --build payment-mock      # http://localhost:8090/docs

    # failure modes (the /v1 API only; the checkout page keeps working)
    curl -X POST localhost:8090/admin/mode -H 'Content-Type: application/json' -d '{"mode":"slow","delay_ms":8000}'
    curl -X POST localhost:8090/admin/mode -H 'Content-Type: application/json' -d '{"mode":"normal"}'

Key id and secret are dev-only constants (`rzp_test_kiranamock` / `kirana-mock-secret`),
matching `kirana.payment.mock` in the backend's application.yml. State is in memory.
