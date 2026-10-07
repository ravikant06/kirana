package com.kirana.config;

import java.util.List;

import com.kirana.auth.CurrentUserResolver;
import com.kirana.auth.PermissionInterceptor;
import com.kirana.ratelimit.RateLimitInterceptor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final RateLimitInterceptor rateLimit;
    private final PermissionInterceptor permissions;
    private final CurrentUserResolver currentUser;

    public WebConfig(RateLimitInterceptor rateLimit, PermissionInterceptor permissions, CurrentUserResolver currentUser) {
        this.rateLimit = rateLimit;
        this.permissions = permissions;
        this.currentUser = currentUser;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        // Permissions first: an anonymous or forbidden call never spends a rate-limit token.
        registry.addInterceptor(permissions);
        registry.addInterceptor(rateLimit);
    }

    @Override
    public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
        resolvers.add(currentUser);
    }
}
