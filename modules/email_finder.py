"""
modules/email_finder.py

Free-tier email discovery + SMTP-handshake verification.

Cascade order (cheapest to most expensive):
  1. Hunter.io free tier  — 50 unified credits/month (HUNTER_API_KEY)
                            Search + verification SHARE one pool since 2026.
                            One named lookup = 1 credit. Domain-search = 1 credit.
                            We only fall through to domain-search when email-finder
                            returns literally nothing, not just a low score.
  2. Snov.io free tier    — 50 leads/month    (SNOV_CLIENT_ID + SNOV_CLIENT_SECRET)
  3. SMTP RCPT TO probe   — free, DIY         (sends MAIL FROM + RCPT TO, no DATA)

We only ever return personal-looking addresses (first.last@, flast@, etc.).
Role mailboxes (info@, hr@, careers@) are filtered upstream by researcher.

What is SMTP, briefly:
  SMTP (Simple Mail Transfer Protocol) is the protocol email servers use to
  hand mail off to each other. Before delivering a message a server can ask
  the recipient's MX server "do you accept mail for X?" by issuing
  HELO → MAIL FROM → RCPT TO and reading the response code:
      250 = yes, 550 = no such mailbox.
  We use that handshake to validate guesses without actually sending mail.
  Drawbacks: large providers (Gmail, Outlook, Zoho) accept-all and only
  bounce later, so SMTP probes are most useful against self-hosted SMB
  domains, which is exactly our ICP.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import socket
from typing import Iterable

import dns.resolver
import requests

log = logging.getLogger(__name__)

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
SNOV_CLIENT_ID = os.getenv("SNOV_CLIENT_ID")
SNOV_CLIENT_SECRET = os.getenv("SNOV_CLIENT_SECRET")
SMTP_PROBE_FROM = os.getenv("SMTP_PROBE_FROM", "verify@example.com")
SMTP_PROBE_TIMEOUT = 6

_NAME_RE = re.compile(r"[^a-z]")  # strip non-letter chars after lowercasing


def _split_name(full_name: str | None) -> tuple[str, str] | None:
    if not full_name:
        return None
    parts = [p for p in full_name.strip().split() if p]
    if len(parts) < 2:
        return None
    first = _NAME_RE.sub("", parts[0].lower())
    last = _NAME_RE.sub("", parts[-1].lower())
    if not first or not last:
        return None
    return first, last


# Common B2B handle patterns ordered by observed deliverability rate.
def _candidate_locals(first: str, last: str) -> list[str]:
    return [
        f"{first}.{last}",
        f"{first}{last}",
        f"{first}",
        f"{first[0]}{last}",
        f"{first}_{last}",
        f"{first}-{last}",
        f"{last}.{first}",
        f"{first}{last[0]}",
    ]


# ── Hunter.io ────────────────────────────────────────────────────────────────

def _hunter_lookup(domain: str, full_name: str | None) -> str | None:
    if not HUNTER_API_KEY:
        return None
    try:
        email_finder_returned_email = False
        if full_name:
            split = _split_name(full_name)
            if split:
                first, last = split
                r = requests.get(
                    "https://api.hunter.io/v2/email-finder",
                    params={
                        "domain": domain,
                        "first_name": first,
                        "last_name": last,
                        "api_key": HUNTER_API_KEY,
                    },
                    timeout=10,
                )
                if r.ok:
                    data = (r.json() or {}).get("data") or {}
                    email = data.get("email")
                    score = data.get("score") or 0
                    if email:
                        email_finder_returned_email = True
                    if email and score >= 60:  # 60+ = "deliverable" per Hunter
                        return email
        # With the unified 50/mo credit pool (2026), every Hunter call counts.
        # If email-finder already returned an email but flagged it as low-score,
        # don't burn a second credit on domain-search to retrieve emails the same
        # provider just told us aren't deliverable. Cascade falls to Snov + SMTP.
        if email_finder_returned_email:
            return None
        # Fallback: domain-search picks the best email Hunter has on record.
        r = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 5},
            timeout=10,
        )
        if r.ok:
            emails = ((r.json() or {}).get("data") or {}).get("emails", [])
            for e in emails:
                if e.get("type") == "personal" and (e.get("confidence") or 0) >= 60:
                    return e.get("value")
    except Exception as e:
        log.warning("Hunter.io lookup failed for %s: %s", domain, e)
    return None


# ── Snov.io ──────────────────────────────────────────────────────────────────

_snov_token: str | None = None


def _snov_token_get() -> str | None:
    global _snov_token
    if _snov_token:
        return _snov_token
    if not (SNOV_CLIENT_ID and SNOV_CLIENT_SECRET):
        return None
    try:
        r = requests.post(
            "https://api.snov.io/v1/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": SNOV_CLIENT_ID,
                "client_secret": SNOV_CLIENT_SECRET,
            },
            timeout=10,
        )
        if r.ok:
            _snov_token = (r.json() or {}).get("access_token")
            return _snov_token
    except Exception as e:
        log.warning("Snov.io auth failed: %s", e)
    return None


def _snov_lookup(domain: str, full_name: str | None) -> str | None:
    token = _snov_token_get()
    if not token:
        return None
    try:
        params: dict[str, str] = {"domain": domain, "type": "all", "limit": "5", "access_token": token}
        if full_name:
            split = _split_name(full_name)
            if split:
                params["firstName"], params["lastName"] = split
        r = requests.get("https://api.snov.io/v2/domain-emails-with-info", params=params, timeout=10)
        if r.ok:
            emails = (r.json() or {}).get("emails", [])
            for e in emails:
                addr = e.get("email")
                if addr and (e.get("emailType") or e.get("type")) == "personal":
                    return addr
            if emails:
                return emails[0].get("email")
    except Exception as e:
        log.warning("Snov.io lookup failed for %s: %s", domain, e)
    return None


# ── SMTP RCPT-TO probe ───────────────────────────────────────────────────────

def _mx_for(domain: str) -> str | None:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=4)
        # dnspython sorts by preference already
        ranked = sorted(answers, key=lambda r: r.preference)
        return str(ranked[0].exchange).rstrip(".")
    except Exception:
        return None


# Hosts that happily 250 every RCPT TO and bounce later. Probing them
# against guessed locals is worthless — short-circuit and skip.
_ACCEPT_ALL_HOSTS = (
    "google.com", "googlemail.com", "outlook.com", "hotmail.com",
    "zoho.com", "yandex.net", "mail.protection.outlook.com",
    "registrar-servers.com",  # Namecheap fwd
)


def smtp_probe(email: str, mx_host: str | None = None) -> bool:
    """RCPT TO probe — True if the MX server says the mailbox exists."""
    try:
        domain = email.split("@", 1)[1]
    except IndexError:
        return False
    host = mx_host or _mx_for(domain)
    if not host:
        return False
    if any(h in host.lower() for h in _ACCEPT_ALL_HOSTS):
        return False  # cannot trust positive result
    try:
        with smtplib.SMTP(host, 25, timeout=SMTP_PROBE_TIMEOUT) as smtp:
            smtp.helo("verify")
            smtp.mail(SMTP_PROBE_FROM)
            code, _ = smtp.rcpt(email)
            return 200 <= code < 300
    except (smtplib.SMTPException, socket.error, OSError) as e:
        log.debug("SMTP probe %s on %s: %s", email, host, e)
        return False


def _smtp_guess(domain: str, full_name: str | None) -> str | None:
    if not full_name:
        return None
    split = _split_name(full_name)
    if not split:
        return None
    mx = _mx_for(domain)
    if not mx or any(h in mx.lower() for h in _ACCEPT_ALL_HOSTS):
        return None  # accept-all hosts can't be probed reliably
    first, last = split
    for local in _candidate_locals(first, last):
        candidate = f"{local}@{domain}"
        if smtp_probe(candidate, mx_host=mx):
            return candidate
    return None


# ── Public entry point ───────────────────────────────────────────────────────

def find_personal_email(
    *,
    domain: str,
    full_name: str | None = None,
    candidates: Iterable[str] | None = None,
) -> str | None:
    """
    Try each provider in order; return the first verified personal email.

    `candidates` lets the caller pre-supply emails to verify (e.g. when
    Apollo returned an unverified guess). When provided, we run them
    through the SMTP probe before falling back to lookup providers.
    """
    if candidates:
        for c in candidates:
            if smtp_probe(c):
                return c

    found = _hunter_lookup(domain, full_name)
    if found:
        return found

    found = _snov_lookup(domain, full_name)
    if found:
        return found

    return _smtp_guess(domain, full_name)


def verify(email: str) -> bool:
    """Last-line defense: True only if SMTP probe confirms the mailbox."""
    if not email or "@" not in email:
        return False
    return smtp_probe(email)
