FROM golang:1.22-alpine AS builder

WORKDIR /app

# Install CA certificates for HTTPS calls
RUN apk add --no-cache ca-certificates curl

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o /orchestration ./cmd/main.go

# ─── Final stage ─────────────────────────────
FROM alpine:3.19

RUN apk add --no-cache ca-certificates curl

WORKDIR /app

COPY --from=builder /orchestration /app/orchestration

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

ENTRYPOINT ["/app/orchestration"]
