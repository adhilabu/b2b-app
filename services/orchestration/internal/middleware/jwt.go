package middleware

import (
	"crypto/rsa"
	"fmt"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// Claims represents the JWT payload structure issued by the Identity service.
type Claims struct {
	Sub      string   `json:"sub"`
	TenantID string   `json:"tenant_id"`
	Roles    []string `json:"roles"`
	JTI      string   `json:"jti"`
	jwt.RegisteredClaims
}

// LoadPublicKey reads an RSA public key from PEM file.
func LoadPublicKey(path string) (*rsa.PublicKey, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading public key: %w", err)
	}
	key, err := jwt.ParseRSAPublicKeyFromPEM(data)
	if err != nil {
		return nil, fmt.Errorf("parsing RSA public key: %w", err)
	}
	return key, nil
}

// JWTMiddleware validates the Bearer token in the Authorization header.
// On success, it sets the following context values:
//   - "user_id" (string)
//   - "tenant_id" (string)
//   - "roles" ([]string)
//   - "claims" (*Claims)
func JWTMiddleware(publicKey *rsa.PublicKey) gin.HandlerFunc {
	return func(c *gin.Context) {
		authHeader := c.GetHeader("Authorization")
		if authHeader == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Authorization header is required",
			})
			return
		}

		parts := strings.SplitN(authHeader, " ", 2)
		if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Authorization header must be 'Bearer <token>'",
			})
			return
		}

		tokenStr := parts[1]
		claims := &Claims{}

		token, err := jwt.ParseWithClaims(tokenStr, claims, func(token *jwt.Token) (interface{}, error) {
			if _, ok := token.Method.(*jwt.SigningMethodRSA); !ok {
				return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
			}
			return publicKey, nil
		})

		if err != nil || !token.Valid {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": fmt.Sprintf("Invalid token: %v", err),
			})
			return
		}

		// Inject claims into Gin context
		c.Set("user_id", claims.Sub)
		c.Set("tenant_id", claims.TenantID)
		c.Set("roles", claims.Roles)
		c.Set("claims", claims)

		// Forward the original token to downstream services
		c.Set("raw_token", tokenStr)

		c.Next()
	}
}

// RequireRole returns a middleware that enforces role-based access.
func RequireRole(allowedRoles ...string) gin.HandlerFunc {
	allowed := make(map[string]bool, len(allowedRoles))
	for _, r := range allowedRoles {
		allowed[r] = true
	}

	return func(c *gin.Context) {
		roles, exists := c.Get("roles")
		if !exists {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "No roles found in token"})
			return
		}

		userRoles, ok := roles.([]string)
		if !ok {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "Invalid roles format"})
			return
		}

		for _, r := range userRoles {
			if allowed[r] {
				c.Next()
				return
			}
		}

		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
			"error":    "Insufficient permissions",
			"required": allowedRoles,
			"yours":    userRoles,
		})
	}
}
