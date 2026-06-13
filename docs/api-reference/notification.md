# Notification Service API Reference

## Base URL
`http://localhost:8006` (direct) or `http://localhost:8000/notification` (via Orchestration)

---

## Notification Channels

| Channel | Technology | Use Case |
|---|---|---|
| **WebSocket** | FastAPI native WS | In-app real-time alerts (active sessions) |
| **FCM** | firebase-admin | Mobile push (Android/iOS background) |
| **Web Push** | pywebpush (VAPID) | Browser push notifications |
| **Email** | aiosmtplib | Async fallback, important alerts |

---

## WebSocket Connection

### Connect
```
WS ws://localhost:8006/ws/{user_id}?token={jwt_access_token}
```

The JWT is validated before accepting the connection. Invalid tokens receive close code `4001`.

**Heartbeat:**
```
Client → "ping"
Server → "pong"
```

**Incoming notification message:**
```json
{
  "type": "OrderRejected",
  "title": "Order Rejected",
  "body": "Order #INV-2024-0042 was rejected due to pricing mismatch.",
  "data": {
    "order_id": "uuid",
    "reason": "Price exception"
  },
  "timestamp": "2024-06-13T10:00:00Z"
}
```

### JavaScript Client Example
```javascript
const token = localStorage.getItem('access_token');
const userId = 'your-user-uuid';
const ws = new WebSocket(`ws://localhost:8006/ws/${userId}?token=${token}`);

ws.onmessage = (event) => {
  const notification = JSON.parse(event.data);
  console.log('Notification:', notification.title, notification.body);
  showToast(notification);
};

ws.onclose = (event) => {
  if (event.code === 4001) {
    console.error('JWT invalid — please re-login');
  } else {
    // Reconnect after 5s
    setTimeout(connectWebSocket, 5000);
  }
};

// Heartbeat
setInterval(() => ws.send('ping'), 30000);
```

---

## Web Push (VAPID) — Browser Notifications

### 1. Get VAPID Public Key
```
GET /subscriptions/vapid-public-key
```
**Response:**
```json
{"vapid_public_key": "BExampleVAPIDPublicKeyBase64Encoded..."}
```

### 2. Register Subscription
```
POST /subscriptions/webpush
Authorization: Bearer <token>
```
```json
{
  "endpoint": "https://fcm.googleapis.com/fcm/send/...",
  "keys": {
    "p256dh": "BGl...base64...",
    "auth": "MTI...base64..."
  },
  "user_agent": "Mozilla/5.0..."
}
```

### 3. Browser Integration Example
```javascript
// In your service worker registration
const registration = await navigator.serviceWorker.register('/sw.js');

// Get VAPID key
const resp = await fetch('/notification/subscriptions/vapid-public-key');
const { vapid_public_key } = await resp.json();

// Subscribe to push
const subscription = await registration.pushManager.subscribe({
  userVisibleOnly: true,
  applicationServerKey: urlBase64ToUint8Array(vapid_public_key)
});

// Register with backend
await fetch('/notification/subscriptions/webpush', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    endpoint: subscription.endpoint,
    keys: {
      p256dh: btoa(String.fromCharCode(...new Uint8Array(subscription.getKey('p256dh')))),
      auth: btoa(String.fromCharCode(...new Uint8Array(subscription.getKey('auth'))))
    }
  })
});
```

### 4. Service Worker (sw.js)
```javascript
self.addEventListener('push', (event) => {
  const data = event.data.json();
  self.registration.showNotification(data.title, {
    body: data.body,
    icon: '/icon-192x192.png',
    badge: '/badge-72x72.png',
    data: data.data
  });
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
```

---

## Send Notification (Internal)

Used by the Orchestration service or admin tools:

```
POST /send
Authorization: Bearer <token>
```
```json
{
  "event_type": "OrderRejected",
  "title": "Order Rejected",
  "body": "Your order was flagged for review.",
  "user_id": "user-uuid",
  "fcm_token": "device-fcm-token",
  "email_to": "salesrep@example.com",
  "extra_data": {
    "order_id": "order-uuid",
    "redirect": "/orders/order-uuid"
  }
}
```

---

## Health Check
```
GET /health
```
```json
{
  "status": "healthy",
  "service": "notification",
  "channels": {
    "websocket": "active",
    "fcm": "configured",
    "webpush": "configured",
    "email": "stub"
  }
}
```
