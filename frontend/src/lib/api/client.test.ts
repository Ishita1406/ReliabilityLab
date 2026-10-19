import { afterEach, describe, expect, it, vi } from "vitest";

import { api, apiBaseUrl, request } from "./client";

function respond(status: number, body: unknown, asJson = true) {
  return vi.fn().mockResolvedValue(
    new Response(asJson ? JSON.stringify(body) : String(body), {
      status,
      headers: { "content-type": asJson ? "application/json" : "text/html" },
    }),
  );
}

describe("API client", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("returns typed data on success and calls the configured backend", async () => {
    vi.stubEnv("RELIABILITYLAB_API_URL", "http://backend.test:9000/");
    const fetchMock = respond(200, [{ id: "evaluation-samples" }]);
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.suites();

    expect(result).toEqual({ ok: true, data: [{ id: "evaluation-samples" }] });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe("http://backend.test:9000/api/suites");
    expect(init).toMatchObject({ method: "GET", cache: "no-store" });
    expect(apiBaseUrl()).toBe("http://backend.test:9000");
  });

  it("classifies 404 as not_found with the API's detail", async () => {
    vi.stubGlobal(
      "fetch",
      respond(404, { error: "not_found", detail: "experiment 'x' not found" }),
    );
    const result = await api.experiment("x");
    expect(result).toEqual({
      ok: false,
      error: { kind: "not_found", status: 404, message: "experiment 'x' not found" },
    });
  });

  it("joins FastAPI validation details", async () => {
    vi.stubGlobal(
      "fetch",
      respond(422, { detail: [{ msg: "Field required" }, { msg: "Too long" }] }),
    );
    const result = await request("/api/x");
    expect(result).toEqual({
      ok: false,
      error: { kind: "http", status: 422, message: "Field required; Too long" },
    });
  });

  it("reports an unreachable backend as a network error instead of throwing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const result = await api.overview();
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("network");
      expect(result.error.status).toBeNull();
      expect(result.error.message).toContain("fetch failed");
    }
  });

  it("rejects non-JSON responses", async () => {
    vi.stubGlobal("fetch", respond(502, "<html>Bad gateway</html>", false));
    const result = await api.overview();
    expect(result).toEqual({
      ok: false,
      error: {
        kind: "invalid_response",
        status: 502,
        message: "The API returned a non-JSON response (HTTP 502).",
      },
    });
  });

  it("sends JSON bodies and encodes path segments and query parameters", async () => {
    const fetchMock = respond(201, { id: "hrv_1" });
    vi.stubGlobal("fetch", fetchMock);
    await api.submitReview("run/../x", {
      reviewer: "alice",
      label: "pass",
      blind: true,
    });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain("/api/runs/run%2F..%2Fx/reviews");
    expect(init).toMatchObject({
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reviewer: "alice", label: "pass", blind: true }),
    });

    await api.comparison("exp_a", "exp b");
    expect(String(fetchMock.mock.calls[1]![0])).toContain("baseline=exp_a&candidate=exp+b");
  });
});
