-- AI Phase 5: sign-in with a password, and a role per user (D72, D73).
-- password_hash is bcrypt (cost 10). NULL = can't sign in until a password is set.
ALTER TABLE users ADD COLUMN password_hash VARCHAR(100);
ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'SHOPPER'
    CHECK (role IN ('SHOPPER', 'ADMIN'));

-- Dev data only: every existing user gets the demo password "kirana123", user 1 is the admin.
-- On an empty database (tests, a fresh install) both updates touch nothing.
UPDATE users SET password_hash = '$2a$10$dDIOEweO7gFbyqFoCnAewO7gzA03xcVJdlRfdb/1unFP7ss.0ye7K';
UPDATE users SET role = 'ADMIN' WHERE id = 1;
