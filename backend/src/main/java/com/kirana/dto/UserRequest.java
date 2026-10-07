package com.kirana.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * Sign-up. The password is required over HTTP (8 to 72 characters: bcrypt reads only 72 bytes).
 * Code and tests may pass null for a user who can't sign in. No role field: everyone signs up as
 * a SHOPPER; only the database makes an admin.
 */
public record UserRequest(
        @NotBlank @Size(max = 100) String name,
        @NotBlank @Email @Size(max = 255) String email,
        @NotBlank @Size(min = 8, max = 72) String password) {

    public UserRequest(String name, String email) {
        this(name, email, null);
    }
}
