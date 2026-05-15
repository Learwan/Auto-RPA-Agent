# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

**Do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via:

1. **GitHub Security Advisories** (preferred): Use the "Security" tab → "Report a vulnerability"
2. **Email**: Send details to the maintainers privately

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact
- Suggested fix (if available)

### Response Timeline

| Stage           | Expected Time |
|-----------------|---------------|
| Acknowledgment  | Within 48 hours |
| Initial assessment | Within 5 business days |
| Fix development | Depends on severity |
| Advisory published | After fix is released |

## Security Measures

### Credential Management

- All credentials stored in the encrypted vault (`src/vault/`)
- `.env` files are excluded from version control via `.gitignore`
- API keys and secrets never appear in logs or error messages
- Sensitive data is redacted in audit logs

### Input Validation

- All API inputs validated via Pydantic models with strict schemas
- SQL queries use parameterized statements via SQLAlchemy ORM
- Output is escaped to prevent XSS in web interfaces
- File paths are validated and sanitized

### Dependency Security

- Regular dependency audits via `pip-audit` / `npm audit`
- Dependabot enabled for automated vulnerability alerts
- Dependencies pinned with minimum version constraints
- Known vulnerable versions blocked in CI

### Runtime Security

- RBAC (Role-Based Access Control) for API endpoints
- Audit logging for all sensitive operations
- Rate limiting on API endpoints
- Session management with secure tokens

## Security Best Practices for Contributors

1. **Never commit secrets** — Use `.env.example` for documentation only
2. **Validate all inputs** — Use Pydantic models with `model_config = ConfigDict(extra="forbid")`
3. **Use parameterized queries** — Never construct SQL from user input
4. **Escape output** — Prevent XSS in any HTML/web output
5. **Log responsibly** — Never log passwords, tokens, or PII
6. **Minimize dependencies** — Each new dependency is a potential attack surface
7. **Review permissions** — Follow least-privilege principle for RBAC roles

## Security Architecture

```
┌─────────────────────────────────────────────┐
│                  API Layer                    │
│  ┌─────────┐  ┌─────────┐  ┌──────────────┐ │
│  │  Auth   │  │  RBAC   │  │ Rate Limiter │ │
│  └─────────┘  └─────────┘  └──────────────┘ │
├─────────────────────────────────────────────┤
│               Service Layer                   │
│  ┌─────────┐  ┌─────────┐  ┌──────────────┐ │
│  │  Vault  │  │  Audit  │  │  Validator   │ │
│  └─────────┘  └─────────┘  └──────────────┘ │
├─────────────────────────────────────────────┤
│                 Data Layer                    │
│  ┌─────────┐  ┌─────────┐  ┌──────────────┐ │
│  │   DB    │  │ Encrypt │  │  Sanitizer   │ │
│  └─────────┘  └─────────┘  └──────────────┘ │
└─────────────────────────────────────────────┘
```
