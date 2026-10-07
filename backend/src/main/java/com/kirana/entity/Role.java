package com.kirana.entity;

import java.util.EnumSet;
import java.util.Set;

import com.kirana.auth.Permission;

/**
 * A role is a named bundle of permissions. Code checks permissions, never role names, so a new
 * role (say SUPPORT: read any order, change nothing) is one line here and no endpoint changes.
 * Matches the CHECK constraint on users.role.
 */
public enum Role {
    SHOPPER(EnumSet.of(Permission.ORDERS_READ, Permission.ORDERS_WRITE, Permission.CART_READ,
            Permission.CART_WRITE, Permission.CHAT)),
    ADMIN(EnumSet.allOf(Permission.class));

    private final Set<Permission> permissions;

    Role(Set<Permission> permissions) {
        this.permissions = permissions;
    }

    public Set<Permission> permissions() {
        return permissions;
    }
}
