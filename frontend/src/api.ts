// Typed fetch client mirroring the backend Pydantic schemas (backend/app/schemas.py).
// Hand-written types, no codegen, no `any` (spec §7 / §10 quality bar).

// --------------------------------------------------------------------------- //
// Payload shapes (§6.4)
// --------------------------------------------------------------------------- //

export interface WinRateColor {
  win: number;
  loss: number;
  draw: number;
}

export interface PhaseErrors {
  blunders: number;
  mistakes: number;
  inaccuracies: number;
}

export interface OpeningSummary {
  name: string;
  eco: string;
  games: number;
  score_pct: number;
}

export interface TrendPoint {
  game_index: number;
  played_at: string;
  avg_cp_loss: number;
  result: string; // "win" | "loss" | "draw"
}

export interface BlunderBucket {
  move_bucket: string;
  count: number;
}

export interface SignatureLeak {
  headline: string;
  detail: string;
}

export interface ReportPayload {
  username: string;
  generated_at: string;
  games_analyzed: number;
  win_rate: Record<string, WinRateColor>;
  errors_by_phase: Record<string, PhaseErrors>;
  top_openings: OpeningSummary[];
  accuracy_trend: TrendPoint[];
  blunder_distribution_by_move: BlunderBucket[];
  signature_leak: SignatureLeak;
}

// --------------------------------------------------------------------------- //
// API response shapes (§5)
// --------------------------------------------------------------------------- //

export type ReportStatus = "queued" | "fetching" | "analyzing" | "done" | "failed";

export interface ReportCreateResponse {
  report_id: string;
  status: string; // "done" (cached) | "queued"
}

export interface ReportStatusResponse {
  status: ReportStatus;
  progress: number; // 0–100
  queue_position: number | null;
  payload: ReportPayload | null;
  error: string | null;
  username: string;
  total_new: number | null;
  analyzed_new: number | null;
}

export interface ReportByUsernameResponse {
  payload: ReportPayload | null;
  games_analyzed: number | null;
}

export interface FeaturedReportItem {
  username: string;
  report_id: string;
}

export interface FeaturedReportsResponse {
  featured: FeaturedReportItem[];
}

// --------------------------------------------------------------------------- //
// Client
// --------------------------------------------------------------------------- //

const API_BASE = "/api";

/** An HTTP error carrying the backend status code + the document-voice detail. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface ErrorBody {
  detail?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(0, "Network request failed; check your connection.");
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as ErrorBody;
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — keep the generic message */
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

/** POST /api/reports — freshness cache-hit (200) or queued (202); 404/409/429 throw ApiError. */
export function createReport(username: string): Promise<ReportCreateResponse> {
  return request<ReportCreateResponse>("/reports", {
    method: "POST",
    body: JSON.stringify({ username }),
  });
}

/** GET /api/reports/{report_id} — status + progress + honest queue_position. */
export function getReportStatus(reportId: string): Promise<ReportStatusResponse> {
  return request<ReportStatusResponse>(`/reports/${encodeURIComponent(reportId)}`);
}

/** GET /api/reports/by-username/{username} — latest done report payload, 404 if none. */
export function getReportByUsername(username: string): Promise<ReportByUsernameResponse> {
  return request<ReportByUsernameResponse>(
    `/reports/by-username/${encodeURIComponent(username)}`,
  );
}

/** GET /api/featured — pre-generated demo reports for the homepage. */
export function getFeatured(): Promise<FeaturedReportsResponse> {
  return request<FeaturedReportsResponse>("/featured");
}
