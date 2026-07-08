# Security Recommendations

This project is CLI-only. It does not expose a REST server, web backend, CORS surface, or HTTP endpoints.

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

- Limited privileges: the CLI can read data but cannot modify or delete it.
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
- `.env` is ignored by git.
- Password input in the CLI uses `getpass` and is not echoed.

Recommendations:

- Never commit `.env`.
- Use a dedicated database user instead of an admin account.
- Rotate credentials regularly.
- Keep API keys out of screenshots, logs, and shared terminals.

## Logging

The application writes logs to `logs/app.log`. This folder is ignored by git.

Recommendations:

- Keep `DEBUG_MODE=false` for normal use.
- Review logs before sharing them.
- Avoid pasting logs publicly if they may contain database names, table names, or business-sensitive query context.

## Production Checklist

- [ ] Use a dedicated read-only database user.
- [ ] Keep `.env` out of version control.
- [ ] Review SQL validation rules after adding new SQL generation features.
- [ ] Keep dependencies updated.
- [ ] Rotate database credentials regularly.
- [ ] Review generated query history before sharing output files.
- [ ] Ensure Ollama is properly secured if running on a network-accessible server.
- [ ] Review vector index files before sharing if they contain sensitive schema information.
