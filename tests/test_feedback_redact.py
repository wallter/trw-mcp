"""PRD-INFRA-132 FR04a — unit tests for the PII redactor.

Pure-function tests; no filesystem I/O. Covers each pattern class + the
idempotency requirement called out in the FR acceptance criteria.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools.submit_feedback import _redact_pii

# ---------------------------------------------------------------------------
# License key — trw_lic_*
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "license is trw_lic_abc123XYZ in config",
        "trw_lic_short",
        "prefix trw_lic_very-long_with-dashes_42 suffix",
    ],
)
def test_redacts_license_key(raw: str) -> None:
    redacted = _redact_pii(raw)
    assert "trw_lic_" not in redacted
    assert "<REDACTED:license_key>" in redacted


# ---------------------------------------------------------------------------
# API keys — sk_, pk_, AKIA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,must_contain",
    [
        ("token sk_live_abcDEF123", "sk_live_"),
        ("token sk_test_xyz789", "sk_test_"),
        ("token pk_live_pubkey42", "pk_live_"),
        ("token pk_test_pubkey99", "pk_test_"),
        ("AWS access AKIAIOSFODNN7EXAMPLE here", "AKIA"),
    ],
)
def test_redacts_api_keys(raw: str, must_contain: str) -> None:
    redacted = _redact_pii(raw)
    assert must_contain not in redacted
    assert "<REDACTED:api_key>" in redacted


# ---------------------------------------------------------------------------
# Connection-string credentials — scheme://user:password@host (P1-2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,leaked_pw,scheme",
    [
        ("db postgres://admin:hunter2@db.host:5432/app here", "hunter2", "postgres"),
        ("db postgresql://u:p4ss-w0rd@localhost/x", "p4ss-w0rd", "postgresql"),
        ("db mysql://root:secretpw@127.0.0.1:3306/db", "secretpw", "mysql"),
        ("db mongodb://app:m0ngo@cluster.mongodb.net/prod", "m0ngo", "mongodb"),
        ("db redis://default:redispass@cache:6379", "redispass", "redis"),
    ],
)
def test_redacts_connection_string_password(raw: str, leaked_pw: str, scheme: str) -> None:
    """P1-2: a DB connection string must not ship user:password to the wire."""
    redacted = _redact_pii(raw)
    assert leaked_pw not in redacted, f"password leaked: {redacted!r}"
    assert "<REDACTED:credentials>" in redacted
    # Scheme + host preserved for diagnostics.
    assert f"{scheme}://" in redacted


@pytest.mark.parametrize(
    "raw,leaked_pw,scheme",
    [
        # Finding 1a: additional schemes.
        ("mq amqp://guest:rabbitpw@broker:5672/vhost", "rabbitpw", "amqp"),
        ("mq amqps://guest:rabbitTLS@broker:5671/vhost", "rabbitTLS", "amqps"),
        ("dir ldap://cn=admin:ldappw@ds.host:389", "ldappw", "ldap"),
        ("dir ldaps://cn=admin:ldapTLS@ds.host:636", "ldapTLS", "ldaps"),
        ("file ftp://user:ftppw@files.host:21", "ftppw", "ftp"),
        ("file sftp://user:sftppw@files.host:22", "sftppw", "sftp"),
        ("db mssql://sa:sqlpw@db.host:1433", "sqlpw", "mssql"),
        ("db sqlserver://sa:sqlpw2@db.host:1433", "sqlpw2", "sqlserver"),
    ],
)
def test_redacts_extended_connection_string_schemes(raw: str, leaked_pw: str, scheme: str) -> None:
    """Finding 1a: amqp/ldap/ftp/mssql/sqlserver schemes must redact creds."""
    redacted = _redact_pii(raw)
    assert leaked_pw not in redacted, f"password leaked: {redacted!r}"
    assert "<REDACTED:credentials>" in redacted
    assert f"{scheme}://" in redacted


@pytest.mark.parametrize(
    "raw,leaked_pw",
    [
        # Finding 1a: empty-username URL (postgres://:pw@host) — the username
        # group is now `*` so the password still collapses.
        ("db postgres://:secretpw@db.host:5432/app", "secretpw"),
        ("db amqp://:rabbitpw@broker:5672", "rabbitpw"),
    ],
)
def test_redacts_empty_username_connection_string(raw: str, leaked_pw: str) -> None:
    """Finding 1a: an empty-username connection string must still redact."""
    redacted = _redact_pii(raw)
    assert leaked_pw not in redacted, f"password leaked: {redacted!r}"
    assert "<REDACTED:credentials>" in redacted


@pytest.mark.parametrize(
    "raw,leaked_val,key",
    [
        # Finding 1a: query-string credentials.
        ("url https://api.host/v1?password=hunter2 done", "hunter2", "password"),
        ("url https://api.host/v1?api_key=ak_live_999&x=1 done", "ak_live_999", "api_key"),
        ("url https://api.host/v1?foo=1&token=tok_abc done", "tok_abc", "token"),
        ("url https://api.host/v1?passwd=p4ss done", "p4ss", "passwd"),
        ("url https://api.host/v1?secret=s3cr3t done", "s3cr3t", "secret"),
    ],
)
def test_redacts_query_string_credentials(raw: str, leaked_val: str, key: str) -> None:
    """Finding 1a: credentials smuggled into a URL query must redact.

    The separator + key survive for diagnostics; only the value collapses.
    """
    redacted = _redact_pii(raw)
    assert leaked_val not in redacted, f"value leaked: {redacted!r}"
    assert f"{key}=<REDACTED:credentials>" in redacted


@pytest.mark.parametrize(
    "benign",
    [
        "see https://example.com/path for docs",  # no creds
        "http://localhost:8080/health is up",  # no creds
        "redis://cache:6379 (no auth)",  # host:port, no user:pass@
        "https://api.example.com/v1",  # creds-free API URL
        "url https://api.host/v1?page=2&limit=50 done",  # benign query params
    ],
)
def test_connection_string_no_false_positive(benign: str) -> None:
    """A creds-free URL (incl. host:port) must NOT be redacted."""
    assert _redact_pii(benign) == benign


# ---------------------------------------------------------------------------
# JSON-embedded secrets — "password": "..." (P1-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,leaked,key",
    [
        ('{"password": "hunter2"}', "hunter2", "password"),
        ('{"secret":"s3cr3t"}', "s3cr3t", "secret"),
        ('{"token": "tok_abc"}', "tok_abc", "token"),
        ('{"api_key": "ak_live_999"}', "ak_live_999", "api_key"),
        ('{"access_key": "AKVALUE"}', "AKVALUE", "access_key"),
        ('{"access_token": "at_xyz"}', "at_xyz", "access_token"),
        ('{"private_key": "-----BEGIN..."}', "BEGIN", "private_key"),
        # Case-insensitive on the key.
        ('{"Password": "MixedCase"}', "MixedCase", "Password"),
        ('{"API_KEY": "upperkey"}', "upperkey", "API_KEY"),
    ],
)
def test_redacts_json_embedded_secret(raw: str, leaked: str, key: str) -> None:
    """P1-3: a JSON secret value must redact while the key is preserved."""
    redacted = _redact_pii(raw)
    assert leaked not in redacted, f"value leaked: {redacted!r}"
    assert "<REDACTED:json_secret>" in redacted
    # The key (diagnostic context) survives.
    assert f'"{key}"' in redacted


@pytest.mark.parametrize(
    "raw,leaked,key",
    [
        # Finding 1b: camelCase + snake/kebab variant keys.
        ('{"apiKey": "leakValue123"}', "leakValue123", "apiKey"),
        ('{"apiToken": "tok_camel"}', "tok_camel", "apiToken"),
        ('{"clientSecret":"abc"}', "abc", "clientSecret"),
        ('{"client_secret": "snake_abc"}', "snake_abc", "client_secret"),
        ('{"refresh_token":"1//xRefresh"}', "1//xRefresh", "refresh_token"),
        ('{"refreshToken": "camelRefresh"}', "camelRefresh", "refreshToken"),
        ('{"authToken": "camelAuth"}', "camelAuth", "authToken"),
        ('{"auth_token": "snakeAuth"}', "snakeAuth", "auth_token"),
        ('{"api-key": "kebabKey"}', "kebabKey", "api-key"),
    ],
)
def test_redacts_json_variant_key_secrets(raw: str, leaked: str, key: str) -> None:
    """Finding 1b: camelCase/snake/kebab secret keys must redact the value.

    NFR01: {"apiKey": "…"} previously leaked entirely. The key is preserved
    for diagnostics; the value collapses to the placeholder.
    """
    redacted = _redact_pii(raw)
    assert leaked not in redacted, f"value leaked: {redacted!r}"
    assert "<REDACTED:json_secret>" in redacted
    assert f'"{key}"' in redacted


@pytest.mark.parametrize(
    "benign",
    [
        '{"description": "this is a normal field"}',
        '{"token_count": 5}',  # not a quoted string value, and key is token_count not token
        '{"username": "alice"}',
        '{"secretariat": "office"}',  # key contains 'secret' as substring but is not 'secret'
        # Finding 1b: clientId is an identifier, NOT a secret — must NOT redact.
        '{"clientId": "public-app-id"}',
        '{"client_id": "public-app-id"}',
    ],
)
def test_json_secret_no_false_positive(benign: str) -> None:
    """Benign JSON fields must NOT be redacted."""
    assert _redact_pii(benign) == benign


# ---------------------------------------------------------------------------
# $HOME paths
# ---------------------------------------------------------------------------


def test_redacts_home_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/operator")
    raw = "config lives at /home/operator/.trw/config.yaml ok"
    redacted = _redact_pii(raw)
    assert "/home/operator" not in redacted
    assert "$HOME/.trw/config.yaml" in redacted


def test_skips_home_substitution_when_home_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force expanduser to return literal "~" so the resolver short-circuits
    # the path-substitution branch.
    monkeypatch.setattr(
        "trw_mcp.tools.submit_feedback.os.path.expanduser",
        lambda _: "~",
    )
    raw = "path is /home/operator/.trw"
    redacted = _redact_pii(raw)
    # Path is left untouched when HOME cannot be resolved.
    assert "/home/operator/.trw" in redacted


def test_home_substitution_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/op/")
    raw = "see /home/op/config.yaml"
    redacted = _redact_pii(raw)
    assert "$HOME/config.yaml" in redacted


# ---------------------------------------------------------------------------
# Env-var KEY=value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "PASSWORD=hunter2",
        "SECRET=topsecret",
        "TOKEN=abcdef",
        "API_KEY=keyval",
        "API-KEY=keyval-dash",
        "AWS_ACCESS_KEY=AKIAfoo",
        "AWS_SECRET_KEY=barbaz",
        "password=lowercase",  # case-insensitive
    ],
)
def test_redacts_env_kv(raw: str) -> None:
    redacted = _redact_pii(raw)
    assert "<REDACTED:env>" in redacted
    # The value portion must be gone — `KEY=value` collapses to a single
    # placeholder so a regex regression that only drops the value while
    # keeping `KEY=` is caught (audit P1-3).
    value = raw.split("=", 1)[1]
    assert value not in redacted, f"value {value!r} survived redaction in {redacted!r}"
    # And the key=value token itself does not survive intact.
    assert raw not in redacted


def test_env_redaction_preserves_surrounding_text() -> None:
    raw = "context PASSWORD=secret123 and more after"
    redacted = _redact_pii(raw)
    assert redacted.startswith("context ")
    assert redacted.endswith(" and more after")
    assert "<REDACTED:env>" in redacted


@pytest.mark.parametrize(
    "raw,leaked_value",
    [
        ("DB_PASSWORD=hunter2", "hunter2"),
        ("OPENAI_API_KEY=leakedvalue1", "leakedvalue1"),
        ("GITHUB_TOKEN=leakedvalue2", "leakedvalue2"),
        ("AWS_ACCESS_KEY=AKIAfoo", "AKIAfoo"),
        ("AWS_SECRET_KEY=barbaz", "barbaz"),
        ("MY_APP_ACCESS_TOKEN=tok", "tok"),
    ],
)
def test_redacts_prefixed_env_names(raw: str, leaked_value: str) -> None:
    """Prefixed env names (DB_, OPENAI_, AWS_SECRET_) must redact.

    Regression guard: a leading ``\\b`` anchor never matched between two word
    characters (``_`` is a word char), so ``DB_PASSWORD=`` silently leaked its
    value. NFR01 requires zero false-negatives, so prefixed keys must redact.

    This is also why a per-vendor token zoo is unnecessary: an ``OPENAI_API_KEY=``
    / ``GITHUB_TOKEN=`` assignment is caught by the env pattern regardless of the
    value's shape — the whole ``KEY=value`` token collapses to ``<REDACTED:env>``.
    """
    redacted = _redact_pii(raw)
    assert leaked_value not in redacted, f"value leaked: {redacted!r}"
    assert "<REDACTED:" in redacted


@pytest.mark.parametrize(
    "raw,leaked_value",
    [
        ('PASSWORD="my multi word secret"', "secret"),
        ("SECRET='single quoted value'", "value"),
        ('AWS_SECRET_KEY="space separated"', "separated"),
    ],
)
def test_redacts_quoted_env_value_with_spaces(raw: str, leaked_value: str) -> None:
    """Quoted values containing spaces must redact whole.

    Regression guard: a bare ``\\S+`` value matcher stopped at the first space,
    leaking everything after it (``PASSWORD="my secret"`` left ``secret"`` in
    clear text on the wire).
    """
    redacted = _redact_pii(raw)
    assert leaked_value not in redacted, f"value leaked: {redacted!r}"
    assert "<REDACTED:env>" in redacted


@pytest.mark.parametrize(
    "benign",
    [
        "secretariat=public",  # keyword is a substring, not the whole token
        "description=normal text",
        "token_count=5 widgets",  # keyword followed by more identifier, not '='
    ],
)
def test_env_redaction_no_false_positive_on_benign_keys(benign: str) -> None:
    """The keyword must be immediately followed by ``=`` — no over-matching."""
    assert _redact_pii(benign) == benign


# ---------------------------------------------------------------------------
# Clean input — no false positives
# ---------------------------------------------------------------------------


def test_clean_input_unchanged() -> None:
    raw = "Just an ordinary bug report with no secrets in it at all."
    assert _redact_pii(raw) == raw


def test_empty_string_unchanged() -> None:
    assert _redact_pii("") == ""


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_double_application(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/me")
    raw = "license trw_lic_ABC123 key sk_live_XYZ999 path /home/me/.trw env PASSWORD=secret"
    once = _redact_pii(raw)
    twice = _redact_pii(once)
    assert once == twice


def test_idempotent_on_already_redacted_markers() -> None:
    raw = "<REDACTED:license_key> and <REDACTED:api_key>"
    assert _redact_pii(raw) == raw


def test_idempotent_double_application_new_patterns() -> None:
    """Re-running the pattern classes on already-redacted text is stable.

    NFR / acceptance criterion: placeholders must not contain re-matchable
    tokens. Covers API keys, connection strings, and JSON secrets together.
    """
    raw = 'stripe sk_live_leak123 db postgres://admin:hunter2@host/app json {"password": "topsecret"}'
    once = _redact_pii(raw)
    twice = _redact_pii(once)
    assert once == twice
    # And the placeholders themselves survived a second pass intact.
    assert "<REDACTED:api_key>" in twice
    assert "<REDACTED:credentials>" in twice
    assert "<REDACTED:json_secret>" in twice


def test_idempotent_on_all_new_markers() -> None:
    """The new placeholder markers must be inert on a second pass."""
    raw = 'scheme://<REDACTED:credentials>@host "password": "<REDACTED:json_secret>" <REDACTED:api_key>'
    assert _redact_pii(raw) == raw


# ---------------------------------------------------------------------------
# Finding 1d — single comprehensive enumeration of EVERY claimed secret shape
# ---------------------------------------------------------------------------
# One realistic example per alternation branch of _API_KEY_RE / _CONN_STR_RE /
# _QUERY_CRED_RE / _JSON_SECRET_RE. Each must redact AND remain idempotent on a
# second pass — the two requirements the NFR pins together.

_API_KEY_SHAPES: list[tuple[str, str]] = [
    ("sk_live_51Abc123Def456Ghi789", "<REDACTED:api_key>"),
    ("sk_test_51Abc123Def456Ghi789", "<REDACTED:api_key>"),
    ("pk_live_51Abc123Def456Ghi789", "<REDACTED:api_key>"),
    ("pk_test_51Abc123Def456Ghi789", "<REDACTED:api_key>"),
    ("AKIAIOSFODNN7EXAMPLE", "<REDACTED:api_key>"),
]

_CONN_STR_SHAPES: list[tuple[str, str]] = [
    ("postgres://u:pw@host/db", "<REDACTED:credentials>"),
    ("postgresql://u:pw@host/db", "<REDACTED:credentials>"),
    ("mysql://u:pw@host/db", "<REDACTED:credentials>"),
    ("mongodb://u:pw@host/db", "<REDACTED:credentials>"),
    ("redis://u:pw@host:6379", "<REDACTED:credentials>"),
    ("amqp://u:pw@host:5672", "<REDACTED:credentials>"),
    ("amqps://u:pw@host:5671", "<REDACTED:credentials>"),
    ("ldap://u:pw@host:389", "<REDACTED:credentials>"),
    ("ldaps://u:pw@host:636", "<REDACTED:credentials>"),
    ("ftp://u:pw@host:21", "<REDACTED:credentials>"),
    ("sftp://u:pw@host:22", "<REDACTED:credentials>"),
    ("mssql://u:pw@host:1433", "<REDACTED:credentials>"),
    ("sqlserver://u:pw@host:1433", "<REDACTED:credentials>"),
    ("postgres://:pw@host/db", "<REDACTED:credentials>"),  # empty username
]

_QUERY_CRED_SHAPES: list[tuple[str, str]] = [
    ("https://h/v1?password=hunter2", "<REDACTED:credentials>"),
    ("https://h/v1?passwd=hunter2", "<REDACTED:credentials>"),
    ("https://h/v1?secret=s3cr3t", "<REDACTED:credentials>"),
    ("https://h/v1?token=tok_abc", "<REDACTED:credentials>"),
    ("https://h/v1?api_key=ak_live_9", "<REDACTED:credentials>"),
]

_JSON_SECRET_SHAPES: list[tuple[str, str]] = [
    ('{"password": "hunter2"}', "<REDACTED:json_secret>"),
    ('{"secret": "s3cr3t"}', "<REDACTED:json_secret>"),
    ('{"token": "tok_abc"}', "<REDACTED:json_secret>"),
    ('{"private_key": "-----BEGIN"}', "<REDACTED:json_secret>"),
    ('{"api_key": "ak_live_9"}', "<REDACTED:json_secret>"),
    ('{"apiKey": "ak_camel"}', "<REDACTED:json_secret>"),
    ('{"api-key": "ak_kebab"}', "<REDACTED:json_secret>"),
    ('{"apiToken": "tok_camel"}', "<REDACTED:json_secret>"),
    ('{"api_token": "tok_snake"}', "<REDACTED:json_secret>"),
    ('{"authToken": "auth_camel"}', "<REDACTED:json_secret>"),
    ('{"auth_token": "auth_snake"}', "<REDACTED:json_secret>"),
    ('{"clientSecret": "cs_camel"}', "<REDACTED:json_secret>"),
    ('{"client_secret": "cs_snake"}', "<REDACTED:json_secret>"),
    ('{"refreshToken": "rt_camel"}', "<REDACTED:json_secret>"),
    ('{"refresh_token": "rt_snake"}', "<REDACTED:json_secret>"),
    ('{"access_key": "AKVALUE"}', "<REDACTED:json_secret>"),
    ('{"access_token": "at_xyz"}', "<REDACTED:json_secret>"),
]

_ALL_SECRET_SHAPES: list[tuple[str, str]] = (
    _API_KEY_SHAPES + _CONN_STR_SHAPES + _QUERY_CRED_SHAPES + _JSON_SECRET_SHAPES
)


@pytest.mark.parametrize("token,placeholder", _ALL_SECRET_SHAPES)
def test_every_claimed_secret_shape_is_redacted(token: str, placeholder: str) -> None:
    """Finding 1d: every shape the three regexes claim to cover must redact.

    NFR01 (zero false-negative) consolidated probe — one realistic example per
    alternation branch with realistic token lengths so the min-length floors
    are genuinely exercised.
    """
    raw = f"context before {token} context after"
    redacted = _redact_pii(raw)
    assert token not in redacted, f"secret leaked: {redacted!r}"
    assert placeholder in redacted, f"expected {placeholder} in {redacted!r}"


@pytest.mark.parametrize("token,placeholder", _ALL_SECRET_SHAPES)
def test_every_claimed_secret_shape_is_idempotent(token: str, placeholder: str) -> None:
    """Finding 1d + CRITICAL idempotency: re-running on output is a no-op.

    Guards that no pattern re-consumes an earlier ``<REDACTED:…>`` placeholder
    on the second pass.
    """
    raw = f"context before {token} context after"
    once = _redact_pii(raw)
    twice = _redact_pii(once)
    assert once == twice, f"not idempotent: {once!r} -> {twice!r}"


@pytest.mark.parametrize(
    "benign",
    [
        "https://api.example.com/v1",  # creds-free API URL
        "the password: field is documented in the guide",  # prose, no '=' / JSON
        '{"token_count": 5}',  # numeric value, key is token_count not token
        '{"clientId": "public-id"}',  # id is not a secret
        "ski lift opens at nine",  # 'sk' substring only
        "https://h/v1?page=2&limit=50",  # benign query params
    ],
)
def test_no_over_redaction_on_benign_inputs(benign: str) -> None:
    """Finding 1d: a creds-free / prose / benign-ML corpus must be untouched.

    The mirror of the zero-false-negative probe — zero false-POSITIVES on
    realistic non-secret text that merely shares a prefix or keyword.
    """
    assert _redact_pii(benign) == benign
