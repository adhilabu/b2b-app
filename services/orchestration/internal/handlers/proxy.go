package handlers

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

// ProxyHandler forwards requests to downstream microservices.
// It strips the service prefix from the path and preserves headers.
type ProxyHandler struct {
	serviceURLs map[string]string
	httpClient  *http.Client
}

func NewProxyHandler(serviceURLs map[string]string) *ProxyHandler {
	return &ProxyHandler{
		serviceURLs: serviceURLs,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// ProxyTo forwards the request to the named downstream service.
// The :path wildcard from the Gin route is appended to the service URL.
func (h *ProxyHandler) ProxyTo(serviceName string) gin.HandlerFunc {
	return func(c *gin.Context) {
		serviceURL, ok := h.serviceURLs[serviceName]
		if !ok {
			c.JSON(http.StatusBadGateway, gin.H{"error": fmt.Sprintf("Unknown service: %s", serviceName)})
			return
		}

		// Build target URL
		targetPath := c.Param("path")
		if targetPath == "" {
			targetPath = "/"
		}
		queryStr := c.Request.URL.RawQuery
		targetURL := serviceURL + targetPath
		if queryStr != "" {
			targetURL += "?" + queryStr
		}

		// Read request body
		var bodyBytes []byte
		if c.Request.Body != nil {
			bodyBytes, _ = io.ReadAll(c.Request.Body)
		}

		// Create upstream request
		req, err := http.NewRequestWithContext(
			c.Request.Context(),
			c.Request.Method,
			targetURL,
			bytes.NewReader(bodyBytes),
		)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create upstream request"})
			return
		}

		// Copy headers (preserve Content-Type, Authorization, etc.)
		for key, values := range c.Request.Header {
			// Skip hop-by-hop headers
			if isHopByHop(key) {
				continue
			}
			for _, v := range values {
				req.Header.Add(key, v)
			}
		}

		// Add X-Forwarded headers
		req.Header.Set("X-Forwarded-For", c.ClientIP())
		req.Header.Set("X-Forwarded-Host", c.Request.Host)
		req.Header.Set("X-Gateway", "orchestration")

		// Forward request
		resp, err := h.httpClient.Do(req)
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{
				"error":   "Upstream service unavailable",
				"service": serviceName,
				"detail":  err.Error(),
			})
			return
		}
		defer resp.Body.Close()

		// Copy response headers
		for key, values := range resp.Header {
			if isHopByHop(key) {
				continue
			}
			for _, v := range values {
				c.Header(key, v)
			}
		}

		// Stream response body
		respBody, err := io.ReadAll(resp.Body)
		if err != nil {
			c.JSON(http.StatusBadGateway, gin.H{"error": "Failed to read upstream response"})
			return
		}

		c.Data(resp.StatusCode, resp.Header.Get("Content-Type"), respBody)
	}
}

var hopByHopHeaders = map[string]bool{
	"connection":          true,
	"keep-alive":          true,
	"proxy-authenticate":  true,
	"proxy-authorization": true,
	"te":                  true,
	"trailers":            true,
	"transfer-encoding":   true,
	"upgrade":             true,
}

func isHopByHop(header string) bool {
	return hopByHopHeaders[strings.ToLower(header)]
}
