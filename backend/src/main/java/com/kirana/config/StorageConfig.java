package com.kirana.config;

import io.minio.BucketExistsArgs;
import io.minio.MakeBucketArgs;
import com.kirana.storage.MinioSigner;
import io.minio.MinioClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class StorageConfig {

    private static final Logger log = LoggerFactory.getLogger(StorageConfig.class);

    /**
     * For the backend's own calls to MinIO (stat, delete, bucket check). Explicit timeouts
     * (Stage 5): the SDK's defaults are 5 minutes, long enough for a hung MinIO to hold
     * request threads until the server stops answering.
     */
    @Bean
    public MinioClient minioClient(StorageProperties props) {
        MinioClient client = MinioClient.builder()
                .endpoint(props.apiEndpointOrDefault())
                .region(props.region())
                .credentials(props.accessKey(), props.secretKey())
                .build();
        long read = props.readTimeout().toMillis();
        client.setTimeout(props.connectTimeout().toMillis(), read, read);
        return client;
    }

    /** For signing browser URLs only: public endpoint, fixed region, no network needed. */
    @Bean
    public MinioSigner minioSigner(StorageProperties props) {
        return new MinioSigner(MinioClient.builder()
                .endpoint(props.endpoint())
                .region(props.region())
                .credentials(props.accessKey(), props.secretKey())
                .build());
    }

    /**
     * Creates the image bucket on startup if it is missing, so a fresh MinIO works
     * without any manual setup. If MinIO is down, startup fails here, on purpose:
     * better to fail at boot than on the first upload.
     */
    @Bean
    public ApplicationRunner ensureBucketExists(MinioClient client, StorageProperties props) {
        return args -> {
            String bucket = props.bucket();
            boolean exists = client.bucketExists(BucketExistsArgs.builder().bucket(bucket).build());
            if (exists) {
                log.info("Storage bucket '{}' found at {}", bucket, props.endpoint());
            } else {
                client.makeBucket(MakeBucketArgs.builder().bucket(bucket).build());
                log.info("Storage bucket '{}' created at {}", bucket, props.endpoint());
            }
        };
    }
}
