package com.kirana.ratelimit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Duration;
import java.util.UUID;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * The three algorithms side by side, with exact timestamps: 5 requests per 1000 ms.
 * The interesting moment is the window boundary at t = 11,000 ms.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class RateLimiterIntegrationTest {

    private static final Duration SECOND = Duration.ofSeconds(1);

    @MockitoBean MinioClient minio;

    @Autowired MockMvc mvc;
    @Autowired FixedWindowRateLimiter fixed;
    @Autowired SlidingWindowRateLimiter sliding;
    @Autowired TokenBucketRateLimiter bucket;

    @Test
    void fixedWindowLetsTwiceTheLimitThroughAcrossTheBoundary() {
        String key = key();
        assertThat(allowed(fixed, key, 5, 10_900)).isEqualTo(5); // end of window 10
        assertThat(allowed(fixed, key, 5, 11_000)).isEqualTo(5); // start of window 11
        // 10 requests within 100 ms, although the limit says 5 per second.
    }

    @Test
    void slidingWindowHoldsTheLimitAcrossTheBoundary() {
        String key = key();
        assertThat(allowed(sliding, key, 5, 10_900)).isEqualTo(5);
        assertThat(allowed(sliding, key, 5, 11_000)).isZero();
        assertThat(allowed(sliding, key, 1, 11_901)).isEqualTo(1); // the first request has aged out
    }

    @Test
    void tokenBucketAllowsABurstThenTheSteadyRate() {
        String key = key();
        assertThat(allowed(bucket, key, 6, 20_000)).isEqualTo(5); // burst of 5, the 6th refused
        RateLimiter.Decision refused = bucket.tryAcquire(key, 5, SECOND, 20_000);
        assertThat(refused.retryAfterMillis()).isEqualTo(200); // one token every 200 ms
        assertThat(allowed(bucket, key, 2, 20_200)).isEqualTo(1);
    }

    @Test
    void theSixthCheckoutInAMinuteIsRefusedWith429() throws Exception {
        String user = Long.toString(900_000 + (System.nanoTime() % 1000));
        for (int i = 0; i < 5; i++) {
            mvc.perform(post("/orders").header("X-User-Id", user)).andExpect(status().isNotFound()); // no such user, but counted
        }
        mvc.perform(post("/orders").header("X-User-Id", user))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().exists("Retry-After"))
                .andExpect(jsonPath("$.title").value("Too many requests"));
    }

    private static int allowed(RateLimiter limiter, String key, int attempts, long now) {
        int ok = 0;
        for (int i = 0; i < attempts; i++) {
            if (limiter.tryAcquire(key, 5, SECOND, now).allowed()) {
                ok++;
            }
        }
        return ok;
    }

    private static String key() {
        return "test:rl:" + UUID.randomUUID();
    }
}
