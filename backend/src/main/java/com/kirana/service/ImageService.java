package com.kirana.service;

import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.kirana.dto.ImageResponse;
import com.kirana.dto.UploadRequest;
import com.kirana.dto.UploadTicket;
import com.kirana.entity.ImageStatus;
import com.kirana.entity.Product;
import com.kirana.entity.ProductImage;
import com.kirana.exception.ConflictException;
import com.kirana.exception.NotFoundException;
import com.kirana.exception.StorageException;
import com.kirana.mapper.ProductMapper;
import com.kirana.repository.ProductImageRepository;
import com.kirana.repository.ProductRepository;
import com.kirana.storage.ImageStorage;
import com.kirana.storage.ImageStorage.SignedUpload;
import com.kirana.storage.ImageStorage.StoredObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The three-step upload (D7): request a policy, the browser uploads to MinIO, confirm.
 * Uses TransactionTemplate instead of @Transactional so each database step is its own short
 * transaction and no storage call ever runs while a connection is held.
 */
@Service
public class ImageService {

    private static final Logger log = LoggerFactory.getLogger(ImageService.class);
    private static final Pattern EXTENSION = Pattern.compile("\\.([A-Za-z0-9]{1,10})$");

    private final ProductImageRepository images;
    private final ProductRepository products;
    private final ImageStorage storage;
    private final TransactionTemplate tx;

    public ImageService(ProductImageRepository images, ProductRepository products, ImageStorage storage,
                        TransactionTemplate tx) {
        this.images = images;
        this.products = products;
        this.storage = storage;
        this.tx = tx;
    }

    /**
     * Step 1. Records a PENDING row and signs a policy for a key we choose. The client never
     * picks the key, so it cannot overwrite another product's image.
     *
     * The row is written in its own short transaction and the storage call comes after it
     * commits, so a slow MinIO never holds a database connection (P5). If signing fails,
     * the PENDING row is left for the Stage 8 cleanup job.
     */
    public UploadTicket requestUpload(Long productId, UploadRequest req) {
        ProductImage image = tx.execute(status -> {
            Product product = requireLive(productId);
            String key = "products/%d/%s%s".formatted(productId, UUID.randomUUID(), extension(req.fileName()));
            return images.save(new ProductImage(product, key, req.contentType()));
        });
        SignedUpload signed = storage.signUpload(image.getObjectKey(), req.contentType());
        return new UploadTicket(image.getId(), image.getObjectKey(), signed.expiresAt(), signed.uploadUrl(), signed.formFields());
    }

    /**
     * Step 3. Trusts storage, not the client: statObject tells us the object really exists and
     * its real size. Confirming an already active image just returns it.
     *
     * Three steps, and only the first and last touch the database, each in its own short
     * transaction. The MinIO call in the middle holds no connection, so however long it
     * hangs, other requests still get one (P5).
     */
    public ImageResponse confirm(Long productId, Long imageId) {
        ProductImage pending = tx.execute(status -> {
            requireLive(productId);
            return requireImage(productId, imageId);
        });
        if (pending.getStatus() == ImageStatus.ACTIVE) {
            return toResponse(pending);
        }

        StoredObject stored = storage.stat(pending.getObjectKey())
                .orElseThrow(() -> new ConflictException("Upload not found",
                        "Nothing has been uploaded for image %d yet. Upload the file, then confirm.".formatted(imageId)));

        // R6: lock the product row first, so confirms for one product run one at a time and
        // each reads the true max position. Other products are not blocked.
        ProductImage active = tx.execute(status -> {
            products.lockLive(productId)
                    .orElseThrow(() -> new NotFoundException("Product %d not found".formatted(productId)));
            ProductImage image = requireImage(productId, imageId);
            if (image.getStatus() == ImageStatus.ACTIVE) {
                return image; // confirmed by another request while we were asking storage
            }
            String contentType = stored.contentType() != null ? stored.contentType() : image.getContentType();
            int position = images.findMaxPosition(productId, ImageStatus.ACTIVE) + 1;
            image.activate(stored.sizeBytes(), contentType, position);
            return image;
        });
        return toResponse(active);
    }

    /**
     * Not @Transactional on purpose. The row is deleted and committed first, then the object.
     * If the object delete fails we are left with an orphan file nobody can see (costs storage),
     * never with a row pointing at a missing file (a broken image in the UI).
     */
    public void delete(Long productId, Long imageId) {
        requireLive(productId);
        ProductImage image = requireImage(productId, imageId);
        images.delete(image);
        try {
            storage.delete(image.getObjectKey());
        } catch (StorageException e) {
            log.warn("Image {} row deleted but object {} was not; it is now an orphan", imageId, image.getObjectKey(), e);
        }
    }

    /** Confirmed images of one product, in display order, with fresh read URLs. */
    public List<ImageResponse> activeImages(Long productId) {
        return images.findByProductIdAndStatusOrderByPositionAscIdAsc(productId, ImageStatus.ACTIVE).stream()
                .map(this::toResponse)
                .toList();
    }

    /** productId → URL of its first confirmed image, for many products in one query. */
    public Map<Long, String> thumbnailUrls(Collection<Long> productIds) {
        Map<Long, String> result = new LinkedHashMap<>();
        if (productIds.isEmpty()) {
            return result;
        }
        for (ProductImage img : images.findByProductIdInAndStatusOrderByPositionAscIdAsc(productIds, ImageStatus.ACTIVE)) {
            result.computeIfAbsent(img.getProduct().getId(), id -> storage.readUrl(img.getObjectKey()));
        }
        return result;
    }

    // Looked up here rather than through ProductService, because ProductService depends on
    // this class and a cycle between the two would not start.
    private Product requireLive(Long productId) {
        return products.findByIdAndDeletedAtIsNull(productId)
                .orElseThrow(() -> new NotFoundException("Product %d not found".formatted(productId)));
    }

    private ProductImage requireImage(Long productId, Long imageId) {
        return images.findByIdAndProductId(imageId, productId)
                .orElseThrow(() -> new NotFoundException("Image %d not found on product %d".formatted(imageId, productId)));
    }

    private ImageResponse toResponse(ProductImage image) {
        return ProductMapper.toImage(image, storage.readUrl(image.getObjectKey()));
    }

    private static String extension(String fileName) {
        Matcher m = EXTENSION.matcher(fileName.trim());
        return m.find() ? "." + m.group(1).toLowerCase(Locale.ROOT) : "";
    }
}
