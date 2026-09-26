package com.kirana.dto;

import java.util.List;

import org.springframework.data.domain.Page;

/** Our own paging shape, so Spring's PageImpl JSON never becomes part of the contract. */
public record PageResponse<T>(List<T> content, int page, int size, long totalElements, int totalPages) {

    public static <T> PageResponse<T> of(Page<?> page, List<T> content) {
        return new PageResponse<>(content, page.getNumber(), page.getSize(), page.getTotalElements(), page.getTotalPages());
    }
}
