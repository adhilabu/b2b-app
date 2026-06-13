package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
)

// SyncPayload represents an offline push event batch from a mobile client.
type SyncPayload struct {
	DeviceID       string          `json:"device_id" binding:"required"`
	PushTimestamp  int64           `json:"push_timestamp"`
	Events         []SyncEvent     `json:"events" binding:"required"`
	LastWatermarks map[string]int64 `json:"last_watermarks"` // domain -> watermark
}

// SyncEvent is a single domain event from the mobile outbox.
type SyncEvent struct {
	EventID   string          `json:"event_id" binding:"required"`
	EventType string          `json:"event_type" binding:"required"`  // e.g., "OrderCreated"
	Domain    string          `json:"domain" binding:"required"`      // e.g., "sales"
	Timestamp int64           `json:"timestamp"`
	Payload   json.RawMessage `json:"payload"`
}

// SyncPushResponse is the response from a push sync.
type SyncPushResponse struct {
	Accepted int      `json:"accepted"`
	Failed   []string `json:"failed_event_ids"`
	Message  string   `json:"message"`
}

// SyncPullResponse is the aggregated delta from all downstream services.
type SyncPullResponse struct {
	Watermarks map[string]int64       `json:"watermarks"`
	Deltas     map[string]interface{} `json:"deltas"`
	FetchedAt  string                 `json:"fetched_at"`
}

// DomainEventRouter maps event types to downstream service endpoints.
var domainEventRouter = map[string]string{
	"OrderCreated":      "sales",
	"OrderUpdated":      "sales",
	"InvoiceCreated":    "sales",
	"SalesReturnCreated": "sales",
	"CustomerCreated":   "identity",
	"CustomerUpdated":   "identity",
	"AttendanceLogged":  "attendance",
	"LeaveRequested":    "attendance",
	"BeatPlanCreated":   "route",
	"RouteOptimized":    "route",
}

type SyncHandler struct {
	serviceURLs map[string]string
	httpClient  *http.Client
}

func NewSyncHandler(serviceURLs map[string]string) *SyncHandler {
	return &SyncHandler{
		serviceURLs: serviceURLs,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// Push handles the batched event push from mobile clients.
// Decodes events, routes to appropriate downstream services.
func (h *SyncHandler) Push(c *gin.Context) {
	var payload SyncPayload
	if err := c.ShouldBindJSON(&payload); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	rawToken, _ := c.Get("raw_token")
	token := fmt.Sprintf("%v", rawToken)

	accepted := 0
	var failed []string

	for _, event := range payload.Events {
		targetDomain, ok := domainEventRouter[event.EventType]
		if !ok {
			// Unknown event type — log and skip
			failed = append(failed, event.EventID)
			continue
		}

		serviceURL, hasURL := h.serviceURLs[targetDomain]
		if !hasURL {
			failed = append(failed, event.EventID)
			continue
		}

		// Forward event to the domain service
		if err := h.forwardEvent(c.Request.Context(), serviceURL, event, token); err != nil {
			failed = append(failed, event.EventID)
		} else {
			accepted++
		}
	}

	c.JSON(http.StatusOK, SyncPushResponse{
		Accepted: accepted,
		Failed:   failed,
		Message:  fmt.Sprintf("Processed %d events", len(payload.Events)),
	})
}

// Pull aggregates watermark deltas from all downstream services in parallel.
func (h *SyncHandler) Pull(c *gin.Context) {
	rawToken, _ := c.Get("raw_token")
	token := fmt.Sprintf("%v", rawToken)

	// Extract client watermarks from query params
	watermarks := map[string]int64{
		"identity":     getWatermark(c, "identity"),
		"catalog":      getWatermark(c, "catalog"),
		"sales":        getWatermark(c, "sales"),
		"route":        getWatermark(c, "route"),
		"attendance":   getWatermark(c, "attendance"),
	}

	// Parallel fetch from all services
	type domainDelta struct {
		domain string
		data   interface{}
		wm     int64
		err    error
	}

	results := make(chan domainDelta, len(watermarks))
	var wg sync.WaitGroup

	for domain, wm := range watermarks {
		wg.Add(1)
		go func(domain string, sinceWM int64) {
			defer wg.Done()
			serviceURL, ok := h.serviceURLs[domain]
			if !ok {
				results <- domainDelta{domain: domain, err: fmt.Errorf("no URL for domain %s", domain)}
				return
			}

			url := fmt.Sprintf("%s/sync/?since_version=%d", serviceURL, sinceWM)
			req, err := http.NewRequest("GET", url, nil)
			if err != nil {
				results <- domainDelta{domain: domain, err: err}
				return
			}
			req.Header.Set("Authorization", "Bearer "+token)

			resp, err := h.httpClient.Do(req)
			if err != nil {
				results <- domainDelta{domain: domain, err: err}
				return
			}
			defer resp.Body.Close()

			var data interface{}
			body, _ := io.ReadAll(resp.Body)
			json.Unmarshal(body, &data)

			results <- domainDelta{domain: domain, data: data, wm: sinceWM}
		}(domain, wm)
	}

	wg.Wait()
	close(results)

	deltas := make(map[string]interface{})
	newWatermarks := make(map[string]int64)

	for result := range results {
		if result.err != nil {
			deltas[result.domain] = gin.H{"error": result.err.Error()}
		} else {
			deltas[result.domain] = result.data
		}
		newWatermarks[result.domain] = result.wm
	}

	c.JSON(http.StatusOK, SyncPullResponse{
		Watermarks: newWatermarks,
		Deltas:     deltas,
		FetchedAt:  time.Now().UTC().Format(time.RFC3339),
	})
}

func (h *SyncHandler) forwardEvent(ctx context.Context, serviceURL string, event SyncEvent, token string) error {
	// Build event forwarding request to the appropriate domain service
	url := fmt.Sprintf("%s/sync/events", serviceURL)
	body, _ := json.Marshal(event)

	req, err := http.NewRequestWithContext(
		ctx,
		"POST", url,
		bytes.NewReader(body),
	)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")

	resp, err := h.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return fmt.Errorf("service returned %d", resp.StatusCode)
	}
	return nil
}

func getWatermark(c *gin.Context, domain string) int64 {
	v := c.Query(fmt.Sprintf("wm_%s", domain))
	if v == "" {
		return 0
	}
	var n int64
	fmt.Sscanf(v, "%d", &n)
	return n
}
