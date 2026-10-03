package com.kirana.entity;

/** Stage 6d. See V7__refunds.sql for what each state means. */
public enum RefundStatus {
    REQUESTED,
    PENDING,
    PROCESSED,
    FAILED
}
