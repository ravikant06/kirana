package com.kirana.controller;

import com.kirana.auth.Permission;
import com.kirana.auth.RequiresPermission;
import com.kirana.dto.ImageResponse;
import com.kirana.dto.UploadRequest;
import com.kirana.dto.UploadTicket;
import com.kirana.service.ImageService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/products/{productId}/images")
@RequiresPermission(Permission.CATALOG_WRITE)
public class ImageController {

    private final ImageService images;

    public ImageController(ImageService images) {
        this.images = images;
    }

    @PostMapping("/upload-url")
    public UploadTicket requestUpload(@PathVariable Long productId, @Valid @RequestBody UploadRequest req) {
        return images.requestUpload(productId, req);
    }

    @PostMapping("/{imageId}/confirm")
    public ImageResponse confirm(@PathVariable Long productId, @PathVariable Long imageId) {
        return images.confirm(productId, imageId);
    }

    @DeleteMapping("/{imageId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable Long productId, @PathVariable Long imageId) {
        images.delete(productId, imageId);
    }
}
