package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/elixiretech/b2b-app/orchestration/internal/config"
	"github.com/elixiretech/b2b-app/orchestration/internal/handlers"
	"github.com/elixiretech/b2b-app/orchestration/internal/middleware"
)

func main() {
	cfg := config.Load()

	if cfg.Environment == "production" {
		gin.SetMode(gin.ReleaseMode)
	}

	// Load JWT RS256 public key
	publicKey, err := middleware.LoadPublicKey(cfg.JWTPublicKeyPath)
	if err != nil {
		log.Fatalf("❌ Failed to load JWT public key: %v", err)
	}
	log.Printf("✅ JWT RS256 public key loaded from: %s", cfg.JWTPublicKeyPath)

	// Service URL map
	serviceURLs := map[string]string{
		"identity":     cfg.IdentityServiceURL,
		"catalog":      cfg.CatalogServiceURL,
		"sales":        cfg.SalesServiceURL,
		"route":        cfg.RouteServiceURL,
		"attendance":   cfg.AttendanceServiceURL,
		"notification": cfg.NotificationServiceURL,
	}

	syncHandler := handlers.NewSyncHandler(serviceURLs)
	proxyHandler := handlers.NewProxyHandler(serviceURLs)

	router := gin.New()
	router.Use(gin.Logger())
	router.Use(gin.Recovery())

	// CORS
	router.Use(func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Request-ID")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	})

	// ─────────────────────────────────────────────
	// PUBLIC ROUTES (no auth)
	// ─────────────────────────────────────────────
	router.GET("/", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"message": "Welcome to the DSD B2B SaaS Orchestration API Gateway",
			"health":  "/health",
			"services": []string{"identity", "catalog", "sales", "route", "attendance", "notification"},
		})
	})

	router.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"status":      "healthy",
			"service":     "orchestration",
			"environment": cfg.Environment,
			"downstream": map[string]string{
				"identity":     cfg.IdentityServiceURL,
				"catalog":      cfg.CatalogServiceURL,
				"sales":        cfg.SalesServiceURL,
				"route":        cfg.RouteServiceURL,
				"attendance":   cfg.AttendanceServiceURL,
				"notification": cfg.NotificationServiceURL,
			},
		})
	})

	// Auth — forwarded to identity service (no JWT required to login)
	router.POST("/auth/login", proxyHandler.ProxyTo("identity"))
	router.POST("/auth/refresh", proxyHandler.ProxyTo("identity"))
	router.POST("/auth/logout", proxyHandler.ProxyTo("identity"))

	// ─────────────────────────────────────────────
	// PROTECTED ROUTES (JWT required)
	// ─────────────────────────────────────────────
	protected := router.Group("/")
	protected.Use(middleware.JWTMiddleware(publicKey))

	// Sync Protocol endpoints
	protected.POST("/sync/push", syncHandler.Push)
	protected.GET("/sync/pull", syncHandler.Pull)

	// Proxy routes to downstream services — most are passthrough
	protected.Any("/identity/*path", proxyHandler.ProxyTo("identity"))
	protected.Any("/catalog/*path", proxyHandler.ProxyTo("catalog"))
	protected.Any("/attendance/*path", proxyHandler.ProxyTo("attendance"))
	protected.Any("/notification/*path", proxyHandler.ProxyTo("notification"))

	// Sales routes — attendance check applied inline only for van settlement paths
	attendanceBlocker := middleware.AttendanceBlocker(cfg.AttendanceServiceURL)
	salesProxy := proxyHandler.ProxyTo("sales")
	protected.Any("/sales/*path", func(c *gin.Context) {
		path := c.Param("path")
		// Van settlement requires the field rep to be clocked in
		if strings.HasPrefix(path, "/van/settle") {
			attendanceBlocker(c)
			if c.IsAborted() {
				return
			}
		}
		salesProxy(c)
	})

	// All route service operations require attendance check
	routeGuarded := protected.Group("/route")
	routeGuarded.Use(attendanceBlocker)
	routeGuarded.Any("/*path", proxyHandler.ProxyTo("route"))

	// ─────────────────────────────────────────────
	// START SERVER WITH GRACEFUL SHUTDOWN
	// ─────────────────────────────────────────────
	srv := &http.Server{
		Addr:         fmt.Sprintf(":%s", cfg.Port),
		Handler:      router,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	go func() {
		log.Printf("🚀 Orchestration Service started on :%s (env: %s)", cfg.Port, cfg.Environment)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Server error: %v", err)
		}
	}()

	// Wait for shutdown signal
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("🛑 Shutting down Orchestration Service...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("Forced shutdown: %v", err)
	}
	log.Println("✅ Orchestration Service stopped cleanly")
}
