/** Hand-rolled OIDC auth-code + PKCE (S256) against Keycloak.
 *
 * prompt=login forces the credential form on every login so the demo can
 * switch users without fighting Keycloak's SSO cookie. Tokens live in
 * sessionStorage: gone when the tab closes, never written to disk.
 */

const KEYCLOAK = "http://localhost:8080";
const REALM = "acme";
const CLIENT_ID = "acme-chat";

const AUTH_URL = `${KEYCLOAK}/realms/${REALM}/protocol/openid-connect/auth`;
const TOKEN_URL = `${KEYCLOAK}/realms/${REALM}/protocol/openid-connect/token`;

type Tokens = { access_token: string; refresh_token?: string; expires_at: number };

function b64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function sha256(text: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return new Uint8Array(digest);
}

function save(tokens: Tokens): void {
  sessionStorage.setItem("acme_tokens", JSON.stringify(tokens));
}

function load(): Tokens | null {
  const raw = sessionStorage.getItem("acme_tokens");
  return raw ? (JSON.parse(raw) as Tokens) : null;
}

export function clearTokens(): void {
  sessionStorage.removeItem("acme_tokens");
}

export async function beginLogin(usernameHint?: string): Promise<void> {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(48)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem("pkce_verifier", verifier);
  sessionStorage.setItem("pkce_state", state);

  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    redirect_uri: window.location.origin + "/",
    scope: "openid",
    state,
    code_challenge: b64url(await sha256(verifier)),
    code_challenge_method: "S256",
    prompt: "login",
  });
  if (usernameHint) params.set("login_hint", usernameHint);
  window.location.assign(`${AUTH_URL}?${params}`);
}

async function tokenRequest(body: URLSearchParams): Promise<Tokens> {
  const resp = await fetch(TOKEN_URL, { method: "POST", body });
  if (!resp.ok) throw new Error(`token endpoint ${resp.status}`);
  const data = await resp.json();
  return {
    access_token: data.access_token,
    refresh_token: data.refresh_token,
    expires_at: Date.now() + data.expires_in * 1000,
  };
}

/** Handles the ?code= redirect leg. Returns true if a login completed. */
export async function completeLoginFromUrl(): Promise<boolean> {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  if (!code) return false;

  const state = url.searchParams.get("state");
  const verifier = sessionStorage.getItem("pkce_verifier");
  if (!verifier || state !== sessionStorage.getItem("pkce_state")) {
    throw new Error("PKCE state mismatch");
  }
  save(
    await tokenRequest(
      new URLSearchParams({
        grant_type: "authorization_code",
        client_id: CLIENT_ID,
        code,
        redirect_uri: window.location.origin + "/",
        code_verifier: verifier,
      }),
    ),
  );
  sessionStorage.removeItem("pkce_verifier");
  sessionStorage.removeItem("pkce_state");
  window.history.replaceState({}, "", "/");
  return true;
}

export async function getAccessToken(): Promise<string | null> {
  const tokens = load();
  if (!tokens) return null;
  if (Date.now() < tokens.expires_at - 30_000) return tokens.access_token;
  if (!tokens.refresh_token) return null;
  try {
    const fresh = await tokenRequest(
      new URLSearchParams({
        grant_type: "refresh_token",
        client_id: CLIENT_ID,
        refresh_token: tokens.refresh_token,
      }),
    );
    save(fresh);
    return fresh.access_token;
  } catch {
    clearTokens();
    return null;
  }
}
