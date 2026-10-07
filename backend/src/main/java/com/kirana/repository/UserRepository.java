package com.kirana.repository;

import java.util.List;
import java.util.Optional;

import com.kirana.entity.User;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface UserRepository extends JpaRepository<User, Long> {

    List<User> findByOrderByIdDesc(Limit limit);

    /** lower(email) = lower(?): served by the uq_users_email_lower index. */
    Optional<User> findByEmailIgnoreCase(String email);

    /**
     * Substring match on name or email. A leading % cannot use a B-tree index, so this scans
     * users (about 5 ms at 50k rows). A trigram index (pg_trgm) would fix that if it matters.
     */
    @Query("""
            select u from User u
            where lower(u.name) like :pattern escape '\\' or lower(u.email) like :pattern escape '\\'
            order by u.id desc
            """)
    List<User> search(String pattern, Limit limit);
}
