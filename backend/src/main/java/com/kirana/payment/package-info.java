/**
 * Payment gateways (Stage 5), the payment counterpart of the storage layer: external systems
 * behind one interface. Nothing here touches the database or runs inside a transaction.
 *
 * Both gateways speak Razorpay's API: the real one in test mode, and payment-mock (a local
 * imitation that can also be made slow, down or flaky). So one HTTP adapter serves both.
 */
package com.kirana.payment;
