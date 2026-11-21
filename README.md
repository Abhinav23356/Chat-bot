# Lyftr AI Chat

A small full-stack AI chat app built for the Lyftr assignment. It uses React and Vite in the browser, FastAPI and LiteLLM in the backend, and SQLite for session history. Each page load gets a fresh session ID. The optional system prompt is remembered for that session.

## 1. Live URLs

- Frontend: not deployed yet. Local URL: http://localhost:3000
- Backend: not deployed yet. Local URL: http://localhost:8000
- Public repository: not published yet

The local application is ready to run. A live deployment and public repository require a configured hosting/GitHub account and two provider API keys; no credentials or destination were supplied with the assignment. The frontend is built with `VITE_API_BASE_URL` set at image build time, so configure it to the public backend URL when deploying.

## 2. Run locally: with Docker and without

### With Docker

1. Copy `.env.example` to `.env` and set `LLM_API_KEYS` to at least two valid provider keys. Keys are tried in order. Keep `.env` private.
2. Start both services from this directory:

   ```sh
   make up
   ```

3. Open http://localhost:3000. The backend listens at http://localhost:8000 and stores SQLite data at `/data/app.db` in the named `sqlite_data` volume.
4. Stop the services with `make down`. This keeps the named data volume. To intentionally remove stored history, use `docker compose down -v`.

### Without Docker

- Backend: from `backend/`, create a Python 3.12 virtual environment, install `requirements.txt`, set the variables from `.env.example` in your shell (use `DATABASE_URL=sqlite:///./app.db` for a local file), then run `uvicorn app.main:app --reload --port 8000`.
- Frontend: from `frontend/`, run `npm install` and `npm run dev -- --port 3000`. Set `VITE_API_BASE_URL` before starting Vite if the backend is elsewhere.

## 3. Design decisions: key fallback, caching, SQLite session storage

- **Key fallback:** `LLM_API_KEYS` is parsed in order as `key_1`, `key_2`, and so on. Provider errors (including rate limits, quota failures, and 5xx responses) move to the next key. The endpoint returns a stable JSON 503 after all keys fail. Key values are never returned or stored in conversation metadata. The default Gemini model is `gemini/gemini-3.5-flash-lite`.
- **Caching:** LiteLLM's exact-match local in-memory cache is enabled. LiteLLM hashes the complete model request, including system prompt and full message history, so another session's identical first turn can hit the cache while a changed conversation cannot. Cache hits retain usage, have `key_id: null`, and report zero server inference latency. Failure simulation skips cache lookup to exercise the key route on demand.
- **SQLite:** `messages` stores each user and assistant turn in order, with assistant metadata as JSON. A small `sessions` table persists the optional system prompt. The database path comes from `DATABASE_URL`; Docker mounts its parent at `/data`. A single backend worker is used so LiteLLM's in-memory cache is shared for local/demo requests.
- **Errors:** FastAPI validation returns JSON 422 responses. Failed inference is not written to history. The client renders API/network errors in the conversation and keeps the composer available.

## 4. Verification: how to see failover, cache hits and per-message token usage

- **Health:** `curl http://localhost:8000/health` returns `status`, `keys_configured`, and `cache_enabled`.
- **Cache:** POST the same first-turn message and model request twice using two different session IDs. The first reply should say `LIVE_INFERENCE`; the second should say `LLM_CACHE` with the same reply and per-request usage. Use a new message or different system prompt to force a different cache key.
- **Fallback:** enable `ENABLE_FAILURE_SIMULATION=true` (it is enabled by the Docker Compose default), then send `X-Simulate-Failure: key_1=429` with a unique prompt. The response should identify `key_2` and one fallback attempt. `X-Simulate-Failure: *=429` returns `503 {"detail":"all providers unavailable"}` without creating that session.
- **History:** `GET /sessions/{session_id}/messages` returns only user/assistant turns from SQLite, oldest first. System instructions are omitted.
- **Tokens:** each assistant card shows that request's input and output token counts, along with its source, model, and latency. Values are stored per assistant response, not summed across the chat.
- The backend test suite is in `backend/tests/`; run it from `backend/` with `pytest` after installing the development dependencies with `pip install -r requirements-dev.txt`.

## 5. Setup Used: your editor and AI tools

Built in Codex with an AI coding assistant. The app uses a standard Vite/React frontend and FastAPI/LiteLLM backend.

## 6. Bonus features (if any): what you added beyond the requirements and why

- Optional system prompt editor, persisted per session.
- In-app failover toggle that sends the assignment's simulation header, making the key fallback easy to demonstrate.
- Session identifier and backend connection status in the interface.
- Suggested starter prompts and responsive layout.

## Deployment notes: free Railway plan with SQLite

The backend can keep using SQLite; Railway provides one persistent 0.5 GB volume on its $0 Free plan. The plan includes $1 of usage each month and does not require a credit card. Services sleep after 5–10 minutes idle, so the first request may be slower; if the monthly usage credit is exhausted, services stop until the next billing cycle. See [Railway Free plan](https://docs.railway.com/pricing/free-plan).

1. Push this repository to GitHub, then create a Railway project from that repository. Create a backend service with root directory `/backend`; Railway will build `backend/Dockerfile`.
2. In the backend service, add a volume mounted at `/data`.
3. Set these backend variables in Railway (put real provider keys in the dashboard, never in Git):
   - `LLM_API_KEYS`: comma-separated provider API keys
   - `LLM_MODEL=gemini/gemini-3.5-flash-lite`
   - `DATABASE_URL=sqlite:////data/app.db`
   - `ENABLE_FAILURE_SIMULATION=true`
   - `ALLOWED_ORIGINS`: the deployed frontend origin
   - `RAILWAY_RUN_UID=0`: needed because Railway mounts volumes as root and the container otherwise runs as `appuser`
4. Generate a Railway public domain for the backend. The container reads Railway's `PORT` automatically; confirm the service at `https://<backend-domain>/health`.
5. Deploy `frontend/` to a static host such as Cloudflare Pages. Set `VITE_API_BASE_URL=https://<backend-domain>` at build time. Then set the backend's `ALLOWED_ORIGINS` to the resulting frontend origin and redeploy the backend.

SQLite remains persistent only when the database file is under `/data`. Railway's Free allowance is usage-based, not guaranteed always-on hosting. Keep one backend worker so the in-memory LiteLLM cache is shared for demo requests. See [Railway volume docs](https://docs.railway.com/volumes) for mount behavior and permissions.
