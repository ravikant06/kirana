package com.kirana.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

/** Turns on @Scheduled (Stage 5 payment jobs). */
@Configuration
@EnableScheduling
public class SchedulingConfig {
}
