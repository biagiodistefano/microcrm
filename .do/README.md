# DigitalOcean Droplet Deployment

MicroCRM is designed for Droplet deployment with Docker Compose, not App Platform.

## Quick Deploy

1. **Create a Droplet** (Ubuntu 22.04+, 1GB+ RAM recommended)
   - In "Advanced Options", paste the contents of `cloud-init.yaml` as user-data

2. **Point your domain** to the Droplet IP

3. **SSH in and run the wizard**:
   ```bash
   cd /opt/microcrm
   ./prod_wizard.sh
   ```

## Non-Interactive Deploy

Set these environment variables before creating the Droplet:

```bash
MICROCRM_DOMAIN=crm.example.com        # Required
MICROCRM_ADMIN_PASSWORD=secure-pass    # Optional (default: admin)
MICROCRM_GEMINI_KEY=your-key           # Optional
```

The cloud-init script will automatically configure and start MicroCRM.

## Why Droplet Instead of App Platform?

MicroCRM uses Docker Compose with multiple services (Caddy, PostgreSQL, Redis, Django, Celery, Beat). App Platform doesn't support Docker Compose, so Droplet deployment is optimal for:

- Full control over the stack
- Caddy for automatic HTTPS
- Self-contained PostgreSQL and Redis
- Background task processing with Celery
- Lower cost for small deployments
