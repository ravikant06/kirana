package com.kirana.config;

import javax.sql.DataSource;

import com.kirana.diagnostics.TimedDataSource;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Stage 2 measurement: wrap the pool so SQL statements can be counted and timed per request. */
@Configuration
@ConditionalOnProperty(name = "kirana.diagnostics.query-metrics", havingValue = "true", matchIfMissing = true)
public class DiagnosticsConfig {

    // static: a BeanPostProcessor must exist before the beans it processes.
    @Bean
    static BeanPostProcessor timedDataSourcePostProcessor() {
        return new BeanPostProcessor() {
            @Override
            public Object postProcessAfterInitialization(Object bean, String beanName) {
                if (bean instanceof DataSource ds && !(bean instanceof TimedDataSource)) {
                    return new TimedDataSource(ds);
                }
                return bean;
            }
        };
    }
}
