package com.kirana.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record LoginRequest(@NotBlank @Size(max = 255) String email, @NotBlank @Size(max = 72) String password) {
}
