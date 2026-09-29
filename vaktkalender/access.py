"""Who is this? Answered by Cloudflare Access, verified via its signed JWT.

Checking the signature (not just trusting a header) means a misconfigured Access
policy fails closed instead of letting anyone claim any email.
"""

from __future__ import annotations

import jwt

from .config import Config


class NotAuthenticated(Exception):
    pass


class AccessVerifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._jwks: jwt.PyJWKClient | None = None
        if cfg.cf_team_domain:
            self._issuer = f"https://{cfg.cf_team_domain}"
            self._jwks = jwt.PyJWKClient(f"{self._issuer}/cdn-cgi/access/certs", cache_keys=True)

    def email(self, token: str | None) -> str:
        if self.cfg.dev_user_email:
            return self.cfg.dev_user_email.lower()
        if not (self._jwks and self.cfg.cf_audience):
            raise NotAuthenticated("Cloudflare Access er ikke konfigurert.")
        if not token:
            raise NotAuthenticated("Mangler Cloudflare Access-token.")
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.cfg.cf_audience, issuer=self._issuer)
        except jwt.PyJWTError as e:
            raise NotAuthenticated(f"Ugyldig Access-token ({type(e).__name__}).") from None
        email = (claims.get("email") or "").strip().lower()
        if not email:
            raise NotAuthenticated("Access-tokenet mangler e-post.")
        return email
