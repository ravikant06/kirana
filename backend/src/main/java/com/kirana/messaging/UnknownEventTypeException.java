package com.kirana.messaging;

/**
 * Stage 6f: a consumer received an event type it is supposed to handle but does not know (an
 * older version of the app, a newer producer). Never retried, never silently acknowledged: the
 * record goes to the dead-letter topic, to be re-driven once a version that knows it is deployed.
 */
public class UnknownEventTypeException extends RuntimeException {

    public UnknownEventTypeException(String type) {
        super("Unknown event type '" + type + "'");
    }
}
