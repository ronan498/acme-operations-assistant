-- Acme Operations — schema
-- Idempotent: safe on first boot (postgres entrypoint) and via `make seed`.
-- CHECK constraints over enums: legible to a cold reader, trivial to evolve.

CREATE TABLE IF NOT EXISTS customers (
    id            uuid PRIMARY KEY,
    name          text NOT NULL,
    tier          text NOT NULL CHECK (tier IN ('enterprise', 'mid-market', 'smb')),
    industry      text NOT NULL,
    account_owner text,          -- nullable on purpose: a missing owner is a story beat
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS issues (
    id           uuid PRIMARY KEY,
    customer_id  uuid NOT NULL REFERENCES customers(id),
    title        text NOT NULL,
    description  text NOT NULL,
    status       text NOT NULL CHECK (status IN ('open', 'investigating', 'waiting_on_customer', 'resolved')),
    priority     text NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    assigned_to  text,           -- nullable on purpose
    opened_at    timestamptz NOT NULL,
    closed_at    timestamptz
);

CREATE TABLE IF NOT EXISTS issue_updates (
    id         uuid PRIMARY KEY,
    issue_id   uuid NOT NULL REFERENCES issues(id),
    author     text NOT NULL,
    body       text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS next_actions (
    id          uuid PRIMARY KEY,
    issue_id    uuid NOT NULL REFERENCES issues(id),
    action      text NOT NULL,
    status      text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'in_progress', 'done', 'cancelled')),
    created_by  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text,
    updated_at  timestamptz
);

-- Keycloak stays the identity source of truth; this table mirrors its
-- subject IDs for FK integrity on audit and authorship (see DECISIONS.md).
CREATE TABLE IF NOT EXISTS users (
    id           uuid PRIMARY KEY,   -- equals the Keycloak subject (realm import pins these)
    username     text NOT NULL UNIQUE,
    display_name text NOT NULL,
    email        text NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id uuid NOT NULL REFERENCES users(id),
    role    text NOT NULL CHECK (role IN ('sales_user', 'support_user', 'admin')),
    PRIMARY KEY (user_id, role)
);

-- Not required by the brief (§4.6) — exists because §3.1 demands "auditable".
-- Every tool-dispatch decision lands here, allow AND deny.
CREATE TABLE IF NOT EXISTS audit_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    actor_sub    uuid NOT NULL,
    actor_name   text NOT NULL,
    actor_roles  text[] NOT NULL,
    tool         text NOT NULL,
    args         jsonb NOT NULL DEFAULT '{}',
    decision     text NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason       text NOT NULL,
    latency_ms   numeric,
    request_id   text,
    trace_id     text
);

CREATE INDEX IF NOT EXISTS idx_issues_customer   ON issues(customer_id);
CREATE INDEX IF NOT EXISTS idx_issues_status     ON issues(status);
CREATE INDEX IF NOT EXISTS idx_updates_issue     ON issue_updates(issue_id);
CREATE INDEX IF NOT EXISTS idx_actions_issue     ON next_actions(issue_id);
CREATE INDEX IF NOT EXISTS idx_audit_at          ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_customers_name    ON customers(lower(name));
