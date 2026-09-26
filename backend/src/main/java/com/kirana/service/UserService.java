package com.kirana.service;

import java.util.List;

import com.kirana.dto.UserRequest;
import com.kirana.dto.UserResponse;
import com.kirana.entity.User;
import com.kirana.exception.ConflictException;
import com.kirana.exception.NotFoundException;
import com.kirana.mapper.UserMapper;
import com.kirana.repository.UserRepository;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class UserService {

    private static final String EMAIL_UNIQUE_INDEX = "uq_users_email_lower";

    private final UserRepository users;

    public UserService(UserRepository users) {
        this.users = users;
    }

    @Transactional(readOnly = true)
    public List<UserResponse> list() {
        return users.findAll(Sort.by("id")).stream().map(UserMapper::toResponse).toList();
    }

    /**
     * No "does this email exist?" query first: two requests could both see "no" and both insert.
     * The unique index is the only race-free check, so we insert and translate its violation.
     */
    @Transactional
    public UserResponse create(UserRequest req) {
        String email = req.email().trim();
        try {
            // saveAndFlush: run the INSERT now, inside this try, instead of at commit.
            User user = users.saveAndFlush(new User(req.name().trim(), email));
            return UserMapper.toResponse(user);
        } catch (DataIntegrityViolationException e) {
            if (Constraints.violated(e, EMAIL_UNIQUE_INDEX)) {
                throw new ConflictException("Email already registered",
                        "A shopper with email '%s' already exists".formatted(email));
            }
            throw e;
        }
    }

    /** For other services: the user entity, or 404. */
    public User require(Long id) {
        return users.findById(id).orElseThrow(() -> new NotFoundException("User %d not found".formatted(id)));
    }
}
