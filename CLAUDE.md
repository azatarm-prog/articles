# Environment Setup

## Credentials

This project uses a `.env` file (gitignored) for secrets. Load it at the start of a session:

```bash
source .env
```

Expected environment variables:

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | GitHub personal access token for API operations |
| `RAILWAY_TOKEN` | Railway API token for deployments |
| `RAILWAY_PROJECT_ID` | Railway project ID |
| `RAILWAY_ENVIRONMENT_ID` | Railway environment ID |
| `RAILWAY_SERVICE_URL` | Railway service dashboard URL |

# Editorial Rules

## Style Guidelines

- **No emdashes (—)**: Never use emdashes in any article. Replace them with alternative punctuation such as commas, semicolons, colons, parentheses, or separate sentences. Before publishing, always scan the article for emdashes and replace any that are found.
