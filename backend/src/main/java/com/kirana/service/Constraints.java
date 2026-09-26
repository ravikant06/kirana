package com.kirana.service;

import org.hibernate.exception.ConstraintViolationException;

/** Tells which database constraint a failed write broke. */
final class Constraints {

    private Constraints() {
    }

    static boolean violated(Throwable e, String constraintName) {
        for (Throwable t = e; t != null; t = t.getCause()) {
            if (t instanceof ConstraintViolationException cve) {
                return constraintName.equalsIgnoreCase(cve.getConstraintName());
            }
        }
        return false;
    }
}
