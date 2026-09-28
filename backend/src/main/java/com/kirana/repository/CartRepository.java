package com.kirana.repository;

import java.util.Optional;

import com.kirana.entity.Cart;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;

public interface CartRepository extends JpaRepository<Cart, Long> {

    /** Cart, its lines and their products in one query. */
    @Query("""
            select distinct c from Cart c
            left join fetch c.items i
            left join fetch i.product
            where c.user.id = :userId
            """)
    Optional<Cart> findWithItemsByUserId(Long userId);

    @Query("select c.id from Cart c where c.user.id = :userId")
    Optional<Long> findIdByUserId(Long userId);

    /**
     * R5: create the user's cart unless it exists, in one statement. Two first-ever adds at the
     * same moment no longer race: the second waits on the unique index, then does nothing.
     * nextval() runs even when nothing is inserted, so cart IDs have gaps; harmless.
     */
    @Modifying
    @Query(nativeQuery = true, value = """
            insert into carts (id, user_id, created_at, updated_at)
            values (nextval('carts_seq'), :userId, now(), now())
            on conflict (user_id) do nothing
            """)
    int createIfAbsent(Long userId);

    /**
     * R4: add a line, or add to the existing line's quantity, in one statement, so two clicks at
     * the same moment both count. Returns 0 when the new total would exceed :max.
     */
    @Modifying
    @Query(nativeQuery = true, value = """
            insert into cart_items (id, cart_id, product_id, quantity, created_at, updated_at)
            values (nextval('cart_items_seq'), :cartId, :productId, :quantity, now(), now())
            on conflict (cart_id, product_id) do update
                set quantity = cart_items.quantity + excluded.quantity, updated_at = now()
                where cart_items.quantity + excluded.quantity <= :max
            """)
    int addOrIncrement(Long cartId, Long productId, int quantity, int max);
}
