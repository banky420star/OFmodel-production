# Persona Studio — EC2 Deployment Guide

## Prerequisites

- AWS EC2 instance (any type — t2.micro works for mock mode)
- SSH key pair (e.g., `persona-studio-gpu.pem`)
- Security group with ports 3000 and 8000 open to your IP

## Quick Deploy

### Step 1: Upload the code

From your **Mac terminal** (not EC2):

```bash
# If you have a git repo:
scp -i ~/Downloads/persona-studio-gpu.pem -r . ec2-user@YOUR_IP:/home/ec2-user/persona-studio/

# Or zip and upload:
zip -r persona-studio.zip . -x "*.db*" ".env" "node_modules/*" "__pycache__/*"
scp -i ~/Downloads/persona-studio-gpu.pem persona-studio.zip ec2-user@YOUR_IP:/home/ec2-user/
```

### Step 2: SSH into EC2

```bash
ssh -i ~/Downloads/persona-studio-gpu.pem ec2-user@YOUR_IP
```

### Step 3: Run deployment script

```bash
# If you uploaded via zip:
bash /home/ec2-user/scripts/deploy-ec2.sh

# Or manually:
sudo dnf install -y docker git
sudo systemctl enable docker && sudo systemctl start docker

mkdir -p /opt/persona-studio && cd /opt/persona-studio
# ... copy files here ...

# Create .env with your public IP:
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)
cat > .env << EOF
PROVIDER_REGISTRY=mock
NEXT_PUBLIC_API_URL=http://$PUBLIC_IP:8000
EOF

docker compose up --build -d
```

### Step 4: Open browser

```
Frontend:  http://YOUR_PUBLIC_IP:3000
API Docs:  http://YOUR_PUBLIC_IP:8000/docs
MinIO:     http://YOUR_PUBLIC_IP:9001
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| `web` | 3000 | Next.js frontend |
| `api` | 8000 | FastAPI backend |
| `postgres` | 5432 | Database (internal) |
| `minio` | 9000/9001 | Object storage |

## Adding Real Providers Later

Edit `.env` on the EC2 instance:

```bash
cd /opt/persona-studio
nano .env
```

Add:

```env
PROVIDER_REGISTRY=hybrid
COMFYUI_URL=http://YOUR_GPU_SERVER:8188
ELEVENLABS_API_KEY=sk_your_key
```

Then restart:

```bash
docker compose restart api
```

## Useful Commands

```bash
# View logs
docker compose logs -f api

# Restart a service
docker compose restart api

# Rebuild after code changes
docker compose up --build -d api

# Check health
curl http://localhost:8000/health

# Stop everything
docker compose down

# Stop and remove data
docker compose down -v
```
