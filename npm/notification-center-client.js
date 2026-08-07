/** A small dependency-free producer client for NoticePlace (Node.js 18+). */

export class NotificationCenterError extends Error {
  constructor(message, { status, payload } = {}) {
    super(message);
    this.name = "NotificationCenterError";
    this.status = status;
    this.payload = payload || {};
  }
}

export class WaitTimeoutError extends NotificationCenterError {
  constructor(incident) {
    super("timed out waiting for an operator acknowledgement or resolution", { payload: incident });
    this.name = "WaitTimeoutError";
    this.incident = incident || {};
  }
}

export class NotificationCenterClient {
  static responseStates = new Set(["acknowledged", "resolved"]);

  constructor({ eventUrl, token, requestTimeoutMs = 8000, fetchImpl = globalThis.fetch }) {
    this.eventUrl = String(eventUrl || "").replace(/\/$/, "");
    this.token = token;
    this.requestTimeoutMs = requestTimeoutMs;
    this.fetchImpl = fetchImpl;
    if (!this.eventUrl.endsWith("/v1/events")) throw new TypeError("eventUrl must end with /v1/events");
    if (!this.token) throw new TypeError("token is required");
    if (typeof this.fetchImpl !== "function") throw new TypeError("fetch is required (Node.js 18+)");
    if (!(requestTimeoutMs > 0)) throw new TypeError("requestTimeoutMs must be positive");
    this.incidentsUrl = `${this.eventUrl.slice(0, -"/v1/events".length)}/v1/incidents`;
  }

  static fromEnvironment(options = {}) {
    const eventUrl = process.env.NOTIFY_CENTER_EVENT_URL;
    const token = process.env.NOTIFY_CENTER_TOKEN;
    if (!eventUrl || !token) throw new TypeError("NOTIFY_CENTER_EVENT_URL and NOTIFY_CENTER_TOKEN are required");
    return new NotificationCenterClient({ eventUrl, token, ...options });
  }

  async emit({
    project, severity, title, dedupKey, recipient = "me", body = "", kind = "incident",
    idempotencyKey = crypto.randomUUID(), waitForResponse = false, waitTimeoutMs = 3600000, pollIntervalMs = 10000,
  }) {
    const accepted = await this.#request("POST", this.eventUrl, {
      payload: { schema: "notify.event.v1", project, recipient, kind, severity, title, body, dedup_key: dedupKey },
      idempotencyKey,
    });
    accepted.idempotency_key = idempotencyKey;
    if (!waitForResponse) return accepted;
    return this.waitForResponse(accepted.incident_id, { timeoutMs: waitTimeoutMs, pollIntervalMs });
  }

  async getIncident(incidentId) {
    if (!incidentId) throw new TypeError("incidentId is required");
    return this.#request("GET", `${this.incidentsUrl}/${encodeURIComponent(incidentId)}`);
  }

  async waitForResponse(incidentId, { timeoutMs = 3600000, pollIntervalMs = 10000 } = {}) {
    if (!(timeoutMs > 0) || !(pollIntervalMs > 0)) throw new TypeError("timeoutMs and pollIntervalMs must be positive");
    const deadline = Date.now() + timeoutMs;
    let last;
    while (true) {
      last = await this.getIncident(incidentId);
      if (NotificationCenterClient.responseStates.has(last.state)) return last;
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new WaitTimeoutError(last);
      await new Promise((resolve) => setTimeout(resolve, Math.min(pollIntervalMs, remaining)));
    }
  }

  async #request(method, url, { payload, idempotencyKey } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.requestTimeoutMs);
    try {
      const headers = { Authorization: `Bearer ${this.token}`, Accept: "application/json" };
      if (payload !== undefined) headers["Content-Type"] = "application/json";
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
      const response = await this.fetchImpl(url, {
        method, headers, body: payload === undefined ? undefined : JSON.stringify(payload), signal: controller.signal,
      });
      let decoded;
      try { decoded = await response.json(); } catch { throw new NotificationCenterError("NoticePlace returned invalid JSON"); }
      if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) {
        throw new NotificationCenterError("NoticePlace returned an invalid response envelope");
      }
      if (!response.ok) {
        throw new NotificationCenterError(decoded.error || `NoticePlace returned HTTP ${response.status}`, { status: response.status, payload: decoded });
      }
      return decoded;
    } catch (error) {
      if (error instanceof NotificationCenterError) throw error;
      throw new NotificationCenterError("NoticePlace request failed");
    } finally {
      clearTimeout(timer);
    }
  }
}
