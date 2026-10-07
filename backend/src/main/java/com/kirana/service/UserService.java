package com.kirana.service;

import java.util.List;
import java.util.Locale;

import com.kirana.dto.UserRequest;
import com.kirana.dto.UserResponse;
import com.kirana.entity.User;
import com.kirana.exception.ConflictException;
import com.kirana.exception.NotFoundException;
import com.kirana.mapper.UserMapper;
import com.kirana.repository.UserRepository;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class UserService {

    private static final String EMAIL_UNIQUE_INDEX = "uq_users_email_lower";

    private final UserRepository users;
    // Cost 10: ~70 ms per check. Slow on purpose: it is what makes a stolen hash expensive to guess.
    private final BCryptPasswordEncoder passwords = new BCryptPasswordEncoder(10);
    /** Checked when the email is unknown, so "no such user" takes as long as "wrong password". */
    private static final String DUMMY_HASH = "$2a$10$dDIOEweO7gFbyqFoCnAewO7gzA03xcVJdlRfdb/1unFP7ss.0ye7K";

    public UserService(UserRepository users) {
        this.users = users;
    }

    static final int MAX_LIMIT = 50;

    /**
     * Newest shoppers first, optionally filtered by a substring of name or email. Always
     * bounded: the unbounded Stage 1 list returned all 50k users (3.9 MB) on every page load.
     */
    @Transactional(readOnly = true)
    public List<UserResponse> search(String query, int limit) {
        Limit max = Limit.of(Math.clamp(limit, 1, MAX_LIMIT));
        List<User> found = query == null || query.isBlank()
                ? users.findByOrderByIdDesc(max)
                : users.search("%" + escapeLike(query.trim().toLowerCase(Locale.ROOT)) + "%", max);
        return found.stream().map(UserMapper::toResponse).toList();
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
            String hash = req.password() == null ? null : passwords.encode(req.password());
            User user = users.saveAndFlush(new User(req.name().trim(), email, hash));
            return UserMapper.toResponse(user);
        } catch (DataIntegrityViolationException e) {
            if (Constraints.violated(e, EMAIL_UNIQUE_INDEX)) {
                throw new ConflictException("Email already registered",
                        "A shopper with email '%s' already exists".formatted(email));
            }
            throw e;
        }
    }

    /** % and _ are LIKE wildcards; a shopper searching for "50%" means the characters. */
    private static String escapeLike(String s) {
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    /**
     * Sign-in. Unknown email and wrong password give the same answer in the same time, so the
     * login form can't be used to find out which emails are registered.
     */
    @Transactional(readOnly = true)
    public User authenticate(String email, String password) {
        User user = users.findByEmailIgnoreCase(email.trim()).orElse(null);
        String hash = user == null || user.getPasswordHash() == null ? DUMMY_HASH : user.getPasswordHash();
        boolean ok = passwords.matches(password, hash);
        if (!ok || user == null || user.getPasswordHash() == null) {
            throw new com.kirana.auth.UnauthenticatedException("Invalid email or password");
        }
        return user;
    }

    /** For other services: the user entity, or 404. */
    public User require(Long id) {
        return users.findById(id).orElseThrow(() -> new NotFoundException("User %d not found".formatted(id)));
    }
}
