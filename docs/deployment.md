# Deployment Guide

This guide covers the deployment strategies for the DSD B2B SaaS Platform, including local Docker Compose orchestration, production environment variables, and Kubernetes deployment architecture.

---

## 🐋 Docker Compose Deployment

The simplest way to deploy the entire stack is using Docker Compose. The platform contains a production-grade `docker-compose.yml` defining all 7 microservices and the 3 core backing services (PostgreSQL, Redis, Apache Pulsar).

### Prerequisites
- Docker Engine v24.0+
- Docker Compose v2.20+
- OpenSSL (for key generation)

### 1. Key Generation
The Identity service requires an RSA private key to sign JWT tokens, and all other services require the public key to verify them. 

Run the following command to generate the keypair inside `infra/keys/`:
```bash
make keygen
```
Or manually:
```bash
mkdir -p infra/keys
openssl genrsa -out infra/keys/private.pem 4096
openssl rsa -in infra/keys/private.pem -pubout -out infra/keys/public.pem
```

### 2. Environment Variables
Copy `.env.example` to `.env` and configure the following variables:
- `POSTGRES_PASSWORD`: The root database password.
- `JWT_PUBLIC_KEY_PATH`: Path to the JWT public key (default: `/app/keys/public.pem` in containers).
- `JWT_PRIVATE_KEY_PATH`: Path to the private key (Identity service only, default: `/app/keys/private.pem`).
- `VAPID_PUBLIC_KEY` & `VAPID_PRIVATE_KEY`: Keys for Web Push notification delivery.
- `FCM_CREDENTIALS_JSON`: Firebase service account credentials for push notifications to mobile.

### 3. Running the Stack
To start all services in detached mode:
```bash
make up
```
To view the status of all containers:
```bash
make ps
```
To tail logs for all services:
```bash
make logs
```

---

## 🛡️ Production Security Checklist

When deploying to a production environment (e.g. AWS, GCP, Azure), ensure the following configurations are applied:

1. **Database Isolation**: Do not expose PostgreSQL (`5432`) or Redis (`6379`) to the public internet. Keep them inside a private subnet.
2. **Secrets Management**: Use a secret manager (AWS Secrets Manager, HashiCorp Vault) rather than hardcoded environment files.
3. **HTTPS / TLS Terminating Reverse Proxy**: Place an ALB, Nginx, or Traefik in front of the Orchestration service (`8000`) to handle SSL termination.
4. **JWT Expiry**: Ensure access tokens expire in 15 minutes or less, and implement Redis-backed Refresh Token rotation.
5. **Pulsar Authentication**: Enable JWT-based authentication/authorization on the Pulsar cluster.

---

## ☸️ Kubernetes Deployment (K8s)

For a highly available production environment, Kubernetes is recommended.

### Infrastructure (Stateful)
Deploy the backing databases using dedicated Helm charts or Managed Cloud Services:
- **Database**: Use managed PostgreSQL (AWS RDS / GCP Cloud SQL) rather than running PostgreSQL in K8s.
- **Cache**: Use managed Redis (AWS ElastiCache / GCP Memorystore).
- **Message Broker**: Deploy Apache Pulsar using the official Apache Pulsar Helm Chart with multiple bookies, brokers, and ZooKeeper replicas.

### Stateless Services (Deployments)
Each of the 7 microservices should be deployed as a K8s `Deployment` with a matching `Service`.

#### Example Deployment Manifest (Orchestration Gateway)
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orchestration-gateway
  namespace: dsd-platform
  labels:
    app: orchestration-gateway
spec:
  replicas: 3
  selector:
    matchLabels:
      app: orchestration-gateway
  template:
    metadata:
      labels:
        app: orchestration-gateway
    spec:
      containers:
      - name: gateway
        image: elixiretech/dsd-orchestration:latest
        ports:
        - containerPort: 8000
        env:
        - name: ORCHESTRATION_PORT
          value: "8000"
        - name: REDIS_URL
          value: "redis://redis-sentinel.dsd-platform.svc.cluster.local:6379/0"
        - name: IDENTITY_SERVICE_URL
          value: "http://identity-service.dsd-platform.svc.cluster.local:8001"
        - name: CATALOG_SERVICE_URL
          value: "http://catalog-service.dsd-platform.svc.cluster.local:8002"
        resources:
          limits:
            cpu: "500m"
            memory: "512Mi"
          requests:
            cpu: "100m"
            memory: "128Mi"
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
```

---

## 📈 Auto-scaling Strategies

- **Orchestration / Gateway**: Auto-scale based on CPU utilization and HTTP request rate.
- **Notification Service**: Runs WebSockets, meaning connections are persistent. Use **Session Affinity (sticky sessions)** on the ingress controller if deploying behind load balancers. Auto-scale based on active connection counts or memory.
- **Async Event Consumers**: Python Pulsar consumers inside `route` and `notification` services should be scaled up/down based on the length of queue backlogs.
