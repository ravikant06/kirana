/**
 * Sign-in, roles and permissions (AI Phase 5, D72-D73).
 *
 *   authentication  POST /auth/login checks email + bcrypt password and issues an RS256 JWT
 *                   (sub, role, scope = permissions, iss, aud, exp). BearerTokenFilter verifies it
 *                   on every request and stores the caller as an AuthUser request attribute.
 *   authorisation   @RequiresPermission on a controller or method (PermissionInterceptor): no
 *                   token → 401, missing permission → 403. Roles are bundles of permissions
 *                   (entity.Role); code checks permissions, never role names.
 *   identity        @CurrentUser Long userId is the only way a controller learns who is calling.
 *                   Ownership (your orders, your cart) is still checked by the services: 404 for
 *                   someone else's order.
 *
 * Why RS256: the AI service verifies tokens with the public key from /.well-known/jwks.json and
 * can't mint them. Gaps kept on purpose: no refresh tokens or revocation (1-hour lifetime), the
 * key lives in memory (restart = sign in again), no password reset.
 */
package com.kirana.auth;
