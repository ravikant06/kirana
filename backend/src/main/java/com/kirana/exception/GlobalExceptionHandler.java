package com.kirana.exception;

import java.util.List;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpHeaders;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.transaction.CannotCreateTransactionException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

/**
 * The one place exceptions become HTTP responses, all as RFC 7807 ProblemDetail.
 * The parent class already maps Spring MVC's own errors (bad JSON, wrong types,
 * missing X-User-Id header, unknown URL); this class adds ours.
 */
@RestControllerAdvice
public class GlobalExceptionHandler extends ResponseEntityExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    public record FieldError(String field, String message) {
    }

    @ExceptionHandler(NotFoundException.class)
    public ProblemDetail notFound(NotFoundException ex) {
        return problem(HttpStatus.NOT_FOUND, "Not found", ex.getMessage());
    }

    @ExceptionHandler(ConflictException.class)
    public ProblemDetail conflict(ConflictException ex) {
        return problem(HttpStatus.CONFLICT, ex.getTitle(), ex.getMessage());
    }

    @ExceptionHandler(InvalidFieldException.class)
    public ProblemDetail invalidField(InvalidFieldException ex) {
        return validation(List.of(new FieldError(ex.getField(), ex.getMessage())));
    }

    @ExceptionHandler(StorageException.class)
    public ProblemDetail storage(StorageException ex) {
        log.error("Object storage call failed", ex);
        return problem(HttpStatus.SERVICE_UNAVAILABLE, "Storage unavailable", ex.getMessage());
    }

    /**
     * R3: two saves of one product based on the same version; the second one's
     * "UPDATE ... WHERE version = ?" matched no row.
     */
    @ExceptionHandler(ObjectOptimisticLockingFailureException.class)
    public ProblemDetail optimisticLock(ObjectOptimisticLockingFailureException ex) {
        return problem(HttpStatus.CONFLICT, "Product changed",
                "Someone else saved this product after you opened it. Reload to see their changes.");
    }

    /**
     * No pooled connection became free within hikari.connection-timeout (P6), or the database
     * is unreachable. 503 tells the client to retry later; the default would have been a 500.
     */
    @ExceptionHandler({CannotCreateTransactionException.class, DataAccessResourceFailureException.class})
    public ProblemDetail databaseUnavailable(Exception ex) {
        log.warn("Database unavailable: {}", ex.getMessage());
        return problem(HttpStatus.SERVICE_UNAVAILABLE, "Database busy",
                "No database connection was available. Try again in a moment.");
    }

    /** A constraint the service did not anticipate. Never echo the SQL back to the client. */
    @ExceptionHandler(DataIntegrityViolationException.class)
    public ProblemDetail dataIntegrity(DataIntegrityViolationException ex) {
        log.warn("Constraint violation: {}", ex.getMostSpecificCause().getMessage());
        return problem(HttpStatus.CONFLICT, "Conflict", "The change conflicts with existing data");
    }

    @ExceptionHandler(Exception.class)
    public ProblemDetail unexpected(Exception ex) {
        log.error("Unhandled exception", ex);
        return problem(HttpStatus.INTERNAL_SERVER_ERROR, "Internal error", "Something went wrong on our side");
    }

    /** Bean validation failures on @Valid bodies: one entry per field. */
    @Override
    protected ResponseEntity<Object> handleMethodArgumentNotValid(
            MethodArgumentNotValidException ex, HttpHeaders headers, HttpStatusCode status, WebRequest request) {
        List<FieldError> errors = ex.getBindingResult().getFieldErrors().stream()
                .map(e -> new FieldError(e.getField(), e.getDefaultMessage()))
                .toList();
        return handleExceptionInternal(ex, validation(errors), headers, status, request);
    }

    @Override
    protected ResponseEntity<Object> handleHttpMessageNotReadable(
            HttpMessageNotReadableException ex, HttpHeaders headers, HttpStatusCode status, WebRequest request) {
        ProblemDetail body = problem(HttpStatus.BAD_REQUEST, "Malformed request",
                "The request body is missing, is not valid JSON, or has a value of the wrong type");
        return handleExceptionInternal(ex, body, headers, status, request);
    }

    private static ProblemDetail validation(List<FieldError> errors) {
        ProblemDetail pd = problem(HttpStatus.BAD_REQUEST, "Validation failed", "One or more fields are invalid");
        pd.setProperty("errors", errors);
        return pd;
    }

    private static ProblemDetail problem(HttpStatus status, String title, String detail) {
        ProblemDetail pd = ProblemDetail.forStatusAndDetail(status, detail);
        pd.setTitle(title);
        return pd;
    }
}
