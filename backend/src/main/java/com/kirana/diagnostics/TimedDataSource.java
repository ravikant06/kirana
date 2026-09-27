package com.kirana.diagnostics;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.sql.CallableStatement;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Statement;

import javax.sql.DataSource;

import org.springframework.jdbc.datasource.DelegatingDataSource;

/**
 * Wraps the connection pool so every JDBC execute call is timed. Plain JDK dynamic
 * proxies, no library: Connection hands out wrapped Statements, and a Statement's
 * execute* methods report to {@link QueryMetrics}.
 *
 * Measured: time inside execute*, which for Postgres includes fetching all rows (the
 * driver reads the whole result unless a fetch size is set). Not measured: waiting for
 * a pooled connection, and Hibernate turning rows into entities.
 */
public class TimedDataSource extends DelegatingDataSource {

    public TimedDataSource(DataSource target) {
        super(target);
    }

    @Override
    public Connection getConnection() throws SQLException {
        return wrapConnection(super.getConnection());
    }

    @Override
    public Connection getConnection(String username, String password) throws SQLException {
        return wrapConnection(super.getConnection(username, password));
    }

    private static Connection wrapConnection(Connection target) {
        InvocationHandler handler = (proxy, method, args) -> {
            Object result = call(target, method, args);
            if (result instanceof CallableStatement s) {
                return wrapStatement(s, CallableStatement.class);
            }
            if (result instanceof PreparedStatement s) {
                return wrapStatement(s, PreparedStatement.class);
            }
            if (result instanceof Statement s) {
                return wrapStatement(s, Statement.class);
            }
            return result;
        };
        return (Connection) Proxy.newProxyInstance(TimedDataSource.class.getClassLoader(),
                new Class<?>[] {Connection.class}, handler);
    }

    private static Object wrapStatement(Statement target, Class<? extends Statement> type) {
        InvocationHandler handler = (proxy, method, args) -> {
            if (!method.getName().startsWith("execute")) {
                return call(target, method, args);
            }
            long start = System.nanoTime();
            try {
                return call(target, method, args);
            } finally {
                QueryMetrics.record(System.nanoTime() - start);
            }
        };
        return Proxy.newProxyInstance(TimedDataSource.class.getClassLoader(), new Class<?>[] {type}, handler);
    }

    private static Object call(Object target, Method method, Object[] args) throws Throwable {
        try {
            return method.invoke(target, args);
        } catch (InvocationTargetException e) {
            throw e.getCause();
        }
    }
}
