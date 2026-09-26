package com.kirana.mapper;

import com.kirana.dto.UserResponse;
import com.kirana.entity.User;

public final class UserMapper {

    private UserMapper() {
    }

    public static UserResponse toResponse(User user) {
        return new UserResponse(user.getId(), user.getName(), user.getEmail());
    }
}
