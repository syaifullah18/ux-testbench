# 006: Sign in with Google and GitHub, passwords optional

**Context**  
Public mode started with email and password accounts. The passwords were hashed with Werkzeug, but every account was still a credential the instance had to protect, and every one of them depended on SMTP: verification on sign-up, password reset, and invitations. User feedback asked for Google or GitHub sign-in so the instance stores no credentials at all.

**Decision**  
- **Google and GitHub through the OAuth 2.0 authorization code flow with PKCE**, offered when `{GOOGLE,GITHUB}_CLIENT_ID` and `_SECRET` are set (`testbench/oauth.py`). The flow starts from a plain GET link, so CSP `form-action 'self'` stays as it is. A random, single-use `state` in the signed cookie replaces the CSRF token on the callback.
- **Accounts are keyed by the provider's account id** in a new `user_identities` table, not by email. Email links an identity to an existing account the first time only, and only an address the provider has verified counts (Google `email_verified`; GitHub's primary verified address from `/user/emails`).
- **An unproven password account loses its password when the address is proven.** If an account with that address never verified it, someone else may have registered it first; its password and sessions are removed before the real owner is signed in.
- **`PASSWORD_LOGIN` stays `on` by default** so existing accounts keep working during the move, and `off` removes the password form, sign-up with a password, verification and reset. Start-up fails if `off` leaves no provider.
- **Invitations give the owner a link to share**, shown once, and are emailed only when `SMTP_URL` is set. With `PASSWORD_LOGIN=off`, SMTP is optional.

**Alternatives rejected**  
- **Authlib or another OAuth library**: two providers need one redirect, one POST and one or two GETs each. The standard library does it in about 150 lines, keeping the "few new dependencies" rule of the SaaS plan.
- **Verifying Google's ID token ourselves**: needs JWT and key-rotation handling. The userinfo endpoint, called with the access token over TLS, gives the same facts.
- **Dropping `users.password_hash` or making it nullable**: the platform schema has no migration step, and SQLite cannot relax `NOT NULL` without rebuilding the table. Accounts without a password store the sentinel `!nopassword`, which no Werkzeug check can ever match, and `users.has_password()` reads it.
- **Matching on email every time**: an address can move between people at the provider; the provider's account id cannot.
- **Removing passwords at once**: would lock out every existing account until it was linked. Linking is automatic on the first provider sign-in, so `off` can follow once people have signed in that way.

**Costs accepted**  
- Sign-in depends on Google or GitHub being reachable.
- The client secrets are credentials on the server, one per provider instead of one per person.
- Someone whose Google or GitHub address differs from their account address gets a new, empty account rather than their old one. They can link the provider from `/account` while signed in the old way, as long as `PASSWORD_LOGIN` is still `on`.
