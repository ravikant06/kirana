package com.kirana.ratelimit;

import com.kirana.cache.CacheKeys;
import com.kirana.config.RateLimitProperties;
import com.kirana.config.RateLimitProperties.Policy;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;
import com.kirana.auth.AuthUser;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * Per-shopper limits on write endpoints (D43), checked before the controller runs, so a
 * refused request costs one Redis call and no database work.
 *   POST /orders                     -> "orders" policy
 *   POST/PUT/DELETE /cart/items/...  -> "cart" policy
 * Keyed by the signed-in user (AuthUser, from the token). Anonymous requests never get here: the
 * permission check runs first and answers 401.
 * Limiter and settings are optional so web-slice tests (no Redis, no properties) still start.
 */
@Component
public class RateLimitInterceptor implements HandlerInterceptor {

    public static final String REMAINING = "X-RateLimit-Remaining";

    private final ObjectProvider<RateLimiter> limiter;
    private final ObjectProvider<RateLimitProperties> props;

    public RateLimitInterceptor(ObjectProvider<RateLimiter> limiter, ObjectProvider<RateLimitProperties> props) {
        this.limiter = limiter;
        this.props = props;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String name = policyName(request);
        AuthUser user = AuthUser.of(request);
        RateLimiter rl = limiter.getIfAvailable();
        RateLimitProperties settings = props.getIfAvailable();
        if (name == null || user == null || rl == null || settings == null) {
            return true;
        }
        long userId = user.id();
        Policy policy = name.equals("orders") ? settings.orders() : settings.cart();
        RateLimiter.Decision d = rl.tryAcquire(CacheKeys.rateLimit(name, userId), policy.limit(), policy.window(),
                System.currentTimeMillis());
        if (d.remaining() >= 0) {
            response.setHeader(REMAINING, Long.toString(d.remaining()));
        }
        if (!d.allowed()) {
            throw new RateLimitedException(name.equals("orders") ? "checkout" : "cart", d.retryAfterMillis());
        }
        return true;
    }

    private static String policyName(HttpServletRequest request) {
        String method = request.getMethod();
        String path = request.getRequestURI();
        if (method.equals("POST") && path.equals("/orders")) {
            return "orders";
        }
        if (!method.equals("GET") && path.startsWith("/cart/items")) {
            return "cart";
        }
        return null;
    }
}
