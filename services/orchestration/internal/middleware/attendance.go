package middleware

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
)

// AvailabilityResponse mirrors the attendance service's /attendance/availability/{user_id} response.
type AvailabilityResponse struct {
	UserID      string `json:"user_id"`
	IsAvailable bool   `json:"is_available"`
	Status      string `json:"status"` // present, absent, on_leave, not_checked_in
	Reason      string `json:"reason"`
}

// AttendanceBlocker returns a middleware that checks whether the authenticated user
// is available (checked in, not on leave) before allowing access to protected routes
// such as route planning and van settlement.
//
// If the attendance service is unreachable, the request is ALLOWED (fail-open) so that
// network issues do not block field operations. A warning header is added instead.
func AttendanceBlocker(attendanceServiceURL string) gin.HandlerFunc {
	client := &http.Client{Timeout: 5 * time.Second}

	return func(c *gin.Context) {
		// Extract user ID from JWT claims injected by JWTMiddleware
		userIDRaw, exists := c.Get("user_id")
		if !exists {
			c.JSON(http.StatusUnauthorized, gin.H{"error": "user identity not found in token"})
			c.Abort()
			return
		}
		userID := fmt.Sprintf("%v", userIDRaw)

		rawToken, _ := c.Get("raw_token")
		token := fmt.Sprintf("%v", rawToken)

		url := fmt.Sprintf("%s/attendance/availability/%s", attendanceServiceURL, userID)
		req, err := http.NewRequestWithContext(c.Request.Context(), "GET", url, nil)
		if err != nil {
			// Fail-open: cannot build request, let through with warning
			c.Header("X-Attendance-Check", "skipped:request-build-failed")
			c.Next()
			return
		}
		req.Header.Set("Authorization", "Bearer "+token)

		resp, err := client.Do(req)
		if err != nil {
			// Fail-open: attendance service unreachable
			c.Header("X-Attendance-Check", "skipped:service-unreachable")
			c.Next()
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			// Fail-open: unexpected status from attendance service
			c.Header("X-Attendance-Check", fmt.Sprintf("skipped:status-%d", resp.StatusCode))
			c.Next()
			return
		}

		body, _ := io.ReadAll(resp.Body)
		var availability AvailabilityResponse
		if err := json.Unmarshal(body, &availability); err != nil {
			// Fail-open: cannot parse response
			c.Header("X-Attendance-Check", "skipped:parse-error")
			c.Next()
			return
		}

		if !availability.IsAvailable {
			c.JSON(http.StatusForbidden, gin.H{
				"error":  "Operation blocked: user is not available for field operations",
				"status": availability.Status,
				"reason": availability.Reason,
			})
			c.Abort()
			return
		}

		c.Header("X-Attendance-Check", fmt.Sprintf("passed:%s", availability.Status))
		c.Next()
	}
}
