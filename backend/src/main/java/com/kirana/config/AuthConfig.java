package com.kirana.config;

import com.kirana.auth.BearerTokenFilter;
import com.kirana.auth.TokenService;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;

/**
 * Puts the token filter first, before anything asks who the caller is. Registered here rather
 * than as a @Component so @WebMvcTest slices (which load filters, not services) stay as they were.
 */
@Configuration
public class AuthConfig {

    @Bean
    FilterRegistrationBean<BearerTokenFilter> bearerTokenFilter(TokenService tokens) {
        var registration = new FilterRegistrationBean<>(new BearerTokenFilter(tokens));
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
        return registration;
    }
}
