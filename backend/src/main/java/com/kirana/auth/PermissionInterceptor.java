package com.kirana.auth;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * Enforces {@link RequiresPermission} before the controller runs. Authorisation lives here, on
 * the server, for every endpoint: the frontend hiding a button is convenience, not security.
 */
@Component
public class PermissionInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        if (!(handler instanceof HandlerMethod method)) {
            return true;
        }
        RequiresPermission required = method.getMethodAnnotation(RequiresPermission.class);
        if (required == null) {
            required = method.getBeanType().getAnnotation(RequiresPermission.class);
        }
        if (required == null) {
            return true;                                   // public endpoint
        }
        AuthUser user = AuthUser.of(request);
        if (user == null) {
            throw new UnauthenticatedException("Sign in first: Authorization: Bearer <token> is required");
        }
        if (!user.can(required.value())) {
            throw new ForbiddenException("Needs permission '%s'; your role %s doesn't have it"
                    .formatted(required.value().scope(), user.role()));
        }
        return true;
    }
}
