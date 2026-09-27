package com.kirana.diagnostics;

import java.io.IOException;
import java.util.Locale;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

/**
 * Adds X-Query-Count and X-DB-Time-Ms to every response, and logs one line per request.
 * The body is buffered so the headers can still be set after the controller has written it
 * (headers must go out before the body). Fine for a dev tool, not for large downloads.
 */
@Component
@ConditionalOnProperty(name = "kirana.diagnostics.query-metrics", havingValue = "true", matchIfMissing = true)
public class QueryMetricsFilter extends OncePerRequestFilter {

    public static final String QUERY_COUNT = "X-Query-Count";
    public static final String DB_TIME = "X-DB-Time-Ms";

    private static final Logger log = LoggerFactory.getLogger(QueryMetricsFilter.class);

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        ContentCachingResponseWrapper buffered = new ContentCachingResponseWrapper(response);
        long start = System.nanoTime();
        QueryMetrics.start();
        try {
            chain.doFilter(request, buffered);
        } finally {
            QueryMetrics.Snapshot sql = QueryMetrics.stop();
            double totalMs = (System.nanoTime() - start) / 1_000_000.0;
            buffered.setHeader(QUERY_COUNT, Integer.toString(sql.statements()));
            buffered.setHeader(DB_TIME, String.format(Locale.ROOT, "%.1f", sql.dbMillis()));
            buffered.copyBodyToResponse();
            if (!request.getRequestURI().startsWith("/actuator")) {
                log.info("{} {} -> {} | {} SQL, {} ms in DB, {} ms total", request.getMethod(), pathWithQuery(request),
                        response.getStatus(), sql.statements(), String.format(Locale.ROOT, "%.1f", sql.dbMillis()),
                        String.format(Locale.ROOT, "%.1f", totalMs));
            }
        }
    }

    private static String pathWithQuery(HttpServletRequest request) {
        String q = request.getQueryString();
        return q == null ? request.getRequestURI() : request.getRequestURI() + "?" + q;
    }
}
