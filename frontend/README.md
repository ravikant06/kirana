# frontend

React UI for Kirana. It exercises the backend and makes its behaviour visible.
The API it expects is in [`../docs/api-contract.md`](../docs/api-contract.md).

    npm install
    npm run dev        # http://localhost:5173

The Vite dev server forwards `/api/*` to `http://localhost:8080` and strips the
`/api` prefix, so the backend sees `/products`, not `/api/products`, and needs
no CORS config. To point at another port: `BACKEND_URL=http://localhost:9090 npm run dev`.

Click **Requests** in the top bar to see every call the page makes: status,
latency, the `X-User-Id` header, and request and response bodies.
