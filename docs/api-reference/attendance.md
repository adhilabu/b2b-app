# Attendance Service API Reference

## Base URL
`http://localhost:8005` (direct) or `http://localhost:8000/attendance` (via Orchestration)

All endpoints require a valid JWT Bearer Token in the headers:
`Authorization: Bearer <access_token>`

---

## Attendance & Check-In/Out

### User Check-In
```
POST /attendance/check-in
```
Records the starting of the shift for the current user with geographical coordinates and timestamp.

#### Request Body
```json
{
  "latitude": 12.9715987,
  "longitude": 77.5945627,
  "notes": "Checked in from main depot"
}
```

#### Response (200 OK)
```json
{
  "id": "76495df7-df42-4211-9a74-d4b971a17c2f",
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "attendance_date": "2026-06-13",
  "status": "present",
  "check_in_at": "2026-06-13T09:00:00Z",
  "check_out_at": null,
  "notes": "Checked in from main depot"
}
```

---

### User Check-Out
```
POST /attendance/check-out
```
Records the ending of the shift for the current user. Requires a check-in record to exist for today.

#### Request Body
```json
{
  "latitude": 12.9789,
  "longitude": 77.6432,
  "notes": "Completed South Zone beat"
}
```

#### Response (200 OK)
```json
{
  "id": "76495df7-df42-4211-9a74-d4b971a17c2f",
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "attendance_date": "2026-06-13",
  "status": "present",
  "check_in_at": "2026-06-13T09:00:00Z",
  "check_out_at": "2026-06-13T18:00:00Z",
  "notes": "Checked in from main depot"
}
```

---

### List Attendance Records
```
GET /attendance/
```
Retrieve a list of historical attendance records for the tenant. Managers can filter by user or date range.

#### Query Parameters
- `user_id` (optional): Filter records by user UUID.
- `from_date` (optional): Filter records from this date inclusive (format: `YYYY-MM-DD`).
- `to_date` (optional): Filter records to this date inclusive (format: `YYYY-MM-DD`).

#### Response (200 OK)
```json
[
  {
    "id": "76495df7-df42-4211-9a74-d4b971a17c2f",
    "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
    "attendance_date": "2026-06-13",
    "status": "present",
    "check_in_at": "2026-06-13T09:00:00Z",
    "check_out_at": "2026-06-13T18:00:00Z",
    "notes": "Checked in from main depot"
  }
]
```

---

### Check User Operational Availability
```
GET /attendance/availability/{user_id}
```
Checks if a user is operationally available for routes, assignments, or end-of-day settlement based on active leaves or marked attendance status.

#### Query Parameters
- `check_date` (optional): The date to check availability for (format: `YYYY-MM-DD`, defaults to today).

#### Response (200 OK - Available)
```json
{
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "check_date": "2026-06-13",
  "is_available": true,
  "status": "present",
  "reason": "Available"
}
```

#### Response (200 OK - Unavailable due to leave)
```json
{
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "check_date": "2026-06-14",
  "is_available": false,
  "status": "on_leave",
  "reason": "Approved casual leave"
}
```

---

## Leave Management

### Create Leave Request
```
POST /leaves/
```
Submit a new leave request for approval.

#### Request Body
```json
{
  "leave_type": "casual",
  "from_date": "2026-06-14",
  "to_date": "2026-06-16",
  "reason": "Family function"
}
```

#### Response (201 Created)
```json
{
  "id": "b96837df-42fa-45b6-bfd4-bb07fa1d24bc",
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "leave_type": "casual",
  "from_date": "2026-06-14",
  "to_date": "2026-06-16",
  "status": "pending",
  "reason": "Family function",
  "created_at": "2026-06-13T18:55:21Z"
}
```

---

### Approve Leave Request
```
PATCH /leaves/{leave_id}/approve
```
Approves a pending leave request. Requires manager or admin role.

#### Response (200 OK)
```json
{
  "id": "b96837df-42fa-45b6-bfd4-bb07fa1d24bc",
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "leave_type": "casual",
  "from_date": "2026-06-14",
  "to_date": "2026-06-16",
  "status": "approved",
  "reason": "Family function",
  "created_at": "2026-06-13T18:55:21Z"
}
```

---

### Reject Leave Request
```
PATCH /leaves/{leave_id}/reject
```
Rejects a pending leave request. Requires manager or admin role.

#### Response (200 OK)
```json
{
  "id": "b96837df-42fa-45b6-bfd4-bb07fa1d24bc",
  "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
  "leave_type": "casual",
  "from_date": "2026-06-14",
  "to_date": "2026-06-16",
  "status": "rejected",
  "reason": "Family function",
  "created_at": "2026-06-13T18:55:21Z"
}
```

---

### List Leave Requests
```
GET /leaves/
```
Lists leave requests for the user's tenant.

#### Query Parameters
- `user_id` (optional): Filter leaves by user UUID.
- `status` (optional): Filter leaves by status (`pending`, `approved`, `rejected`, `cancelled`).

#### Response (200 OK)
```json
[
  {
    "id": "b96837df-42fa-45b6-bfd4-bb07fa1d24bc",
    "user_id": "ca761232-ed42-11ed-a05b-0242ac120003",
    "leave_type": "casual",
    "from_date": "2026-06-14",
    "to_date": "2026-06-16",
    "status": "approved",
    "reason": "Family function",
    "created_at": "2026-06-13T18:55:21Z"
  }
]
```
