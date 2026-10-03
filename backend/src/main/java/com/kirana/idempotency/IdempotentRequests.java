package com.kirana.idempotency;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.HexFormat;
import java.util.function.Function;
import java.util.function.Supplier;

import com.kirana.exception.InvalidFieldException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

/**
 * Runs one request at most once per Idempotency-Key (Stage 7, D69).
 *
 *   no key                         400  (required on every mutating shopper endpoint)
 *   new key                        run it; store the response with the key
 *   key seen, request finished     replay the stored response (header Idempotent-Replayed: true)
 *   key seen, request still runs   409 + Retry-After: 1
 *   key seen, different request    422
 *   key seen, its attempt died     run it again, resuming after its last recovery point
 *
 * Only successful responses are stored. A failed attempt (4xx or 5xx) is forgotten if it
 * committed nothing, so a retry with the same key runs again; if it committed part of the work,
 * the key stays and the retry resumes from there.
 */
@Component
public class IdempotentRequests {

    public static final String HEADER = "Idempotency-Key";

    private static final Logger log = LoggerFactory.getLogger(IdempotentRequests.class);

    private final IdempotencyStore store;
    private final JsonMapper json;
    private final Duration lockFor;

    public IdempotentRequests(IdempotencyStore store, JsonMapper json,
                              @Value("${kirana.idempotency.lock-timeout:30s}") Duration lockFor) {
        this.store = store;
        this.json = json;
        this.lockFor = lockFor;
    }

    /** 200 OK, no Location. */
    public <T> ResponseEntity<?> execute(Long userId, String key, String endpoint, Object request, Supplier<T> action) {
        return execute(userId, key, endpoint, request, HttpStatus.OK, null, action);
    }

    public <T> ResponseEntity<?> execute(Long userId, String key, String endpoint, Object request, HttpStatus status,
                                         Function<T, URI> location, Supplier<T> action) {
        requireValid(key);
        String hash = fingerprint(endpoint, request == null ? "" : json.writeValueAsString(request));
        IdempotencyStore.Claim claim = store.claim(userId, key, endpoint, hash, lockFor);
        return switch (claim) {
            case IdempotencyStore.Completed done -> {
                log.info("Idempotency-Key {} (user {}): {} already done, stored {} replayed", key, userId, endpoint, done.status());
                ResponseEntity.BodyBuilder replay = ResponseEntity.status(done.status())
                        .contentType(MediaType.APPLICATION_JSON)
                        .header("Idempotent-Replayed", "true");
                if (done.location() != null) {
                    replay.location(URI.create(done.location()));
                }
                yield replay.body(done.body());
            }
            case IdempotencyStore.InProgress p -> throw new IdempotencyInProgressException();
            case IdempotencyStore.Mismatch m -> throw new IdempotencyKeyReusedException(m.endpoint());
            case IdempotencyStore.Claimed c -> run(userId, key, endpoint, status, location, action, c);
        };
    }

    private <T> ResponseEntity<?> run(Long userId, String key, String endpoint, HttpStatus status,
                                      Function<T, URI> location, Supplier<T> action, IdempotencyStore.Claimed claim) {
        if (claim.recoveryPoint() != null) {
            log.warn("Idempotency-Key {} (user {}): an earlier attempt of {} died after '{}' ({}); resuming from there",
                    key, userId, endpoint, claim.recoveryPoint(), claim.resourceId());
        }
        IdempotencyContext.bind(new IdempotencyContext.Active(userId, key, claim.recoveryPoint(), claim.resourceId(), store));
        T result;
        try {
            result = action.get();
        } catch (RuntimeException e) {
            try {
                store.fail(userId, key);
            } catch (RuntimeException storeDown) {
                e.addSuppressed(storeDown); // the claim's lock runs out on its own
            }
            throw e;
        } finally {
            IdempotencyContext.clear();
        }
        URI uri = location == null ? null : location.apply(result);
        // If this fails (database gone), the client gets an error although the work is done. Its
        // retry finds the key IN_PROGRESS until the lock runs out, then resumes from the recovery point.
        store.complete(userId, key, status.value(), json.writeValueAsString(result), uri == null ? null : uri.toString());
        ResponseEntity.BodyBuilder ok = ResponseEntity.status(status);
        if (uri != null) {
            ok.location(uri);
        }
        return ok.body(result);
    }

    private static void requireValid(String key) {
        if (key == null || key.isBlank()) {
            throw new InvalidFieldException(HEADER,
                    "header is required: a unique value per action (for example a UUID), reused when retrying that action");
        }
        if (key.length() > 255) {
            throw new InvalidFieldException(HEADER, "at most 255 characters");
        }
    }

    /** SHA-256 of what was asked: the endpoint (with its ids) and the JSON body. */
    public static String fingerprint(String endpoint, String body) {
        try {
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(sha.digest((endpoint + "\n" + body).getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
