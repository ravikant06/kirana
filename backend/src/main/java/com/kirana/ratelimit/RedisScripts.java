package com.kirana.ratelimit;

import java.util.List;

import com.kirana.ratelimit.RateLimiter.Decision;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;

/** Runs a limiter script that returns {allowed, remaining, retryAfterMillis}; fails open. */
final class RedisScripts {

    private static final Logger log = LoggerFactory.getLogger(RedisScripts.class);

    private RedisScripts() {
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    static DefaultRedisScript<List<Long>> script(String lua) {
        return new DefaultRedisScript<>(lua, (Class) List.class);
    }

    static Decision run(StringRedisTemplate redis, DefaultRedisScript<List<Long>> script, String key, Object... args) {
        try {
            String[] argv = new String[args.length];
            for (int i = 0; i < args.length; i++) {
                argv[i] = String.valueOf(args[i]);
            }
            List<Long> r = redis.execute(script, List.of(key), (Object[]) argv);
            return new Decision(r.get(0) == 1, r.get(1), r.get(2));
        } catch (DataAccessException e) {
            log.warn("Rate limiter unavailable, allowing request: {}", e.getMessage());
            return Decision.failOpen();
        }
    }
}
