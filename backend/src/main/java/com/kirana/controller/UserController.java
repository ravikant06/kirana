package com.kirana.controller;

import java.util.List;

import com.kirana.dto.UserRequest;
import com.kirana.dto.UserResponse;
import com.kirana.service.UserService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/users")
public class UserController {

    private final UserService users;

    public UserController(UserService users) {
        this.users = users;
    }

    /** Limit is clamped to 1..50. */
    @GetMapping
    public List<UserResponse> search(@RequestParam(required = false) String q,
                                     @RequestParam(defaultValue = "20") int limit) {
        return users.search(q, limit);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public UserResponse create(@Valid @RequestBody UserRequest req) {
        return users.create(req);
    }
}
