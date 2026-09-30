package com.kirana.storage;

import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

import com.kirana.config.StorageProperties;
import com.kirana.exception.StorageException;
import com.kirana.resilience.Resilience;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.minio.GetPresignedObjectUrlArgs;
import io.minio.MinioClient;
import io.minio.PostPolicy;
import io.minio.RemoveObjectArgs;
import io.minio.StatObjectArgs;
import io.minio.StatObjectResponse;
import io.minio.errors.ErrorResponseException;
import io.minio.http.Method;
import org.springframework.stereotype.Component;

/** All MinIO calls go through here. Checked SDK exceptions become {@link StorageException}. */
@Component
public class ImageStorage {

    /** How long a signed read URL works. Generated per response, never stored. */
    private static final int READ_URL_TTL_HOURS = 1;

    private final MinioClient client;
    private final MinioSigner signer;
    private final Resilience resilience;
    private final StorageProperties props;

    public ImageStorage(MinioClient client, MinioSigner signer, StorageProperties props, Resilience resilience) {
        this.client = client;
        this.signer = signer;
        this.resilience = resilience;
        this.props = props;
    }

    public record SignedUpload(String uploadUrl, Map<String, String> formFields, Instant expiresAt) {
    }

    public record StoredObject(long sizeBytes, String contentType) {
    }

    /**
     * Signs a POST policy (D7). MinIO will accept the upload only if the key matches exactly,
     * Content-Type starts with image/, the size is within 1..max-upload-bytes, and it is not expired.
     */
    public SignedUpload signUpload(String objectKey, String contentType) {
        Instant expiresAt = Instant.now().plus(props.uploadUrlTtl());
        PostPolicy policy = new PostPolicy(props.bucket(), ZonedDateTime.ofInstant(expiresAt, ZoneOffset.UTC));
        policy.addEqualsCondition("key", objectKey);
        policy.addStartsWithCondition("Content-Type", "image/");
        policy.addContentLengthRangeCondition(1, props.maxUploadBytes());
        try {
            Map<String, String> signed = signer.client().getPresignedPostFormData(policy);
            // The SDK returns only the signature fields. The browser must also send the
            // fields the policy constrains, so we add them. Order matters only for the file.
            Map<String, String> form = new LinkedHashMap<>();
            form.put("key", objectKey);
            form.put("Content-Type", contentType);
            form.putAll(signed);
            return new SignedUpload(bucketUrl(), form, expiresAt);
        } catch (Exception e) {
            throw new StorageException("Could not sign the upload policy", e);
        }
    }

    /** What storage actually holds at this key, or empty if nothing was uploaded. */
    public Optional<StoredObject> stat(String objectKey) {
        // Stage 5: breaker + one retry (stat is idempotent). A missing object is a normal
        // answer (empty), not a MinIO failure, so it does not count against the breaker.
        try {
            return resilience.minio(true, () -> statOnce(objectKey));
        } catch (CallNotPermittedException e) {
            throw new StorageException("Storage is failing, so it is not being called for now (circuit open)", e);
        }
    }

    private Optional<StoredObject> statOnce(String objectKey) {
        try {
            StatObjectResponse stat = client.statObject(
                    StatObjectArgs.builder().bucket(props.bucket()).object(objectKey).build());
            return Optional.of(new StoredObject(stat.size(), stat.contentType()));
        } catch (ErrorResponseException e) {
            if ("NoSuchKey".equals(e.errorResponse().code())) {
                return Optional.empty();
            }
            throw new StorageException("Could not check the uploaded object", e);
        } catch (Exception e) {
            throw new StorageException("Could not check the uploaded object", e);
        }
    }

    /** A fresh, time-limited GET URL. Signing is local computation, no network call. */
    public String readUrl(String objectKey) {
        try {
            return signer.client().getPresignedObjectUrl(GetPresignedObjectUrlArgs.builder()
                    .method(Method.GET)
                    .bucket(props.bucket())
                    .object(objectKey)
                    .expiry(READ_URL_TTL_HOURS, TimeUnit.HOURS)
                    .build());
        } catch (Exception e) {
            throw new StorageException("Could not sign a read URL", e);
        }
    }

    /** Deleting a key that does not exist succeeds (S3 semantics). */
    public void delete(String objectKey) {
        try {
            resilience.minio(true, () -> {
                try {
                    client.removeObject(RemoveObjectArgs.builder().bucket(props.bucket()).object(objectKey).build());
                    return null;
                } catch (Exception e) {
                    throw new StorageException("Could not delete the object", e);
                }
            });
        } catch (CallNotPermittedException e) {
            throw new StorageException("Storage is failing, so it is not being called for now (circuit open)", e);
        }
    }

    private String bucketUrl() {
        String endpoint = props.endpoint().replaceAll("/+$", "");
        return endpoint + "/" + props.bucket();
    }
}
