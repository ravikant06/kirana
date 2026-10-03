/**
 * Idempotency keys at the API edge (Stage 7). A client sends an Idempotency-Key with every
 * attempt of one action; the first attempt does the work and its response is stored; a repeat
 * gets the stored response. Follows the IETF draft "The Idempotency-Key HTTP Header Field":
 * missing key 400, key reused for a different request 422, first attempt still running 409.
 *
 * Controllers call IdempotentRequests.execute(...). Services mark recovery points inside their
 * own transactions (IdempotencyContext.reach), which makes "the work happened" and "the key
 * knows it" commit together.
 */
package com.kirana.idempotency;
