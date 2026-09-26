package com.kirana.mapper;

import com.kirana.dto.InventoryResponse;
import com.kirana.entity.Inventory;

public final class InventoryMapper {

    private InventoryMapper() {
    }

    public static InventoryResponse toResponse(Inventory inv) {
        return new InventoryResponse(inv.getProductId(), inv.getQuantity(), inv.getUpdatedAt());
    }
}
