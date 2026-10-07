package com.kirana.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;

import com.kirana.entity.Role;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class BearerTokenFilterTest {

    private final TokenService tokens = new TokenService(Duration.ofHours(1));

    /** The AuthUser a controller would see, or "401" if the filter answered itself. */
    private Object callerSeenBy(String authorization, String userHeader) throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/orders");
        if (authorization != null) req.addHeader("Authorization", authorization);
        if (userHeader != null) req.addHeader("X-User-Id", userHeader);
        MockHttpServletResponse res = new MockHttpServletResponse();
        AtomicReference<Object> seen = new AtomicReference<>();
        new BearerTokenFilter(tokens).doFilter(req, res, new MockFilterChain() {
            @Override
            public void doFilter(ServletRequest r, ServletResponse s) {
                seen.set(AuthUser.of((HttpServletRequest) r));
            }
        });
        return res.getStatus() == 401 ? "401" : seen.get();
    }

    @Test
    void validTokenBecomesTheCaller() throws Exception {
        AuthUser user = (AuthUser) callerSeenBy("Bearer " + tokens.issue(1, "Ravi", Role.ADMIN).token(), null);
        assertThat(user.id()).isEqualTo(1);
        assertThat(user.role()).isEqualTo("ADMIN");
    }

    @Test
    void badTokenIs401NeverAnonymous() throws Exception {
        assertThat(callerSeenBy("Bearer not-a-jwt", null)).isEqualTo("401");
    }

    @Test
    void theOldHeaderMeansNothing() throws Exception {
        assertThat(callerSeenBy(null, "1")).isNull();          // anonymous: protected endpoints answer 401
    }
}
