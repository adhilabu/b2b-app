package config

import (
	"os"
)

// Config holds all orchestration service configuration.
type Config struct {
	Port                    string
	Environment             string
	JWTPublicKeyPath        string
	JWTAlgorithm            string
	RedisURL                string
	PulsarServiceURL        string
	OrchestratorSecret      string

	// Downstream service URLs
	IdentityServiceURL     string
	CatalogServiceURL      string
	SalesServiceURL        string
	RouteServiceURL        string
	AttendanceServiceURL   string
	NotificationServiceURL string
}

// Load reads configuration from environment variables.
func Load() *Config {
	return &Config{
		Port:                    getEnv("ORCHESTRATION_PORT", "8000"),
		Environment:             getEnv("ENVIRONMENT", "development"),
		JWTPublicKeyPath:        getEnv("JWT_PUBLIC_KEY_PATH", "../../infra/keys/public.pem"),
		JWTAlgorithm:            getEnv("JWT_ALGORITHM", "RS256"),
		RedisURL:                getEnv("REDIS_URL", "redis://localhost:6379/0"),
		PulsarServiceURL:        getEnv("PULSAR_SERVICE_URL", "pulsar://localhost:6650"),
		OrchestratorSecret:      getEnv("ORCHESTRATION_SERVICE_SECRET", "change-me"),
		IdentityServiceURL:     getEnv("IDENTITY_SERVICE_URL", "http://localhost:8001"),
		CatalogServiceURL:      getEnv("CATALOG_SERVICE_URL", "http://localhost:8002"),
		SalesServiceURL:        getEnv("SALES_SERVICE_URL", "http://localhost:8003"),
		RouteServiceURL:        getEnv("ROUTE_SERVICE_URL", "http://localhost:8004"),
		AttendanceServiceURL:   getEnv("ATTENDANCE_SERVICE_URL", "http://localhost:8005"),
		NotificationServiceURL: getEnv("NOTIFICATION_SERVICE_URL", "http://localhost:8006"),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
