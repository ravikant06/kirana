package com.kirana.warehouse;

/** The warehouse's answer. replayed: it already had a shipment for this idempotency key. */
public record Shipment(String shipmentId, String status, boolean replayed) {
}
