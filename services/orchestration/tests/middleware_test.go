package middleware_test

import (
	"crypto/rand"
	"crypto/rsa"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"

	mw "github.com/elixiretech/b2b-app/orchestration/internal/middleware"
)

// generateTestKeyPair creates a fresh RSA key pair for testing.
func generateTestKeyPair(t *testing.T) (*rsa.PrivateKey, *rsa.PublicKey) {
	t.Helper()
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("Failed to generate RSA key: %v", err)
	}
	return privateKey, &privateKey.PublicKey
}

// makeValidToken creates a valid RS256 JWT signed with the given private key.
func makeValidToken(t *testing.T, privateKey *rsa.PrivateKey, userID, tenantID string, roles []string) string {
	t.Helper()
	claims := mw.Claims{
		Sub:      userID,
		TenantID: tenantID,
		Roles:    roles,
		JTI:      "test-jti",
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(1 * time.Hour)),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	signed, err := token.SignedString(privateKey)
	if err != nil {
		t.Fatalf("Failed to sign token: %v", err)
	}
	return signed
}

func setupRouter(publicKey *rsa.PublicKey) *gin.Engine {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(mw.JWTMiddleware(publicKey))
	router.GET("/protected", func(c *gin.Context) {
		userID, _ := c.Get("user_id")
		tenantID, _ := c.Get("tenant_id")
		c.JSON(http.StatusOK, gin.H{
			"user_id":   userID,
			"tenant_id": tenantID,
		})
	})
	return router
}

func TestJWTMiddleware_ValidToken(t *testing.T) {
	privateKey, publicKey := generateTestKeyPair(t)
	router := setupRouter(publicKey)

	token := makeValidToken(t, privateKey, "user-123", "tenant-456", []string{"sales_rep"})

	req := httptest.NewRequest("GET", "/protected", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()

	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("Expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestJWTMiddleware_MissingToken(t *testing.T) {
	_, publicKey := generateTestKeyPair(t)
	router := setupRouter(publicKey)

	req := httptest.NewRequest("GET", "/protected", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("Expected 401, got %d", w.Code)
	}
}

func TestJWTMiddleware_InvalidBearerFormat(t *testing.T) {
	_, publicKey := generateTestKeyPair(t)
	router := setupRouter(publicKey)

	req := httptest.NewRequest("GET", "/protected", nil)
	req.Header.Set("Authorization", "InvalidFormat")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("Expected 401, got %d", w.Code)
	}
}

func TestJWTMiddleware_ExpiredToken(t *testing.T) {
	privateKey, publicKey := generateTestKeyPair(t)
	router := setupRouter(publicKey)

	// Create expired token
	claims := mw.Claims{
		Sub:      "user-123",
		TenantID: "tenant-456",
		Roles:    []string{"sales_rep"},
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(-1 * time.Hour)), // Expired
			IssuedAt:  jwt.NewNumericDate(time.Now().Add(-2 * time.Hour)),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	signed, _ := token.SignedString(privateKey)

	req := httptest.NewRequest("GET", "/protected", nil)
	req.Header.Set("Authorization", "Bearer "+signed)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("Expected 401 for expired token, got %d", w.Code)
	}
}

func TestJWTMiddleware_WrongSigningKey(t *testing.T) {
	privateKey, _ := generateTestKeyPair(t)
	_, differentPublicKey := generateTestKeyPair(t) // Different key pair!

	router := setupRouter(differentPublicKey) // Verify with wrong key

	token := makeValidToken(t, privateKey, "user-123", "tenant-456", []string{"admin"})
	req := httptest.NewRequest("GET", "/protected", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("Expected 401 for wrong signing key, got %d", w.Code)
	}
}

func TestRequireRole_Authorized(t *testing.T) {
	privateKey, publicKey := generateTestKeyPair(t)
	gin.SetMode(gin.TestMode)

	router := gin.New()
	router.Use(mw.JWTMiddleware(publicKey))
	router.GET("/admin", mw.RequireRole("admin"), func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"message": "welcome admin"})
	})

	token := makeValidToken(t, privateKey, "user-1", "tenant-1", []string{"admin"})
	req := httptest.NewRequest("GET", "/admin", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("Expected 200 for admin, got %d: %s", w.Code, w.Body.String())
	}
}

func TestRequireRole_Forbidden(t *testing.T) {
	privateKey, publicKey := generateTestKeyPair(t)
	gin.SetMode(gin.TestMode)

	router := gin.New()
	router.Use(mw.JWTMiddleware(publicKey))
	router.GET("/admin", mw.RequireRole("admin"), func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"ok": true})
	})

	token := makeValidToken(t, privateKey, "user-2", "tenant-1", []string{"sales_rep"})
	req := httptest.NewRequest("GET", "/admin", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Errorf("Expected 403 for sales_rep on admin endpoint, got %d", w.Code)
	}
}
