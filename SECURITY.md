# Security Recommendations

SQLSense is a full-stack application with multiple interfaces: CLI, Web UI, and API Gateway. This document covers security considerations for all interfaces.

## Database User Security

### Current Configuration

Avoid using the MySQL `root` user for normal project use. The CLI only needs read access for schema inspection, profiling, and SELECT query execution.

### Recommended Database User

Create a dedicated MySQL user with limited privileges:

```sql
CREATE USER 'sqlsense_user'@'localhost' IDENTIFIED BY 'strong_password_here';
GRANT SELECT ON your_database_name.* TO 'sqlsense_user'@'localhost';
FLUSH PRIVILEGES;
```

Then update `.env`:

```env
DB_USER=sqlsense_user
DB_PASSWORD=strong_password_here
```

### Benefits

- Limited privileges: all interfaces can read data but cannot modify or delete it.
- Better audit trail: database logs can distinguish this tool from admin use.
- Smaller blast radius: leaked credentials have reduced impact.

## SQL Safety

The project validates generated SQL before execution:

- Only `SELECT` queries are allowed.
- Dangerous keywords such as `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `CREATE`, and `REPLACE` are blocked.
- Multiple SQL statements are rejected.
- A default `LIMIT` is added when the generated query does not include one.
- SQL structure is checked against the generated knowledge base where possible.
- SQL is re-validated before execution even if previously validated.

## Secrets Management

Current behavior:

- Database credentials live in `.env`.
- Local AI backend (Ollama) credentials are configured in `.env`.
- Frontend configuration lives in `forentendNew/.env.local`.
- `.env` and `.env.local` are ignored by git.
- Password input in the CLI uses `getpass` and is not echoed.

Recommendations:

- Never commit `.env` or `.env.local`.
- Use a dedicated database user instead of an admin account.
- Rotate credentials regularly.
- Keep API keys out of screenshots, logs, and shared terminals.
- Use environment-specific configuration files for development, staging, and production.

## Web Security

### API Gateway Security

The API Gateway (`api_gateway/app.py`) provides a REST API for the web UI and programmatic access. Consider these security measures:

**Authentication & Authorization:**
- Implement OAuth2/JWT authentication for production deployments
- Add API key support for programmatic access
- Implement role-based access control (RBAC)
- Use secure session management with HTTP-only cookies

**Network Security:**
- Enable HTTPS/TLS for all API endpoints in production
- Configure CORS properly to limit allowed origins
- Implement rate limiting to prevent abuse
- Use a reverse proxy (nginx, Apache) for production deployments

**Input Validation:**
- Validate all incoming request data
- Sanitize user inputs to prevent injection attacks
- Implement proper error handling without exposing sensitive information

### Frontend Security

The React frontend (`forentendNew/`) should follow these security best practices:

**Environment Variables:**
- Store sensitive configuration in environment variables
- Never commit `.env.local` files
- Use different configurations for development and production

**Content Security:**
- Implement Content Security Policy (CSP) headers
- Use HTTPS for all external resources
- Validate and sanitize user inputs

**Authentication:**
- Store tokens securely (httpOnly cookies, not localStorage)
- Implement proper logout functionality
- Handle token expiration and refresh

### Development vs Production

**Development:**
- Use HTTP for local development
- Allow localhost in CORS configuration
- Enable debug logging for troubleshooting

**Production:**
- Enable HTTPS with valid SSL certificates
- Restrict CORS to specific domains
- Disable debug logging
- Use strong session secrets
- Implement proper monitoring and alerting

## Logging

The application writes logs to `logs/app.log`. This folder is ignored by git.

Recommendations:

- Keep `DEBUG_MODE=false` for normal use.
- Review logs before sharing them.
- Avoid pasting logs publicly if they may contain database names, table names, or business-sensitive query context.

## Production Checklist

**Database Security:**
- [ ] Use a dedicated read-only database user
- [ ] Keep `.env` out of version control
- [ ] Review SQL validation rules after adding new SQL generation features
- [ ] Keep dependencies updated
- [ ] Rotate database credentials regularly
- [ ] Review generated query history before sharing output files
- [ ] Ensure Ollama is properly secured if running on a network-accessible server
- [ ] Review vector index files before sharing if they contain sensitive schema information

**API Gateway Security:**
- [ ] Implement OAuth2/JWT authentication
- [ ] Add API key support for programmatic access
- [ ] Enable HTTPS/TLS for all endpoints
- [ ] Configure CORS to specific domains only
- [ ] Implement rate limiting
- [ ] Use a reverse proxy (nginx, Apache)
- [ ] Implement proper error handling without exposing sensitive information
- [ ] Add monitoring and alerting for security events

**Frontend Security:**
- [ ] Implement Content Security Policy (CSP) headers
- [ ] Use HTTPS for all external resources
- [ ] Store tokens securely (httpOnly cookies)
- [ ] Implement proper logout functionality
- [ ] Handle token expiration and refresh
- [ ] Validate and sanitize all user inputs
- [ ] Use environment-specific configuration files

**General Security:**
- [ ] Keep `DEBUG_MODE=false` in production
- [ ] Review logs before sharing them
- [ ] Implement proper backup and recovery procedures
- [ ] Regular security audits and penetration testing
- [ ] Keep all dependencies updated
- [ ] Monitor for security vulnerabilities in dependencies
